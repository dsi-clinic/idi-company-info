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
    CIK_DATA_FILE: ClassVar[str] = "cik_data.json"
    PERMID_DATA_FILE: ClassVar[str] = "permid_data.json"
    PERMID_BATCH_TRACKING_FILE: ClassVar[str] = "permid_batch_tracking.json"
    COMPANY_INFO_FILE: ClassVar[str] = "company_info.json"
    COMPANY_BATCH_TRACKING_FILE: ClassVar[str] = "company_batch_tracking.json"

    # Instance configuration
    input_file: pathlib.Path
    output_directory: pathlib.Path
    batch_size: int
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

                # Stream output to parent's stdout/stderr (no capture)
                subprocess.run(cmd, check=True)

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
                name="extract_ciks",
                module="idi_company_info.retrieve_cik",
                required_args=["input-file", "output-file"],
                optional_args={},
                output_file="cik_data.json"
            ),
            StageConfig(
                name="query_permids",
                module="idi_company_info.query_permid",
                required_args=["api-key", "input-file", "output-file", "batch-file"],
                optional_args={"batch-size": str(self.config.batch_size)},
                output_file="permid_data.json"
            ),
            StageConfig(
                name="query_company_info",
                module="idi_company_info.query_company_info",
                required_args=["api-key", "geonames-user", "input-file", "output-file", "batch-file"],
                optional_args={
                    "batch-size": str(self.config.batch_size),
                    "threshold-days": str(self.config.threshold_days) if self.config.threshold_days else None
                },
                output_file="company_info.json"
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

    def run_pipeline(self) -> bool:
        """
        Execute all pipeline stages in sequence for the configured input file.

        Returns:
            True if all stages succeeded, False otherwise
        """
        input_file = self.config.input_file

        # Validate input file exists
        if not input_file.exists():
            self.logger.error(f"Input file does not exist: {input_file}")
            return False

        self.logger.info("=" * 80)
        self.logger.info(f"Starting pipeline for: {input_file}")
        self.logger.info("=" * 80)

        start_time = datetime.now()

        # Prepare output paths
        output_dir = self.config.output_directory
        output_dir.mkdir(parents=True, exist_ok=True)

        cik_file = output_dir / PipelineConfig.CIK_DATA_FILE
        permid_file = output_dir / PipelineConfig.PERMID_DATA_FILE
        permid_batch_file = output_dir / PipelineConfig.PERMID_BATCH_TRACKING_FILE
        company_file = output_dir / PipelineConfig.COMPANY_INFO_FILE
        company_batch_file = output_dir / PipelineConfig.COMPANY_BATCH_TRACKING_FILE

        # Stage 1: Extract CIKs
        status, error = self.stages[0].execute(
            **{
                "input-file": str(input_file),
                "output-file": str(cik_file)
            }
        )

        if status != StageStatus.SUCCESS:
            self.logger.error(f"Stage 1 failed: {error}")
            return False

        # Stage 2: Query PermIDs
        status, error = self.stages[1].execute(
            **{
                "api-key": self.config.permid_api_key,
                "input-file": str(cik_file),
                "output-file": str(permid_file),
                "batch-file": str(permid_batch_file),
                "batch-size": str(self.config.batch_size)
            }
        )

        if status != StageStatus.SUCCESS:
            self.logger.error(f"Stage 2 failed: {error}")
            return False

        # Stage 3: Query Company Info
        stage3_args = {
            "api-key": self.config.permid_api_key,
            "geonames-user": self.config.geonames_user,
            "input-file": str(permid_file),
            "output-file": str(company_file),
            "batch-file": str(company_batch_file),
            "batch-size": str(self.config.batch_size)
        }

        # Add threshold-days if configured
        if self.config.threshold_days is not None:
            stage3_args["threshold-days"] = str(self.config.threshold_days)

        status, error = self.stages[2].execute(**stage3_args)

        if status != StageStatus.SUCCESS:
            self.logger.error(f"Stage 3 failed: {error}")
            return False

        # Stage 4: Save Results
        status, error = self.stages[3].execute(
            **{
                "input-file": str(company_file),
                "postgres-connection": self.config.postgres_connection,
                "s3-bucket": self.config.s3_bucket,
                "s3-prefix": self.config.s3_prefix
            }
        )

        if status != StageStatus.SUCCESS:
            self.logger.error(f"Stage 4 (export results) failed: {error}")
            return False

        elapsed_time = datetime.now() - start_time
        self.logger.info("=" * 80)
        self.logger.info(f"Pipeline completed successfully in {elapsed_time}")
        self.logger.info(f"Output file: {company_file}")
        self.logger.info("=" * 80)

        return True

    def run(self):
        """Run the orchestrator once for the specified input file."""
        self.logger.info("Starting Pipeline Orchestrator")
        self.logger.info(f"Input file: {self.config.input_file}")
        self.logger.info(f"Output directory: {self.config.output_directory}")
        self.logger.info(f"Batch size: {self.config.batch_size}")

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
        "--batch-size",
        type=int,
        default=5000,
        help="Number of items to process per batch (default: 5000)"
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
        batch_size=args.batch_size,
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
