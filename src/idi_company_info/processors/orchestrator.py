#!/usr/bin/env python3
"""
Pipeline Orchestrator - Runs the identifier processing pipeline for a specified input file.

Supports three identifier types:
  cik    — CIK-based Entity Search (IdentifierCik)
  cusip  — CUSIP-based Entity Search (IdentifierCusip)
  ticker — Ticker-based Record Match (IdentifierCusip in RECORD_MATCH mode)

To add a new identifier type, register it in IDENTIFIER_REGISTRY.
"""

import argparse
import os
import pathlib
import sys
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, StrEnum

from idi_company_info.common.logs import get_logger
from idi_company_info.processors.identifier import (
    ApiCredentials,
    BatchConfig,
    FilePaths,
    Identifier,
    QueryType,
)
from idi_company_info.processors.IdentifierCik import IdentifierCik
from idi_company_info.processors.IdentifierCusip import IdentifierCusip

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class StageStatus(Enum):
    """Execution status for pipeline stages."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Identifier type registry
# ---------------------------------------------------------------------------

class IdentifierType(StrEnum):
    """Supported identifier types."""
    CIK = "cik"
    CIK_MATCH = "cik-match"
    CUSIP = "cusip"
    TICKER = "ticker"


@dataclass
class IdentifierSpec:
    """Specification for a single identifier type.

    Adding a new type requires only a new entry in IDENTIFIER_REGISTRY —
    no other code needs to change.
    """
    cls: type[Identifier]
    query_type: QueryType
    permid_filename: str
    result_filename: str
    failure_filename: str


IDENTIFIER_REGISTRY: dict[IdentifierType, IdentifierSpec] = {
    IdentifierType.CIK: IdentifierSpec(
        cls=IdentifierCik,
        query_type=QueryType.ENTITY_SEARCH,
        permid_filename="permid_tracking_cik.json",
        result_filename="company_info_cik.json",
        failure_filename="failures_cik.json",
    ),
    IdentifierType.CUSIP: IdentifierSpec(
        cls=IdentifierCusip,
        query_type=QueryType.ENTITY_SEARCH,
        permid_filename="permid_tracking_cusip.json",
        result_filename="company_info_cusip.json",
        failure_filename="failures_cusip.json",
    ),
    IdentifierType.CIK_MATCH: IdentifierSpec(
        cls=IdentifierCik,
        query_type=QueryType.RECORD_MATCH,
        permid_filename="permid_tracking_cik_match.json",
        result_filename="company_info_cik_match.json",
        failure_filename="failures_cik_match.json",
    ),
    IdentifierType.TICKER: IdentifierSpec(
        cls=IdentifierCusip,
        query_type=QueryType.RECORD_MATCH,
        permid_filename="permid_tracking_ticker.json",
        result_filename="company_info_ticker.json",
        failure_filename="failures_ticker.json",
    ),
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class OrchestratorConfig:
    """Configuration for a single orchestrator run."""
    input_file: pathlib.Path
    output_dir: pathlib.Path
    identifier_type: IdentifierType
    api_key: str
    geonames_user: str
    batch_size: int = 2450
    buffer_size: int = 500
    threshold_days: int | None = None
    match_score_threshold: int = 1


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class IdentifierFactory:
    """Builds a configured Identifier instance from an OrchestratorConfig.

    Single responsibility: translate orchestrator-level config into the
    dataclasses expected by the Identifier base class, then instantiate the
    correct subclass.
    """

    @staticmethod
    def build(config: OrchestratorConfig) -> Identifier:
        """Build and return the appropriate Identifier for the given config.

        Args:
            config: Orchestrator configuration.

        Returns:
            A fully configured Identifier subclass instance.

        Raises:
            KeyError: If config.identifier_type is not in IDENTIFIER_REGISTRY.
        """
        spec = IDENTIFIER_REGISTRY[config.identifier_type]

        file_paths = FilePaths(
            input_file=str(config.input_file),
            result_file=str(
                config.output_dir / "company_info" / spec.result_filename
            ),
            permid_file=str(
                config.output_dir / "permid_data" / spec.permid_filename
            ),
            failure_file=str(
                config.output_dir / "failures" / spec.failure_filename
            ),
        )

        batch_config = BatchConfig(
            batch_size=config.batch_size,
            buffer_size=config.buffer_size,
            threshold_days=config.threshold_days,
        )

        api_credentials = ApiCredentials(
            api_key=config.api_key,
            geonames_user=config.geonames_user,
        )

        return spec.cls(
            file_paths=file_paths,
            batch_config=batch_config,
            api_credentials=api_credentials,
            query_type=spec.query_type,
            match_score_threshold=config.match_score_threshold,
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class PipelineOrchestrator:
    """Orchestrates identifier processing for a single input file.

    Delegates all domain logic to the appropriate Identifier subclass.
    Responsibilities limited to: validation, startup/shutdown logging, error
    containment, and exit-code reporting.
    """

    def __init__(self, config: OrchestratorConfig) -> None:
        """Initialize the orchestrator.

        Args:
            config: Orchestrator configuration.
        """
        self.config = config
        self.logger = get_logger("PipelineOrchestrator")

    def _log_banner(self, message: str) -> None:
        self.logger.info("=" * 60)
        self.logger.info(message)
        self.logger.info("=" * 60)

    def run(self) -> bool:
        """Execute the identifier pipeline.

        Returns:
            True if processing completed successfully, False otherwise.
        """
        if not self.config.input_file.exists():
            self.logger.error("Input file does not exist: %s", self.config.input_file)
            return False

        self._log_banner(
            f"Starting pipeline | type={self.config.identifier_type} | "
            f"input={self.config.input_file.name}"
        )
        self.logger.info("Output directory:       %s", self.config.output_dir)
        self.logger.info("Batch size:             %d", self.config.batch_size)
        self.logger.info("Threshold days:         %s", self.config.threshold_days)
        self.logger.info("Match score threshold:  %d", self.config.match_score_threshold)

        start_time = datetime.now()

        try:
            identifier = IdentifierFactory.build(self.config)
            identifier.run()

        except KeyboardInterrupt:
            self.logger.info("Pipeline interrupted by user")
            return False

        except Exception:
            self.logger.exception("Pipeline failed with an unexpected error")
            return False

        elapsed = datetime.now() - start_time
        self._log_banner(f"Pipeline completed successfully in {elapsed}")
        return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def get_args() -> argparse.Namespace:
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Run the identifier processing pipeline for a parquet input file. "
            "Supports cik, cusip, and ticker identifier types."
        )
    )

    parser.add_argument(
        "--input-file",
        type=pathlib.Path,
        required=True,
        help="Path to input parquet file",
    )
    parser.add_argument(
        "--output-directory",
        type=pathlib.Path,
        required=True,
        help="Root directory for output files",
    )
    parser.add_argument(
        "--type",
        type=IdentifierType,
        choices=list(IdentifierType),
        required=True,
        help="Identifier type: cik (CIK Entity Search), cik-match (CIK Record Match), cusip (CUSIP Entity Search), ticker (Ticker Record Match)",
    )
    parser.add_argument(
        "--permid-api-key",
        type=str,
        default=None,
        help="LSEG PermID API access token (or set PERMID_API_KEY env var)",
    )
    parser.add_argument(
        "--geonames-user",
        type=str,
        default=None,
        help="Geonames API username (or set GEONAMES_USER env var)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2450,
        help="Number of entities to process per batch (default: 2450)",
    )
    parser.add_argument(
        "--buffer-size",
        type=int,
        default=500,
        help="Write-buffer size before flushing to disk (default: 500)",
    )
    parser.add_argument(
        "--threshold-days",
        type=int,
        default=None,
        help="Re-process records not updated in the last N days (default: None)",
    )
    parser.add_argument(
        "--match-score-threshold",
        type=int,
        default=1,
        help="Minimum Record Match score to accept (default: 1 = 100%%)",
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = get_args()

    # Resolve credentials: CLI flag takes priority, then environment variable.
    api_key = args.permid_api_key or os.environ.get("PERMID_API_KEY")
    geonames_user = args.geonames_user or os.environ.get("GEONAMES_USER")

    missing = []
    if not api_key:
        missing.append("--permid-api-key / PERMID_API_KEY")
    if not geonames_user:
        missing.append("--geonames-user / GEONAMES_USER")
    if missing:
        print(f"Error: missing required credentials: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

    config = OrchestratorConfig(
        input_file=args.input_file,
        output_dir=args.output_directory,
        identifier_type=args.type,
        api_key=api_key,
        geonames_user=geonames_user,
        batch_size=args.batch_size,
        buffer_size=args.buffer_size,
        threshold_days=args.threshold_days,
        match_score_threshold=args.match_score_threshold,
    )

    orchestrator = PipelineOrchestrator(config)

    try:
        success = orchestrator.run()
    except KeyboardInterrupt:
        sys.exit(130)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
