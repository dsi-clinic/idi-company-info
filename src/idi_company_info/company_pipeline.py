"""Processes identifiers for company information."""

# Standard library imports
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Third party imports
import pandas as pd
from idi_ftm2j_shared.failures import FailureRegistry
from idi_ftm2j_shared.logs import get_logger
from idi_ftm2j_shared.storage import load_json

# Application imports
from idi_company_info.api import (
    GeonamesApi,
    LSEGEntityLookup,
    LsegRecordMatch,
)
from idi_company_info.batch import BatchProcessing
from idi_company_info.cache_keys import permid_cache_key
from idi_company_info.failures import CompanyInfoFailureClassifier
from idi_company_info.retrieval_company_info import CompInfoRetrieval
from idi_company_info.retrieval_permid import PermidRetrieval
from idi_company_info.types import (
    ApiCredentials,
    APIRateLimits,
    BatchConfig,
    BatchStats,
    FilePaths,
)


@dataclass
class ApiClients:
    """Grouping of all API client instances used by the pipeline."""

    record_match: LsegRecordMatch
    entity_lookup: LSEGEntityLookup
    geonames_api: GeonamesApi


class CompanyPipeline(ABC):
    """Base class for identifier types."""

    def __init__(
        self,
        file_paths: FilePaths,
        batch_config: BatchConfig,
        api_credentials: ApiCredentials,
        match_score_threshold: int = 1,
    ) -> None:
        """Initialize the Identifier.

        Args:
            file_paths: The file paths.
            batch_config: The batch config.
            api_credentials: The API credentials.
            match_score_threshold: The match score threshold.
        """
        self.file_paths = file_paths
        self._init_dirs()

        self.batch_config = batch_config

        company_info_failure_classifier = CompanyInfoFailureClassifier()
        self.failure_registry: FailureRegistry | None = (
            FailureRegistry(file_paths.failure_file, company_info_failure_classifier)
            if file_paths.failure_file
            else None
        )

        self.api_credentials = api_credentials
        self.api_clients = ApiClients(
            record_match=LsegRecordMatch(
                api_key=api_credentials.api_key, rate_limit=APIRateLimits.permid
            ),
            entity_lookup=LSEGEntityLookup(
                api_key=api_credentials.api_key, rate_limit=APIRateLimits.company_info
            ),
            geonames_api=GeonamesApi(
                api_key=api_credentials.api_key,
                geonames_user=api_credentials.geonames_user,
                rate_limit=APIRateLimits.geonames,
            ),
        )

        self.match_score_threshold = match_score_threshold

        self.logger = get_logger(type(self).__name__)

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

    @property
    def std_ticker_map(self) -> dict[str, str]:
        """Formatted Standard Identifier strings keyed by local ID.

        Overridden by subclasses that need to supply auxiliary search identifiers
        to PermidRetrieval (e.g. CompanyByCusipPipeline supplies CUSIP → ticker string).
        """
        return {}

    @property
    def raw_ticker_map(self) -> dict[str, str]:
        """Raw ticker symbols keyed by local ID, stored in company info output.

        Overridden by CompanyByCusipPipeline; returns empty dict for all other types.
        """
        return {}

    @staticmethod
    def read_parquet(input_file: str, required_columns: list[str]) -> pd.DataFrame:
        """Read parquet file and validate required columns exist.

        Args:
            input_file: The input file to read.
            required_columns: The required columns to validate.

        Returns:
            The dataframe with the required columns.
        """
        input_df = pd.read_parquet(input_file)

        # Validate required columns
        missing_columns = [col for col in required_columns if col not in input_df.columns]
        if missing_columns:
            raise ValueError(f"Required columns {missing_columns} not found in dataframe")

        return input_df

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
        needs_permid, has_permid_count = self._determine_needs_permid(entities_to_process)
        needs_count = sum(len(identifiers) for identifiers in needs_permid.values())

        # Shared date between retrievals
        shared = {
            "file_paths": self.file_paths,
            "batch_config": self.batch_config,
            "api_clients": self.api_clients,
            "failure_registry": self.failure_registry,
            "identifier_type": self.identifier_type,
        }

        # Retrieve the PermIDs for the entities that need them
        permid_data = self._retrieve_permid(
            needs_permid, needs_count, has_permid_count, shared, batch_stats
        )

        # Retrieve the company info for the entities that have PermIDs
        company_info_retriever = CompInfoRetrieval(**shared)
        company_info_retriever.retrieve(permid_data, num_existing_entities, batch_stats)
        return batch_stats

    def _determine_needs_permid(
        self, entities_to_process: dict[str, list[str]]
    ) -> tuple[dict[str, list[str]], int]:
        """Determine what entities need permids and what already have them

        Args:
            entities_to_process: Dictonary of entities to process

        Returns:
            Tuple of entities that need a permid and the count of ones that don't
        """
        existing_permid_data = load_json(self.file_paths.permid_file, return_type="dict")

        needs_permid = {}
        for entity_name, identifiers in entities_to_process.items():
            for identifier in identifiers:
                key = permid_cache_key(entity_name, f"{self.identifier_type}_{identifier}")
                if key not in existing_permid_data:
                    needs_permid.setdefault(entity_name, []).append(identifier)

        needs_count = sum(len(identifiers) for identifiers in needs_permid.values())
        entity_count = sum(len(identifiers) for identifiers in entities_to_process.values())
        has_permid_count = entity_count - needs_count

        return needs_permid, has_permid_count

    def _retrieve_permid(
        self,
        needs_permid: dict[str, list[str]],
        needs_count: int,
        has_permid_count: int,
        shared: dict[str, Any],
        batch_stats: BatchStats,
    ) -> dict[str, dict]:
        """Retrieve PermIDs for entities that need them and return the refreshed cache.

        When ``needs_permid`` is non-empty, runs the Record Match API then reloads
        ``permid_file``. When empty, skips retrieval and just reloads the existing
        cache.

        Args:
            needs_permid: Mapping of entity_name -> [identifier, ...]
            needs_count: Total number of identifiers across ``needs_permid`` (queued count).
            has_permid_count: Number of to-process identifiers already resolved in the cache.
            shared: Common keyword args (file_paths, batch_config, api_clients, failure_registry, identifier_type).
            batch_stats: Accumulator for run-level statistics, mutated in place.

        Returns:
            The reloaded permid_data: ``{cache_key: {"search": {...}, "result": [permid_url, ...]}}``.
        """
        if needs_permid:
            retrieval_count = min(needs_count, self.batch_config.batch_size)
            self.logger.info(
                "PermID retrieval: %d queued, %d will be retrieved this run, %d already resolved",
                needs_count,
                retrieval_count,
                has_permid_count,
            )
            permid_retriever = PermidRetrieval(
                **shared,
                match_score_threshold=self.match_score_threshold,
                std_ticker_map=self.std_ticker_map,
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

        return permid_data

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
            1
            for k, ids in list(needs_permid.items())[:retrieved_count]
            if any(permid_cache_key(k, f"{self.identifier_type}_{i}") in permid_data for i in ids)
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

    def run(self) -> None:
        """Run the identifier pipeline."""
        try:
            # Load identifier data
            identifier_data = self.load_data()

            # Load existing caches
            result_data = load_json(self.file_paths.result_file, return_type="dict")
            permid_data = load_json(self.file_paths.permid_file, return_type="dict")

            batch_processing = BatchProcessing(
                result_data,
                permid_data,
                self.identifier_type,
                self.batch_config.threshold_days,
                failure_registry=self.failure_registry,
            )

            # Prune stale results + their permid keys FIRST so stale rows re-resolve
            # naturally as "unprocessed" below.
            num_not_stale = batch_processing.filter_stale_entities(
                self.file_paths.result_file, self.file_paths.permid_file
            )

            # Determine what still needs processing against the pruned caches.
            unprocessed_entities = batch_processing.get_unprocessed_entities(identifier_data)
            to_process = sum(len(ids) for ids in unprocessed_entities.values())
            self.logger.info(
                "To process: %d | Remaining saved results: %d", to_process, num_not_stale
            )

            # Process entities
            batch_stats = self.process_entities(unprocessed_entities, num_not_stale)

            # Print stats
            self.print_stats(batch_stats)
        finally:
            # Persist any buffered failures so partial buffers (<flush_every)
            # and end-of-run failures aren't lost on exit.
            if self.failure_registry:
                self.failure_registry.flush()
