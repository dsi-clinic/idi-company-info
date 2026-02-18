# Pulumi Infrastructure for IDI Company Information Pipeline

This directory contains Pulumi infrastructure as code for deploying AWS resources.

## Prerequisites

1. **Install Pulumi CLI**:
   ```bash
   curl -fsSL https://get.pulumi.com | sh
   ```

2. **Configure AWS credentials**:
   ```bash
   aws configure
   # Or set environment variables:
   # export AWS_ACCESS_KEY_ID=your-access-key
   # export AWS_SECRET_ACCESS_KEY=your-secret-key
   # export AWS_REGION=us-east-2
   ```

3. **Install Python dependencies**:
   ```bash
   cd pulumi
   pip install -r requirements.txt
   ```

## Quick Start

### 1. Initialize Pulumi Stack

Create a new stack (e.g., `dev`, `staging`, `prod`):

```bash
cd pulumi
pulumi login  # Login to Pulumi Cloud (or use local/S3 backend)
pulumi stack init dev
```

### 2. Configure Stack

Set configuration values (all optional with defaults):

```bash
# AWS region (default: us-east-2)
pulumi config set aws:region us-east-2

# EC2 instance type (default: t2.small)
pulumi config set idi:instance_type t2.small

# SSH key pair name (default: idi-acct-REMOVED)
pulumi config set idi:key_name idi-acct-REMOVED

# API credentials (RECOMMENDED - stored securely)
pulumi config set --secret idi:permid_api_key YOUR_PERMID_API_KEY
pulumi config set idi:geonames_user YOUR_GEONAMES_USERNAME
```

**Important**: If you configure `permid_api_key` and `geonames_user` here, they will be:
1. Encrypted in Pulumi state
2. Stored in AWS Secrets Manager
3. Automatically retrieved by EC2 instances at launch time
4. No manual configuration needed after instance launch

If you don't set these values, instances will launch with placeholder values and you'll need to manually update the `.env` file via SSH.

### 3. Preview Changes

See what resources will be created:

```bash
pulumi preview
```

### 4. Deploy Infrastructure

Deploy the resources:

```bash
pulumi up
```

Review the changes and confirm with `yes`.

### 5. View Outputs

After deployment, view the exported values:

```bash
pulumi stack output
```

This will show:
- `role_arn`: ARN of the IAM role
- `role_name`: Name of the IAM role
- `instance_profile_name`: Name of the instance profile
- `instance_profile_arn`: ARN of the instance profile
- `vpc_endpoints_sg_id`: ID of the VPC endpoints security group
- `vpc_endpoints_sg_name`: Name of the VPC endpoints security group
- `default_vpc_id`: ID of the default VPC
- `default_sg_id`: ID of the default VPC security group
- `ssm_endpoint_id`: ID of the SSM VPC endpoint
- `ssm_messages_endpoint_id`: ID of the SSM Messages VPC endpoint
- `ec2_messages_endpoint_id`: ID of the EC2 Messages VPC endpoint
- `launch_template_id`: ID of the EC2 launch template
- `launch_template_name`: Name of the launch template
- `ami_id`: AMI ID used in the launch template

## Resources Created

### IAM Role
- **Name**: `{project}-{stack}-role-ssm-agent`
- **Purpose**: EC2 instances can assume this role to access AWS services
- **Trust Policy**: Allows EC2 service to assume the role
- **Attached Policies**:
  - `AmazonSSMManagedInstanceCore`: Enables AWS Systems Manager Session Manager

### Instance Profile
- **Name**: `{project}-{stack}-instance-profile-ssm`
- **Purpose**: Attach to EC2 instances to grant them the IAM role

### Security Group
- **Name**: `{project}-{stack}-sg-vpc-endpoints`
- **Purpose**: Security group for VPC endpoints
- **VPC**: Default VPC
- **Ingress Rules**:
  - Port 443 (HTTPS) from default VPC security group
- **Egress Rules**:
  - All traffic allowed

### VPC Endpoints
Three Interface VPC endpoints for AWS Systems Manager (Session Manager) connectivity:

#### SSM Endpoint
- **Name**: `{project}-{stack}-endpoint-ssm`
- **Service**: `com.amazonaws.{region}.ssm`
- **Type**: Interface
- **Private DNS**: Enabled
- **Subnets**: All subnets in default VPC
- **Security Groups**: `{project}-{stack}-sg-vpc-endpoints` and default VPC security group

#### SSM Messages Endpoint
- **Name**: `{project}-{stack}-endpoint-ssmmessages`
- **Service**: `com.amazonaws.{region}.ssmmessages`
- **Type**: Interface
- **Private DNS**: Enabled
- **Subnets**: All subnets in default VPC
- **Security Groups**: `{project}-{stack}-sg-vpc-endpoints` and default VPC security group

#### EC2 Messages Endpoint
- **Name**: `{project}-{stack}-endpoint-ec2messages`
- **Service**: `com.amazonaws.{region}.ec2messages`
- **Type**: Interface
- **Private DNS**: Enabled
- **Subnets**: All subnets in default VPC
- **Security Groups**: `{project}-{stack}-sg-vpc-endpoints` and default VPC security group

These endpoints enable private connectivity to AWS Systems Manager without requiring internet access, allowing EC2 instances to use Session Manager even in private subnets without NAT gateways or Internet Gateways.

### Secrets Manager (Optional)
If you configure API credentials via Pulumi config, the following secrets are created:
- **PermID API Key Secret**: `{project}/{stack}/permid-api-key`
- **GeoNames User Secret**: `{project}/{stack}/geonames-user`

These secrets are:
- Encrypted at rest in AWS Secrets Manager
- Automatically retrieved by EC2 instances at launch time
- Accessible only by instances with the IAM role attached

