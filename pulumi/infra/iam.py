"""IAM roles, policies, and instance profile."""

import json

import pulumi
import pulumi_aws as aws

from . import config

# -----------------------------------------------------------------------------
# EC2 Role
# -----------------------------------------------------------------------------
ec2_role = aws.iam.Role(
    "idi-role-ssm-agent",
    name=f"{config.name_prefix}-role-ssm-agent",
    description="IAM role for EC2 instances with ssm agent access",
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "ec2.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }),
    tags=config.tags(),
)

# Attach the AmazonSSMManagedInstanceCore managed policy
ssm_policy_attachment = aws.iam.RolePolicyAttachment(
    "idi-policy-ssm-agent",
    role=ec2_role.name,
    policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
)

# Inline CloudWatch Logs policy for watchtower (least-privilege)
# See: https://kislyuk.github.io/watchtower/#iam-permissions
# Scoped to idi-company-info-* log groups (matches logs.py)
cloudwatch_logs_policy = aws.iam.RolePolicy(
    "idi-policy-cloudwatch-logs",
    role=ec2_role.id,
    policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:DescribeLogStreams",
                "logs:PutLogEvents",
            ],
            "Resource": [
                "arn:aws:logs:*:*:log-group:idi-company-info-*",
                "arn:aws:logs:*:*:log-group:idi-company-info-*:*",
            ],
        }],
    }),
)

# Instance profile
instance_profile = aws.iam.InstanceProfile(
    "idi-instance-profile-ssm",
    name=f"{config.name_prefix}-instance-profile-ssm",
    role=ec2_role.name,
    tags=config.tags(),
)

# -----------------------------------------------------------------------------
# ECR IAM Policy (CI-pushed images)
# -----------------------------------------------------------------------------
ecr_orchestrator_repo = f"{config.name_prefix}-company-info-orchestrator"
ecr_scheduler_repo = f"{config.name_prefix}-company-info-scheduler"

ecr_policy = aws.iam.RolePolicy(
    "idi-policy-ecr-pull",
    role=ec2_role.id,
    policy=pulumi.Output.from_input(config.caller.account_id).apply(
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
                        f"arn:aws:ecr:{config.aws_region}:{aid}:repository/{ecr_orchestrator_repo}",
                        f"arn:aws:ecr:{config.aws_region}:{aid}:repository/{ecr_scheduler_repo}",
                    ],
                },
            ],
        })
    ),
)
