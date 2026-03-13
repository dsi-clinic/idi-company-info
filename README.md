# IDI Company Information Pipeline

Automated pipeline for resolving investor identifiers (CIK, CUSIP, Ticker) to PermIDs and fetching detailed company information via the LSEG PermID API.

## Pipeline Overview

Each run performs two stages:

1. **PermID Retrieval** — resolve identifiers to PermID URLs via Entity Search or Record Match
2. **Company Info Lookup** — one Entity Lookup API call per PermID, plus optional Geonames calls for country fields

Processing is resumable: interrupted runs pick up where they left off. Stale records can be re-queried via `--threshold-days`.

### Identifier Types

| Type | API strategy | Input columns |
|---|---|---|
| `cik` | Entity Search | `investor_name`, `investor_cik` |
| `cik-match` | Record Match | `investor_name`, `investor_cik` |
| `cusip` | Entity Search | `issuer_name`, `security_cusip` |
| `ticker` | Record Match | `issuer_name`, `stock_ticker` |

### Output Layout

```
{output_dir}/
  company_info/   company_info_{type}.json
  permid_data/    permid_tracking_{type}.json
  failures/       failures_{type}.json
```

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
uv run python -m idi_company_info.processors.orchestrator \
  --input-file path/to/input.parquet \
  --output-directory data/output \
  --type cik
```

### CLI Reference

| Flag | Required | Default | Description |
|---|---|---|---|
| `--input-file` | Yes | — | Local path or `s3://` URL |
| `--output-directory` | Yes | — | Local path or `s3://` URL |
| `--type` | Yes | — | `cik`, `cik-match`, `cusip`, `ticker` |
| `--permid-api-key` | Env/CLI | `$PERMID_API_KEY` | LSEG PermID access token |
| `--geonames-user` | Env/CLI | `$GEONAMES_USER` | Geonames username |
| `--batch-size` | No | `2450` | Max entities per run |
| `--buffer-size` | No | `500` | Write-buffer flush size |
| `--threshold-days` | No | `None` | Re-process records older than N days |
| `--match-score-threshold` | No | `1` | Minimum Record Match score (cik-match/ticker only) |

---

## Architecture

```
orchestrator.py
  └── PipelineOrchestrator.run()
        └── IdentifierFactory.build()   ← selects class from IDENTIFIER_REGISTRY
              ├── IdentifierCik         (cik, cik-match)
              └── IdentifierCusip       (cusip, ticker)
                    └── IdentifierPipeline.run()
                          ├── BatchProcessing          — unprocessed/stale entity tracking
                          ├── PermidRetriever          — Stage 1: PermID API calls
                          │     ├── EntitySearchRetriever   (cik, cusip)
                          │     └── RecordMatchRetriever    (cik-match, ticker)
                          └── generate_company_info()  — Stage 2: Entity Lookup + Geonames
```

**Object composition**: `IdentifierFactory.build()` reads the matching `IdentifierSpec` from `IDENTIFIER_REGISTRY`, which bundles the concrete class (`IdentifierCik` or `IdentifierCusip`), the `QueryType`, and all output filenames. `IdentifierFactory` then translates the `OrchestratorConfig` into the three typed dataclasses (`FilePaths`, `BatchConfig`, `ApiCredentials`) and instantiates the class. Inside `IdentifierPipeline.__init__`, the `query_type` drives which `PermidRetriever` strategy is injected: `EntitySearchRetriever` for `cik`/`cusip`, or `RecordMatchRetriever` for `cik-match`/`ticker`. All four API clients are always constructed and held in an `ApiClients` dataclass regardless of type.

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
| `test_identifier_cusip.py` | IdentifierCusip unit tests |
| `test_permid_retriever.py` | EntitySearch + RecordMatch retrievers |
| `test_orchestrator_integration.py` | End-to-end orchestrator |
| `test_storage.py` | JSON helpers |
| `test_logging.py` | Logger setup |

---

## Docker

```bash
cp .env.example .env          # Set PERMID_API_KEY, GEONAMES_USER, and input paths
mkdir -p data/output logs
docker compose up -d          # Start scheduler (CIK at 2 AM, CUSIP at 2:30 AM)
```

Manual runs:

```bash
docker compose run --rm orchestrator-cik
docker compose run --rm orchestrator-cik-match
docker compose run --rm orchestrator-cusip
docker compose run --rm orchestrator-ticker
```

Key `.env` variables:

| Variable | Default | Description |
|---|---|---|
| `PERMID_API_KEY` | — | Required |
| `GEONAMES_USER` | — | Required |
| `INPUT_FILE_CIK` | `./data/input/investors_cik.parquet` | CIK + CIK Match input |
| `INPUT_FILE_CUSIP` | `./data/input/securities_cusip.parquet` | CUSIP input |
| `INPUT_FILE_TICKER` | `./data/input/securities_ticker.parquet` | Ticker input |
| `OUTPUT_DIR` | `./data/output` | Root output directory |
| `THRESHOLD_DAYS` | `30` | Re-query threshold |
| `SCHEDULE_CIK` | `0 0 2 * * *` | Cron: CIK (2:00 AM) |
| `SCHEDULE_CUSIP` | `0 30 2 * * *` | Cron: CUSIP (2:30 AM) |

---

## Branching Strategy

The CI pipeline (`ci.yml`) triggers on push to `main`, `dev`, and `release/**`.

| Branch | Version bump | Deploy target | Notes |
|---|---|---|---|
| `dev` | `patch` + `alpha` pre-release | `dev` | Feature development |
| `release/**` | `rc` pre-release | `dev` | Release candidates |
| `main` | Stable (drops pre-release tag) | `prod` | Production releases |

Each push to a tracked branch runs in order:

1. **Lint** — `ruff check` + `ruff format`
2. **Tests** — `pytest` with coverage
3. **Security** — `pip-audit` + CodeQL
4. **Pulumi preview** — validates infra changes without deploying
5. **Version bump** — updates `pyproject.toml`, commits, and tags
6. **Docker build + push** — builds and pushes to GHCR
7. **Pulumi deploy** — `pulumi up` against the target stack
8. **ECR sync** — re-tags and pushes the GHCR image to ECR

**Issue branches** (e.g. `issue-123-my-feature`) are used for all feature and bug-fix work. They do not trigger CI automatically — open a PR to `dev` to run the full pipeline. The Pulumi deploy step will run against the `dev` stack if triggered manually via `workflow_dispatch`.
