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
from idi_company_info.batch import BatchProcessing, find_cusip_collisions
from idi_company_info.buffer import permid_cache_key
from idi_company_info.failures import CompanyInfoFailureClassifier
from idi_company_info.input import Input
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
        input_source: Input,
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

        self.input_source = input_source
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

    def process_entities(
        self, entities_to_process: dict[str, Any], num_existing_entities: int
    ) -> tuple[BatchStats, set[str]]:
        """Process the entities.

        For entities that already have PermIDs stored from a previous run, skip the
        PermID retrieval step entirely and proceed directly to company info lookup.
        This allows the company info stage to drain its backlog without burning
        PermID API quota on identifiers that are already resolved.

        Args:
            entities_to_process: The entities to process.
            num_existing_entities: The number of existing entities.

        Returns:
            A tuple of (batch stats, permid cache keys resolved during this run).
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
            "identifier_type": self.input_source.identifier_type,
        }

        # Retrieve the PermIDs for the entities that need them
        permid_data, resolved_keys = self._retrieve_permid(
            needs_permid, needs_count, has_permid_count, shared, batch_stats
        )

        # Retrieve the company info for the entities that have PermIDs
        company_info_retriever = CompInfoRetrieval(**shared)
        company_info_retriever.retrieve(permid_data, num_existing_entities, batch_stats)
        return batch_stats, resolved_keys

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
                key = permid_cache_key(entity_name, f"{self.input_source.identifier_type}_{identifier}")
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
    ) -> tuple[dict[str, dict], set[str]]:
        """Retrieve PermIDs for entities that need them and return the refreshed cache.

        When ``needs_permid`` is non-empty, runs the Record Match API then reloads
        ``permid_file``. When empty, skips retrieval and just reloads the existing
        cache. Do-not-retry entities (in the failure registry) are filtered out of the
        returned cache so the company-info stage does not re-fetch them every run.

        Args:
            needs_permid: Mapping of entity_name -> [identifier, ...]
            needs_count: Total number of identifiers across ``needs_permid`` (queued count).
            has_permid_count: Number of to-process identifiers already resolved in the cache.
            shared: Common keyword args (file_paths, batch_config, api_clients, failure_registry, identifier_type).
            batch_stats: Accumulator for run-level statistics, mutated in place.

        Returns:
            A tuple of (reloaded permid_data, resolved_keys), where permid_data is
            ``{cache_key: {"search": {...}, "result": [permid_url, ...]}}`` and resolved_keys
            is the set of cache keys from ``needs_permid`` resolved during this run.
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
                std_ticker_map=self.input_source.std_ticker_map,
            )
            permid_retriever.retrieve(needs_permid, self.batch_config.batch_size, batch_stats)
        else:
            self.logger.info(
                "All %d entities already have PermIDs — skipping PermID retrieval",
                has_permid_count,
            )

        # Reload after retrieval so newly resolved PermIDs are included
        permid_data = load_json(self.file_paths.permid_file, return_type="dict")

        # Cache keys from this run's queue that are now resolved
        resolved_keys = {
            key
            for name, ids in needs_permid.items()
            for identifier in ids
            if (key := permid_cache_key(name, f"{self.input_source.identifier_type}_{identifier}"))
            in permid_data
        }

        # Log how many of the queued entities were successfully resolved
        if needs_permid:
            self._log_permid_retrieval_stats(needs_permid, resolved_keys)

        # Drop do-not-retry entities before the company-info stage consumes this cache.
        if self.failure_registry:
            permid_data = {
                key: entry
                for key, entry in permid_data.items()
                if (entry["search"]["Name"], entry["search"]["LocalID"])
                not in self.failure_registry
            }

        return permid_data, resolved_keys

    def _log_permid_retrieval_stats(
        self, needs_permid: dict[str, Any], resolved_keys: set[str]
    ) -> None:
        """Log the PermID retrieval stats.

        Counts are by *identifier* (the unit batch_size caps), not entity name.

        Args:
            needs_permid: The entities that need PermIDs.
            resolved_keys: Cache keys from ``needs_permid`` resolved during this run.
        """
        queued_count = sum(len(ids) for ids in needs_permid.values())
        attempted = min(queued_count, self.batch_config.batch_size)
        self.logger.info(
            "PermID retrieval complete: %d/%d identifiers resolved this run",
            len(resolved_keys),
            attempted,
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

    def _report_cusip_collisions(self, resolved_keys: set[str]) -> None:
        """Warn about PermIDs reached by multiple CUSIP issuers (likely false matches).

        Detection only — no API calls, nothing pruned. Membership is computed over the full
        permid_file (so a CUSIP resolved this run that collides with a previously-cached CUSIP
        is caught), but reporting is scoped to PermIDs reached during this run so historical
        collisions are not re-warned on every run.

        Args:
            resolved_keys: Cache keys for the CUSIPs resolved during this run.
        """
        permid_data = load_json(self.file_paths.permid_file, return_type="dict")
        result_data = load_json(self.file_paths.result_file, return_type="dict")
        collisions = find_cusip_collisions(permid_data)

        # Scope to PermIDs reached by a CUSIP resolved this run.
        current_urls = {
            url for key in resolved_keys for url in permid_data.get(key, {}).get("result", [])
        }
        collisions = {url: members for url, members in collisions.items() if url in current_urls}

        for permid_url, members in collisions.items():
            canonical = result_data.get(permid_url, {}).get("result", {}).get("investor_name")
            detail = ", ".join(f"{cusip} ({name})" for cusip, name in sorted(members.items()))
            self.logger.warning(
                "Possible false match: %d CUSIPs across multiple issuers resolved to "
                "PermID %s (%s): %s",
                len(members),
                permid_url,
                canonical,
                detail,
            )

        if collisions:
            self.logger.warning(
                "CUSIP collision summary: %d PermID(s) reached by multiple CUSIP issuers "
                "(review for false Record Match hits)",
                len(collisions),
            )

    def run(self) -> None:
        """Run the identifier pipeline."""
        try:
            # Load identifier data
            input_data = self.input_source.load_data()

            # Load existing caches
            result_data = load_json(self.file_paths.result_file, return_type="dict")
            permid_data = load_json(self.file_paths.permid_file, return_type="dict")

            batch_processing = BatchProcessing(
                result_data,
                permid_data,
                self.input_source.identifier_type,
                self.batch_config.threshold_days,
                failure_registry=self.failure_registry,
            )

            # Prune stale results + their permid keys FIRST so stale rows re-resolve
            # naturally as "unprocessed" below.
            num_not_stale = batch_processing.filter_stale_entities(self.file_paths.result_file)

            # Determine what still needs processing against the pruned caches.
            unprocessed_entities = batch_processing.get_unprocessed_entities(input_data)
            to_process = sum(len(ids) for ids in unprocessed_entities.values())
            self.logger.info(
                "To process: %d | Remaining saved results: %d", to_process, num_not_stale
            )

            # Process entities
            batch_stats, resolved_keys = self.process_entities(unprocessed_entities, num_not_stale)

            # Print stats
            self.print_stats(batch_stats)

            # CUSIP: flag likely false Record Match collisions (no extra API calls).
            if self.input_source.identifier_type == "cusip":
                self._report_cusip_collisions(resolved_keys)
        finally:
            # Persist any buffered failures so partial buffers (<flush_every)
            # and end-of-run failures aren't lost on exit.
            if self.failure_registry:
                self.failure_registry.flush()
