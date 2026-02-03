# IDI Company Information

A four-stage automated pipeline for querying the PermID API to retrieve detailed company information from investor CIK data.

## Description

This tool processes institutional investor data through four sequential stages:

1. **Extract CIKs** - Extracts unique investor names and CIK identifiers from parquet files
2. **Query PermIDs** - Queries the PermID API to retrieve PermID URLs for each CIK
3. **Retrieve Company Info** - Fetches detailed company information (LEI, addresses, URLs, etc.) for each PermID
4. **Save Results** - Saves data to PostgreSQL/S3 and archives the source file

## Architecture

The pipeline can be run in two modes:

- **Orchestrated Mode** (Recommended): Continuously watches for new files and automatically processes them through all stages
- **Manual Mode**: Run individual stages via Makefile or direct commands for debugging or custom workflows

## Setup

```bash
# Install dependencies
uv pip install -e .

# For development with tests
uv pip install -e ".[dev]"
```

## Quick Start

### Orchestrated Mode (Recommended for Production)

Run the orchestrator to automatically watch for and process new files:

```bash
# Set API credentials
export PERMID_API_KEY='your-permid-api-key'
export GEONAMES_USER='your-geonames-username'

# Run orchestrator (watches for files continuously)
python -m idi_company_info.orchestrator \
  --watch-directory /path/to/data/shareholder_tracker \
  --output-directory output \
  --archive-directory archive \
  --permid-api-key $PERMID_API_KEY \
  --geonames-user $GEONAMES_USER \
  --batch-size 5000 \
  --poll-interval 30
```

When a new `shareholder_tracker_*.parquet` file appears in the watch directory, the orchestrator will:
1. Process all four stages automatically
2. Save results to database/S3 (if configured)
3. Archive the source file with a timestamp
4. Wait for the next file

**Optional database/storage arguments:**
```bash
--postgres-connection "postgresql://user:pass@host:5432/dbname"
--s3-bucket "my-company-data-bucket"
--s3-prefix "company-info/production"
```

### Manual Mode (Using Makefile)

```bash
# Install dependencies
make install

# Set API credentials
export PERMID_API_KEY='your-permid-api-key'
export GEONAMES_USER='your-geonames-username'

# Run complete pipeline
make pipeline INPUT_PARQUET=data/input.parquet

# Or run individual stages
make stage1 INPUT_PARQUET=data/input.parquet
make stage2 OUTPUT_DIR=data/results.json
make stage3 OUTPUT_DIR=data/results.json
```

Run `make help` to see all available targets.

## Makefile Configuration

The Makefile supports flexible configuration through variables. All variables can be:
- Set via command-line arguments: `make stage1 OUTPUT_DIR=results`
- Set as environment variables: `export OUTPUT_DIR=results`
- Left as defaults

### Available Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `INPUT_PARQUET` | `data/input.parquet` | Input parquet file path |
| `OUTPUT_DIR` | `output` | Directory for all output files |
| `BATCH_SIZE` | `5000` | Number of items to process per batch |
| `CIK_DATA` | `$(OUTPUT_DIR)/cik_data.json` | Stage 1 output file |
| `PERMID_DATA` | `$(OUTPUT_DIR)/permid_data.json` | Stage 2 output file |
| `COMPANY_INFO` | `$(OUTPUT_DIR)/company_info.json` | Stage 3 output file |
| `PERMID_BATCH_TRACKING` | `$(OUTPUT_DIR)/permid_batch_tracking.json` | Stage 2 batch tracking |
| `COMPANY_BATCH_TRACKING` | `$(OUTPUT_DIR)/company_batch_tracking.json` | Stage 3 batch tracking |
| `PERMID_API_KEY` | *(from env)* | PermID API access token |
| `GEONAMES_USER` | *(from env)* | Geonames API username |

### Configuration Examples

**Simple: Use OUTPUT_DIR**
```bash
# All files go to the same directory
make pipeline \
  INPUT_PARQUET=/path/to/data.parquet \
  OUTPUT_DIR=results \
  BATCH_SIZE=1000
```

