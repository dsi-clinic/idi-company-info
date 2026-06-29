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

# All non-secret config is committed in Pulumi.<stack>.yaml (see below).
# The only value you set by hand is the secret — once per stack:
pulumi config set --secret idi:permid_api_key YOUR_KEY

pulumi preview   # Validate
pulumi up        # Deploy
```

---

## Configuration: dev vs prod

Each environment is a Pulumi stack with its own committed config file
(`Pulumi.dev.yaml`, `Pulumi.prod.yaml`). **The committed stack file is the single
source of truth for that environment's pipeline arguments** — CI does not inject
them. The branch→stack mapping lives in `.github/workflows/deploy.yml`
(`main` → `prod`, every other branch → `dev`).

### What lives where

| Kind | Where | Examples |
|---|---|---|
| Non-secret pipeline args | **Committed** in `Pulumi.<stack>.yaml` | `input_sources`, `output_dir`, `bucket_name`, `cpu`, `memory`, `buffer_size`, `threshold_days`, `match_score_threshold`, `schedule_enabled`, `geonames_user`, `shared_dlq_name`, `aws:region`, `app_name` |
| The one secret | `pulumi config set --secret` (CI-injected from the `PERMID_API_KEY` GitHub secret, or set manually per stack) | `permid_api_key` |
| Deploy plumbing | GitHub repo secrets only | `PULUMI_ACCESS_TOKEN`, `PULUMI_CONFIG_PASSPHRASE`, `PULUMI_STATE_BUCKET`, `AWS_ROLE_ARN_*` |

`permid_api_key` is **never committed in plaintext**. It is encrypted per stack
against that stack's `encryptionsalt`, so it must be set **once per stack** and CI
must use the **same** `PULUMI_CONFIG_PASSPHRASE` that encrypted it:

```bash
pulumi stack select dev   # then again for prod
pulumi config set --secret idi:permid_api_key <key>
```

### Scheduled input sources

Schedules are driven entirely by the `idi:input_sources` list in each stack file —
one EventBridge schedule per entry. The active set is exactly three:
`shareholder_tracker_cik`, `commercial_debt_tracker`, `corporate_subsidiaries`.
(`shareholder_tracker_cusip` remains a valid `InputSource` but is intentionally not
scheduled.) Each entry is `{source, input_file, cron, batch_size, max_requests}`
(`batch_size` = new identifiers resolved per run; `max_requests` = per-run PermID
enrichment-request budget); `input_file` is a
single parquet file (for `commercial_debt_tracker`,
`…/processors/cdt/debt-instruments/latest.parquet`). `idi:schedule_enabled`
is currently `"false"` in both stacks — set it to `"true"` per stack to arm the crons.

### Bringing up prod (first deploy)

`Pulumi.prod.yaml` ships as a scaffold with `REPLACE_ME` placeholders. Before the
first `main` deploy:

1. Fill every `REPLACE_ME` — prod `bucket_name`/`output_dir`, the per-source
   `input_file` URIs, `geonames_user`, `shared_dlq_name`.
2. Set the secret: `pulumi stack select prod && pulumi config set --secret idi:permid_api_key <prod-key>`.
3. Validate: `pulumi stack select prod && pulumi preview` — expect three (disabled)
   schedules and no missing-config errors.
4. Flip `idi:schedule_enabled` to `"true"` when ready to arm the schedules.

> **Note:** the previously committed dev PermID key was exposed in git history —
> rotate it and update the `PERMID_API_KEY` GitHub secret.

### Running the final aggregation on demand

Each per-processor run regenerates the combined `latest.parquet` automatically. To force a
re-aggregation out of band, run the dedicated **aggregate task definition**
(`{prefix}-aggregate`). It runs `idi_company_info.output` instead of the orchestrator — the
image ENTRYPOINT is `pipeline` and ECS `containerOverrides` cannot change `entryPoint`, so a
separate task definition (with its own entryPoint) is required; passing aggregate args to the
orchestrator task definition would not work.

```bash
aws ecs run-task \
  --cluster "$(pulumi stack output ecs_cluster_name)" \
  --task-definition "$(pulumi stack output aggregate_task_definition_arn)" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$(pulumi stack output primary_subnet_id)],securityGroups=[$(pulumi stack output ecs_sg_id)],assignPublicIp=ENABLED}" \
  --overrides '{"containerOverrides":[{"name":"company-info-aggregate","command":["--output-directory","s3://<bucket>/company-info/output"]}]}' \
  --region us-east-2
```

`command` accepts the aggregate CLI flags (`--output-directory` is required; `--final-output-file`
and `--lock-timeout` are optional). Logs stream to CloudWatch under the `aggregate/...` prefix.

> The IAM identity invoking `run-task` needs `ecs:RunTask` on the aggregate task-definition
> arn and `iam:PassRole` on both the task execution and task roles.

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
