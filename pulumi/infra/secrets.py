"""AWS Secrets Manager resources for required API credentials."""

import pulumi_aws as aws

import pulumi

from . import config, iam

# -----------------------------------------------------------------------------
# Config (required — Pulumi fails at deploy time if either is missing)
# -----------------------------------------------------------------------------
permid_api_key = config.config.require_secret("permid_api_key")
geonames_user = config.config.require("geonames_user")

# -----------------------------------------------------------------------------
# Secrets
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

geonames_secret = aws.secretsmanager.Secret(
    "idi-secret-geonames-user",
    name=f"{config.name_prefix}-geonames-user",
    description="GeoNames username for geocoding",
    tags=config.tags(),
)

geonames_secret_version = aws.secretsmanager.SecretVersion(
    "idi-secret-version-geonames",
    secret_id=geonames_secret.id,
    secret_string=geonames_user,
    opts=pulumi.ResourceOptions(
        depends_on=[geonames_secret],
        ignore_changes=["secret_string"],
    ),
)

# IAM policy to allow reading secrets
secrets_policy = aws.iam.RolePolicy(
    "idi-policy-secrets-access",
    role=iam.ec2_role.id,
    policy=pulumi.Output.json_dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "secretsmanager:GetSecretValue",
                        "secretsmanager:DescribeSecret",
                    ],
                    "Resource": [permid_secret.arn, geonames_secret.arn],
                }
            ],
        }
    ),
)
