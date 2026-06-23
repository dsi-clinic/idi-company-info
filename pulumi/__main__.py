"""Pulumi infrastructure for IDI Company Information Pipeline (ECS Fargate).

Imports all resource modules (creation order matters) and exports stack outputs.
"""

# Import order matters: config first, then resources by dependency
from infra import ecr, ecs, iam, logs, networking, scheduling, secrets

import pulumi

# -----------------------------------------------------------------------------
# Exports
# -----------------------------------------------------------------------------

# Networking
pulumi.export("default_vpc_id", networking.default_vpc.id)
pulumi.export("ecs_sg_id", networking.ecs_sg.id)
pulumi.export("ecs_sg_name", networking.ecs_sg.name)
pulumi.export("primary_subnet_id", networking.primary_subnet_id)

# IAM
pulumi.export("task_execution_role_arn", iam.task_execution_role.arn)
pulumi.export("task_execution_role_name", iam.task_execution_role.name)
pulumi.export("task_role_arn", iam.task_role.arn)
pulumi.export("task_role_name", iam.task_role.name)

# Logs
pulumi.export("log_group_arn", logs.log_group.arn)
pulumi.export("log_group_name", logs.log_group.name)
pulumi.export("log_group_retention_days", logs.log_group.retention_in_days)

# ECR
pulumi.export("ecr_repo_url", ecr.ecr_repo.repository_url)
pulumi.export("ecr_orchestrator_image", ecr.orchestrator_image)

# ECS
pulumi.export("ecs_cluster_arn", ecs.cluster.arn)
pulumi.export("ecs_cluster_name", ecs.cluster.name)
pulumi.export("task_definition_arn", ecs.task_definition.arn)
pulumi.export("aggregate_task_definition_arn", ecs.aggregate_task_definition.arn)

# Secrets
pulumi.export("permid_secret_arn", secrets.permid_secret.arn)
pulumi.export("permid_secret_name", secrets.permid_secret.name)

# Scheduling — one schedule per configured input source
for _source, _schedule in scheduling.schedules.items():
    pulumi.export(f"schedule_{_source}_name", _schedule.name)
    pulumi.export(f"schedule_{_source}_arn", _schedule.arn)
pulumi.export("scheduler_role_arn", scheduling.scheduler_role.arn)
pulumi.export("scheduler_role_name", scheduling.scheduler_role.name)
pulumi.export("shared_dlq_arn", scheduling.shared_dlq.arn)
