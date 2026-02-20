"""Processes identifiers for company information."""

# Standard library imports
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

# Application imports
from ftm2j.common.logs import get_logger
from ftm2j.common.batch import BatchProcessing


@dataclass
class FilePaths:
    input_file: str
    result_file: str
    batch_file: str


@dataclass
class BatchConfig:
    batch_size: int
    buffer_size: int = 1000
    threshold_days: int


@dataclass
class ApiCredentials:
    api_key: str
    geonames_user: str


class Identifier(ABC):
    """Base class for identifier types."""

    def __init__(self, file_paths: FilePaths, batch_config: BatchConfig, api_credentials: ApiCredentials):
        """Initialize the Identifier.

        Args:
            file_paths: The file paths.
            batch_config: The batch config.
            api_credentials: The API credentials.
        """
        self.file_paths = file_paths
        self.batch_config = batch_config
        self.api_credentials = api_credentials
        self.logger = get_logger(__name__)

    @abstractmethod
    def load_data(self) -> dict[str, Any]:
        """Load the data from the input file."""
        ...

    @abstractmethod
    def retrieve_permid(self) -> dict[str, Any]:
        """Retrieve the PermID for the company."""
        ...

    @abstractmethod
    def retrieve_company_info(self) -> list[dict[str, Any]]:
        """Retrieve the company information."""
        ...

    @abstractmethod
    def save_company_info(self) -> list[str]:
        """Save the company information."""
        ...

    def print_stats(self, batch_stats: dict[str, Any]) -> None:
        """Print the stats.

        Args:
            batch_stats: The batch stats.
        """
        self.logger.info(f"Batch stats: {batch_stats}")

    def run(self):
        """Run the identifier pipeline."""

        # Load identifier data
        identifier_data = self.load_data()

        # Batch processing
        batch_processing = BatchProcessing(self.file_paths.batch_file, self.batch_config.threshold_days)
        unprocessed_entities = batch_processing.get_unprocessed_entities(identifier_data)
        existing_results, stale_entities = batch_processing.filter_stale_entities(unprocessed_entities)

        # Process entities
        buffer = []
        new_results = []
        batch_stats = {}
        for entity in unprocessed_entities + stale_entities:
            try:
                permid_data = self.retrieve_permid(entity, batch_stats)
                company = self.retrieve_company_info(permid_data, batch_stats)
                new_results.extend(company)

                buffer.append(company)
                if len(buffer) >= self.batch_config.buffer_size:
                    self.save_company_info(new_results + existing_results)
                    batch_processing.update_batch_tracking(buffer, batch_stats)
                    buffer = []

            except Exception as e:
                self.logger.error(f"Error processing entity {entity}: {e}")
                continue

        # Save company info and batch tracking
        if buffer:
            self.save_company_info(new_results + existing_results)
            batch_processing.update_batch_tracking(buffer, batch_stats)

        # Print stats
        self.print_stats(batch_stats)