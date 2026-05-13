# IDI Company Information Pipeline

Automated pipeline for resolving investor identifiers (CIK, CUSIP) to PermIDs and fetching detailed company information via the LSEG PermID API. This is the Company Info Processor which is a part of the FTM2J Terminal.

## Pipeline Overview

Each run performs two stages:

1. **PermID Retrieval** — resolve identifiers to PermID URLs via the LSEG Record Match API
2. **Company Info Lookup** — one Entity Lookup API call per PermID, plus optional Geonames calls for country fields

Processing is resumable: interrupted runs pick up where they left off. Stale records can be re-queried via `--threshold-days`.

### Identifier Types

| Type | API strategy | Input columns | Notes |
|---|---|---|---|
| `cik` | Record Match | `investor_name`, `investor_cik` | CIK sent as `Cik:<value>` Standard Identifier |
| `cusip` | Record Match | `issuer_name`, `security_cusip`, `stock_ticker` | CUSIP is LocalID; ticker is Standard Identifier; raw ticker stored in output |

### Output Layout

```
{output_dir}/
  company_info/   company_info_{type}.json
  permid_data/    permid_tracking_{type}.json
  failures/       failures_{type}.json
```

Each `company_info_*.json` record includes a `ticker` field (populated for CUSIP runs, `null` for CIK).

Paths support local directories or S3 URLs (`s3://bucket/path`).

---

## Quick Start

### Installation

```bash
uv sync              # Production
uv sync --all-groups # Development (includes tests)
```

### Credentials

```bash
export PERMID_API_KEY='your-key'
export GEONAMES_USER='your-username'
```

