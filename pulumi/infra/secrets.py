"""AWS Secrets Manager (optional)."""

import pulumi_aws as aws

import pulumi

from . import config, iam

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
permid_api_key = config.config.get_secret("permid_api_key")
geonames_user = config.config.get("geonames_user")

# -----------------------------------------------------------------------------
# Secrets
# -----------------------------------------------------------------------------
secrets_created = []

if permid_api_key:
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
    secrets_created.append(permid_secret.arn)

if geonames_user:
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
    secrets_created.append(geonames_secret.arn)

# IAM policy to allow reading secrets
if secrets_created:
    secrets_policy = aws.iam.RolePolicy(
        "idi-policy-secrets-access",
        role=iam.ec2_role.id,
        policy=pulumi.Output.json_dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": [
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:DescribeSecret",
                ],
                "Resource": secrets_created,
            }],
        }),
    )
