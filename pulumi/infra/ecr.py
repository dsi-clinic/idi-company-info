"""ECR repository for the company-info orchestrator image."""

import json

import pulumi_aws as aws

import pulumi

from . import config

# -----------------------------------------------------------------------------
# ECR Registry & Image URIs
# -----------------------------------------------------------------------------
ecr_registry = pulumi.Output.from_input(config.caller.account_id).apply(
    lambda aid: f"{aid}.dkr.ecr.{config.aws_region}.amazonaws.com"
)

ecr_repo = aws.ecr.Repository(
    "idi-ecr-orchestrator",
    name=f"{config.name_prefix}-orchestrator",
    force_delete=True,
    tags=config.tags(),
)

# Using `:latest` means each scheduled run pulls whatever image was most recently
# pushed; Pulumi will NOT cut a new task-definition revision on app-only deploys
# (the resolved string never changes). Rollback via `pulumi up` against an older
# commit therefore has no effect — to roll back you must re-push the older image
# to `:latest`. If strict rollback-via-pulumi is ever required, switch to an
# `idi:image_version` config passed from CI so each deploy produces a new revision.
orchestrator_image = ecr_registry.apply(lambda r: f"{r}/{config.name_prefix}-orchestrator:latest")

# Lifecycle policy — expire images beyond the retention count
ecr_lifecycle_policy = aws.ecr.LifecyclePolicy(
    "idi-ecr-lifecycle",
    repository=ecr_repo.name,
    policy=json.dumps(
        {
            "rules": [
                {
                    "rulePriority": 1,
                    "description": f"Keep last {config.ecr_image_count} images",
                    "selection": {
                        "tagStatus": "any",
                        "countType": "imageCountMoreThan",
                        "countNumber": config.ecr_image_count,
                    },
                    "action": {"type": "expire"},
                }
            ]
        }
    ),
)
