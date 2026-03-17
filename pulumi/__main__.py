"""Pulumi infrastructure for IDI Company Information Pipeline.

Imports all resource modules (creation order matters) and exports stack outputs.
"""

# Import order matters: config first, then resources by dependency
from infra import compute, ecr, iam, networking, secrets, storage

import pulumi

# -----------------------------------------------------------------------------
# Exports
# -----------------------------------------------------------------------------
pulumi.export("role_arn", iam.ec2_role.arn)
pulumi.export("role_name", iam.ec2_role.name)
pulumi.export("instance_profile_name", iam.instance_profile.name)
pulumi.export("instance_profile_arn", iam.instance_profile.arn)

pulumi.export("vpc_endpoints_sg_id", networking.vpc_endpoints_sg.id)
pulumi.export("vpc_endpoints_sg_name", networking.vpc_endpoints_sg.name)
pulumi.export("ec2_sg_id", networking.ec2_sg.id)
pulumi.export("ec2_sg_name", networking.ec2_sg.name)
pulumi.export("default_vpc_id", networking.default_vpc.id)

pulumi.export("s3_endpoint_id", networking.s3_endpoint.id)

pulumi.export("ssm_endpoint_id", networking.ssm_endpoint.id)
pulumi.export("ssm_endpoint_dns_entries", networking.ssm_endpoint.dns_entries)
pulumi.export("ssm_messages_endpoint_id", networking.ssm_messages_endpoint.id)
pulumi.export("ssm_messages_endpoint_dns_entries", networking.ssm_messages_endpoint.dns_entries)
pulumi.export("ec2_messages_endpoint_id", networking.ec2_messages_endpoint.id)
pulumi.export("ec2_messages_endpoint_dns_entries", networking.ec2_messages_endpoint.dns_entries)

pulumi.export("processor_asg_name", compute.processor_asg.name)
pulumi.export("processor_asg_arn", compute.processor_asg.arn)

pulumi.export("processor_bucket_name", storage.processor_bucket.id)
pulumi.export("processor_bucket_arn", storage.processor_bucket.arn)

pulumi.export("ecr_orchestrator_image", ecr.orchestrator_image)

pulumi.export("launch_template_id", compute.launch_template.id)
pulumi.export("launch_template_name", compute.launch_template.name)
pulumi.export("launch_template_latest_version", compute.launch_template.latest_version)
pulumi.export("ami_id", compute.ami.id)
pulumi.export("ami_name", compute.ami.name)

pulumi.export("secrets_configured", bool(secrets.permid_api_key or secrets.geonames_user))
if secrets.permid_api_key:
    pulumi.export("permid_secret_arn", secrets.permid_secret.arn)
    pulumi.export("permid_secret_name", secrets.permid_secret.name)
if secrets.geonames_user:
    pulumi.export("geonames_secret_arn", secrets.geonames_secret.arn)
    pulumi.export("geonames_secret_name", secrets.geonames_secret.name)
