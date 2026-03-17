#!/usr/bin/env python3
"""Pipeline Orchestrator - Runs the identifier processing pipeline for a specified input file.

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
from dataclasses import asdict
from datetime import datetime

from idi_company_info.common.logs import get_logger
from idi_company_info.processors.factory import IdentifierFactory
from idi_company_info.processors.types import IdentifierType, OrchestratorConfig


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

    def _log_config(self) -> None:
        config_safe = {}
        for k, v in asdict(self.config).items():
            if k in ("api_key", "geonames_user"):
                config_safe[k] = "***"
            else:
                config_safe[k] = str(v) if isinstance(v, pathlib.Path) else v
        for k, v in config_safe.items():
            self.logger.info(f"{k}: {v}")

    def run(self) -> bool:
        """Execute the identifier pipeline.

        Returns:
            True if processing completed successfully, False otherwise.
        """
        input_str = str(self.config.input_file)
        if not input_str.startswith("s3://") and not pathlib.Path(input_str).exists():
            self.logger.error("Input file does not exist: %s", input_str)
            return False

        input_display = input_str.split("/")[-1] if "/" in input_str else input_str
        self._log_banner(
            f"Starting pipeline | type={self.config.identifier_type} | input={input_display}"
        )
        self._log_config()

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
        type=str,
        required=True,
        help="Path to input parquet file (local or s3:// URL)",
    )
    parser.add_argument(
        "--output-directory",
        type=str,
        required=True,
        help="Root directory for output files (local path or s3:// URL)",
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
