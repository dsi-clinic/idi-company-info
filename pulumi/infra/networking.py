"""VPC, security groups, and VPC endpoints."""

import pulumi
import pulumi_aws as aws

from . import config

# -----------------------------------------------------------------------------
# Default VPC
# -----------------------------------------------------------------------------
default_vpc = aws.ec2.get_vpc(default=True)
default_sg = aws.ec2.get_security_group(
    vpc_id=default_vpc.id,
    filters=[aws.ec2.GetSecurityGroupFilterArgs(name="group-name", values=["default"])],
)
default_vpc_subnets = aws.ec2.get_subnets(
    filters=[aws.ec2.GetSubnetsFilterArgs(name="vpc-id", values=[default_vpc.id])],
)

# -----------------------------------------------------------------------------
# Security Group for VPC Endpoints
# -----------------------------------------------------------------------------
vpc_endpoints_sg = aws.ec2.SecurityGroup(
    "idi-sg-vpc-endpoints",
    name=f"{config.name_prefix}-sg-vpc-endpoints",
    description="Security group for VPC endpoints - allows HTTPS from default VPC",
    vpc_id=default_vpc.id,
    ingress=[aws.ec2.SecurityGroupIngressArgs(
        description="HTTPS from default VPC security group",
        from_port=443,
        to_port=443,
        protocol="tcp",
        security_groups=[default_sg.id],
    )],
    egress=[aws.ec2.SecurityGroupEgressArgs(
        description="Allow all outbound traffic",
        from_port=0,
        to_port=0,
        protocol="-1",
        cidr_blocks=["0.0.0.0/0"],
    )],
    tags=config.tags({"purpose": "VPC Endpoints for SSM agent access"}),
)

# -----------------------------------------------------------------------------
# ECR Image URIs
# -----------------------------------------------------------------------------
ecr_registry = pulumi.Output.from_input(config.caller.account_id).apply(
    lambda aid: f"{aid}.dkr.ecr.{config.aws_region}.amazonaws.com"
)
orchestrator_image = ecr_registry.apply(
    lambda r: f"{r}/{config.name_prefix}-company-info-orchestrator:latest"
)
scheduler_image = ecr_registry.apply(
    lambda r: f"{r}/{config.name_prefix}-company-info-scheduler:latest"
)

# -----------------------------------------------------------------------------
# VPC Endpoints (SSM for Session Manager)
# -----------------------------------------------------------------------------
ssm_endpoint = aws.ec2.VpcEndpoint(
    "idi-endpoint-ssm",
    vpc_id=default_vpc.id,
    service_name=f"com.amazonaws.{config.aws_region}.ssm",
    vpc_endpoint_type="Interface",
    subnet_ids=default_vpc_subnets.ids,
    security_group_ids=[vpc_endpoints_sg.id, default_sg.id],
    private_dns_enabled=True,
    tags=config.tags({"Name": f"{config.name_prefix}-endpoint-ssm", "service": "ssm"}),
)

ssm_messages_endpoint = aws.ec2.VpcEndpoint(
    "idi-endpoint-ssmmessages",
    vpc_id=default_vpc.id,
    service_name=f"com.amazonaws.{config.aws_region}.ssmmessages",
    vpc_endpoint_type="Interface",
    subnet_ids=default_vpc_subnets.ids,
    security_group_ids=[vpc_endpoints_sg.id, default_sg.id],
    private_dns_enabled=True,
    tags=config.tags({"Name": f"{config.name_prefix}-endpoint-ssmmessages", "service": "ssmmessages"}),
)

ec2_messages_endpoint = aws.ec2.VpcEndpoint(
    "idi-endpoint-ec2messages",
    vpc_id=default_vpc.id,
    service_name=f"com.amazonaws.{config.aws_region}.ec2messages",
    vpc_endpoint_type="Interface",
    subnet_ids=default_vpc_subnets.ids,
    security_group_ids=[vpc_endpoints_sg.id, default_sg.id],
    private_dns_enabled=True,
    tags=config.tags({"Name": f"{config.name_prefix}-endpoint-ec2messages", "service": "ec2messages"}),
)
