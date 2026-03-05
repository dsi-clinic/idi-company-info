"""Pulumi infrastructure for IDI Company Information Pipeline"""

import json
from pathlib import Path

import pulumi
import pulumi_aws as aws

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
config = pulumi.Config()
project_name = pulumi.get_project()
stack_name = pulumi.get_stack()
name_prefix = f"{project_name}-{stack_name}"

# -----------------------------------------------------------------------------
# Functions (alphabetical)
# -----------------------------------------------------------------------------


def _load_template(name: str, **replacements: str) -> str:
    """Load a template file and apply string replacements."""
    path = _TEMPLATES_DIR / name
    content = path.read_text()
    for key, value in replacements.items():
        content = content.replace("{" + key + "}", value)
    return content


def build_user_data(name_prefix, has_secrets, orch_img, sched_img):
    """Build EC2 user data script from templates (matches .env.example structure)."""
    if has_secrets:
        secret_retrieval = _load_template(
            "secret_retrieval_secrets_manager.sh",
            name_prefix=name_prefix,
        )
    else:
        secret_retrieval = _load_template("secret_retrieval_placeholders.sh")

    compose_content = _load_template(
        "docker-compose.yml.template",
        ORCHESTRATOR_IMAGE=orch_img,
        SCHEDULER_IMAGE=sched_img,
    )

    return _load_template(
        "user_data.sh.template",
        SECRET_RETRIEVAL=secret_retrieval,
        COMPOSE_DELIM="COMPOSE_END",
        COMPOSE_CONTENT=compose_content,
        ORCHESTRATOR_IMAGE=orch_img,
    )


# -----------------------------------------------------------------------------
# IAM
# -----------------------------------------------------------------------------
ec2_role = aws.iam.Role(
    "idi-role-ssm-agent",
    name=f"{name_prefix}-role-ssm-agent",
    description="IAM role for EC2 instances with ssm agent access",
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {
                "Service": "ec2.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }]
    }),
    tags={
        "project": project_name,
        "environment": stack_name,
        "managed_by": "Pulumi"
    }
)

# Attach the AmazonSSMManagedInstanceCore managed policy
ssm_policy_attachment = aws.iam.RolePolicyAttachment(
    "idi-policy-ssm-agent",
    role=ec2_role.name,
    policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
)

# Attach CloudWatch Logs policy for watchtower (Python CloudWatch logging)
# See: https://kislyuk.github.io/watchtower/#iam-permissions
cloudwatch_logs_policy_attachment = aws.iam.RolePolicyAttachment(
    "idi-policy-cloudwatch-logs",
    role=ec2_role.name,
    policy_arn="arn:aws:iam::aws:policy/AWSOpsWorksCloudWatchLogs"
)

# -----------------------------------------------------------------------------
# Secrets Manager (optional)
# -----------------------------------------------------------------------------
permid_api_key = config.get_secret("permid_api_key")
geonames_user = config.get("geonames_user")

# Create AWS Secrets Manager secrets if provided
secrets_created = []

if permid_api_key:
    permid_secret = aws.secretsmanager.Secret(
        "idi-secret-permid-api-key",
        name=f"{name_prefix}-permid-api-key",
        description="PermID API Key for company information queries",
        tags={
            "project": project_name,
            "environment": stack_name,
            "managed_by": "Pulumi"
        }
    )

    permid_secret_version = aws.secretsmanager.SecretVersion(
        "idi-secret-version-permid",
        secret_id=permid_secret.id,
        secret_string=permid_api_key,
        opts=pulumi.ResourceOptions(
            depends_on=[permid_secret],
            ignore_changes=["secret_string"]  # Prevent reading back the secret value
        )
    )
    secrets_created.append(permid_secret.arn)

if geonames_user:
    geonames_secret = aws.secretsmanager.Secret(
        "idi-secret-geonames-user",
        name=f"{name_prefix}-geonames-user",
        description="GeoNames username for geocoding",
        tags={
            "project": project_name,
            "environment": stack_name,
            "managed_by": "Pulumi"
        }
    )

    geonames_secret_version = aws.secretsmanager.SecretVersion(
        "idi-secret-version-geonames",
        secret_id=geonames_secret.id,
        secret_string=geonames_user,
        opts=pulumi.ResourceOptions(
            depends_on=[geonames_secret],
            ignore_changes=["secret_string"]  # Prevent reading back the secret value
        )
    )
    secrets_created.append(geonames_secret.arn)

