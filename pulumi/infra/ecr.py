"""ECR image URIs for CI-pushed containers."""

import pulumi
import pulumi_aws as aws


from . import config

# -----------------------------------------------------------------------------
# ECR Registry & Image URIs
# -----------------------------------------------------------------------------
ecr_registry = pulumi.Output.from_input(config.caller.account_id).apply(
    lambda aid: f"{aid}.dkr.ecr.{config.aws_region}.amazonaws.com"
)

ecr_repo = aws.ecr.Repository("idi-ecr-orchestrator", name=f"{config.name_prefix}-company-info-orchestrator")

orchestrator_image = ecr_registry.apply(
    lambda r: f"{r}/{config.name_prefix}-company-info-orchestrator:latest"
)
