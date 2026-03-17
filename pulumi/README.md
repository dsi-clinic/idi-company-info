# Pulumi Infrastructure

AWS infrastructure for the IDI Company Information Pipeline, managed with Pulumi.

## Prerequisites

- [Pulumi CLI](https://www.pulumi.com/docs/install/)
- AWS credentials configured (`aws configure` or environment variables)
- Python dependencies: `uv sync --group pulumi`

---

## Quick Start

```bash
cd pulumi
pulumi login s3://your-pulumi-state-bucket
pulumi stack select dev   # or: pulumi stack init dev

# Required
pulumi config set aws:region us-east-2

# For local developemt set secret values
pulumi config set --secret idi:permid_api_key YOUR_KEY
pulumi config set idi:geonames_user YOUR_USERNAME

# Optional overrides
pulumi config set idi:instance_type t2.small
pulumi config set idi:key_name your-key-pair   # omit to use SSM-only access

pulumi preview   # Validate
pulumi up        # Deploy
```

---

## Resources Created

### Networking

**Security Groups**

| Name | Purpose | Inbound | Outbound |
|---|---|---|---|
| `{prefix}-sg-ec2` | EC2 processing instances | None | All |
| `{prefix}-sg-vpc-endpoints` | SSM VPC endpoints | HTTPS from `sg-ec2` | All |

**VPC Endpoints** (all in primary subnet of default VPC, single AZ)

| Name | Type | Service |
|---|---|---|
| `{prefix}-endpoint-s3` | Gateway (free) | S3 |
| `{prefix}-endpoint-ssm` | Interface | SSM |
| `{prefix}-endpoint-ssmmessages` | Interface | SSM Messages |
| `{prefix}-endpoint-ec2messages` | Interface | EC2 Messages |

The three SSM Interface endpoints enable Session Manager access without internet access or an SSH key. The S3 Gateway endpoint routes S3 traffic over the private AWS network at no cost.

### IAM

- **Role** `{prefix}-role-ssm-agent` — assumed by EC2, grants SSM, CloudWatch Logs, ECR pull, and S3 access
- **Instance Profile** `{prefix}-instance-profile-ssm` — attached to EC2 instances

### Compute

- **Launch Template** `{prefix}-lt-processing` — Amazon Linux 2023, 30GB gp3 EBS (encrypted), uses `sg-ec2`
- **Auto Scaling Group** `{prefix}-processor-asg` — min/max/desired: 1, single subnet (same AZ as VPC endpoints)

### Storage & Secrets

- **S3 bucket** `{prefix}-processor` — input/output data
- **Secrets Manager** (if credentials configured) — `{prefix}/permid-api-key` and `{prefix}/geonames-user`; auto-retrieved by instances at launch

---

## Stack Outputs

```bash
pulumi stack output
```

Key outputs:

| Output | Description |
|---|---|
| `ec2_sg_id` | Dedicated EC2 security group ID |
| `vpc_endpoints_sg_id` | VPC endpoints security group ID |
| `default_vpc_id` | Default VPC ID |
| `s3_endpoint_id` | S3 Gateway endpoint ID |
| `ssm_endpoint_id` | SSM Interface endpoint ID |
| `instance_profile_arn` | IAM instance profile ARN |
| `launch_template_id` | EC2 launch template ID |
| `processor_bucket_name` | S3 bucket name |

---

## Stack Management

```bash
pulumi up        # Deploy / update
pulumi preview   # Dry run
pulumi destroy   # Remove all resources
pulumi refresh   # Sync state from AWS
pulumi stack ls  # List stacks
```

---

## Connect to an Instance

```bash
INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=iam-instance-profile.arn,Values=$(pulumi stack output instance_profile_arn)" \
            "Name=instance-state-name,Values=running" \
  --query "Reservations[0].Instances[0].InstanceId" \
  --output text)

aws ssm start-session --target $INSTANCE_ID
```

---

## Cost Notes

| Resource | Monthly cost |
|---|---|
| EC2 instance (t2.small) | ~$17 |
| SSM Interface endpoints (3 × 1 AZ) | ~$21.90 |
| S3 Gateway endpoint | Free |
| S3 storage + requests | Usage-based |

See [`docs/vpc-endpoints-evaluation.md`](docs/vpc-endpoints-evaluation.md) for a full analysis of endpoint options.

---

## Troubleshooting

```bash
# Verify AWS credentials
aws sts get-caller-identity

# Check Pulumi login
pulumi whoami

# Refresh state if resources were changed outside Pulumi
pulumi refresh

# Cancel an interrupted update
pulumi cancel
```
