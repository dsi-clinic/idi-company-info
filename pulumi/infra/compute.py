"""Compute: AMI, launch template, Auto Scaling Group."""

import pulumi_aws as aws

import pulumi

from . import config, ecr, iam, networking, secrets, storage, user_data

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
instance_type = config.config.get("instance_type") or "t2.small"
key_name = config.config.get("key_name")

# -----------------------------------------------------------------------------
# AMI
# -----------------------------------------------------------------------------
ami = aws.ec2.get_ami(
    most_recent=True,
    owners=["amazon"],
    filters=[
        aws.ec2.GetAmiFilterArgs(name="name", values=["al2023-ami-2023.*-x86_64"]),
        aws.ec2.GetAmiFilterArgs(name="architecture", values=["x86_64"]),
        aws.ec2.GetAmiFilterArgs(name="virtualization-type", values=["hvm"]),
    ],
)

# -----------------------------------------------------------------------------
# User Data
# -----------------------------------------------------------------------------
_use_s3 = config.config.get("use_s3_output")
use_s3_output = _use_s3 is None or str(_use_s3).lower() in ("true", "1", "yes")

user_data_script = pulumi.Output.all(
    ecr.orchestrator_image,
    storage.processor_bucket.id,
).apply(
    lambda args: user_data.build_user_data(
        name_prefix=config.name_prefix,
        orch_img=args[0],
        processor_bucket=args[1],
        use_s3_output=use_s3_output,
    )
)

# -----------------------------------------------------------------------------
# Launch Template
# -----------------------------------------------------------------------------
launch_template_args = {
    "image_id": ami.id,
    "instance_type": instance_type,
    "iam_instance_profile": aws.ec2.LaunchTemplateIamInstanceProfileArgs(
        arn=iam.instance_profile.arn
    ),
    "vpc_security_group_ids": [networking.ec2_sg.id],
    "block_device_mappings": [
        aws.ec2.LaunchTemplateBlockDeviceMappingArgs(
            device_name="/dev/xvda",
            ebs=aws.ec2.LaunchTemplateBlockDeviceMappingEbsArgs(
                volume_size=30,
                volume_type="gp3",
                delete_on_termination=True,
                encrypted=True,
            ),
        )
    ],
    "user_data": user_data_script.apply(
        lambda s: __import__("base64").b64encode(s.encode()).decode()
    ),
    "tag_specifications": [
        aws.ec2.LaunchTemplateTagSpecificationArgs(
            resource_type="instance",
            tags=config.tags(
                {
                    "Name": f"{config.name_prefix}-processing-instance",
                    "purpose": "Data Processing Pipeline",
                }
            ),
        ),
        aws.ec2.LaunchTemplateTagSpecificationArgs(
            resource_type="volume",
            tags=config.tags({"Name": f"{config.name_prefix}-processing-volume"}),
        ),
    ],
    "tags": config.tags({"Name": f"{config.name_prefix}-lt-processing"}),
}
if key_name:
    launch_template_args["key_name"] = key_name

launch_template = aws.ec2.LaunchTemplate(
    "idi-lt-processing",
    name=f"{config.name_prefix}-lt-processing",
    description=f"Launch template for {config.project_name} processing instances",
    update_default_version=True,
    **launch_template_args,
)

# -----------------------------------------------------------------------------
# Auto Scaling Group
# -----------------------------------------------------------------------------
processor_asg = aws.autoscaling.Group(
    "idi-processor-asg",
    name=f"{config.name_prefix}-processor-asg",
    launch_template=aws.autoscaling.GroupLaunchTemplateArgs(
        id=launch_template.id,
        version=launch_template.latest_version,
    ),
    vpc_zone_identifiers=[networking.primary_subnet_id],
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
        {"key": k, "value": v, "propagate_at_launch": True}
        for k, v in config.tags({"Name": f"{config.name_prefix}-processor-asg"}).items()
    ],
    opts=pulumi.ResourceOptions(
        depends_on=[secrets.secrets_policy],
    ),
)