| Credential | Source |
|---|---|
| PermID API key | [LSEG Developer Portal](https://developers.lseg.com/en/api-catalog/open-perm-id/permid-entity-search) |
| Geonames username | [geonames.org](https://www.geonames.org/login) (free) |

### Run

```bash
# CIK pipeline
uv run pipeline \
  --input-file path/to/investors.parquet \
  --output-directory data/output \
  --type cik

# CUSIP pipeline (input must have issuer_name, security_cusip, stock_ticker)
uv run pipeline \
  --input-file path/to/securities.parquet \
  --output-directory data/output \
  --type cusip
```

### CLI Reference

| Flag | Required | Default | Description |
|---|---|---|---|
| `--input-file` | Yes | — | Local path or `s3://` URL |
| `--output-directory` | Yes | — | Local path or `s3://` URL |
| `--type` | Yes | — | `cik` or `cusip` |
| `--permid-api-key` | Env/CLI | `$PERMID_API_KEY` | LSEG PermID access token |
| `--geonames-user` | Env/CLI | `$GEONAMES_USER` | Geonames username |
| `--batch-size` | No | `2450` | Max entities per run |
| `--buffer-size` | No | `500` | Write-buffer flush size |
| `--threshold-days` | No | `None` | Re-process records older than N days |
| `--match-score-threshold` | No | `1` | Minimum Record Match score (0–1); `1` = 100% match required |

---

## Architecture

```
orchestrator.py
  └── PipelineOrchestrator.run()
        └── IdentifierFactory.build()   ← selects class from IDENTIFIER_REGISTRY
              ├── IdentifierCik         (cik)
              └── IdentifierCusip       (cusip)
                    └── IdentifierPipeline.run()
                          ├── BatchProcessing   — unprocessed/stale entity tracking
                          ├── PermidRetrieval   — Stage 1: Record Match API → PermID URLs
                          └── CompInfoRetrieval — Stage 2: Entity Lookup + Geonames
```

**Object composition**: `IdentifierFactory.build()` reads the matching `IdentifierSpec` from `IDENTIFIER_REGISTRY`, which bundles the concrete class (`IdentifierCik` or `IdentifierCusip`) and all output filenames. `IdentifierFactory` translates the `OrchestratorConfig` into three typed dataclasses (`FilePaths`, `BatchConfig`, `ApiCredentials`) and instantiates the class.

**CUSIP identifier flow**: `IdentifierCusip.load_data()` returns `{issuer_name: [cusip, ...]}` so that CUSIP is the stable identifier throughout `BatchProcessing`. Two auxiliary maps are built as side effects — `std_ticker_map` (CUSIP → formatted `ticker:X&&mic:Y` string, passed to `PermidRetrieval` as the Record Match Standard Identifier) and `raw_ticker_map` (CUSIP → raw ticker symbol, stored in the `CompanyInfo.ticker` output field).

**Adding a new identifier type**: add one entry to `IDENTIFIER_REGISTRY` in `registry.py`. No other code changes needed.

**Common modules** (`src/idi_company_info/common/`):

| Module | Purpose |
|---|---|
| `api.py` | LSEG + Geonames API clients |
| `batch.py` | Stale/unprocessed entity tracking |
| `buffer.py` | Write-buffering to JSON files |
| `failures.py` | Permanent-failure registry (do-not-retry) |
| `logs.py` | Structured logging + CloudWatch |
| `storage.py` | JSON load/save (local + S3) |

---

## Testing

```bash
make test            # Run all tests
make test-verbose    # Verbose output
make test-coverage   # With HTML coverage report
```

| Test file | Coverage |
|---|---|
| `test_api.py` | API client classes |
| `test_batch.py` | BatchProcessing |
| `test_identifier_cik.py` | IdentifierCik unit tests |
| `test_identifier_cusip.py` | IdentifierCusip unit tests (ticker maps, CUSIP grouping) |
| `test_permid_retriever.py` | PermidRetrieval (score parsing, record building, response parsing) |
| `test_orchestrator_integration.py` | End-to-end CIK and CUSIP pipelines |
| `test_storage.py` | JSON helpers |
| `test_logging.py` | Logger setup |

---

## Docker

### Compose file layout

Two Compose files are used together:

| File | Purpose |
|---|---|
| `compose.yml` | Base config — image references, volumes, env vars. Used as-is on EC2. |
| `compose.override.yml` | Local dev override — adds `build` blocks so services build from source. |

`docker compose` automatically merges both files when run locally. On EC2, only
`compose.yml` is deployed (no build blocks), so services pull from ECR
via `ORCHESTRATOR_IMAGE`. This means no Dockerfile or source code is needed on
the instance.

### Local development

```bash
cp .env.example .env          # Set PERMID_API_KEY, GEONAMES_USER, and input paths
mkdir -p data/output logs
docker compose build          # Build from source (uses override file automatically)
docker compose run --rm orchestrator-cik
docker compose run --rm orchestrator-cusip
```

To run against a pre-built registry image instead of building locally:

```bash
docker compose -f compose.yml run --rm orchestrator-cik
```

### Key `.env` variables

| Variable | Default | Description |
|---|---|---|
| `PERMID_API_KEY` | — | Required |
| `GEONAMES_USER` | — | Required |
| `ORCHESTRATOR_IMAGE` | `ghcr.io/dsi-clinic/idi-company-info-orchestrator:latest` | Image to pull (EC2 / registry runs) |
| `INPUT_FILE_CIK` | `./data/input/investors_cik.parquet` | CIK input (`investor_name`, `investor_cik`) |
| `INPUT_FILE_CUSIP` | `./data/input/securities_cusip.parquet` | CUSIP input (`issuer_name`, `security_cusip`, `stock_ticker`) |
| `OUTPUT_DIR` | `./data/output` | Root output directory |
| `THRESHOLD_DAYS` | `30` | Re-query records older than N days |
| `MATCH_SCORE_THRESHOLD` | `1` | Minimum Record Match score (1 = 100%) |
| `SCHEDULE_CIK` | `0 0 2 * * *` | Cron: CIK (2:00 AM) |
| `SCHEDULE_CUSIP` | `0 30 2 * * *` | Cron: CUSIP (2:30 AM) |

---

## Branching Strategy

Documentation governing all processors: https://github.com/dsi-clinic/idi-ftm2j-shared/tree/main#development

### CI/CD specifics

**No path filtering.** Unlike the shared infrastructure pipeline, `deploy.yml` runs the full job sequence on every push to `dev` or `main` regardless of what changed — there is no change-detection gate.

**Strict job sequence.** Jobs run in order: `version` → `docker` → `deploy-pulumi` → `sync-ecr`. Each job gates the next, so a Docker build failure will block the Pulumi deploy and ECR sync.

**Single Docker image.** `idi-company-info-orchestrator` is built and published — to GHCR first, then re-tagged and pushed to ECR by `sync-ecr`.

**Manual dispatch on issue branches.** `deploy-pulumi` and `sync-ecr` include `issue-*` in their `if` conditions. Pushes to issue branches do not trigger the workflow, but `workflow_dispatch` can be used to manually run a deploy from a feature branch — useful for testing before merging.

**Required GitHub secrets.** The `deploy-pulumi` job requires the following secrets to be set in the repository. Secrets passed via `[ -n "$VAR" ]` are optional and skip silently if unset; all others are required for the deploy to succeed.

| Secret | Required | Notes |
|---|---|---|
| `AWS_ROLE_ARN_DEPLOY` | Yes | IAM role for Pulumi and ECR |
| `AWS_REGION` | No | Defaults to `us-east-2` |
| `PULUMI_ACCESS_TOKEN` | Yes | |
| `PULUMI_CONFIG_PASSPHRASE` | Yes | |
| `PULUMI_STATE_BUCKET` | Yes | S3 bucket for Pulumi state |
| `ECR_REPOSITORY_PREFIX` | No | Defaults to `{pulumi_project}-{env}-{app_name}` |
| `BUCKET_NAME` | No | S3 bucket for pipeline I/O |
| `PERMID_API_KEY` | No | Set as a Pulumi secret |
| `GEONAMES_USER` | No | |
| `CRON_CIK` | No | Cron schedule for CIK runs |
| `CRON_CUSIP` | No | Cron schedule for CUSIP runs |
| `INPUT_FILE_CIK` | No | S3 path to CIK input parquet |
| `INPUT_FILE_CUSIP` | No | S3 path to CUSIP input parquet |
| `ECS_TASK_CPU` | No | |
| `ECS_TASK_MEMORY` | No | |
| `SCHEDULE_ENABLED` | No | |
| `BATCH_SIZE_CIK` | No | |
| `BATCH_SIZE_CUSIP` | No | |
| `BUFFER_SIZE` | No | |
| `THRESHOLD_DAYS` | No | |
| `MATCH_SCORE_THRESHOLD` | No | |
| `SHARED_DLQ_NAME` | No | Name of the shared DLQ from `idi-ftm2j-shared` |