# Create IAM policy to allow reading secrets
if secrets_created:
    secrets_policy = aws.iam.RolePolicy(
        "idi-policy-secrets-access",
        role=ec2_role.id,
        policy=pulumi.Output.json_dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": [
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:DescribeSecret"
                ],
                "Resource": secrets_created
            }]
        })
    )

# Create an instance profile for the role
instance_profile = aws.iam.InstanceProfile(
    "idi-instance-profile-ssm",
    name=f"{name_prefix}-instance-profile-ssm",
    role=ec2_role.name,
    tags={
        "project": project_name,
        "environment": stack_name,
        "managed_by": "Pulumi"
    }
)

# -----------------------------------------------------------------------------
# Networking (VPC, security groups, endpoints)
# -----------------------------------------------------------------------------
default_vpc = aws.ec2.get_vpc(default=True)
default_sg = aws.ec2.get_security_group(
    vpc_id=default_vpc.id,
    filters=[aws.ec2.GetSecurityGroupFilterArgs(
        name="group-name",
        values=["default"]
    )]
)

# Get all subnets in the default VPC
default_vpc_subnets = aws.ec2.get_subnets(
    filters=[aws.ec2.GetSubnetsFilterArgs(
        name="vpc-id",
        values=[default_vpc.id]
    )]
)

# Create security group for VPC endpoints
vpc_endpoints_sg = aws.ec2.SecurityGroup(
    "idi-sg-vpc-endpoints",
    name=f"{name_prefix}-sg-vpc-endpoints",
    description="Security group for VPC endpoints - allows HTTPS from default VPC",
    vpc_id=default_vpc.id,
    ingress=[aws.ec2.SecurityGroupIngressArgs(
        description="HTTPS from default VPC security group",
        from_port=443,
        to_port=443,
        protocol="tcp",
        security_groups=[default_sg.id]
    )],
    egress=[aws.ec2.SecurityGroupEgressArgs(
        description="Allow all outbound traffic",
        from_port=0,
        to_port=0,
        protocol="-1",
        cidr_blocks=["0.0.0.0/0"]
    )],
    tags={
        "project": project_name,
        "environment": stack_name,
        "managed_by": "Pulumi",
        "purpose": "VPC Endpoints for SSM agent access"
    }
)

# Get AWS region and account for ECR URLs
aws_config = pulumi.Config("aws")
aws_region = aws_config.require("region")
caller = aws.get_caller_identity()
ecr_registry = caller.account_id.apply(lambda aid: f"{aid}.dkr.ecr.{aws_region}.amazonaws.com")
orchestrator_image = ecr_registry.apply(
    lambda r: f"{r}/{name_prefix}-company-info-orchestrator:latest"
)
scheduler_image = ecr_registry.apply(
    lambda r: f"{r}/{name_prefix}-company-info-scheduler:latest"
)

# Create VPC Endpoint for SSM
ssm_endpoint = aws.ec2.VpcEndpoint(
    "idi-endpoint-ssm",
    vpc_id=default_vpc.id,
    service_name=f"com.amazonaws.{aws_region}.ssm",
    vpc_endpoint_type="Interface",
    subnet_ids=default_vpc_subnets.ids,
    security_group_ids=[vpc_endpoints_sg.id, default_sg.id],
    private_dns_enabled=True,
    tags={
        "Name": f"{name_prefix}-endpoint-ssm",
        "project": project_name,
        "environment": stack_name,
        "managed_by": "Pulumi",
        "service": "ssm"
    }
)

# Create VPC Endpoint for SSM Messages
ssm_messages_endpoint = aws.ec2.VpcEndpoint(
    "idi-endpoint-ssmmessages",
    vpc_id=default_vpc.id,
    service_name=f"com.amazonaws.{aws_region}.ssmmessages",
    vpc_endpoint_type="Interface",
    subnet_ids=default_vpc_subnets.ids,
    security_group_ids=[vpc_endpoints_sg.id, default_sg.id],
    private_dns_enabled=True,
    tags={
        "Name": f"{name_prefix}-endpoint-ssmmessages",
        "project": project_name,
        "environment": stack_name,
        "managed_by": "Pulumi",
        "service": "ssmmessages"
    }
)

