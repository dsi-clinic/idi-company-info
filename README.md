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
| `cusip` | Record Match | `issuer_name`, `security_cusip`, `stock_ticker` | CUSIP is LocalID; ticker is the Standard Identifier used for Record Match |

### Output Layout

```
{output_dir}/
  company_info/   company_info_{type}.json
  permid_data/    permid_tracking_{type}.json
  failures/       failures_{type}.json
```

Each `company_info_*.json` record is flat company info keyed by its PermID URL (no `search`/`result` envelope).

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
| `--batch-size` | No | `2450` | Max NEW identifiers resolved to PermIDs per run (intake cap; Record Match batches 1000/call, so cheap on quota) |
| `--max-requests` | No | `1650` | Total PermID-request budget per run against the shared daily quota — see [API Quota Budgeting](#api-quota-budgeting) |
| `--buffer-size` | No | `500` | Write-buffer flush size |
| `--threshold-days` | No | `None` | Re-process records older than N days |
| `--match-score-threshold` | No | `1` | Minimum Record Match score (0–1); `1` = 100% match required |

---

## API Quota Budgeting

The PermID daily request quota is **per API key, not per pipeline**. Every scheduled input source runs its own ECS task against the *same* key, so the quota is a shared resource that has to be divided up ahead of time — no source can discover at runtime how much of it a sibling has already spent.

### The budget

| | |
|---|---|
| PermID daily quota | **5,000 requests/day** per key |
| Scheduled sources (dev) | 4 |
| Total worst case | `4,500 + 3 × 100 = 4,800 ≤ 5,000` |

The caps are **deliberately lopsided rather than split evenly**, because only one source has a backlog:

| Source | `max_requests` | Why |
|---|---|---|
| `shareholder_tracker_cusip` | 4,500 | Backfilling 13,209 securities from cold at ~2 live requests each |
| `shareholder_tracker_cik` | 100 | Steady state — no new rows per day; retries ~3 retryable failures per run |
| `commercial_debt_tracker` | 100 | Not currently producing new rows |
| `corporate_subsidiaries` | 100 | Only a few new filings per day |

**This split is temporary.** It is sized for the CUSIP backfill and should be rebalanced once that drains — watch for `Reached max_requests budget` disappearing from the CUSIP source's logs, which is the signal it has caught up. A steady-state source that starts producing volume again will crawl at 100/day and will say so with that same log line.

The 200-request headroom absorbs ad-hoc local runs. Schedules are staggered (`02:00`, `02:30`, `03:00`, `03:30` UTC) so a source that aborts early does not overlap the next one, but staggering is *not* what keeps the total in bounds — the per-source caps are.

The prod stack is still scaffolded at 3 sources × 1,650 with `schedule_enabled: "false"`; adding `shareholder_tracker_cusip` there means re-deriving its caps the same way. The CLI default remains 1,650 (see `--max-requests`) because it sizes a single ad-hoc run, not a fleet — scheduled runs always get an explicit value from `idi:input_sources`.

**Adding or re-enabling a source means re-deriving the cap**: `max_requests × number of enabled sources ≤ 5,000`. Both values live in `idi:input_sources` in `pulumi/Pulumi.<stack>.yaml`, one entry per source, so the arithmetic is visible in one place. There is no cross-source enforcement at runtime; if the caps sum above the quota, the last source of the night is the one that starves.

### What counts against it

Every HTTP call to a PermID endpoint counts, whether it succeeds or fails, because the request is spent either way:

| Call | Endpoint | Cost |
|---|---|---|
| Record Match | `permid/match` | 1 per ≤1,000 identifiers (stage 1 batches, so this is cheap) |
| Entity Lookup | `permid.org/<id>` | 1 per candidate PermID, including candidates whose lookup fails |
| Sector / quote follow-ups | `permid.org/<id>` | ≤4 per company (3 sectors + 1 quote), skipped entirely with `--no-enrich-metadata` |

Sector labels are memoized in a `sector_cache.json` shared across all sources at the output root, so after the first run most companies cost ~2 live requests rather than ~6. **Geonames is a separate API with its own quota** — credits, capped daily and hourly ([credits](https://www.geonames.org/export/credits.html)) — and is deliberately excluded from these counters.

`max_requests` budgets the **whole run**, not just stage 2: `BatchStats.record_permid_call()` is called at each request site, and the company-info stage checks the running total before starting each new company. Record Match runs first, so its calls are already reflected in the total and shrink the enrichment headroom accordingly. `--batch-size` is an intake cap on *new identifiers*, not a request cap; it is set high because Record Match batches.

### When the quota runs out anyway

The quota can still be exhausted mid-run — a manual run, a retried task, or another consumer of the key. A PermID `429` is **not** retried (see `QuotaSafeApiClient` in `api.py`), so it surfaces immediately as a `RATE_LIMIT` failure, aborts the company-info stage, and lets the run tear down normally: buffered results are flushed, the sector cache is persisted, partial results are aggregated into `latest.parquet`, and the task exits `0` so the EventBridge retry policy does not fire a second run into a spent quota. Nothing is added to the do-not-retry registry — the remaining candidates are simply picked up by the next run.

### What to look for in the logs

| Line | Meaning |
|---|---|
| `PermID requests used: N total against the daily quota (...)` | End-of-run total with its per-stage breakdown |
| `Reached max_requests budget (N PermID requests); stopping before candidate ...` | The run hit **our** cap and stopped cleanly — expected on a large backlog |
| `PermID daily quota exhausted (...); stopping the company-info stage at ...` | The run hit **LSEG's** quota; check whether the caps still sum under 5,000 |
| `Sector cache hits: N sector resolves served from cache` | Requests avoided by the shared memo |
| `Run outcome: COMPLETE` / `INTERRUPTED — PermID daily quota exhausted` | The run-level verdict, from `BatchStats.quota_exhausted`. Both exit `0`, so this is the line that tells them apart |

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

**CUSIP identifier flow**: `IdentifierCusip.load_data()` returns `{issuer_name: [cusip, ...]}` so that CUSIP is the stable identifier throughout `BatchProcessing`. As a side effect it builds `std_ticker_map` (CUSIP → formatted `ticker:X&&mic:Y` string, passed to `PermidRetrieval` as the Record Match Standard Identifier).

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
| `ORCHESTRATOR_IMAGE` | `ghcr.io/dsi-rse/idi-company-info-orchestrator:latest` | Image to pull (EC2 / registry runs) |
| `INPUT_FILE_CIK` | `./data/input/investors_cik.parquet` | CIK input (`investor_name`, `investor_cik`) |
| `INPUT_FILE_CUSIP` | `./data/input/securities_cusip.parquet` | CUSIP input (`issuer_name`, `security_cusip`, `stock_ticker`) |
| `OUTPUT_DIR` | `./data/output` | Root output directory |
| `THRESHOLD_DAYS` | `30` | Re-query records older than N days |
| `MATCH_SCORE_THRESHOLD` | `1` | Minimum Record Match score (1 = 100%) |
| `SCHEDULE_CIK` | `0 0 2 * * *` | Cron: CIK (2:00 AM) |
| `SCHEDULE_CUSIP` | `0 30 2 * * *` | Cron: CUSIP (2:30 AM) |

---

## Development cycle

Documentation governing all processors: https://github.com/dsi-rse/idi-ftm2j-shared/tree/main#development--contributing

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

