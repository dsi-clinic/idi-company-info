"""IAM roles, policies, and instance profile."""

import json

import pulumi_aws as aws

import pulumi

from . import config
from . import ecr

# -----------------------------------------------------------------------------
# EC2 Role
# -----------------------------------------------------------------------------
ec2_role = aws.iam.Role(
    "idi-role-ec2",
    name=f"{config.name_prefix}-role-ec2",
    description="IAM role for EC2 instances with ssm agent access",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    ),
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
    policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:DescribeLogStreams",
                        "logs:PutLogEvents",
                        "logs:PutRetentionPolicy",
                    ],
                    "Resource": [
                        "arn:aws:logs:*:*:log-group:idi-ftm2j",
                        "arn:aws:logs:*:*:log-group:idi-ftm2j:*",
                    ],
                }
            ],
        }
    ),
)

# Instance profile
instance_profile = aws.iam.InstanceProfile(
    "idi-instance-profile-ssm",
    name=f"{config.name_prefix}-instance-profile-ssm",
    role=ec2_role.name,
    tags=config.tags(),
)

# -----------------------------------------------------------------------------
# ECR IAM Policy (CI-pushed orchestrator image)
# -----------------------------------------------------------------------------
ecr_policy = aws.iam.RolePolicy(
    "idi-policy-ecr-pull",
    role=ec2_role.id,
    policy=ecr.ecr_repo.arn.apply(
        lambda arn: json.dumps(
            {
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
                        "Resource": [arn],
                    },
                ],
            }
        )
    ),
)
