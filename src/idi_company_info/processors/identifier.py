"""Processes identifiers for company information."""

# Standard library imports
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Third party imports
import pandas as pd

# Application imports
from idi_company_info.common.api import (
    GeonamesApi,
    LSEGEntityLookup,
    LsegEntitySearch,
    LsegRecordMatch,
)
from idi_company_info.common.batch import BatchProcessing
from idi_company_info.common.failures import FailureRegistry
from idi_company_info.common.logs import get_logger
from idi_company_info.common.storage import load_json, save_json
from idi_company_info.processors.retrieval_company_info import CompInfoRetrieval
from idi_company_info.processors.retrieval_permid import PermidRetrieval
from idi_company_info.processors.types import (
    ApiCredentials,
    BatchConfig,
    BatchStats,
    FilePaths,
    QueryType,
)


@dataclass
class ApiClients:
    """Grouping of all API client instances used by the pipeline."""

    entity_search: LsegEntitySearch
    record_match: LsegRecordMatch
    entity_lookup: LSEGEntityLookup
    geonames_api: GeonamesApi


class IdentifierPipeline(ABC):
    """Base class for identifier types."""

    def __init__(
        self,
        file_paths: FilePaths,
        batch_config: BatchConfig,
        api_credentials: ApiCredentials,
        query_type: QueryType = QueryType.ENTITY_SEARCH,
        match_score_threshold: int = 1,
    ) -> None:
        """Initialize the Identifier.

        Args:
            file_paths: The file paths.
            batch_config: The batch config.
            api_credentials: The API credentials.
            identifier_type: The identifier type.
            query_type: The query type.
            match_score_threshold: The match score threshold.
        """
        self.file_paths = file_paths
        self._init_dirs()

        self.batch_config = batch_config

        self.failure_registry: FailureRegistry | None = (
            FailureRegistry(file_paths.failure_file) if file_paths.failure_file else None
        )

        self.api_credentials = api_credentials
        self.api_clients = ApiClients(
            entity_search=LsegEntitySearch(api_key=api_credentials.api_key),
            record_match=LsegRecordMatch(api_key=api_credentials.api_key),
            entity_lookup=LSEGEntityLookup(api_key=api_credentials.api_key),
            geonames_api=GeonamesApi(
                api_key=api_credentials.api_key, geonames_user=api_credentials.geonames_user
            ),
        )

        self.query_type = query_type
        self.match_score_threshold = match_score_threshold

        self.logger = get_logger("IdentifierPipeline")

    def _init_dirs(self) -> None:
        """Initialize the directories. Skip for S3 paths (no local dirs needed)."""
        for path in (
            self.file_paths.result_file,
            self.file_paths.permid_file,
            self.file_paths.failure_file or "",
        ):
            if path and not path.startswith("s3://"):
                Path(path).parent.mkdir(parents=True, exist_ok=True)

    @property
    @abstractmethod
    def identifier_type(self) -> str:
        """Get the identifier type."""
        ...

    @abstractmethod
    def load_data(self) -> dict[str, Any]:
        """Load the data from the input file.

        Returns:
            The data from the input file.
        """
        ...

    @staticmethod
    def read_parquet(input_file: str, required_columns: list[str]) -> pd.DataFrame:
        """Read parquet file and validate required columns exist.

        Args:
            input_file: The input file to read.
            required_columns: The required columns to validate.

        Returns:
            The dataframe with the required columns.
        """
        df = pd.read_parquet(input_file)

        # Validate required columns
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise ValueError(f"Required columns {missing_columns} not found in dataframe")

        return df

    @abstractmethod
    def _build_query_params(self, identifier: str) -> dict[str, Any]:
        """Build the query parameters.

        Args:
            identifier: The identifier.

        Returns:
            The query parameters.
        """
        ...

    def process_entities(
        self, entities_to_process: dict[str, Any], num_existing_entities: int
    ) -> list[dict[str, Any]]:
        """Process the entities.

        For entities that already have PermIDs stored from a previous run, skip the
        PermID retrieval step entirely and proceed directly to company info lookup.
        This allows the company info stage to drain its backlog without burning
        PermID API quota on identifiers that are already resolved.

        Args:
            entities_to_process: The entities to process.
            num_existing_entities: The number of existing entities.

        Returns:
            The batch stats.
        """
        batch_stats = BatchStats()

        # Partition: entities that already have PermIDs vs those that still need retrieval
        existing_permid_data = load_json(self.file_paths.permid_file, return_type="dict")
        needs_permid = {
            k: v for k, v in entities_to_process.items() if k not in existing_permid_data
        }
        has_permid_count = len(entities_to_process) - len(needs_permid)

        shared = dict(
            file_paths=self.file_paths,
            batch_config=self.batch_config,
            api_clients=self.api_clients,
            failure_registry=self.failure_registry,
            identifier_type=self.identifier_type,
        )

        # Retrieve the PermIDs for the entities that need them
        if needs_permid:
            retrieval_count = min(len(needs_permid), self.batch_config.batch_size)
            self.logger.info(
                "PermID retrieval: %d queued, %d will be retrieved this run, %d already resolved",
                len(needs_permid),
                retrieval_count,
                has_permid_count,
            )
            permid_retriever = PermidRetrieval(
                **shared, match_score_threshold=self.match_score_threshold
            )
            permid_retriever.retrieve(needs_permid, self.batch_config.batch_size, batch_stats)
        else:
            self.logger.info(
                "All %d entities already have PermIDs — skipping PermID retrieval",
                has_permid_count,
            )

        # Reload after retrieval so newly resolved PermIDs are included
        permid_data = load_json(self.file_paths.permid_file, return_type="dict")

        # Log how many of the queued entities were successfully resolved
        if needs_permid:
            self._log_permid_retrieval_stats(needs_permid, permid_data)

        # Retrieve the company info for the entities that have PermIDs
        all_entities = list(entities_to_process.keys())
        company_info_retriever = CompInfoRetrieval(**shared)
        company_info_retriever.retrieve(permid_data, all_entities, num_existing_entities, batch_stats)
        return batch_stats

    def _log_permid_retrieval_stats(
        self, needs_permid: dict[str, Any], permid_data: dict[str, Any]
    ) -> None:
        """Log the PermID retrieval stats.

        Args:
            needs_permid: The entities that need PermIDs.
            permid_data: The PermID data.
        """
        retrieved_count = min(len(needs_permid), self.batch_config.batch_size)
        resolved_this_run = sum(
            1 for k in list(needs_permid.keys())[:retrieved_count] if k in permid_data
        )
        self.logger.info(
            "PermID retrieval complete: %d/%d entities resolved this run",
            resolved_this_run,
            retrieved_count,
        )

    def print_stats(self, batch_stats: BatchStats) -> None:
        """Print the stats.

        Args:
            batch_stats: The batch stats.
        """
        stats = asdict(batch_stats)

        # Compute rates (avoid division by zero)
        permid_total = stats["total_permids"] + stats["total_permid_failed"]
        permid_rate = (stats["total_permids"] / permid_total * 100) if permid_total else 0

        company_total = stats["total_company_info"] + stats["total_company_info_failed"]
        company_rate = (stats["total_company_info"] / company_total * 100) if company_total else 0

        self.logger.info("=" * 50)
        self.logger.info("BATCH PROCESSING STATS")
        self.logger.info("=" * 50)
        self.logger.info(
            "PermID retrieval:     %d found, %d failed (%.1f%% success)",
            stats["total_permids"],
            stats["total_permid_failed"],
            permid_rate,
        )
        self.logger.info(
            "Company info lookup:  %d fetched, %d failed (%.1f%% success)",
            stats["total_company_info"],
            stats["total_company_info_failed"],
            company_rate,
        )
        self.logger.info(
            "Processed:   Entities: %d | Records: %d | Duplicates removed: %d",
            stats["total_entities"],
            stats["total_records"],
            stats["duplicates_ids_removed"],
        )
        self.logger.info("=" * 50)

    def save_company_info(self, company_info: list[dict[str, Any]]) -> None:
        """Save the company information.

        Args:
            company_info: The company information.
        """
        save_json(self.file_paths.result_file, company_info)

    def run(self) -> None:
        """Run the identifier pipeline."""
        # Load identifier data
        identifier_data = self.load_data()

        # Load existing results
        existing_results = load_json(self.file_paths.result_file, return_type="list")

        # Load previous batch processing data
        batch_processing = BatchProcessing(
            existing_results,
            self.batch_config.threshold_days,
            failure_registry=self.failure_registry,
        )
        unprocessed_entities = batch_processing.get_unprocessed_entities(identifier_data)
        filtered_results, stale_identifiers = batch_processing.filter_stale_entities()
        unprocessed_entities.update(stale_identifiers)
        to_process = sum(len(v) for v in unprocessed_entities.values())
        self.logger.info("To process: %d | Not to process: %d", to_process, len(filtered_results))

        # If stale entities were removed, persist the pruned list so the buffer
        # appends fresh results without duplicating the old stale records.
        if stale_identifiers:
            self.logger.info("Removing %d stale record(s) from result file", len(stale_identifiers))
            save_json(self.file_paths.result_file, filtered_results)

        # Process entities
        batch_stats = self.process_entities(unprocessed_entities, len(filtered_results))

        # Print stats
        self.print_stats(batch_stats)
