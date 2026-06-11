.PHONY: help install install-dev test test-verbose test-coverage clean clean-all
.PHONY: run-shareholder-cik run-cdt run-subsidiaries
.PHONY: docker-build docker-up docker-down docker-run-cik docker-run-cdt docker-run-subsidiaries

# ── Python runner ────────────────────────────────────────────────────────────
RUN := uv run

# ── Local run defaults (override on the command line) ────────────────────────
INPUT_PARQUET  ?= data/input/data.parquet
OUTPUT_DIR     ?= data/output
BATCH_SIZE     ?= 2450
BUFFER_SIZE    ?= 500
THRESHOLD_DAYS ?=
MATCH_SCORE    ?= 1

# ── API credentials (resolved from env) ──────────────────────────────────────
PERMID_API_KEY ?= $(shell echo $$PERMID_API_KEY)
GEONAMES_USER  ?= $(shell echo $$GEONAMES_USER)

# ── Docker variables ──────────────────────────────────────────────────────────
LOG_DIR        ?= ./logs

# ── Helpers ───────────────────────────────────────────────────────────────────
define check-creds
	@if [ -z "$(PERMID_API_KEY)" ]; then \
		echo "Error: PERMID_API_KEY not set. Run: export PERMID_API_KEY='your-key'"; \
		exit 1; \
	fi
	@if [ -z "$(GEONAMES_USER)" ]; then \
		echo "Error: GEONAMES_USER not set. Run: export GEONAMES_USER='your-username'"; \
		exit 1; \
	fi
endef

define check-input
	@if [ ! -e "$(INPUT_PARQUET)" ]; then \
		echo "Error: input not found: $(INPUT_PARQUET)"; \
		echo "Set INPUT_PARQUET=path/to/file.parquet (or a shard directory for CDT)"; \
		exit 1; \
	fi
endef

# ── Help ──────────────────────────────────────────────────────────────────────
help:
	@echo "IDI Company Information Pipeline"
	@echo "================================="
	@echo ""
	@echo "Setup:"
	@echo "  make install          Install production dependencies"
	@echo "  make install-dev      Install dev dependencies (includes tests)"
	@echo ""
	@echo "Local pipeline runs (each writes to OUTPUT_DIR/<input-source>/):"
	@echo "  make run-shareholder-cik  Shareholder CIK  (input: investor_name + investor_cik)"
	@echo "  make run-cdt           Commercial Debt     (input: shard DIRECTORY of company_name + cik)"
	@echo "  make run-subsidiaries  Corporate Subsidiaries (input: parent_name + parent_cik)"
	@echo ""
	@echo "  Common options (pass as make args):"
	@echo "    INPUT_PARQUET=path/to/file.parquet  (default: $(INPUT_PARQUET))"
	@echo "    OUTPUT_DIR=data/output              (default: $(OUTPUT_DIR))"
	@echo "    BATCH_SIZE=2450                     (default: $(BATCH_SIZE))"
	@echo "    BUFFER_SIZE=500                     (default: $(BUFFER_SIZE))"
	@echo "    THRESHOLD_DAYS=30                   (default: unset)"
	@echo "    MATCH_SCORE=1                       (default: $(MATCH_SCORE), 1=100% match required)"
	@echo ""
	@echo "  Credentials (env vars or make args):"
	@echo "    PERMID_API_KEY=$${PERMID_API_KEY:-<not set>}"
	@echo "    GEONAMES_USER=$${GEONAMES_USER:-<not set>}"
	@echo ""
	@echo "Tests:"
	@echo "  make test             Run all tests"
	@echo "  make test-verbose     Verbose output"
	@echo "  make test-coverage    HTML + terminal coverage report"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-build     Build orchestrator image"
	@echo "  make docker-up        Start scheduler stack (runs on schedule)"
	@echo "  make docker-down      Stop Docker Compose stack"
	@echo "  make docker-run-cik           Manual Shareholder CIK run via Docker"
	@echo "  make docker-run-cdt           Manual Commercial Debt run via Docker"
	@echo "  make docker-run-subsidiaries  Manual Corporate Subsidiaries run via Docker"
	@echo ""
	@echo "Utility:"
	@echo "  make clean            Remove output and log files"
	@echo "  make clean-all        Remove output, logs, and test artifacts"
	@echo ""
	@echo "Example:"
	@echo "  export PERMID_API_KEY='your-api-key'"
	@echo "  export GEONAMES_USER='your-username'"
	@echo "  make run-shareholder-cik INPUT_PARQUET=data/input/investors.parquet OUTPUT_DIR=data/output"

# ── Installation ──────────────────────────────────────────────────────────────
install:
	uv pip install -e .

install-dev:
	uv pip install -e ".[dev]"

# ── Local pipeline runs ───────────────────────────────────────────────────────
_orchestrator_args = \
	--input-file $(INPUT_PARQUET) \
	--output-directory $(OUTPUT_DIR) \
	--permid-api-key $(PERMID_API_KEY) \
	--geonames-user $(GEONAMES_USER) \
	--batch-size $(BATCH_SIZE) \
	--buffer-size $(BUFFER_SIZE) \
	--match-score-threshold $(MATCH_SCORE) \
	$(if $(THRESHOLD_DAYS),--threshold-days $(THRESHOLD_DAYS),)

run-shareholder-cik:
	$(call check-creds)
	$(call check-input)
	@mkdir -p $(OUTPUT_DIR)
	$(RUN) -m idi_company_info.orchestrator \
		$(_orchestrator_args) \
		--input-type shareholder_tracker_cik

run-cdt:
	$(call check-creds)
	$(call check-input)
	@mkdir -p $(OUTPUT_DIR)
	$(RUN) -m idi_company_info.orchestrator \
		$(_orchestrator_args) \
		--input-type commercial_debt_tracker

run-subsidiaries:
	$(call check-creds)
	$(call check-input)
	@mkdir -p $(OUTPUT_DIR)
	$(RUN) -m idi_company_info.orchestrator \
		$(_orchestrator_args) \
		--input-type corporate_subsidiaries

# ── Tests ─────────────────────────────────────────────────────────────────────
test:
	$(RUN) pytest

test-verbose:
	$(RUN) pytest -vv

test-coverage:
	$(RUN) pytest --cov=idi_company_info --cov-report=html --cov-report=term
	@echo ""
	@echo "Coverage report: htmlcov/index.html"

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	@echo "Removing output and log files..."
	rm -rf $(OUTPUT_DIR)/shareholder_tracker_cik \
		$(OUTPUT_DIR)/commercial_debt_tracker \
		$(OUTPUT_DIR)/corporate_subsidiaries
	rm -f $(LOG_DIR)/orchestrator_*.log
	@echo "Done"

clean-all: clean
	@echo "Removing test artifacts..."
	rm -rf .pytest_cache htmlcov .coverage
	find . -type d -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -not -path "./.venv/*" -delete
	@echo "Clean complete"

# ── Docker ────────────────────────────────────────────────────────────────────
docker-build:
	@echo "Building orchestrator image..."
	docker compose build orchestrator-cik

docker-up:
	@echo "Starting scheduler stack..."
	docker compose up -d

docker-down:
	@echo "Stopping Docker Compose stack..."
	docker compose down

docker-run-cik:
	@echo "Running Shareholder CIK pipeline via Docker..."
	docker compose run --rm orchestrator-cik

docker-run-cdt:
	@echo "Running Commercial Debt pipeline via Docker..."
	docker compose run --rm orchestrator-cdt

docker-run-subsidiaries:
	@echo "Running Corporate Subsidiaries pipeline via Docker..."
	docker compose run --rm orchestrator-subsidiaries
