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
# The two secrets are SSM SecureStrings set out-of-band (NOT Pulumi config).
# `pulumi up` creates them with placeholders; set the real values once per stack:
aws ssm put-parameter --name /idi/dev/company-info/secrets/permid_api_key \
  --type SecureString --value '<permid-key>' --overwrite
aws ssm put-parameter --name /idi/dev/company-info/secrets/geonames_user \
  --type SecureString --value '<geonames-username>' --overwrite

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
| Non-secret pipeline args | **Committed** in `Pulumi.<stack>.yaml` | `input_sources`, `output_dir`, `cpu`, `memory`, `buffer_size`, `threshold_days`, `match_score_threshold`, `schedule_enabled`, `aws:region`, `app_name` |
| Shared values | Read at runtime from SSM (`/idi/<stack>/shared/*`), published by the shared stack | `processor_bucket_name`, `dlq_name` |
| The two secrets | **SSM SecureString** parameters set out-of-band via `aws ssm put-parameter` (Pulumi creates a placeholder and never manages the value) | `permid_api_key`, `geonames_user` |
| Deploy plumbing | GitHub repo secrets only | `PULUMI_ACCESS_TOKEN`, `PULUMI_CONFIG_PASSPHRASE`, `PULUMI_STATE_BUCKET`, `AWS_ROLE_ARN_*` |

The two secrets are **never committed** — not even as encrypted Pulumi config.
`pulumi up` creates each as an SSM `SecureString` parameter seeded with a
`PLACEHOLDER` value and `ignore_changes=["value"]`, so Pulumi never reads or
overwrites the real value. You set (and rotate) the real values out-of-band,
once per stack:

```bash
# dev; use /idi/prod/... for prod
aws ssm put-parameter --name /idi/dev/company-info/secrets/permid_api_key \
  --type SecureString --value '<permid-key>' --overwrite
aws ssm put-parameter --name /idi/dev/company-info/secrets/geonames_user \
  --type SecureString --value '<geonames-username>' --overwrite
```

At task launch, the ECS agent injects both into the container as the
`PERMID_API_KEY` and `GEONAMES_USER` env vars (task-definition `secrets:` block,
see `infra/ecs.py`). The task **execution** role is granted `ssm:GetParameters`
scoped to exactly these two ARNs plus `kms:Decrypt` via `kms:ViaService`
(`infra/iam.py`). Rotation is another `put-parameter --overwrite`, picked up at
the next task launch — no redeploy.

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

1. Fill every `REQUIRED` placeholder — the per-source `input_file` keys (and
   `output_dir` if it differs). The processor bucket and DLQ come from SSM
   (`/idi/prod/shared/*`), so they are not set here.
2. Validate: `pulumi stack select prod && pulumi preview` — expect three (disabled)
   schedules and no missing-config errors.
3. Deploy once (`pulumi up`) to create the placeholder SSM parameters, then set
   both secrets out-of-band:
   ```bash
   aws ssm put-parameter --name /idi/prod/company-info/secrets/permid_api_key \
     --type SecureString --value '<prod-permid-key>' --overwrite
   aws ssm put-parameter --name /idi/prod/company-info/secrets/geonames_user \
     --type SecureString --value '<geonames-username>' --overwrite
   ```
4. Flip `idi:schedule_enabled` to `"true"` when ready to arm the schedules.

> **Note:** the previously committed dev PermID key was exposed in git history —
> rotate it (set the new value via `aws ssm put-parameter` as above).

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

- **S3 bucket** — input/output data; **not created here.** The processor bucket is
  owned by the shared stack; its name is read from SSM (`/idi/<stack>/shared/processor_bucket_name`).
- **SSM SecureString parameters** — `/idi/<stack>/company-info/secrets/permid_api_key`
  and `/idi/<stack>/company-info/secrets/geonames_user`. Created here with placeholder
  values (`ignore_changes`); real values set out-of-band and injected into the ECS
  task as env vars at launch. See [Configuration](#configuration-dev-vs-prod) above.

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
