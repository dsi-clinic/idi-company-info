.PHONY: help install install-dev test clean clean-all stage1 stage2 stage3 pipeline resume-stage2 resume-stage3

# Configuration variables
# Use .venv Python if it exists, otherwise use system python
PYTHON := $(shell if [ -f .venv/bin/python ]; then echo .venv/bin/python; else echo python; fi)
INPUT_PARQUET ?= data/input.parquet
OUTPUT_DIR ?= output
BATCH_SIZE ?= 5000

# API credentials (set via environment variables)
PERMID_API_KEY ?= $(shell echo $$PERMID_API_KEY)
GEONAMES_USER ?= $(shell echo $$GEONAMES_USER)

# Output files (can be overridden individually)
CIK_DATA ?= $(OUTPUT_DIR)/cik_data.json
PERMID_DATA ?= $(OUTPUT_DIR)/permid_data.json
COMPANY_INFO ?= $(OUTPUT_DIR)/company_info.json
PERMID_BATCH_TRACKING ?= $(OUTPUT_DIR)/permid_batch_tracking.json
COMPANY_BATCH_TRACKING ?= $(OUTPUT_DIR)/company_batch_tracking.json

help:
	@echo "IDI Company Information Pipeline"
	@echo "================================"
	@echo ""
	@echo "Setup targets:"
	@echo "  make install          Install production dependencies"
	@echo "  make install-dev      Install development dependencies (includes tests)"
	@echo ""
	@echo "Pipeline targets:"
	@echo "  make stage1           Extract CIKs from parquet file"
	@echo "  make stage2           Query PermID API by CIK"
	@echo "  make stage3           Retrieve detailed company information"
	@echo "  make pipeline         Run complete pipeline (all 3 stages)"
	@echo ""
	@echo "Test targets:"
	@echo "  make test             Run all tests"
	@echo "  make test-verbose     Run tests with verbose output"
	@echo "  make test-coverage    Run tests with coverage report"
	@echo ""
	@echo "Utility targets:"
	@echo "  make clean            Remove output files"
	@echo "  make clean-all        Remove output files and test artifacts"
	@echo ""
	@echo "Configuration (set via environment variables or make arguments):"
	@echo "  INPUT_PARQUET=$(INPUT_PARQUET)"
	@echo "  OUTPUT_DIR=$(OUTPUT_DIR)"
	@echo "  BATCH_SIZE=$(BATCH_SIZE)"
	@echo "  CIK_DATA=$(CIK_DATA)"
	@echo "  PERMID_DATA=$(PERMID_DATA)"
	@echo "  COMPANY_INFO=$(COMPANY_INFO)"
	@echo "  PERMID_BATCH_TRACKING=$(PERMID_BATCH_TRACKING)"
	@echo "  COMPANY_BATCH_TRACKING=$(COMPANY_BATCH_TRACKING)"
	@echo "  PERMID_API_KEY=$${PERMID_API_KEY:-'<not set>'}"
	@echo "  GEONAMES_USER=$${GEONAMES_USER:-'<not set>'}"
	@echo ""
	@echo "Example usage:"
	@echo "  make install"
	@echo "  export PERMID_API_KEY='your-api-key'"
	@echo "  export GEONAMES_USER='your-username'"
	@echo "  make pipeline INPUT_PARQUET=data/myfile.parquet OUTPUT_DIR=results"
	@echo "  # Or specify individual files:"
	@echo "  make stage2 PERMID_DATA=results/permids.json PERMID_BATCH_TRACKING=results/batch.json"

# Installation targets
install:
	uv pip install -e .

install-dev:
	uv pip install -e ".[dev]"

# Pipeline stage targets
stage1:
	@echo "Running Stage 1: Extract CIKs from parquet"
	@if [ ! -f "$(INPUT_PARQUET)" ]; then \
		echo "Error: Input file not found: $(INPUT_PARQUET)"; \
		exit 1; \
	fi
	@mkdir -p $(OUTPUT_DIR)
	$(PYTHON) -m idi_company_info.retrieve_cik \
		--input-file $(INPUT_PARQUET) \
		--output-file $(CIK_DATA)
	@echo "Stage 1 complete: $(CIK_DATA)"

