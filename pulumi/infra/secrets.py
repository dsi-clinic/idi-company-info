"""AWS Secrets Manager resources for the PermID API key."""

import pulumi_aws as aws

import pulumi

from . import config

# -----------------------------------------------------------------------------
# Config (required — Pulumi fails at deploy time if missing)
# -----------------------------------------------------------------------------
permid_api_key = config.config.require_secret("permid_api_key")

# -----------------------------------------------------------------------------
# Secret
# -----------------------------------------------------------------------------
permid_secret = aws.secretsmanager.Secret(
    "idi-secret-permid-api-key",
    name=f"{config.name_prefix}-permid-api-key",
    description="PermID API Key for company information queries",
    tags=config.tags(),
)

permid_secret_version = aws.secretsmanager.SecretVersion(
    "idi-secret-version-permid",
    secret_id=permid_secret.id,
    secret_string=permid_api_key,
    opts=pulumi.ResourceOptions(
        depends_on=[permid_secret],
        ignore_changes=["secret_string"],
    ),
)
