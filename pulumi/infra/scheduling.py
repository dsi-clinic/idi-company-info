"""EventBridge Schedules (CIK + CUSIP) for triggering Fargate tasks on cron.

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
# Per-pipeline schedules
# -----------------------------------------------------------------------------
schedule_enabled = config.config.require("schedule_enabled") == "true"
geonames_user = config.config.require("geonames_user")
buffer_size = config.config.require("buffer_size")
threshold_days = config.config.require("threshold_days")
match_score_threshold = config.config.require("match_score_threshold")


def _container_override_input(pipeline_type: str, input_file: str, batch_size: str) -> str:
    """Build the EventBridge Scheduler `input` JSON for an ECS containerOverride.

    The output-directory is rooted at the Pulumi-managed bucket and partitioned
    by pipeline type so CIK and CUSIP runs don't collide. All inputs resolve to
    plain strings at plan time, so no Pulumi Output wrapping is needed.
    """
    command = [
        "--input-file",
        input_file,
        "--output-directory",
        f"s3://{config.bucket_name}/{pipeline_type}/output",
        "--type",
        pipeline_type,
        "--geonames-user",
        geonames_user,
        "--batch-size",
        batch_size,
        "--buffer-size",
        buffer_size,
        "--match-score-threshold",
        match_score_threshold,
    ]
    if threshold_days:
        command += ["--threshold-days", threshold_days]

    return json.dumps({"containerOverrides": [{"name": ecs.CONTAINER_NAME, "command": command}]})


def _build_schedule(
    pipeline_type: str,
    cron_key: str,
    input_file_key: str,
    batch_size_key: str,
) -> aws.scheduler.Schedule:
    schedule_expression = config.config.require(cron_key)
    input_file = config.config.require(input_file_key)
    batch_size = config.config.require(batch_size_key)
    return aws.scheduler.Schedule(
        f"idi-schedule-{pipeline_type}",
        name=f"{config.name_prefix}-schedule-{pipeline_type}",
        description=f"Triggers the {config.app_name} {pipeline_type} orchestrator ECS task",
        schedule_expression=schedule_expression,
        flexible_time_window=aws.scheduler.ScheduleFlexibleTimeWindowArgs(mode="OFF"),
        state="ENABLED" if schedule_enabled else "DISABLED",
        target=aws.scheduler.ScheduleTargetArgs(
            arn=ecs.cluster.arn,
            role_arn=scheduler_role.arn,
            input=_container_override_input(pipeline_type, input_file, batch_size),
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


schedule_cik = _build_schedule(
    pipeline_type="cik",
    cron_key="cron_cik",
    input_file_key="input_file_cik",
    batch_size_key="batch_size_cik",
)

schedule_cusip = _build_schedule(
    pipeline_type="cusip",
    cron_key="cron_cusip",
    input_file_key="input_file_cusip",
    batch_size_key="batch_size_cusip",
)
