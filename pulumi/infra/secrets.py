"""Genuine secrets as SSM Parameter Store SecureString parameters.

The PermID API key and GeoNames username are stored as SSM `SecureString`
parameters. The ECS task definition injects each by ARN via `secrets:`, so the
values never touch CI logs or state. Rotation is a `put-parameter --overwrite`,
picked up at the next task launch — no deploy.
"""

import pulumi_aws as aws

import pulumi

from . import config

_secrets_prefix = f"/idi/{config.stack_name}/{config.app_name}/secrets"

permid_api_key_param = aws.ssm.Parameter(
    "idi-ssm-secret-permid-api-key",
    name=f"{_secrets_prefix}/permid_api_key",
    type="SecureString",
    # Placeholder only — the real value is set out-of-band and never managed by
    # Pulumi (hence ignore_changes), so it stays out of git and state.
    value="PLACEHOLDER-set-via-aws-ssm-put-parameter",
    description="PermID API key (real value set out-of-band).",
    tags=config.tags(),
    opts=pulumi.ResourceOptions(ignore_changes=["value"]),
)

geonames_user_param = aws.ssm.Parameter(
    "idi-ssm-secret-geonames-user",
    name=f"{_secrets_prefix}/geonames_user",
    type="SecureString",
    # Placeholder only — the real value is set out-of-band and never managed by
    # Pulumi (hence ignore_changes), so it stays out of git and state.
    value="PLACEHOLDER-set-via-aws-ssm-put-parameter",
    description="GeoNames username (real value set out-of-band).",
    tags=config.tags(),
    opts=pulumi.ResourceOptions(ignore_changes=["value"]),
)
