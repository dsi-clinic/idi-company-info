"""S3 bucket for pipeline input, output, and failures."""

import pulumi_aws as aws

import pulumi

from . import config

# -----------------------------------------------------------------------------
# Processor S3 Bucket
# -----------------------------------------------------------------------------
processor_bucket = aws.s3.Bucket(
    "idi-processor-s3",
    bucket=config.bucket_name,
    tags=config.tags({"Name": config.bucket_name}),
    opts=pulumi.ResourceOptions(
        retain_on_delete=True,  # Bucket is removed from Pulumi state but not from AWS
    ),
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

processor_bucket_encryption = aws.s3.BucketServerSideEncryptionConfiguration(
    "idi-processor-s3-encryption",
    bucket=processor_bucket.id,
    rules=[
        aws.s3.BucketServerSideEncryptionConfigurationRuleArgs(
            apply_server_side_encryption_by_default=aws.s3.BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultArgs(
                sse_algorithm="AES256",
            ),
            bucket_key_enabled=True,
        )
    ],
)
