#!/usr/bin/env python3
"""Pipeline Orchestrator - Runs the company-info pipeline for a specified input source.

The input source (see InputSource) selects the Input loader and identifier type:
  shareholder_tracker_cik    — shareholder CIK Record Match
  shareholder_tracker_cusip  — shareholder CUSIP (ticker) Record Match
  commercial_debt_tracker    — CDT debt instruments parquet (CIK)
  corporate_subsidiaries     — subsidiary parent CIKs

To add a new input source, register it in INPUT_REGISTRY.
"""

# Standard library imports
import argparse
import os
import pathlib
import sys
from dataclasses import asdict
from datetime import datetime

# Third party imports
from idi_ftm2j_shared.logs import get_logger

# Application imports
from idi_company_info.factory import PipelineFactory
from idi_company_info.output import Output
from idi_company_info.types import InputSource, OrchestratorConfig


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
        self.logger = get_logger(type(self).__name__)

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
            f"Starting pipeline | type={self.config.input_type} | input={input_display}"
        )
        self._log_config()

        start_time = datetime.now()

        try:
            pipeline = PipelineFactory.build(self.config)
            pipeline.run()

        except KeyboardInterrupt:
            self.logger.info("Pipeline interrupted by user")
            return False

        except Exception:
            self.logger.exception("Pipeline failed with an unexpected error")
            return False

        # Regenerate the combined final parquet from every processor's caches
        if not self.config.skip_final_output:
            try:
                Output(
                    output_dir=self.config.output_dir,
                    final_output_file=self.config.final_output_file,
                ).aggregate()
            except Exception:
                self.logger.exception(
                    "Final output aggregation failed (pipeline results are saved)"
                )  # failure here is logged, do not trigger an ECS retry that re-burns API quota

        elapsed = datetime.now() - start_time
        self._log_banner(f"Pipeline completed successfully in {elapsed}")
        return True


def get_args() -> argparse.Namespace:
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Run the company-info pipeline for one input source. "
            "Use --input-type to select the source (see InputSource)."
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
        help="Directory for the company info result and PermID tracking files (local path or s3:// URL)",
    )
    parser.add_argument(
        "--input-type",
        type=InputSource,
        choices=list(InputSource),
        required=True,
        help="Input source to process (selects the Input loader and identifier type)",
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
        help="Max NEW identifiers resolved to PermIDs per run, i.e. intake cap (default: 2450)",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=1650,
        help=(
            "Total PermID-request budget per run against the shared daily quota (Record "
            "Match + entity-lookup + follow-up calls); the company-info stage stops "
            "starting new companies once this many requests are made "
            "(default: 1650, which sizes a single ad-hoc run; scheduled runs are capped "
            "lower so that max_requests * enabled sources stays under the 5,000/day "
            "quota — see API Quota Budgeting in the README)"
        ),
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
    parser.add_argument(
        "--final-output-file",
        type=str,
        default=None,
        help="Aggregated parquet path (default: <output-directory>/latest.parquet)",
    )
    parser.add_argument(
        "--skip-final-output",
        action="store_true",
        help="Skip aggregating the combined final parquet after the pipeline run",
    )
    parser.add_argument(
        "--no-enrich-metadata",
        dest="enrich_metadata",
        action="store_false",
        help="Skip sector and ticker/exchange follow-up lookups (1 API call per company)",
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
        input_type=args.input_type,
        api_key=api_key,
        geonames_user=geonames_user,
        batch_size=args.batch_size,
        max_requests=args.max_requests,
        buffer_size=args.buffer_size,
        threshold_days=args.threshold_days,
        match_score_threshold=args.match_score_threshold,
        final_output_file=args.final_output_file,
        skip_final_output=args.skip_final_output,
        enrich_metadata=args.enrich_metadata,
    )

    orchestrator = PipelineOrchestrator(config)

    try:
        success = orchestrator.run()
    except KeyboardInterrupt:
        sys.exit(130)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