**Advanced: Individual File Control**
```bash
# Stage 2 with custom paths
make stage2 \
  CIK_DATA=output/cik_data.json \
  PERMID_DATA=results/my_permids.json \
  PERMID_BATCH_TRACKING=results/batch_tracking.json \
  BATCH_SIZE=500

# Stage 3 with custom paths
make stage3 \
  PERMID_DATA=results/my_permids.json \
  COMPANY_INFO=results/companies.json \
  COMPANY_BATCH_TRACKING=results/company_batch.json \
  BATCH_SIZE=500
```

**Environment Variables**
```bash
# Set once, use for all commands
export OUTPUT_DIR=results
export BATCH_SIZE=1000
export PERMID_API_KEY='your-api-key'
export GEONAMES_USER='your-username'

make stage1 INPUT_PARQUET=/path/to/data.parquet
make stage2
make stage3
```

### Viewing Current Configuration

```bash
make help
```

This displays all current variable values including paths and API credentials (credentials show as "set" or "not set").

## Architecture Details

### Orchestrator

The orchestrator ([orchestrator.py](src/idi_company_info/orchestrator.py)) provides:
- **File watching**: Polls directory for new parquet files
- **Automatic execution**: Runs all stages sequentially
- **Retry logic**: Configurable retries per stage with exponential backoff
- **Error handling**: Centralized logging and error reporting
- **Resumable processing**: Uses batch tracking to resume interrupted runs

### Result Saver

The result saver ([save_result.py](src/idi_company_info/save_result.py)) provides:
- **Database integration**: PostgreSQL insertion (placeholder - ready for implementation)
- **Cloud storage**: S3 upload (placeholder - ready for implementation)
- **File archiving**: Moves processed files to archive directory with timestamps
- **Dry-run mode**: Test without side effects

### Batch Tracking

Both query stages maintain batch tracking files to enable:
- Resuming interrupted processing
- Processing large datasets in chunks
- Monitoring progress across multiple runs
- Avoiding duplicate API calls

## Usage (Direct Commands)

### Stage 1: Extract CIKs from Parquet

Reads a parquet file and extracts unique investor name/CIK pairs.

```bash
python -m idi_company_info.retrieve_cik \
  --input-file data/input.parquet \
  --output-file output/cik_data.json
```

**Arguments:**
- `--input-file`: Path to input parquet file (must contain `investor_name` and `investor_cik` columns)
- `--output-file`: Path to save JSON output

**Output:** JSON file with investor names as keys and lists of CIKs as values
```json
{
  "Company A": ["0001234567", "0001234568"],
  "Company B": ["0001234569"]
}
```

### Stage 2: Query PermID by CIK

Queries the PermID API to retrieve PermID URLs for each CIK. Supports batch processing with tracking.

```bash
python -m idi_company_info.query_permid \
  --api-key YOUR_PERMID_API_KEY \
  --input-file output/cik_data.json \
  --output-file output/permid_data.json \
  --batch-file output/permid_batch_tracking.json \
  --batch-size 5000
```

**Arguments:**
- `--api-key`: PermID API access token (required)
- `--input-file`: Path to CIK data JSON from Stage 1
- `--output-file`: Path to save PermID results
- `--batch-file`: Path to batch tracking file (for resumable processing)
- `--batch-size`: Number of investors to process per batch (default: 5000)

**Output:** JSON file with investor names as keys and lists of PermID URLs as values
```json
{
  "Company A": ["https://permid.org/1-5000051854"],
  "Company B": ["https://permid.org/1-5000051855"]
}
```

### Stage 3: Retrieve Detailed Company Information

Queries the PermID API to retrieve detailed company information for each PermID. Resolves location fields using Geonames API.

```bash
python -m idi_company_info.query_company_info \
  --api-key YOUR_PERMID_API_KEY \
  --geonames-user YOUR_GEONAMES_USERNAME \
  --input-file output/permid_data.json \
  --output-file output/company_info.json \
  --batch-file output/company_batch_tracking.json \
  --batch-size 5000
```