stage2:
	@echo "Running Stage 2: Query PermID API by CIK"
	@if [ ! -f "$(CIK_DATA)" ]; then \
		echo "Error: CIK data file not found: $(CIK_DATA)"; \
		echo "Run 'make stage1' first to generate it."; \
		exit 1; \
	fi
	@if [ -z "$(PERMID_API_KEY)" ]; then \
		echo "Error: PERMID_API_KEY not set. Set it with: export PERMID_API_KEY='your-key'"; \
		exit 1; \
	fi
	@mkdir -p $(OUTPUT_DIR)
	$(PYTHON) -m idi_company_info.query_permid \
		--api-key $(PERMID_API_KEY) \
		--input-file $(CIK_DATA) \
		--output-file $(PERMID_DATA) \
		--batch-file $(PERMID_BATCH_TRACKING) \
		--batch-size $(BATCH_SIZE)
	@echo "Stage 2 complete: $(PERMID_DATA)"

stage3:
	@echo "Running Stage 3: Retrieve detailed company information"
	@if [ ! -f "$(PERMID_DATA)" ]; then \
		echo "Error: PermID data file not found: $(PERMID_DATA)"; \
		echo "Run 'make stage2' first to generate it."; \
		exit 1; \
	fi
	@if [ -z "$(PERMID_API_KEY)" ]; then \
		echo "Error: PERMID_API_KEY not set. Set it with: export PERMID_API_KEY='your-key'"; \
		exit 1; \
	fi
	@if [ -z "$(GEONAMES_USER)" ]; then \
		echo "Error: GEONAMES_USER not set. Set it with: export GEONAMES_USER='your-username'"; \
		exit 1; \
	fi
	@mkdir -p $(OUTPUT_DIR)
	$(PYTHON) -m idi_company_info.query_company_info \
		--api-key $(PERMID_API_KEY) \
		--geonames-user $(GEONAMES_USER) \
		--input-file $(PERMID_DATA) \
		--output-file $(COMPANY_INFO) \
		--batch-file $(COMPANY_BATCH_TRACKING) \
		--batch-size $(BATCH_SIZE)
	@echo "Stage 3 complete: $(COMPANY_INFO)"

# Run complete pipeline
pipeline: stage1 stage2 stage3
	@echo ""
	@echo "=========================================="
	@echo "Pipeline complete!"
	@echo "=========================================="
	@echo "Output files:"
	@echo "  CIK data:        $(CIK_DATA)"
	@echo "  PermID data:     $(PERMID_DATA)"
	@echo "  Company info:    $(COMPANY_INFO)"
	@echo "=========================================="

# Test targets
test:
	pytest

test-verbose:
	pytest -vv

test-coverage:
	pytest --cov=idi_company_info --cov-report=html --cov-report=term
	@echo ""
	@echo "Coverage report generated in htmlcov/index.html"

# Clean targets
clean:
	@echo "Removing output files..."
	rm -f $(CIK_DATA) $(PERMID_DATA) $(COMPANY_INFO)
	rm -f $(PERMID_BATCH_TRACKING) $(COMPANY_BATCH_TRACKING)
	@echo "Output files removed"

clean-all: clean
	@echo "Removing test artifacts..."
	rm -rf .pytest_cache
	rm -rf htmlcov
	rm -rf .coverage
	rm -rf __pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	@echo "Clean complete"

# Resume targets for interrupted runs
resume-stage2:
	@echo "Resuming Stage 2: Query PermID API by CIK"
	@if [ -z "$(PERMID_API_KEY)" ]; then \
		echo "Error: PERMID_API_KEY not set. Set it with: export PERMID_API_KEY='your-key'"; \
		exit 1; \
	fi
	$(PYTHON) -m idi_company_info.query_permid \
		--api-key $(PERMID_API_KEY) \
		--input-file $(CIK_DATA) \
		--output-file $(PERMID_DATA) \
		--batch-file $(PERMID_BATCH_TRACKING) \
		--batch-size $(BATCH_SIZE)

resume-stage3:
	@echo "Resuming Stage 3: Retrieve detailed company information"
	@if [ -z "$(PERMID_API_KEY)" ]; then \
		echo "Error: PERMID_API_KEY not set. Set it with: export PERMID_API_KEY='your-key'"; \
		exit 1; \
	fi
	@if [ -z "$(GEONAMES_USER)" ]; then \
		echo "Error: GEONAMES_USER not set. Set it with: export GEONAMES_USER='your-username'"; \
		exit 1; \
	fi
	$(PYTHON) -m idi_company_info.query_company_info \
		--api-key $(PERMID_API_KEY) \
		--geonames-user $(GEONAMES_USER) \
		--input-file $(PERMID_DATA) \
		--output-file $(COMPANY_INFO) \
		--batch-file $(COMPANY_BATCH_TRACKING) \
		--batch-size $(BATCH_SIZE)
