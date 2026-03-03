# IDI Company Information Pipeline

Automated pipeline for resolving investor identifiers (CIK, CUSIP, Ticker) to PermIDs and fetching detailed company information via the LSEG PermID API.

## Pipeline Overview

Each run performs two stages back-to-back:

1. **PermID Retrieval** — resolve identifiers to PermID URLs
   - CIK / CUSIP: one Entity Search API call per identifier
   - CIK Match / Ticker: one Record Match API call per batch of up to 1,000 rows
   - Entities already resolved in a previous run are skipped (no redundant API calls)
2. **Company Info Lookup** — one Entity Lookup API call per PermID, plus optional Geonames calls for country fields

Processing is resumable: if a run is interrupted, the next run picks up where it left off. Stale records can be automatically re-queried via `--threshold-days`.

### Identifier Types

| Type | API strategy | Input columns | Default batch size |
|------|-------------|--------------|-------------------|
| `cik` | Entity Search | `investor_name`, `investor_cik` | 2,450 |
| `cik-match` | Record Match | `investor_name`, `investor_cik` | 2,450 |
| `cusip` | Entity Search | `issuer_name`, `security_cusip` | 2,450 |
| `ticker` | Record Match | `issuer_name`, `stock_ticker` | 2,450 |

### Output Layout

```
{output_dir}/
  company_info/
    company_info_cik.json
    company_info_cik_match.json
    company_info_cusip.json
    company_info_ticker.json
  permid_data/
    permid_tracking_cik.json
    permid_tracking_cik_match.json
    permid_tracking_cusip.json
    permid_tracking_ticker.json
  failures/
    failures_cik.json
    failures_cik_match.json
    failures_cusip.json
    failures_ticker.json
```

## Quick Start

### Installation

```bash
uv pip install -e .           # Production
uv pip install -e ".[dev]"    # Development (includes tests)
```

### API Credentials

