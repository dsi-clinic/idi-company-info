"""Batch processing utilities for tracking and managing batch operations."""

# Standard library imports
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

# Application imports
from ftm2j.common.logs import get_logger

if TYPE_CHECKING:
    from ftm2j.common.failures import FailureRegistry


class BatchProcessing:

    def __init__(
        self,
        result_data: list[dict[str, Any]],
        threshold_days: int = 30,
        failure_registry: "FailureRegistry | None" = None,
    ):
        """Initialize the BatchProcessing.

        Args:
            result_data: The result data.
            threshold_days: The threshold days.
            failure_registry: Optional registry of permanent failures to exclude from retries.
        """
        self.result_data = result_data
        self.threshold_days = threshold_days
        self.failure_registry = failure_registry
        self.logger = get_logger(__name__)

    def get_unprocessed_entities(self, entity_data: dict[str, list[Any]]) -> dict[str, Any]:
        """
        Get list of entities that haven't been processed yet.

        Args:
            entity_data: Dict of entity_name -> list of identifiers (strings)

        Returns:
            Dict of entity_name -> list of identifiers
        """
        processed_entities = set([
            (entity["original_entity_name"], entity["identifier"])
            for entity in self.result_data
        ])

        new_entities = [
            (entity_name, identifier)
            for entity_name, identifiers in entity_data.items()
            for identifier in identifiers
        ]

        unprocessed_entities = [
            (entity_name, identifier)
            for entity_name, identifier in new_entities
            if (entity_name, identifier) not in processed_entities
        ]

        # Exclude entries in do-not-retry registry
        unprocessed_entities = self._remove_failed_entities(unprocessed_entities)

        self.logger.info("Total new entities: %s", len(new_entities))
        self.logger.info("Already processed entities: %s", len(processed_entities))
        self.logger.info("Remaining to process: %s", len(unprocessed_entities))

        unprocessed_identifiers = self._get_identifier_dict(unprocessed_entities)
        return unprocessed_identifiers

    def _remove_failed_entities(self, entities: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """
        Remove entities that are in the do-not-retry registry from the list.

        Args:
            entities: List of (entity_name, identifier) tuples.

        Returns:
            List of (entity_name, identifier) tuples.
        """
        if not self.failure_registry:
            return entities

        before_count = len(entities)
        result = [
            (entity_name, identifier)
            for entity_name, identifier in entities
            if not self.failure_registry.contains(entity_name, identifier)
        ]

        excluded = before_count - len(result)
        if excluded > 0:
            self.logger.info("Excluded %d entities from do-not-retry registry", excluded)

        return result

    def _get_identifier_dict(self, entities: list[tuple[str, str]]) -> dict[str, list[str]]:
        """
        Get identifier dictionary from entities.

        Args:
            entities: List of (entity_name, identifier) tuples.

        Returns:
            Identifier dictionary {entity_name: [identifier, ...]}
        """
        identifiers: dict[str, list[str]] = {}
        for entity_name, identifier in entities:
            identifiers.setdefault(entity_name, []).append(identifier)
        return identifiers

    def filter_stale_entities(self) -> tuple[list[dict[str, Any]], set[str]]:
        """
        Identify and process stale entities based on threshold.

        Returns:
            Tuple of (filtered_results, stale_entities_set)
        """
        if self.threshold_days is None:
            return self.result_data, set()

        self.logger.info("Checking for entities not updated in last %d days", self.threshold_days)
        stale_entities, stale_dates = self._get_stale_entities()

        if not stale_entities:
            return self.result_data, set()

        self.logger.info("Found %d stale entity(ies) to re-process", len(stale_entities))

        # Remove stale entity records so they can be re-processed
        filtered_results = self._remove_stale_records(stale_entities)

        # Parse back to identifiers dictionary
        filtered_identifiers = {}
        for record in filtered_results:
            filtered_identifiers.setdefault(record["original_entity_name"], []).append(record["identifier"])
        stale_identifiers = self._get_identifier_dict(stale_entities)

        return filtered_identifiers, stale_identifiers

    def _get_stale_entities(self) -> tuple[set[tuple[str, str]], list[datetime]]:
        """
        Get stale entities based on threshold.

        Returns:
            Tuple of (set of stale entity names and identifiers tuples, list of stale dates)
        """
        if self.threshold_days is None:
            return set(), []

        threshold_date = datetime.now() - timedelta(days=self.threshold_days)
        old_entities: set[tuple[str, str]] = set()
        new_entities: set[tuple[str, str]] = set()
        stale_dates: list[str] = []

        for company_info in self.result_data:
            try:
                time_str = company_info.get("last_processed")
                time_dt = datetime.strptime(time_str, "%Y%m%dT%H%M%S")
            except ValueError:
                continue
            if time_dt < threshold_date:
                old_entities.add((company_info["original_entity_name"], company_info["identifier"]))
                stale_dates.append(time_dt)
            else:
                new_entities.add((company_info["original_entity_name"], company_info["identifier"]))

        stale_entries = old_entities - new_entities
        self.logger.info("Located %s stale entities", len(stale_entries))
        return stale_entries, stale_dates

    def _remove_stale_records(self, stale_entities: set[str]) -> list[dict[str, Any]]:
        """
        Remove records for stale entities so they can be re-processed.

        Args:
            stale_entities: Set of entity names to remove

        Returns:
            Filtered list without stale entity records
        """
        if not stale_entities:
            return self.result_data

        filtered_results = [
            record for record in self.result_data
            if record and (record["original_entity_name"], record["identifier"]) not in stale_entities
        ]

        removed_count = len(self.result_data) - len(filtered_results)
        self.logger.info("Removed %d stale record(s) for re-processing", removed_count)

        return filtered_results
