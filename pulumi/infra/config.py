"""Pulumi configuration and shared constants."""

from pathlib import Path

import pulumi_aws as aws

import pulumi

# -----------------------------------------------------------------------------
# Paths (infra/ is inside pulumi/, so parent.parent = pulumi dir)
# -----------------------------------------------------------------------------
PULUMI_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PULUMI_DIR / "templates"
COMPOSE_PATH = PULUMI_DIR.parent / "docker-compose.yml"

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
config = pulumi.Config()
project_name = pulumi.get_project()
stack_name = pulumi.get_stack()
name_prefix = f"{project_name}-{stack_name}"

# AWS
aws_config = pulumi.Config("aws")
aws_region = aws_config.require("region")
caller = aws.get_caller_identity()


def tags(extra: dict | None = None) -> dict:
    """Common resource tags."""
    t = {
        "project": project_name,
        "environment": stack_name,
        "managed_by": "Pulumi",
    }
    if extra:
        t.update(extra)
    return t