# Create VPC Endpoint for EC2 Messages
ec2_messages_endpoint = aws.ec2.VpcEndpoint(
    "idi-endpoint-ec2messages",
    vpc_id=default_vpc.id,
    service_name=f"com.amazonaws.{aws_region}.ec2messages",
    vpc_endpoint_type="Interface",
    subnet_ids=default_vpc_subnets.ids,
    security_group_ids=[vpc_endpoints_sg.id, default_sg.id],
    private_dns_enabled=True,
    tags={
        "Name": f"{name_prefix}-endpoint-ec2messages",
        "project": project_name,
        "environment": stack_name,
        "managed_by": "Pulumi",
        "service": "ec2messages"
    }
)

# -----------------------------------------------------------------------------
# Compute (AMI, launch template, Auto Scaling Group)
# -----------------------------------------------------------------------------
instance_type = config.get("instance_type") or "t2.small"
key_name = config.get("key_name") or "idi-acct-REMOVED"

# Get the latest Amazon Linux 2023 AMI
ami = aws.ec2.get_ami(
    most_recent=True,
    owners=["amazon"],
    filters=[
        aws.ec2.GetAmiFilterArgs(name="name", values=["al2023-ami-2023.*-x86_64"]),
        aws.ec2.GetAmiFilterArgs(name="architecture", values=["x86_64"]),
        aws.ec2.GetAmiFilterArgs(name="virtualization-type", values=["hvm"]),
    ]
)

# Generate user data with resolved ECR image URIs
user_data = pulumi.Output.all(orchestrator_image, scheduler_image).apply(
    lambda args: build_user_data(
        name_prefix=name_prefix,
        has_secrets=bool(permid_api_key or geonames_user),
        orch_img=args[0],
        sched_img=args[1],
    )
)

# Create Launch Template
launch_template = aws.ec2.LaunchTemplate(
    "idi-lt-processing",
    name=f"{name_prefix}-lt-processing",
    description=f"Launch template for {project_name} processing instances",
    image_id=ami.id,
    instance_type=instance_type,
    key_name=key_name,
    iam_instance_profile=aws.ec2.LaunchTemplateIamInstanceProfileArgs(
        arn=instance_profile.arn
    ),
    vpc_security_group_ids=[default_sg.id],
    block_device_mappings=[
        aws.ec2.LaunchTemplateBlockDeviceMappingArgs(
            device_name="/dev/xvda",
            ebs=aws.ec2.LaunchTemplateBlockDeviceMappingEbsArgs(
                volume_size=30,
                volume_type="gp3",
                delete_on_termination=True,
                encrypted=True
            )
        )
    ],
    user_data=user_data.apply(
        lambda s: __import__('base64').b64encode(s.encode()).decode()
    ),
    tag_specifications=[
        aws.ec2.LaunchTemplateTagSpecificationArgs(
            resource_type="instance",
            tags={
                "Name": f"{name_prefix}-processing-instance",
                "project": project_name,
                "environment": stack_name,
                "managed_by": "Pulumi",
                "purpose": "Data Processing Pipeline"
            }
        ),
        aws.ec2.LaunchTemplateTagSpecificationArgs(
            resource_type="volume",
            tags={
                "Name": f"{name_prefix}-processing-volume",
                "project": project_name,
                "environment": stack_name,
                "managed_by": "Pulumi"
            }
        )
    ],
    tags={
        "Name": f"{name_prefix}-lt-processing",
        "project": project_name,
        "environment": stack_name,
        "managed_by": "Pulumi"
    }
)

# Create Auto Scaling Group
processor_asg = aws.autoscaling.Group(
    "idi-processor-asg",
    name=f"{name_prefix}-processor-asg",
    launch_template={
        "id": launch_template.id,
        "version": "1",
    },
    vpc_zone_identifiers=default_vpc_subnets.ids,
    min_size=1,
    max_size=1,
    desired_capacity=1,
    health_check_grace_period=300,
    health_check_type="EC2",
    force_delete=True,
    capacity_reservation_specification={
        "capacity_reservation_preference": "default",
    },
    tags=[
        {"key": "Name", "value": f"{name_prefix}-processor-asg", "propagate_at_launch": True},
        {"key": "project", "value": project_name, "propagate_at_launch": True},
        {"key": "environment", "value": stack_name, "propagate_at_launch": True},
        {"key": "managed_by", "value": "Pulumi", "propagate_at_launch": True},
    ],
)

