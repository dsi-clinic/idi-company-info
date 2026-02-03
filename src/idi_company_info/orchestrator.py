#!/usr/bin/env python3
"""
Pipeline Orchestrator - Watches for input files and runs the complete data pipeline.

This orchestrator continuously monitors a directory for new shareholder tracker files.
When a file is detected, it executes all three stages of the pipeline in sequence:
1. Extract CIKs from parquet file
2. Query PermID API for each CIK
3. Retrieve detailed company information

Supports configurable retry logic, error handling, and notification hooks.
"""

import argparse
import logging
import pathlib
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import ClassVar, Optional

logging.basicConfig(
    format='%(asctime)s,%(msecs)d %(module)s:%(lineno)d %(levelname)s %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S',
    level=logging.INFO
)


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
    watch_directory: pathlib.Path
    file_pattern: str
    output_directory: pathlib.Path
    archive_directory: pathlib.Path
    batch_size: int
    permid_api_key: str
    geonames_user: str
    poll_interval: int = 30  # seconds
    max_retries: int = 3
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
        self.logger = logging.getLogger(f"stage.{config.name}")

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
        self.logger = logging.getLogger("orchestrator")
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
                optional_args={"batch-size": str(self.config.batch_size)},
                output_file="company_info.json"
            ),
            StageConfig(
                name="save_results",
                module="idi_company_info.save_result",
                required_args=["input-file", "source-file", "archive-directory"],
                optional_args={
                    "postgres-connection": self.config.postgres_connection,
                    "s3-bucket": self.config.s3_bucket,
                    "s3-prefix": self.config.s3_prefix
                },
                output_file=None
            )
        ]

        return [StageExecutor(cfg, self.config) for cfg in stage_configs]

    def watch_for_file(self) -> Optional[pathlib.Path]:
        """
        Watch directory for new files matching the pattern.

        Returns:
            Path to detected file or None
        """
        self.logger.info(f"Watching directory: {self.config.watch_directory}")
        self.logger.info(f"Looking for pattern: {self.config.file_pattern}")

        while True:
            try:
                # Check if directory exists
                if not self.config.watch_directory.exists():
                    self.logger.warning(f"Directory does not exist: {self.config.watch_directory}")
                    time.sleep(self.config.poll_interval)
                    continue

                # Look for matching files
                matching_files = list(self.config.watch_directory.glob(self.config.file_pattern))

                if matching_files:
                    # Return the first matching file
                    file_path = matching_files[0]
                    self.logger.info(f"Detected file: {file_path}")
                    return file_path

                # Wait before next check
                self.logger.debug(f"No files found, waiting {self.config.poll_interval}s...")
                time.sleep(self.config.poll_interval)

            except KeyboardInterrupt:
                self.logger.info("Received interrupt signal, stopping watch...")
                return None
            except Exception as e:
                self.logger.error(f"Error while watching directory: {e}")
                time.sleep(self.config.poll_interval)

    def run_pipeline(self, input_file: pathlib.Path) -> bool:
        """
        Execute all pipeline stages in sequence.

        Args:
            input_file: Path to input parquet file

        Returns:
            True if all stages succeeded, False otherwise
        """
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
        status, error = self.stages[2].execute(
            **{
                "api-key": self.config.permid_api_key,
                "geonames-user": self.config.geonames_user,
                "input-file": str(permid_file),
                "output-file": str(company_file),
                "batch-file": str(company_batch_file),
                "batch-size": str(self.config.batch_size)
            }
        )

        if status != StageStatus.SUCCESS:
            self.logger.error(f"Stage 3 failed: {error}")
            return False

        # Stage 4: Save Results and Archive
        status, error = self.stages[3].execute(
            **{
                "input-file": str(company_file),
                "source-file": str(input_file),
                "archive-directory": str(self.config.archive_directory),
                "postgres-connection": self.config.postgres_connection,
                "s3-bucket": self.config.s3_bucket,
                "s3-prefix": self.config.s3_prefix
            }
        )

        if status != StageStatus.SUCCESS:
            self.logger.error(f"Stage 4 (save results) failed: {error}")
            return False

        elapsed_time = datetime.now() - start_time
        self.logger.info("=" * 80)
        self.logger.info(f"Pipeline completed successfully in {elapsed_time}")
        self.logger.info(f"Output file: {company_file}")
        self.logger.info(f"Source file archived to: {self.config.archive_directory}")
        self.logger.info("=" * 80)

        return True

    def run(self):
        """Run the orchestrator in continuous watch mode."""
        self.logger.info("Starting Pipeline Orchestrator")
        self.logger.info(f"Output directory: {self.config.output_directory}")
        self.logger.info(f"Batch size: {self.config.batch_size}")

        try:
            while True:
                # Wait for new file
                input_file = self.watch_for_file()

                if input_file is None:
                    self.logger.info("Orchestrator stopping...")
                    break

                # Run pipeline
                success = self.run_pipeline(input_file)

                if success:
                    self.logger.info("Pipeline execution successful")
                    self.logger.info("Source file has been archived")
                else:
                    self.logger.error("Pipeline execution failed")
                    self.logger.warning("Source file remains in watch directory for manual intervention")

        except KeyboardInterrupt:
            self.logger.info("Orchestrator interrupted by user")
        except Exception as e:
            self.logger.error(f"Orchestrator error: {e}", exc_info=True)
            raise


def get_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Pipeline orchestrator - watches for files and runs the complete pipeline"
    )

    parser.add_argument(
        "--watch-directory",
        type=pathlib.Path,
        required=True,
        help="Directory to watch for input files"
    )

    parser.add_argument(
        "--file-pattern",
        type=str,
        default="shareholder_tracker_*.parquet",
        help="File pattern to match (default: shareholder_tracker_*.parquet)"
    )

    parser.add_argument(
        "--output-directory",
        type=pathlib.Path,
        required=True,
        help="Directory for output files"
    )

    parser.add_argument(
        "--archive-directory",
        type=pathlib.Path,
        required=True,
        help="Directory to move processed files to"
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
        "--poll-interval",
        type=int,
        default=30,
        help="Seconds between directory checks (default: 30)"
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

    return parser.parse_args()


def main():
    """Main entry point."""
    args = get_args()

    # Create pipeline configuration
    config = PipelineConfig(
        watch_directory=args.watch_directory,
        file_pattern=args.file_pattern,
        output_directory=args.output_directory,
        archive_directory=args.archive_directory,
        batch_size=args.batch_size,
        permid_api_key=args.permid_api_key,
        geonames_user=args.geonames_user,
        poll_interval=args.poll_interval,
        postgres_connection=args.postgres_connection,
        s3_bucket=args.s3_bucket,
        s3_prefix=args.s3_prefix
    )

    # Create and run orchestrator
    orchestrator = PipelineOrchestrator(config)
    orchestrator.run()


if __name__ == "__main__":
    main()
