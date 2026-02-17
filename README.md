# IDI Company Information Pipeline

Automated 4-stage pipeline for querying the PermID API to retrieve company information from investor CIK data.

## Pipeline Stages

1. **Extract CIKs** - Extract unique investor names and CIK identifiers from parquet files
2. **Query PermIDs** - Query PermID API to retrieve PermID URLs for each CIK
3. **Retrieve Company Info** - Fetch detailed company information (LEI, addresses, URLs)
4. **Save Results** - Save to PostgreSQL/S3 and archive source file

Features: batch processing, resumable on interruption, rate limiting (1 req/sec), threshold-based re-querying of stale data

## Quick Start

### Installation

```bash
uv pip install -e .              # Production
uv pip install -e ".[dev]"       # Development with tests
```

### API Credentials

Get credentials from:
- **PermID API**: [LSEG/Refinitiv](https://developers.lseg.com/en/api-catalog/open-perm-id/permid-entity-search)
- **Geonames API**: [geonames.org](https://www.geonames.org/login) (free)

### Run Orchestrator (Recommended)

```bash
export PERMID_API_KEY='your-key'
export GEONAMES_USER='your-username'

# CIK mode (Entity Search)
python -m idi_company_info.orchestrator \
  --input-file /path/to/shareholder_tracker.parquet \
  --output-directory output \
  --type cik \
  --permid-api-key $PERMID_API_KEY \
  --geonames-user $GEONAMES_USER \
  --permid-batch-size 5000 \
  --company-info-batch-size 5000 \
  --threshold-days 30

# Record mode (ticker-based Record Match)
# python -m idi_company_info.orchestrator ... --type record

# Optional: add database/S3 storage
# --postgres-connection "postgresql://user:pass@host:5432/db"
# --s3-bucket "my-bucket" --s3-prefix "company-info"
```

### Run Individual Stages (Makefile)

```bash
make install
make pipeline INPUT_PARQUET=data/input.parquet

# Or run stages individually
make stage1 INPUT_PARQUET=data/input.parquet OUTPUT_DIR=output
make stage2 OUTPUT_DIR=output
make stage3 OUTPUT_DIR=output

# See all options
make help
```

## Architecture

**Orchestrator** ([orchestrator.py](src/idi_company_info/orchestrator.py)):
- Single-file processing with automatic execution of all stages
- Retry logic with exponential backoff
- Resumable processing via batch tracking
- Exit codes: 0 (success), 1 (failure)

**Batch Processing**:
- Progress saved to tracking files for resumable processing
- Rate limiting: 1 request/second
- Separate batch sizes for PermID stage and company info stage (default: 5000 each)

**Result Saver** ([save_result.py](src/idi_company_info/save_result.py)):
- PostgreSQL/S3 integration (placeholder implementations with examples)
- File archiving with timestamps
- Dry-run mode for testing

## Individual Stage Commands

If you need to run stages separately:

```bash
# Stage 1: Extract CIKs from parquet
python -m idi_company_info.retrieve_cik \
  --input-file data/input.parquet \
  --output-file output/cik_data.json

# Stage 2: Query PermID by CIK
python -m idi_company_info.query_permid \
  --api-key $PERMID_API_KEY \
  --input-file output/cik_data.json \
  --output-file output/permid_data.json \
  --batch-file output/permid_batch_tracking.json \
  --batch-size 5000

# Stage 3: Retrieve company information
python -m idi_company_info.query_company_info \
  --api-key $PERMID_API_KEY \
  --geonames-user $GEONAMES_USER \
  --input-file output/permid_data.json \
  --output-file output/company_info.json \
  --batch-file output/company_batch_tracking.json \
  --batch-size 5000

# Stage 4: Save and archive
python -m idi_company_info.save_result \
  --input-file output/company_info.json \
  --source-file data/input.parquet \
  --archive-directory archive \
  --dry-run
```

## Testing

```bash
uv pip install -e ".[dev]"
pytest                                    # Run all tests
pytest tests/test_retrieve_cik.py         # Run specific test
pytest --cov=idi_company_info             # With coverage
```

## Docker Deployment (Recommended)

### Setup

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env with your PERMID_API_KEY and GEONAMES_USER

# 2. Create directories and add input file
mkdir -p data/watch data/output data/archive logs
cp /path/to/shareholder_tracker_release.parquet data/watch/

# 3. Start scheduler (runs daily at 2 AM by default)
docker-compose up -d

# 4. Manual run (executes immediately)
docker-compose run --rm orchestrator-cik      # CIK track
docker-compose run --rm orchestrator-record   # Record track
```

See [SCHEDULING.md](SCHEDULING.md) for scheduling configuration.

### Management

```bash
docker-compose logs -f scheduler           # View logs
docker-compose run --rm orchestrator-cik   # Manual CIK track
docker-compose run --rm orchestrator-record  # Manual record track
docker-compose restart scheduler           # Apply config changes
docker-compose up -d --build               # Rebuild after code changes
docker-compose down                        # Stop all services
```

### Configuration

Edit [.env](.env) or [docker-compose.yml](docker-compose.yml) to customize:
- Schedules: `SCHEDULE_CIK` (CIK track, default 2 AM), `SCHEDULE_RECORD` (record track, default 2:30 AM)
- Input file: `INPUT_FILE_PATH=/path/to/your-file.parquet`
- Batch sizes (per run mode): `PERMID_BATCH_SIZE_CIK`, `COMPANY_INFO_BATCH_SIZE_CIK`, `PERMID_BATCH_SIZE_RECORD`, `COMPANY_INFO_BATCH_SIZE_RECORD`
- Threshold: `THRESHOLD_DAYS=30`

**Note:** Changes to `.env` require restarting the scheduler to take effect:
```bash
docker compose restart scheduler
```

For orchestrator services (manual runs), `.env` changes are picked up automatically on each `docker compose run --rm orchestrator-cik` or `docker compose run --rm orchestrator-record` invocation.

## Alternative: systemd Service

For non-Docker deployments, create `/etc/systemd/system/idi-pipeline.service`:

```ini
[Unit]
Description=IDI Company Information Pipeline
After=network.target

[Service]
Type=simple
User=datauser
WorkingDirectory=/opt/idi-company-information
Environment="PERMID_API_KEY=your-key"
Environment="GEONAMES_USER=your-user"
ExecStart=/opt/idi-company-information/.venv/bin/python -m idi_company_info.orchestrator \
  --input-file /data/shareholder_tracker.parquet \
  --output-directory /data/output \
  --archive-directory /data/archive \
  --permid-api-key $PERMID_API_KEY \
  --geonames-user $GEONAMES_USER
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable idi-pipeline
sudo systemctl start idi-pipeline
journalctl -u idi-pipeline -f              # View logs
```

## Monitoring

**Docker:**
```bash
# View scheduler logs
docker logs -f idi-company-info-scheduler

# View orchestrator logs (timestamped files)
tail -f logs/orchestrator_cik_*.log logs/orchestrator_record_*.log
ls -lth logs/                              # List all log files

# Check container health
docker ps

# View output files
ls -lth data/output data/archive
```

**systemd:**
```bash
journalctl -u idi-pipeline -f              # View logs
systemctl status idi-pipeline               # Check status
```

## Database/S3 Integration

See [save_result.py](src/idi_company_info/save_result.py) for placeholder implementations:
- PostgreSQL: Install `psycopg2-binary`, uncomment `save_to_postgres()`
- S3: Install `boto3`, uncomment `upload_to_s3()`

## License

MIT
