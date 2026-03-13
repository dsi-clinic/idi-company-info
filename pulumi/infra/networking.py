"""VPC, security groups, and VPC endpoints."""

import pulumi_aws as aws

from . import config

# -----------------------------------------------------------------------------
# Default VPC
# -----------------------------------------------------------------------------
default_vpc = aws.ec2.get_vpc(default=True)
default_vpc_subnets = aws.ec2.get_subnets(
    filters=[aws.ec2.GetSubnetsFilterArgs(name="vpc-id", values=[default_vpc.id])],
)

# Single subnet used for VPC endpoints and the ASG — keeps all traffic within one AZ
# and avoids per-AZ endpoint charges for the remaining two AZs (~$43/month saving).
primary_subnet_id = default_vpc_subnets.ids.apply(lambda ids: ids[0])

# -----------------------------------------------------------------------------
# Dedicated EC2 Security Group
# -----------------------------------------------------------------------------
ec2_sg = aws.ec2.SecurityGroup(
    "idi-sg-ec2",
    name=f"{config.name_prefix}-sg-ec2",
    description="Security group for EC2 processing instances - no inbound, all outbound",
    vpc_id=default_vpc.id,
    ingress=[],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            description="Allow all outbound traffic",
            from_port=0,
            to_port=0,
            protocol="-1",
            cidr_blocks=["0.0.0.0/0"],
        )
    ],
    tags=config.tags({"purpose": "EC2 processing instances"}),
)

# -----------------------------------------------------------------------------
# Security Group for VPC Endpoints
# -----------------------------------------------------------------------------
vpc_endpoints_sg = aws.ec2.SecurityGroup(
    "idi-sg-vpc-endpoints",
    name=f"{config.name_prefix}-sg-vpc-endpoints",
    description="Security group for VPC endpoints - allows HTTPS from EC2 instances",
    vpc_id=default_vpc.id,
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            description="HTTPS from EC2 security group",
            from_port=443,
            to_port=443,
            protocol="tcp",
            security_groups=[ec2_sg.id],
        )
    ],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            description="Allow all outbound traffic",
            from_port=0,
            to_port=0,
            protocol="-1",
            cidr_blocks=["0.0.0.0/0"],
        )
    ],
    tags=config.tags({"purpose": "VPC Endpoints for SSM agent access"}),
)

# -----------------------------------------------------------------------------
# VPC Endpoints (SSM for Session Manager)
# -----------------------------------------------------------------------------
ssm_endpoint = aws.ec2.VpcEndpoint(
    "idi-endpoint-ssm",
    vpc_id=default_vpc.id,
    service_name=f"com.amazonaws.{config.aws_region}.ssm",
    vpc_endpoint_type="Interface",
    subnet_ids=[primary_subnet_id],
    security_group_ids=[vpc_endpoints_sg.id],
    private_dns_enabled=True,
    tags=config.tags({"Name": f"{config.name_prefix}-endpoint-ssm", "service": "ssm"}),
)

ssm_messages_endpoint = aws.ec2.VpcEndpoint(
    "idi-endpoint-ssmmessages",
    vpc_id=default_vpc.id,
    service_name=f"com.amazonaws.{config.aws_region}.ssmmessages",
    vpc_endpoint_type="Interface",
    subnet_ids=[primary_subnet_id],
    security_group_ids=[vpc_endpoints_sg.id],
    private_dns_enabled=True,
    tags=config.tags(
        {"Name": f"{config.name_prefix}-endpoint-ssmmessages", "service": "ssmmessages"}
    ),
)

ec2_messages_endpoint = aws.ec2.VpcEndpoint(
    "idi-endpoint-ec2messages",
    vpc_id=default_vpc.id,
    service_name=f"com.amazonaws.{config.aws_region}.ec2messages",
    vpc_endpoint_type="Interface",
    subnet_ids=[primary_subnet_id],
    security_group_ids=[vpc_endpoints_sg.id],
    private_dns_enabled=True,
    tags=config.tags(
        {"Name": f"{config.name_prefix}-endpoint-ec2messages", "service": "ec2messages"}
    ),
)
