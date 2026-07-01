"""ECS cluster and Fargate task definition for the company-info orchestrator.

Per-pipeline arguments (input file, type, batch size, etc.) are injected by the
EventBridge schedules via ECS containerOverrides — see scheduling.py. The task
definition's baseline command is `--help` so a misconfigured override fails
loudly instead of silently running a default pipeline.
"""

import json

import pulumi_aws as aws

import pulumi

from . import config, ecr, iam, logs, secrets

# -----------------------------------------------------------------------------
# ECS Cluster (Fargate only)
# -----------------------------------------------------------------------------
cluster = aws.ecs.Cluster(
    "idi-ecs-cluster",
    name=f"{config.name_prefix}-cluster",
    settings=[
        aws.ecs.ClusterSettingArgs(
            name="containerInsights",
            value="enabled",
        )
    ],
    tags=config.tags(),
)

# -----------------------------------------------------------------------------
# Task Definition
# -----------------------------------------------------------------------------
CONTAINER_NAME = "company-info-orchestrator"

cpu = config.config.get("cpu") or "1024"
memory = config.config.get("memory") or "4096"
# GeoNames username — a public credential (not a secret). Injected as a baseline
# env var so the orchestrator has it for ANY invocation (manual `run-task`,
# disabled-schedule test runs), not only when the EventBridge schedule supplies
# `--geonames-user`. The CLI accepts the GEONAMES_USER env var as the equivalent.
geonames_user = config.config.require("geonames_user")

container_definitions = pulumi.Output.all(
    image=ecr.orchestrator_image,
    log_group_name=logs.log_group.name,
    region=config.aws_region,
    secret_arn=secrets.permid_api_key_param.arn,
).apply(
    lambda args: json.dumps(
        [
            {
                "name": CONTAINER_NAME,
                "image": args["image"],
                "essential": True,
                "command": ["--help"],
                "environment": [
                    {"name": "AWS_REGION", "value": args["region"]},
                    {"name": "CLOUDWATCH_LOGS_ENABLED", "value": "false"},
                    {"name": "PYTHONUNBUFFERED", "value": "1"},
                    {"name": "GEONAMES_USER", "value": geonames_user},
                ],
                "secrets": [
                    {
                        "name": "PERMID_API_KEY",
                        "valueFrom": args["secret_arn"],
                    }
                ],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": args["log_group_name"],
                        "awslogs-region": args["region"],
                        "awslogs-stream-prefix": "orchestrator",
                    },
                },
                "stopTimeout": 30,
            }
        ]
    )
)

task_definition = aws.ecs.TaskDefinition(
    "idi-ecs-task-definition",
    family=f"{config.name_prefix}",
    requires_compatibilities=["FARGATE"],
    network_mode="awsvpc",
    cpu=cpu,
    memory=memory,
    execution_role_arn=iam.task_execution_role.arn,
    task_role_arn=iam.task_role.arn,
    container_definitions=container_definitions,
    tags=config.tags(),
)

# -----------------------------------------------------------------------------
# Aggregate Task Definition (on-demand `aws ecs run-task`)
#
# Runs the final-output aggregator (idi_company_info.output) instead of the
# orchestrator.
# -----------------------------------------------------------------------------
AGGREGATE_CONTAINER_NAME = "company-info-aggregate"

aggregate_container_definitions = pulumi.Output.all(
    image=ecr.orchestrator_image,
    log_group_name=logs.log_group.name,
    region=config.aws_region,
).apply(
    lambda args: json.dumps(
        [
            {
                "name": AGGREGATE_CONTAINER_NAME,
                "image": args["image"],
                "essential": True,
                "entryPoint": ["python", "-m", "idi_company_info.output"],
                "command": ["--help"],
                "environment": [
                    {"name": "AWS_REGION", "value": args["region"]},
                    {"name": "CLOUDWATCH_LOGS_ENABLED", "value": "false"},
                    {"name": "PYTHONUNBUFFERED", "value": "1"},
                ],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": args["log_group_name"],
                        "awslogs-region": args["region"],
                        "awslogs-stream-prefix": "aggregate",
                    },
                },
                "stopTimeout": 30,
            }
        ]
    )
)

aggregate_task_definition = aws.ecs.TaskDefinition(
    "idi-ecs-aggregate-task-definition",
    family=f"{config.name_prefix}-aggregate",
    requires_compatibilities=["FARGATE"],
    network_mode="awsvpc",
    cpu=cpu,
    memory=memory,
    execution_role_arn=iam.task_execution_role.arn,
    task_role_arn=iam.task_role.arn,
    container_definitions=aggregate_container_definitions,
    tags=config.tags(),
)
