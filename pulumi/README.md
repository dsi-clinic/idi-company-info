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

`{prefix}` is `idi-<stack>-company-info` (project-stack-app).

### Networking (`infra/networking.py`)

Uses the account's **default VPC** and its first subnet (single AZ, for simplicity).
Fargate tasks are launched with public IPs (`assign_public_ip=True` in the
schedules) and reach AWS APIs and the internet directly — there are no VPC
endpoints or NAT gateway.

| Resource | Name | Inbound | Outbound |
|---|---|---|---|
| Security group | `{prefix}-sg-ecs` | None | All |

### IAM (`infra/iam.py`, `infra/scheduling.py`)

- **Task execution role** `{prefix}-role-ecs-execution` — used by the ECS agent to
  pull the image (ECR), write logs (CloudWatch), and read the two SSM SecureString
  secrets (`ssm:GetParameters` + `kms:Decrypt` via SSM).
- **Task role** `{prefix}-role-ecs-task` — assumed by the container at runtime:
  read/write on the shared processor bucket (S3), plus ECS Exec (SSM Messages) for
  debugging.
- **Scheduler role** `{prefix}-role-scheduler` — assumed by EventBridge Scheduler to
  `ecs:RunTask`, `iam:PassRole` the two task roles, and send failures to the shared DLQ.

### Compute (`infra/ecs.py`)

- **ECS cluster** `{prefix}-cluster` — Fargate, Container Insights enabled.
- **Task definition** `{prefix}` — the orchestrator. Fargate / `awsvpc`, `cpu`/`memory`
  from config (default 1024 / 4096). Baseline command is `--help`; per-run args
  (input source, file, batch size, …) are supplied by each EventBridge schedule via
  `containerOverrides` (see `infra/scheduling.py`).
- **Aggregate task definition** `{prefix}-aggregate` — runs `idi_company_info.output`
  (its own entryPoint) for on-demand final aggregation. See
  [Running the final aggregation on demand](#running-the-final-aggregation-on-demand).

### Container registry (`infra/ecr.py`)

- **ECR repository** `{prefix}-orchestrator` — holds the orchestrator image. Task
  definitions reference the `:latest` tag; a lifecycle policy expires images beyond
  `idi:ecr_image_count` (default 5). Because task defs pin `:latest`, rollback is a
  re-push of the older image, not `pulumi up` (see the note in `infra/ecr.py`).

### Logging (`infra/logs.py`)

- **CloudWatch log group** `/ecs/{prefix}` — retention `idi:log_retention_days`
  (default 30). The orchestrator streams under the `orchestrator/…` prefix and the
  aggregate task under `aggregate/…`.

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
| `default_vpc_id` | Default VPC ID |
| `ecs_sg_id` / `ecs_sg_name` | ECS Fargate security group |
| `primary_subnet_id` | Subnet the tasks run in |
| `task_execution_role_arn` / `_name` | ECS task execution role |
| `task_role_arn` / `_name` | ECS task role |
| `scheduler_role_arn` / `_name` | EventBridge Scheduler role |
| `log_group_arn` / `_name` / `log_group_retention_days` | CloudWatch log group |
| `ecr_repo_url` / `ecr_orchestrator_image` | ECR repo URL and resolved `:latest` image URI |
| `ecs_cluster_arn` / `ecs_cluster_name` | ECS cluster |
| `task_definition_arn` | Orchestrator task definition |
| `aggregate_task_definition_arn` | Aggregate task definition |
| `permid_api_key_param_arn` / `_name` | PermID secret SSM parameter |
| `geonames_user_param_arn` / `_name` | GeoNames secret SSM parameter |
| `schedule_<source>_name` / `_arn` | One pair per configured input source |
| `shared_dlq_arn` | Shared dead-letter queue (looked up by name) |

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

## Debugging a Running Task (ECS Exec)

Tasks launch with ECS Exec enabled (`enable_execute_command=True`), so you can open
a shell in a running task without SSH or a bastion. Requires the AWS CLI
Session Manager plugin.

```bash
CLUSTER=$(pulumi stack output ecs_cluster_name)
TASK=$(aws ecs list-tasks --cluster "$CLUSTER" --query 'taskArns[0]' --output text)

aws ecs execute-command \
  --cluster "$CLUSTER" \
  --task "$TASK" \
  --container company-info-orchestrator \
  --interactive \
  --command /bin/sh
```

Orchestrator runs are short-lived (and the schedules are usually disabled), so there
may be no task to attach to. Launch one on demand with `aws ecs run-task` against the
task definition (or run the aggregate task as shown above), then exec into it.

---

## Cost Notes

There is no always-on compute — Fargate is billed per-second while a task runs, so
cost scales with how often the schedules fire and how long each run takes.

| Resource | Cost |
|---|---|
| Fargate task (1 vCPU / 4 GB default) | ~$0.05 per task-hour of runtime |
| CloudWatch Logs | Storage + ingestion, usage-based |
| ECR storage | Usage-based (lifecycle keeps the last `ecr_image_count` images) |
| S3 storage + requests | Usage-based (shared processor bucket) |

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
