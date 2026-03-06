"""ECR image URIs for CI-pushed containers."""

import pulumi

from . import config

# -----------------------------------------------------------------------------
# ECR Registry & Image URIs
# -----------------------------------------------------------------------------
ecr_registry = pulumi.Output.from_input(config.caller.account_id).apply(
    lambda aid: f"{aid}.dkr.ecr.{config.aws_region}.amazonaws.com"
)
orchestrator_image = ecr_registry.apply(
    lambda r: f"{r}/{config.name_prefix}-company-info-orchestrator:latest"
)