### Launch Template
- **Name**: `{project}-{stack}-lt-processing`
- **Purpose**: Template for launching EC2 instances to run the data processing pipeline
- **AMI**: Amazon Linux 2023 (latest)
- **Instance Type**: Configurable via `idi:instance_type` config (default: t2.small)
- **SSH Key**: Configurable via `idi:key_name` config (default: idi-acct-REMOVED)
- **Storage**: 30GB gp3 EBS volume (encrypted)
- **IAM Profile**: Attached instance profile with Secrets Manager access
- **Security Group**: Default VPC security group
- **User Data**: Automated setup script that:
  - Installs Docker, Git, and AWS CLI
  - Retrieves API credentials from Secrets Manager (if configured)
  - Clones the idi-company-info repository
  - Configures and starts the Docker Compose stack with secrets
  - Schedules the orchestrator to run daily at 18:30 UTC

**Security Benefits**: When secrets are configured in Pulumi:
- ✅ No manual SSH configuration needed
- ✅ Secrets never appear in user-data (which is visible in EC2 metadata)
- ✅ Secrets are encrypted in transit and at rest
- ✅ Fine-grained IAM access control
- ✅ Audit trail of secret access in CloudTrail

## Usage

### Attach to EC2 Instance

When creating an EC2 instance, reference the instance profile:

```python
instance = aws.ec2.Instance(
    "my-instance",
    # ... other configuration ...
    iam_instance_profile=instance_profile.name
)
```

Or via AWS CLI:

```bash
INSTANCE_PROFILE=$(pulumi stack output instance_profile_name)
aws ec2 run-instances \
  --image-id ami-xxxxx \
  --instance-type t3.micro \
  --iam-instance-profile Name=$INSTANCE_PROFILE
```

### Launch EC2 Instance from Template

Launch an instance using the template:

```bash
# Get the launch template ID
TEMPLATE_ID=$(pulumi stack output launch_template_id)

# Launch an instance
aws ec2 run-instances \
  --launch-template LaunchTemplateId=$TEMPLATE_ID \
  --subnet-id subnet-xxxxx \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=idi-processing-instance}]'

# Or get the latest version of the template
TEMPLATE_NAME=$(pulumi stack output launch_template_name)
aws ec2 run-instances \
  --launch-template LaunchTemplateName=$TEMPLATE_NAME,Version='$Latest' \
  --subnet-id subnet-xxxxx
```

**Post-Launch Configuration:**

**Only needed if secrets were NOT configured in Pulumi config.**

If you didn't configure `permid_api_key` and `geonames_user` during setup, update them manually:

```bash
# Get instance ID
INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=idi-processing-instance" "Name=instance-state-name,Values=running" \
  --query "Reservations[0].Instances[0].InstanceId" \
  --output text)

# Connect via Session Manager
aws ssm start-session --target $INSTANCE_ID

# Update the .env file
sudo su - ec2-user
cd idi-company-info
nano .env  # Add your PERMID_API_KEY and GEONAMES_USER

# Restart the services
docker-compose restart
```

**If secrets were configured in Pulumi**: The instance automatically retrieves them from Secrets Manager during launch. No manual configuration needed!

### Connect via Session Manager

Once the instance has the role attached and VPC endpoints are deployed:

```bash
# Get instance ID
INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=iam-instance-profile.arn,Values=$(pulumi stack output instance_profile_arn)" \
  --query "Reservations[0].Instances[0].InstanceId" \
  --output text)

# Connect via Session Manager (no SSH keys needed!)
aws ssm start-session --target $INSTANCE_ID
```

## Stack Management

### Update Infrastructure

After making changes to `__main__.py`:

```bash
pulumi up
```

### Destroy Infrastructure

Remove all resources:

```bash
pulumi destroy
```

### Switch Stacks

```bash
pulumi stack select dev    # Switch to dev stack
pulumi stack select prod   # Switch to prod stack
```

### List Stacks

```bash
pulumi stack ls
```

## Backend Configuration

By default, Pulumi uses Pulumi Cloud. To use a different backend:

### Local Backend
```bash
pulumi login --local
```

### S3 Backend
```bash
pulumi login s3://my-pulumi-state-bucket
```

### Azure Blob Storage
```bash
pulumi login azblob://my-container
```

## Adding More Resources

Edit `__main__.py` to add more AWS resources:

```python
# Example: Add S3 bucket
bucket = aws.s3.Bucket(
    "data-bucket",
    bucket=f"{project_name}-data-{stack_name}",
    tags={
        "Project": project_name,
        "Environment": stack_name
    }
)

pulumi.export("bucket_name", bucket.id)
```

## Cost Estimation

Estimate costs before deploying:

```bash
# Requires Pulumi Cloud Team/Enterprise
pulumi preview --policy-pack aws-cost-estimation
```

### VPC Endpoint Costs

The VPC endpoints incur hourly charges:
- **Interface VPC Endpoints**: ~$0.01/hour per endpoint per AZ (~$7.20/month per endpoint)
- **Data Processing**: $0.01 per GB processed
- **Total for 3 endpoints**: ~$21.60/month + data transfer costs

These endpoints eliminate the need for NAT Gateways (~$32.40/month) or Internet Gateways for Systems Manager access, potentially reducing overall costs while improving security.

## Troubleshooting

### Authentication Issues
```bash
# Verify AWS credentials
aws sts get-caller-identity

# Check Pulumi is logged in
pulumi whoami
```

### State Issues
```bash
# Refresh state from AWS
pulumi refresh

# Cancel an interrupted update
pulumi cancel
```

## Documentation

- [Pulumi AWS Provider](https://www.pulumi.com/registry/packages/aws/)
- [Pulumi Python Guide](https://www.pulumi.com/docs/languages-sdks/python/)
- [AWS IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
