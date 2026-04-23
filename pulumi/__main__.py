"""Pulumi infrastructure for IDI Company Information Pipeline (ECS Fargate).

Imports all resource modules (creation order matters) and exports stack outputs.
"""

# Import order matters: config first, then resources by dependency
from infra import ecr, ecs, iam, networking, scheduling, secrets, storage

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

# ECR
pulumi.export("ecr_repo_url", ecr.ecr_repo.repository_url)
pulumi.export("ecr_orchestrator_image", ecr.orchestrator_image)

# ECS
pulumi.export("ecs_cluster_arn", ecs.cluster.arn)
pulumi.export("ecs_cluster_name", ecs.cluster.name)
pulumi.export("task_definition_arn", ecs.task_definition.arn)
pulumi.export("log_group_name", ecs.log_group.name)

# Storage
pulumi.export("processor_bucket_name", storage.processor_bucket.id)
pulumi.export("processor_bucket_arn", storage.processor_bucket.arn)

# Secrets
pulumi.export("permid_secret_arn", secrets.permid_secret.arn)
pulumi.export("permid_secret_name", secrets.permid_secret.name)

# Scheduling
pulumi.export("schedule_cik_name", scheduling.schedule_cik.name)
pulumi.export("schedule_cik_arn", scheduling.schedule_cik.arn)
pulumi.export("schedule_cusip_name", scheduling.schedule_cusip.name)
pulumi.export("schedule_cusip_arn", scheduling.schedule_cusip.arn)
pulumi.export("scheduler_role_arn", scheduling.scheduler_role.arn)
pulumi.export("scheduler_role_name", scheduling.scheduler_role.name)
pulumi.export("shared_dlq_arn", scheduling.shared_dlq.arn)