| Credential | Source |
|-----------|--------|
| PermID API key | [LSEG Developer Portal](https://developers.lseg.com/en/api-catalog/open-perm-id/permid-entity-search) |
| Geonames username | [geonames.org](https://www.geonames.org/login) (free) |

```bash
export PERMID_API_KEY='your-key'
export GEONAMES_USER='your-username'
```

### Run the Orchestrator

```bash
# CIK mode (investors, Entity Search)
python -m idi_company_info.processors.orchestrator \
  --input-file /path/to/investors.parquet \
  --output-directory data/output \
  --type cik \
  --batch-size 2450

# CIK Match mode (investors, Record Match) — bulk CIK submission
python -m idi_company_info.processors.orchestrator \
  --input-file /path/to/investors.parquet \
  --output-directory data/output \
  --type cik-match \
  --batch-size 2450 \
  --match-score-threshold 1

# CUSIP mode (securities, Entity Search)
python -m idi_company_info.processors.orchestrator \
  --input-file /path/to/securities.parquet \
  --output-directory data/output \
  --type cusip \
  --batch-size 2450

# Ticker mode (securities, Record Match) — manual only
python -m idi_company_info.processors.orchestrator \
  --input-file /path/to/securities.parquet \
  --output-directory data/output \
  --type ticker \
  --batch-size 2450 \
  --match-score-threshold 1

# Re-query records older than 30 days
python -m idi_company_info.processors.orchestrator \
  --input-file /path/to/investors.parquet \
  --output-directory data/output \
  --type cik \
  --threshold-days 30
```

### Makefile Shortcuts

```bash
make install
make run-cik   INPUT_PARQUET=data/investors.parquet   OUTPUT_DIR=data/output
make run-cusip INPUT_PARQUET=data/securities.parquet  OUTPUT_DIR=data/output
make run-ticker INPUT_PARQUET=data/securities.parquet OUTPUT_DIR=data/output

make help   # Full option listing
```

## CLI Reference

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--input-file` | Yes | — | Path to input parquet file |
| `--output-directory` | Yes | — | Root directory for all output files |
| `--type` | Yes | — | `cik`, `cik-match`, `cusip`, or `ticker` |
| `--permid-api-key` | Env/CLI | `$PERMID_API_KEY` | LSEG PermID access token |
| `--geonames-user` | Env/CLI | `$GEONAMES_USER` | Geonames API username |
| `--batch-size` | No | `2450` | Max entities (PermID) and max entity-lookups (company info) per run |
| `--buffer-size` | No | `500` | Write-buffer size before flushing to disk |
| `--threshold-days` | No | `None` | Re-process records not updated in the last N days |
| `--match-score-threshold` | No | `1` | Minimum Record Match score (cik-match/ticker only; `1` = 100%) |

Credentials can be supplied via CLI flags or environment variables; the CLI flag takes priority.

## Architecture

```
orchestrator.py
  └── PipelineOrchestrator.run()
        └── IdentifierFactory.build()  ← selects class from IDENTIFIER_REGISTRY
              ├── IdentifierCik        (cik, cik-match)
              └── IdentifierCusip      (cusip, ticker)
                    ├── Identifier.run()
                    │     ├── BatchProcessing.get_unprocessed_entities()
                    │     ├── BatchProcessing.filter_stale_entities()
                    │     └── Identifier.process_entities()
                    │           ├── PermidRetriever.retrieve()   [stage 1]
                    │           │     ├── EntitySearchRetriever  (cik, cusip)
                    │           │     └── RecordMatchRetriever   (cik-match, ticker)
                    │           └── Identifier.generate_company_info()  [stage 2]
                    └── common/
                          ├── api.py       — LSEG + Geonames API clients
                          ├── batch.py     — stale/unprocessed entity tracking
                          ├── buffer.py    — write-buffering to JSON files
                          ├── failures.py  — permanent-failure registry
                          ├── logs.py      — structured logging
                          └── storage.py   — JSON load/save helpers
```

**Adding a new identifier type**: add one entry to `IDENTIFIER_REGISTRY` in `orchestrator.py` in addtion to creating a new `Identifier` subclass.

**Batch size behaviour**:
- PermID stage: processes up to `batch_size` entities per run; already-resolved entities are skipped
- Company info stage: processes entities until the cumulative PermID lookup count reaches `batch_size`; entities cut off by the batch threshold are picked up automatically on the next run
- Record Match (cik-match, ticker): CSV payload is further chunked at 1,000 rows per API request to stay within the API size limit

## Testing

```bash
uv pip install -e ".[dev]"
make test                  # Run all tests
make test-verbose          # Verbose output
make test-coverage         # With HTML coverage report
```

Test modules:

| File | Coverage |
|------|---------|
| `test_api.py` | API client classes |
| `test_batch.py` | BatchProcessing (stale, unprocessed) |
| `test_identifier_cik.py` | IdentifierCik unit tests |
| `test_identifier_cusip.py` | IdentifierCusip unit tests |
| `test_permid_retriever.py` | EntitySearchRetriever, RecordMatchRetriever |
| `test_orchestrator_integration.py` | End-to-end orchestrator (all 3 types) |
| `test_storage.py` | JSON helpers |
| `test_logging.py` | Logger setup |

## Docker Deployment

### Setup

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env — set PERMID_API_KEY, GEONAMES_USER, and input file paths

# 2. Create output and log directories
mkdir -p data/output logs

# 3. Start scheduler (runs CIK at 2 AM, CUSIP at 2:30 AM by default)
docker compose up -d

# 4. Confirm scheduler is running
docker compose ps
docker compose logs -f scheduler
```

### Manual Runs

```bash
# Run CIK pipeline immediately
docker compose run --rm orchestrator-cik

# Run CIK Match pipeline (manual only — no schedule)
docker compose run --rm orchestrator-cik-match

# Run CUSIP pipeline immediately
docker compose run --rm orchestrator-cusip

# Run Ticker pipeline (manual only — no schedule)
docker compose run --rm orchestrator-ticker
```

### Management

```bash
docker compose logs -f scheduler              # Tail scheduler logs
docker compose restart scheduler              # Apply .env / schedule changes
docker compose up -d --build                  # Rebuild after code changes
docker compose down                           # Stop all services
```

### Configuration

Edit [.env](.env) to customize:

| Variable | Default | Description |
|---------|---------|-------------|
| `PERMID_API_KEY` | — | Required |
| `GEONAMES_USER` | — | Required |
| `INPUT_FILE_CIK` | `./data/input/investors_cik.parquet` | Input for CIK and CIK Match tracks |
| `INPUT_FILE_CUSIP` | `./data/input/securities_cusip.parquet` | Input for CUSIP track |
| `INPUT_FILE_TICKER` | `./data/input/securities_ticker.parquet` | Input for Ticker track (manual) |
| `OUTPUT_DIR` | `./data/output` | Root output directory |
| `LOG_DIR` | `./logs` | Log file directory |
| `BATCH_SIZE_CIK` | `2450` | Batch size for CIK track |
| `BATCH_SIZE_CIK_MATCH` | `2450` | Batch size for CIK Match track |
| `BATCH_SIZE_CUSIP` | `2450` | Batch size for CUSIP track |
| `BATCH_SIZE_TICKER` | `2450` | Batch size for Ticker track |
| `BUFFER_SIZE` | `500` | Write-buffer size |
| `THRESHOLD_DAYS` | `30` | Re-query threshold |
| `MATCH_SCORE_THRESHOLD` | `1` | Record Match score floor (cik-match, ticker) |
| `SCHEDULE_CIK` | `0 0 2 * * *` | Cron: CIK track (2:00 AM) |
| `SCHEDULE_CUSIP` | `0 30 2 * * *` | Cron: CUSIP track (2:30 AM) |

Changes to `.env` require restarting the scheduler:
```bash
docker compose restart scheduler
```

### Docker Compose as systemd Service

To start the stack automatically on boot:

```ini
# /etc/systemd/system/idi-pipeline-docker.service
[Unit]
Description=IDI Company Information Pipeline (Docker Compose)
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/idi-company-information
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
```

```bash
sudo cp -r . /opt/idi-company-information
sudo nano /opt/idi-company-information/.env   # Set credentials + paths
sudo systemctl daemon-reload
sudo systemctl enable idi-pipeline-docker
sudo systemctl start idi-pipeline-docker
sudo systemctl status idi-pipeline-docker
```

## Monitoring

```bash
# Scheduler container logs
docker logs -f idi-company-info-scheduler

# Orchestrator run logs (timestamped files)
tail -f logs/orchestrator_cik_*.log
tail -f logs/orchestrator_cik_match_*.log
tail -f logs/orchestrator_cusip_*.log
tail -f logs/orchestrator_ticker_*.log
ls -lth logs/

# Output files
ls -lth data/output/company_info/
ls -lth data/output/permid_data/
ls -lth data/output/failures/
```

## License

MIT
