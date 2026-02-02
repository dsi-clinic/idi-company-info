# IDI Company Information

A three-stage pipeline for querying the PermID API to retrieve detailed company information from investor CIK data.

## Description

This tool processes institutional investor data through three sequential stages:

1. **Extract CIKs** - Extracts unique investor names and CIK identifiers from parquet files
2. **Query PermIDs** - Queries the PermID API to retrieve PermID URLs for each CIK
3. **Retrieve Company Info** - Fetches detailed company information (LEI, addresses, URLs, etc.) for each PermID

## Setup

```bash
# Install dependencies
uv pip install -e .

# For development with tests
uv pip install -e ".[dev]"
```

## Quick Start (Using Makefile)

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

## Batch Processing

Stages 1 and 2 support batch processing with automatic tracking:
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

## License

MIT License