**Arguments:**
- `--api-key`: PermID API access token (required)
- `--geonames-user`: Geonames API username (required)
- `--input-file`: Path to PermID data JSON from Stage 2
- `--output-file`: Path to save company information results
- `--batch-file`: Path to batch tracking file (for resumable processing)
- `--batch-size`: Number of investors to process per batch (default: 5000)

**Output:** JSON file with array of company information objects
```json
[
  {
    "investor_name": "Company A",
    "permid": "1-5000051854",
    "lei": "ABC123DEF456",
    "hq_address": "123 Main St",
    "incorporated_in": "United States",
    "domiciled_in": "Delaware",
    "url": "https://example.com",
    "original_investor_name": "Company A"
  }
]
```

### Stage 4: Save Results and Archive

Saves pipeline results to database/storage and archives the source file.

```bash
python -m idi_company_info.save_result \
  --input-file output/company_info.json \
  --source-file data/shareholder_tracker_release_20251218.parquet \
  --archive-directory archive \
  --postgres-connection "postgresql://user:pass@localhost:5432/companydb" \
  --s3-bucket my-data-bucket \
  --s3-prefix company-info
```

**Arguments:**
- `--input-file`: Path to company info JSON from Stage 3
- `--source-file`: Path to original parquet file to archive
- `--archive-directory`: Directory to move archived files to
- `--postgres-connection`: PostgreSQL connection string (optional)
- `--s3-bucket`: S3 bucket name (optional)
- `--s3-prefix`: S3 key prefix (optional, default: company-info)
- `--dry-run`: Log actions without executing them

**Output:**
- Data saved to PostgreSQL (placeholder - ready for implementation)
- Data uploaded to S3 (placeholder - ready for implementation)
- Source file moved to: `archive/shareholder_tracker_release_20251218_20251203_142530.parquet`

### Testing Save Result (Dry Run)

```bash
python -m idi_company_info.save_result \
  --input-file output/company_info.json \
  --source-file data/shareholder_tracker_release_20251218.parquet \
  --archive-directory archive \
  --dry-run
```

## Batch Processing

Stages 2 and 3 support batch processing with automatic tracking:
- Progress is saved to batch tracking files
- Interrupted runs can be resumed by re-running the same command
- Rate limiting: 1 request per second (configurable in source)

## Running Tests

```bash
# Install dependencies
uv pip install -e ".[dev]"

# Run all tests
pytest

# Run specific test file
pytest tests/test_retrieve_cik.py

# Run with coverage
pytest --cov=idi_company_info --cov-report=html
```

## API Requirements

- **PermID API**: Requires API key from [LSEG/Refinitiv](https://developers.lseg.com/en/api-catalog/open-perm-id/permid-entity-search)
- **Geonames API**: Requires free username from [geonames.org](https://www.geonames.org/login)

## Production Deployment

### Running as a Service

Use a process manager like systemd or supervisor to run the orchestrator as a background service:

**systemd example** (`/etc/systemd/system/idi-pipeline.service`):
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
  --watch-directory /data/shareholder_tracker \
  --output-directory /data/output \
  --archive-directory /data/archive \
  --permid-api-key $PERMID_API_KEY \
  --geonames-user $GEONAMES_USER \
  --postgres-connection "postgresql://user:pass@localhost:5432/db" \
  --s3-bucket company-data
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable idi-pipeline
sudo systemctl start idi-pipeline
sudo systemctl status idi-pipeline
```

### Monitoring

Monitor the orchestrator with:
```bash
# View logs
journalctl -u idi-pipeline -f

# Check status
systemctl status idi-pipeline

# View output files
ls -lh /data/output/
ls -lh /data/archive/
```

### Implementing Database/S3 Storage

The [save_result.py](src/idi_company_info/save_result.py) file contains placeholder implementations with detailed comments showing how to implement:

1. **PostgreSQL Integration**: See `save_to_postgres()` method
   - Install: `pip install psycopg2-binary`
   - Uncomment and customize the example code

2. **S3 Integration**: See `upload_to_s3()` method
   - Install: `pip install boto3`
   - Uncomment and customize the example code

## License

MIT License
