"""Batch processing utilities for tracking and managing batch operations."""

# Standard library imports
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Third party imports
from idi_ftm2j_shared.failures import FailureRegistry
from idi_ftm2j_shared.logs import get_logger
from idi_ftm2j_shared.storage import load_json, save_json

# Application imports
from idi_company_info.cache_keys import parse_permid_cache_key


class BatchProcessing:
    """Tracks and manages batch processing state for entity pipelines."""

    def __init__(
        self,
        result_data: dict[str, dict[str, dict]],
        threshold_days: int = 30,
        failure_registry: "FailureRegistry | None" = None,
    ) -> None:
        """Initialize the BatchProcessing.

        Args:
            result_data: The result data.
            threshold_days: The threshold days.
            failure_registry: Optional registry of permanent failures to exclude from retries.
        """
        self.result_data = result_data
        self.threshold_days = threshold_days
        self.failure_registry = failure_registry
        self.logger = get_logger(type(self).__name__)

    def get_unprocessed_entities(self, entity_data: dict[str, list[Any]]) -> dict[str, Any]:
        """Get list of entities that haven't been processed yet.

        Args:
            entity_data: Dict of entity_name -> list of identifiers (strings)

        Returns:
            Dict of entity_name -> list of identifiers
        """
        # Format processed entities so they are easy to compare
        processed_entities = [
            (value["identifier"]["name"], value["identifier"]["identifier"])
            for value in self.result_data.values()
        ]

        # Get all new entities from input data that are not in processed
        unprocessed_entities = {}
        for entity_name, identifier_list in entity_data.items():
            for identifier in identifier_list:
                if (entity_name, identifier) not in processed_entities:
                    unprocessed_entities.setdefault(entity_name, []).append(identifier)

        # Exclude entries in do-not-retry registry
        unprocessed_entities, excluded = self._remove_failed_entities(unprocessed_entities)

        new_entity_count = sum(len(identifiers) for identifiers in entity_data.values())
        unprocessed_count = sum(len(identifiers) for identifiers in unprocessed_entities.values())

        self.logger.info("Total new entities: %s", new_entity_count)
        self.logger.info("Already processed entities: %s", len(processed_entities))
        self.logger.info("Excluded %d entities from do-not-retry registry", excluded)
        self.logger.info("Remaining to process: %s", unprocessed_count)

        return unprocessed_entities

    def _remove_failed_entities(
        self, entities: dict[str, list[str]]
    ) -> tuple[dict[str, list[str]], int]:
        """Remove entities that are in the do-not-retry registry from the list.

        Args:
            entities: List of (entity_name, identifier) tuples.

        Returns:
            Tuple of result with failures removed and number of result excluded
        """
        if not self.failure_registry:
            return entities, 0

        before_count = sum(len(identifiers) for identifiers in entities.values())

        result = {}
        for entity_name, identifiers in entities.items():
            for identifier in identifiers:
                if (entity_name, identifier) not in self.failure_registry:
                    result.setdefault(entity_name, []).append(identifier)

        result_count = sum(len(identifiers) for identifiers in result.values())
        excluded = before_count - result_count

        return result, excluded

    def filter_stale_entities(
        self, result_file: Path, permid_file: Path
    ) -> tuple[dict[str, dict], int]:
        """Identify and process stale entities based on threshold.

        Filters out stale entities from result file and saves JSON.

        Args:
            result_file: Path to result file
            permid_file: Path to permid file (cache)

        Returns:
            Tuple of (stale_entities, num_not_stale) where:
              - stale_entities: full company info records that are stale
              - num_not_stale: number of remaining results that are not stale
        """
        if self.threshold_days is None:
            return {}, len(self.result_data.keys())

        self.logger.info("Checking for entities not updated in last %d days", self.threshold_days)
        stale_entities, _ = self._get_stale_entities()
        self.logger.info("Located %s stale entities", len(stale_entities.keys()))

        if not stale_entities:
            return {}, len(self.result_data.keys())

        # Remove stale entity records so they can be re-processed
        filtered_results, removed_result = self._remove_stale_records(stale_entities)

        # Remove stale entities from permid cache
        filtered_permid, removed_permid = self._filter_stale_permid_cache(
            permid_file, stale_entities
        )

        # If stale entities were removed, persist the pruned list so the buffer
        # appends fresh results without duplicating the old stale records.
        if removed_result:
            save_json(str(result_file), filtered_results)
            self.logger.info(
                "Removed %d stale record(s) for re-processing from results", removed_result
            )

        if removed_permid:
            save_json(str(permid_file), filtered_permid)
            self.logger.info(
                "Removed %d stale record(s) for re-processing from permid cache", removed_permid
            )

        return stale_entities, len(filtered_results.keys())

    def _get_stale_entities(self) -> tuple[dict[str, dict], list[datetime]]:
        """Get stale entities based on threshold.

        Returns:
            Tuple of (set of stale entity names and identifiers tuples, list of stale dates)
        """
        if self.threshold_days is None:
            return {}, []

        threshold_date = datetime.now() - timedelta(days=self.threshold_days)
        stale_entries: dict[str, dict] = {}
        stale_dates: list[datetime] = []

        for permid, company_info in self.result_data.items():
            time_str = company_info.get("result", {}).get("last_processed")
            if not time_str:
                continue

            time_dt = datetime.strptime(time_str, "%Y%m%dT%H%M%S")
            if time_dt < threshold_date:
                stale_entries[permid] = company_info
                stale_dates.append(time_dt)

        return stale_entries, stale_dates

    def _remove_stale_records(self, stale_entities: dict[str, dict]) -> tuple[dict[str, dict], int]:
        """Remove records for stale entities so they can be re-processed.

        Args:
            stale_entities: Set of (entity_name, identifier) tuples to remove

        Returns:
            Filtered list without stale entity records
            Number of removed entries
        """
        if not stale_entities:
            return self.result_data

        filtered_results = {
            permid: company_info
            for permid, company_info in self.result_data.items()
            if permid not in stale_entities
        }

        removed_count = len(self.result_data.keys()) - len(filtered_results.keys())
        return filtered_results, removed_count

    def _filter_stale_permid_cache(
        self, permid_file: Path, stale_entities: dict[str, dict]
    ) -> tuple[dict[str, dict | list], int]:
        """Select the permid_file entries that correspond to stale results.

        Walks the permid cache and matches each entry against the stale entities
        by (name, identifier_type, identifier), and removes the stale entry
        from the permid cache.

        Args:
            permid_file: Path to the permid cache JSON
            stale_entities: Mapping of permid_url -> stale result record

        Returns:
            Tuple of (filter_permid, removed_count) where filter_permid
            includes non-stale entries and removed_count is the number
            of removed entries.
        """
        permid_cache = load_json(permid_file, return_type="dict")

        filter_permid = {}
        for permid_key, permid_value in permid_cache.items():
            cache_name, cache_id_type, cache_id = parse_permid_cache_key(permid_key)

            is_stale = any(
                cache_name == ci["identifier"]["name"]
                and cache_id_type == ci["identifier"]["identifier_type"]
                and cache_id == ci["identifier"]["identifier"]
                for ci in stale_entities.values()
            )

            if not is_stale:
                filter_permid[permid_key] = permid_value

        removed_count = len(permid_cache.keys()) - len(filter_permid.keys())
        return filter_permid, removed_count
