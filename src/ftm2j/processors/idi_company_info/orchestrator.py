#!/usr/bin/env python3
"""
Pipeline Orchestrator - Runs the complete data pipeline for a specified input file.

This orchestrator executes all four stages of the pipeline in sequence:
1. Extract CIKs from parquet file
2. Query PermID API for each CIK
3. Retrieve detailed company information
4. Save results to database/storage

Supports configurable retry logic, error handling, and batch processing.
"""

import argparse
import os
import pathlib
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import ClassVar, Optional

from .utils import get_logger


class StageStatus(Enum):
    """Execution status for pipeline stages."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PipelinePaths:
    """Output file paths for a pipeline run (varies by CIK vs record mode)."""

    identifiers: pathlib.Path
    permid: pathlib.Path
    permid_batch: pathlib.Path
    company: pathlib.Path
    company_batch: pathlib.Path


@dataclass
class StageConfig:
    """Configuration for a pipeline stage."""
    name: str
    module: str
    required_args: list[str]
    optional_args: dict[str, str]
    output_file: Optional[str] = None
    retry_count: int = 3
    retry_delay: int = 60  # seconds


@dataclass
class PipelineConfig:
    """Configuration for the entire pipeline."""

    # Output file name constants (class-level, not instance attributes)
    CIK_DATA_FILE: ClassVar[str] = "identifier_cik.json"
    RECORD_DATA_FILE: ClassVar[str] = "identifier_record.json"
    PERMID_DATA_CIK_FILE: ClassVar[str] = "permid_data_cik.json"
    PERMID_DATA_RECORD_FILE: ClassVar[str] = "permid_data_record.json"
    PERMID_BATCH_TRACKING_CIK_FILE: ClassVar[str] = "permid_batch_tracking_cik.json"
    PERMID_BATCH_TRACKING_RECORD_FILE: ClassVar[str] = "permid_batch_tracking_record.json"
    COMPANY_INFO_CIK_FILE: ClassVar[str] = "company_info_cik.json"
    COMPANY_INFO_RECORD_FILE: ClassVar[str] = "company_info_record.json"
    COMPANY_BATCH_TRACKING_CIK_FILE: ClassVar[str] = "company_batch_tracking_cik.json"
    COMPANY_BATCH_TRACKING_RECORD_FILE: ClassVar[str] = "company_batch_tracking_record.json"

    # Instance configuration
    input_file: pathlib.Path
    output_directory: pathlib.Path
    pipeline_type: str  # "cik" or "record"
    permid_batch_size: int  # Batch size for query_permid.py
    company_info_batch_size: int  # Batch size for query_company_info.py
    permid_api_key: str
    geonames_user: str
    max_retries: int = 3
    threshold_days: Optional[int] = None
    postgres_connection: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_prefix: Optional[str] = "company-info"


class StageExecutor:
    """Executes individual pipeline stages with retry logic."""

    def __init__(self, config: StageConfig, pipeline_config: PipelineConfig):
        """
        Initialize stage executor.

        Args:
            config: Stage-specific configuration
            pipeline_config: Overall pipeline configuration
        """
        self.config = config
        self.pipeline_config = pipeline_config
        self.logger = get_logger(f"stage.{config.name}")

    def build_command(self, **kwargs) -> list[str]:
        """
        Build command line arguments for the stage.

        Args:
            **kwargs: Additional arguments to pass to the stage

        Returns:
            List of command line arguments
        """
        cmd = [sys.executable, "-m", self.config.module]

        # Add required arguments
        for arg_name in self.config.required_args:
            value = kwargs.get(arg_name)
            if value is None:
                raise ValueError(f"Required argument '{arg_name}' not provided for {self.config.name}")
            cmd.extend([f"--{arg_name}", str(value)])

        # Add optional arguments
        for arg_name, default_value in self.config.optional_args.items():
            value = kwargs.get(arg_name, default_value)
            if value is not None:
                cmd.extend([f"--{arg_name}", str(value)])

        return cmd

    def execute(self, **kwargs) -> tuple[StageStatus, Optional[str]]:
        """
        Execute the stage with retry logic.

        Args:
            **kwargs: Arguments to pass to the stage

        Returns:
            Tuple of (status, error_message)
        """
        for attempt in range(1, self.config.retry_count + 1):
            try:
                self.logger.info(f"Executing {self.config.name} (attempt {attempt}/{self.config.retry_count})")

                cmd = self.build_command(**kwargs)
                self.logger.debug(f"Command: {' '.join(cmd)}")

                # Run from project root with PYTHONPATH so subprocess finds idi_company_info
                project_root = pathlib.Path(__file__).resolve().parent.parent.parent
                src_path = project_root / "src"
                env = os.environ.copy()
                env["PYTHONPATH"] = str(src_path) + (
                    os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
                )
                subprocess.run(cmd, check=True, cwd=project_root, env=env)

                self.logger.info(f"{self.config.name} completed successfully")
                return StageStatus.SUCCESS, None

            except subprocess.CalledProcessError as e:
                error_msg = f"Stage failed with exit code {e.returncode}"
                self.logger.error(error_msg)

                if attempt < self.config.retry_count:
                    self.logger.info(f"Retrying in {self.config.retry_delay} seconds...")
                    time.sleep(self.config.retry_delay)
                else:
                    return StageStatus.FAILED, error_msg

            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                self.logger.error(error_msg)
                return StageStatus.FAILED, error_msg

        # Note: This point should never be reached due to returns above,
        # but included for completeness
        return StageStatus.FAILED, "Max retries exceeded"


class PipelineOrchestrator:
    """Orchestrates the complete data pipeline."""

    def __init__(self, config: PipelineConfig):
        """
        Initialize pipeline orchestrator.

        Args:
            config: Pipeline configuration
        """
        self.config = config
        self.logger = get_logger("orchestrator")
        self.stages = self._initialize_stages()

    def _initialize_stages(self) -> list[StageExecutor]:
        """
        Initialize all pipeline stages.

        Returns:
            List of configured stage executors
        """
        stage_configs = [
            StageConfig(
                name="retrieve_identifiers",
                module="idi_company_info.retrieve_identifiers",
                required_args=["type", "input-file", "output-file"],
                optional_args={},
                output_file=(
                    PipelineConfig.CIK_DATA_FILE
                    if self.config.pipeline_type == "cik"
                    else PipelineConfig.RECORD_DATA_FILE
                )
            ),
            StageConfig(
                name="query_permids",
                module="idi_company_info.query_permid",
                required_args=["type", "api-key", "input-file", "output-file", "batch-file"],
                optional_args={"batch-size": str(self.config.permid_batch_size)},
                output_file=(
                    PipelineConfig.PERMID_DATA_CIK_FILE
                    if self.config.pipeline_type == "cik"
                    else PipelineConfig.PERMID_DATA_RECORD_FILE
                )
            ),
            StageConfig(
                name="query_company_info",
                module="idi_company_info.query_company_info",
                required_args=["api-key", "geonames-user", "input-file", "output-file", "batch-file"],
                optional_args={
                    "batch-size": str(self.config.company_info_batch_size),
                    "threshold-days": str(self.config.threshold_days) if self.config.threshold_days else None
                },
                output_file=(
                    PipelineConfig.COMPANY_INFO_CIK_FILE
                    if self.config.pipeline_type == "cik"
                    else PipelineConfig.COMPANY_INFO_RECORD_FILE
                )
            ),
            StageConfig(
                name="export_results",
                module="idi_company_info.export_results",
                required_args=["input-file"],
                optional_args={
                    "postgres-connection": self.config.postgres_connection,
                    "s3-bucket": self.config.s3_bucket,
                    "s3-prefix": self.config.s3_prefix
                },
                output_file=None
            )
        ]

        return [StageExecutor(cfg, self.config) for cfg in stage_configs]

    def _get_output_paths(self, output_dir: pathlib.Path) -> PipelinePaths:
        """Build output paths for the current pipeline type (CIK vs record)."""
        is_cik = self.config.pipeline_type == "cik"
        return PipelinePaths(
            identifiers=output_dir / (
                PipelineConfig.CIK_DATA_FILE if is_cik else PipelineConfig.RECORD_DATA_FILE
            ),
            permid=output_dir / (
                PipelineConfig.PERMID_DATA_CIK_FILE if is_cik else PipelineConfig.PERMID_DATA_RECORD_FILE
            ),
            permid_batch=output_dir / (
                PipelineConfig.PERMID_BATCH_TRACKING_CIK_FILE
                if is_cik
                else PipelineConfig.PERMID_BATCH_TRACKING_RECORD_FILE
            ),
            company=output_dir / (
                PipelineConfig.COMPANY_INFO_CIK_FILE if is_cik else PipelineConfig.COMPANY_INFO_RECORD_FILE
            ),
            company_batch=output_dir / (
                PipelineConfig.COMPANY_BATCH_TRACKING_CIK_FILE
                if is_cik
                else PipelineConfig.COMPANY_BATCH_TRACKING_RECORD_FILE
            ),
        )

    def _execute_stage(self, index: int, name: str, **kwargs) -> bool:
        """Run a pipeline stage. Return True on success, False on failure."""
        status, error = self.stages[index].execute(**kwargs)
        if status != StageStatus.SUCCESS:
            self.logger.error(f"{name} failed: {error}")
            return False
        return True

    def run_pipeline(self) -> bool:
        """
        Execute all pipeline stages in sequence for the configured input file.

        Returns:
            True if all stages succeeded, False otherwise
        """
        if not self.config.input_file.exists():
            self.logger.error(f"Input file does not exist: {self.config.input_file}")
            return False

        self.logger.info("=" * 80)
        self.logger.info(f"Starting pipeline for: {self.config.input_file}")
        self.logger.info("=" * 80)
        start_time = datetime.now()

        output_dir = self.config.output_directory
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = self._get_output_paths(output_dir)

        # Stage 1: Extract identifiers
        if not self._execute_stage(
            0,
            "Stage 1 (Extract identifiers)",
            **{"type": self.config.pipeline_type, "input-file": str(self.config.input_file), "output-file": str(paths.identifiers)},
        ):
            return False

        # Stage 2: Query PermIDs
        if not self._execute_stage(
            1,
            "Stage 2 (Query PermIDs)",
            **{
                "type": self.config.pipeline_type,
                "api-key": self.config.permid_api_key,
                "input-file": str(paths.identifiers),
                "output-file": str(paths.permid),
                "batch-file": str(paths.permid_batch),
                "batch-size": str(self.config.permid_batch_size),
            },
        ):
            return False

        # Stage 3: Query Company Info
        stage3_kwargs = {
            "api-key": self.config.permid_api_key,
            "geonames-user": self.config.geonames_user,
            "input-file": str(paths.permid),
            "output-file": str(paths.company),
            "batch-file": str(paths.company_batch),
            "batch-size": str(self.config.company_info_batch_size),
        }
        if self.config.threshold_days is not None:
            stage3_kwargs["threshold-days"] = str(self.config.threshold_days)
        if not self._execute_stage(2, "Stage 3 (Query Company Info)", **stage3_kwargs):
            return False

        # Stage 4: Save Results
        if not self._execute_stage(
            3,
            "Stage 4 (Save Results)",
            **{
                "input-file": str(paths.company),
                "postgres-connection": self.config.postgres_connection,
                "s3-bucket": self.config.s3_bucket,
                "s3-prefix": self.config.s3_prefix,
            },
        ):
            return False

        self.logger.info("=" * 80)
        self.logger.info(f"Pipeline completed successfully in {datetime.now() - start_time}")
        self.logger.info(f"Output file: {paths.company}")
        self.logger.info("=" * 80)
        return True

    def run(self):
        """Run the orchestrator once for the specified input file."""
        self.logger.info("Starting Pipeline Orchestrator")
        self.logger.info(f"Pipeline type: {self.config.pipeline_type}")
        self.logger.info(f"Input file: {self.config.input_file}")
        self.logger.info(f"Output directory: {self.config.output_directory}")
        self.logger.info(f"PermID batch size: {self.config.permid_batch_size}, Company info batch size: {self.config.company_info_batch_size}")

        try:
            # Run pipeline
            success = self.run_pipeline()

            if success:
                self.logger.info("Pipeline execution successful")
                sys.exit(0)
            else:
                self.logger.error("Pipeline execution failed")
                sys.exit(1)

        except KeyboardInterrupt:
            self.logger.info("Orchestrator interrupted by user")
            sys.exit(130)
        except Exception as e:
            self.logger.error(f"Orchestrator error: {e}", exc_info=True)
            sys.exit(1)


def get_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Pipeline orchestrator - runs the complete pipeline for a specified input file"
    )

    parser.add_argument(
        "--input-file",
        type=pathlib.Path,
        required=True,
        help="Path to input parquet file to process"
    )

    parser.add_argument(
        "--output-directory",
        type=pathlib.Path,
        required=True,
        help="Directory for output files"
    )

    parser.add_argument(
        "--type",
        type=str,
        choices=["cik", "record"],
        required=True,
        help="Pipeline mode: 'cik' for CIK-based Entity Search, 'record' for ticker-based Record Match"
    )

    parser.add_argument(
        "--permid-batch-size",
        type=int,
        default=5000,
        help="Batch size for query_permid stage (default: 5000)"
    )

    parser.add_argument(
        "--company-info-batch-size",
        type=int,
        default=5000,
        help="Batch size for query_company_info stage (default: 5000)"
    )

    parser.add_argument(
        "--permid-api-key",
        type=str,
        required=True,
        help="PermID API access token"
    )

    parser.add_argument(
        "--geonames-user",
        type=str,
        required=True,
        help="Geonames API username"
    )

    parser.add_argument(
        "--postgres-connection",
        type=str,
        help="PostgreSQL connection string (optional)"
    )

    parser.add_argument(
        "--s3-bucket",
        type=str,
        help="S3 bucket name for upload (optional)"
    )

    parser.add_argument(
        "--s3-prefix",
        type=str,
        default="company-info",
        help="S3 key prefix (default: company-info)"
    )

    parser.add_argument(
        "--threshold-days",
        type=int,
        default=None,
        help="Re-query company info not updated in last N days (default: None, no re-querying)"
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = get_args()

    # Create pipeline configuration
    config = PipelineConfig(
        input_file=args.input_file,
        output_directory=args.output_directory,
        pipeline_type=args.type,
        permid_batch_size=args.permid_batch_size,
        company_info_batch_size=args.company_info_batch_size,
        permid_api_key=args.permid_api_key,
        geonames_user=args.geonames_user,
        threshold_days=args.threshold_days,
        postgres_connection=args.postgres_connection,
        s3_bucket=args.s3_bucket,
        s3_prefix=args.s3_prefix
    )

    # Create and run orchestrator
    orchestrator = PipelineOrchestrator(config)
    orchestrator.run()


if __name__ == "__main__":
    main()
