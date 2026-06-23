"""EventBridge Schedules for triggering Fargate tasks on cron.

One schedule is built per entry in the structured `idi:input_sources` config, so
the set of pipelines that run is config-driven (see Pulumi.<stack>.yaml).

The SQS dead-letter queue is owned by the sibling `idi-corporate-structure`
project and shared across schedulers; looked up here by name.
"""

import json

import pulumi_aws as aws

import pulumi

from . import config, ecs, iam, networking

# -----------------------------------------------------------------------------
# Shared DLQ (looked up by name — owned by a separate project/stack)
# Required config: deploy must set `idi:shared_dlq_name` per stack.
# -----------------------------------------------------------------------------
shared_dlq = aws.sqs.get_queue_output(name=config.shared_dlq_name)

# -----------------------------------------------------------------------------
# Scheduler IAM Role — allows EventBridge to run ECS tasks and send to DLQ
# -----------------------------------------------------------------------------
scheduler_role = aws.iam.Role(
    "idi-role-scheduler",
    name=f"{config.name_prefix}-role-scheduler",
    description="EventBridge Scheduler role: run ECS tasks, pass roles, send to DLQ",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "scheduler.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    ),
    tags=config.tags(),
)

scheduler_policy = aws.iam.RolePolicy(
    "idi-policy-scheduler",
    role=scheduler_role.id,
    policy=pulumi.Output.all(
        task_execution_role_arn=iam.task_execution_role.arn,
        task_role_arn=iam.task_role.arn,
        dlq_arn=shared_dlq.arn,
        task_definition_arn=ecs.task_definition.arn,
    ).apply(
        lambda args: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "ecs:RunTask",
                        "Resource": args["task_definition_arn"],
                    },
                    {
                        "Effect": "Allow",
                        "Action": "iam:PassRole",
                        "Resource": [
                            args["task_execution_role_arn"],
                            args["task_role_arn"],
                        ],
                    },
                    {
                        "Effect": "Allow",
                        "Action": "sqs:SendMessage",
                        "Resource": args["dlq_arn"],
                    },
                ],
            }
        )
    ),
)

# -----------------------------------------------------------------------------
# Per-input-source schedules
#
# The set of pipelines to run is driven by the structured `input_sources` config
# (one entry per scheduled source), so adding/removing a source is a config-only
# change. Shared scalars below apply to every source.
# -----------------------------------------------------------------------------
schedule_enabled = config.config.require("schedule_enabled") == "true"
geonames_user = config.config.require("geonames_user")
buffer_size = config.config.require("buffer_size")
threshold_days = config.config.require("threshold_days")
match_score_threshold = config.config.require("match_score_threshold")
output_dir = config.config.require("output_dir")

# Each entry: {"source": str, "input_file": str, "cron": str, "batch_size": str}
input_sources = config.config.require_object("input_sources")


def _container_override_input(
    source: str,
    input_file: str,
    batch_size: str,
) -> str:
    """Build the EventBridge Scheduler `input` JSON for an ECS containerOverride.

    The output directory is shared across sources; the orchestrator partitions
    writes (results, permid cache, failures) into a per-source subdirectory at
    runtime. All inputs resolve to plain strings at plan time, so no Pulumi
    Output wrapping is needed.
    """
    command = [
        "--input-type",
        source,
        "--input-file",
        input_file,
        "--output-directory",
        output_dir,
        "--geonames-user",
        geonames_user,
        "--batch-size",
        str(batch_size),
        "--buffer-size",
        str(buffer_size),
        "--match-score-threshold",
        match_score_threshold,
    ]
    if threshold_days:
        command += ["--threshold-days", threshold_days]

    return json.dumps({"containerOverrides": [{"name": ecs.CONTAINER_NAME, "command": command}]})


def _build_schedule(entry: dict) -> aws.scheduler.Schedule:
    """Build one EventBridge schedule from an `input_sources` entry."""
    source = entry["source"]
    input_file = entry["input_file"]
    return aws.scheduler.Schedule(
        f"idi-schedule-{source}",
        name=f"{config.name_prefix}-schedule-{source}",
        description=f"Triggers the {config.app_name} {source} orchestrator ECS task",
        schedule_expression=entry["cron"],
        flexible_time_window=aws.scheduler.ScheduleFlexibleTimeWindowArgs(mode="OFF"),
        state="ENABLED" if schedule_enabled else "DISABLED",
        target=aws.scheduler.ScheduleTargetArgs(
            arn=ecs.cluster.arn,
            role_arn=scheduler_role.arn,
            input=_container_override_input(
                source,
                input_file,
                entry["batch_size"],
            ),
            ecs_parameters=aws.scheduler.ScheduleTargetEcsParametersArgs(
                task_definition_arn=ecs.task_definition.arn,
                launch_type="FARGATE",
                platform_version="LATEST",
                enable_execute_command=True,
                propagate_tags="TASK_DEFINITION",
                network_configuration=aws.scheduler.ScheduleTargetEcsParametersNetworkConfigurationArgs(
                    assign_public_ip=True,
                    subnets=[networking.primary_subnet_id],
                    security_groups=[networking.ecs_sg.id],
                ),
            ),
            retry_policy=aws.scheduler.ScheduleTargetRetryPolicyArgs(
                maximum_retry_attempts=2,
                maximum_event_age_in_seconds=3600,
            ),
            dead_letter_config=aws.scheduler.ScheduleTargetDeadLetterConfigArgs(
                arn=shared_dlq.arn,
            ),
        ),
    )


# Keyed by source slug so __main__ can export each schedule's name/arn.
schedules: dict[str, aws.scheduler.Schedule] = {
    entry["source"]: _build_schedule(entry) for entry in input_sources
}