# -----------------------------------------------------------------------------
# Storage (S3)
# -----------------------------------------------------------------------------
processor_bucket = aws.s3.BucketV2(
    "idi-processor-s3",
    bucket=f"{name_prefix}-processor-s3",
    force_destroy=True,
    tags={
        "Name": f"{name_prefix}-processor-s3",
        "project": project_name,
        "environment": stack_name,
        "managed_by": "Pulumi",
    },
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
# ECR (IAM policy for CI-pushed images)
# -----------------------------------------------------------------------------
# Repo names match CI workflow: {name_prefix}-company-info-orchestrator, -scheduler
ecr_orchestrator_repo = f"{name_prefix}-company-info-orchestrator"
ecr_scheduler_repo = f"{name_prefix}-company-info-scheduler"

ecr_policy = aws.iam.RolePolicy(
    "idi-policy-ecr-pull",
    role=ec2_role.id,
    policy=caller.account_id.apply(
        lambda aid: json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "ecr:GetAuthorizationToken",
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "ecr:BatchGetImage",
                        "ecr:GetDownloadUrlForLayer",
                    ],
                    "Resource": [
                        f"arn:aws:ecr:{aws_region}:{aid}:repository/{ecr_orchestrator_repo}",
                        f"arn:aws:ecr:{aws_region}:{aid}:repository/{ecr_scheduler_repo}",
                    ],
                },
            ],
        })
    ),
)

# S3 IAM policy for processor bucket (smart_open)
# See: https://github.com/piskvorky/smart_open
s3_policy = aws.iam.RolePolicy(
    "idi-policy-s3-processor",
    role=ec2_role.id,
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

# -----------------------------------------------------------------------------
# Exports
# -----------------------------------------------------------------------------
pulumi.export("role_arn", ec2_role.arn)
pulumi.export("role_name", ec2_role.name)
pulumi.export("instance_profile_name", instance_profile.name)
pulumi.export("instance_profile_arn", instance_profile.arn)

# Export security group information
pulumi.export("vpc_endpoints_sg_id", vpc_endpoints_sg.id)
pulumi.export("vpc_endpoints_sg_name", vpc_endpoints_sg.name)
pulumi.export("default_vpc_id", default_vpc.id)
pulumi.export("default_sg_id", default_sg.id)

# Export VPC endpoint information
pulumi.export("ssm_endpoint_id", ssm_endpoint.id)
pulumi.export("ssm_endpoint_dns_entries", ssm_endpoint.dns_entries)
pulumi.export("ssm_messages_endpoint_id", ssm_messages_endpoint.id)
pulumi.export("ssm_messages_endpoint_dns_entries", ssm_messages_endpoint.dns_entries)
pulumi.export("ec2_messages_endpoint_id", ec2_messages_endpoint.id)
pulumi.export("ec2_messages_endpoint_dns_entries", ec2_messages_endpoint.dns_entries)

# Export Auto Scaling Group information
pulumi.export("processor_asg_name", processor_asg.name)
pulumi.export("processor_asg_arn", processor_asg.arn)

# Export S3 bucket information
pulumi.export("processor_bucket_name", processor_bucket.id)
pulumi.export("processor_bucket_arn", processor_bucket.arn)

# Export ECR repository information (repos created by CI, not Pulumi)
pulumi.export("ecr_orchestrator_image", orchestrator_image)
pulumi.export("ecr_scheduler_image", scheduler_image)

# Export Launch Template information
pulumi.export("launch_template_id", launch_template.id)
pulumi.export("launch_template_name", launch_template.name)
pulumi.export("launch_template_latest_version", launch_template.latest_version)
pulumi.export("ami_id", ami.id)
pulumi.export("ami_name", ami.name)

# Export secrets information (if configured)
if permid_api_key:
    pulumi.export("permid_secret_arn", permid_secret.arn)
    pulumi.export("permid_secret_name", permid_secret.name)
if geonames_user:
    pulumi.export("geonames_secret_arn", geonames_secret.arn)
    pulumi.export("geonames_secret_name", geonames_secret.name)
pulumi.export("secrets_configured", bool(permid_api_key or geonames_user))
