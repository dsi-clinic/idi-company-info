"""S3 bucket and IAM policies for storage."""

import json

import pulumi_aws as aws

from . import config
from . import iam

# -----------------------------------------------------------------------------
# Processor S3 Bucket
# -----------------------------------------------------------------------------
processor_bucket = aws.s3.BucketV2(
    "idi-processor-s3",
    bucket=f"{config.name_prefix}-processor-s3",
    force_destroy=True,
    tags=config.tags({"Name": f"{config.name_prefix}-processor-s3"}),
)

processor_bucket_public_access_block = aws.s3.BucketPublicAccessBlock(
    "idi-processor-s3-public-block",
    bucket=processor_bucket.id,
    block_public_acls=True,
    block_public_policy=True,
    ignore_public_acls=True,
    restrict_public_buckets=True,
)

processor_bucket_ownership_controls = aws.s3.BucketOwnershipControls(
    "idi-processor-s3-ownership",
    bucket=processor_bucket.id,
    rule=aws.s3.BucketOwnershipControlsRuleArgs(
        object_ownership="BucketOwnerEnforced",
    ),
)

processor_bucket_encryption = aws.s3.BucketServerSideEncryptionConfigurationV2(
    "idi-processor-s3-encryption",
    bucket=processor_bucket.id,
    rules=[
        aws.s3.BucketServerSideEncryptionConfigurationV2RuleArgs(
            apply_server_side_encryption_by_default=aws.s3.BucketServerSideEncryptionConfigurationV2RuleApplyServerSideEncryptionByDefaultArgs(
                sse_algorithm="AES256",
            ),
            bucket_key_enabled=True,
        )
    ],
)

# -----------------------------------------------------------------------------
# S3 IAM Policy (smart_open)
# See: https://github.com/piskvorky/smart_open
# -----------------------------------------------------------------------------
s3_policy = aws.iam.RolePolicy(
    "idi-policy-s3-processor",
    role=iam.ec2_role.id,
    policy=processor_bucket.arn.apply(
        lambda arn: json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket"],
                    "Resource": arn,
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:AbortMultipartUpload",
                        "s3:CreateMultipartUpload",
                        "s3:UploadPart",
                        "s3:CompleteMultipartUpload",
                        "s3:ListMultipartUploadParts",
                    ],
                    "Resource": f"{arn}/*",
                },
            ],
        })
    ),
)
