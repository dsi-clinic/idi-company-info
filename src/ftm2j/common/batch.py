"""Batch processing utilities for tracking and managing batch operations."""

# Standard library imports
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any
from dataclasses import asdict

# Application imports
from ftm2j.common.logs import get_logger
from ftm2j.common.storage import load_json, save_json

class BatchProcessing:

    def __init__(self, batch_file: pathlib.Path, threshold_days: int = 30):
        self.batch_file = pathlib.Path(batch_file)
        self.logger = get_logger(__name__)
        self.batch_tracking = self.load_batch_tracking()
        self.threshold_days = threshold_days

    def load_batch_tracking(self) -> dict:
        """
        Load batch tracking data or create new tracking dict.

        Returns:
            Dictionary with batch tracking data
        """
        if self.batch_file.exists():
            self.logger.info(f"Loading existing batch tracking from: {self.batch_file}")
            return load_json(self.batch_file)
        else:
            self.logger.info("Creating new batch tracking file")
            return {}

    def get_unprocessed_entities(self, entity_data: list[dict[str, Any]]) -> list[str]:
        """
        Get list of entities that haven't been processed yet.

        Args:
            entity_data: List of entity data

        Returns:
            List of unprocessed entity names
        """
        processed_entities: set[str] = set()

        for batch_info in self.batch_tracking.values():
            entities = batch_info.get("processed_entities", [])
            processed_entities.update(e for e in entities if e is not None)

        all_entities: set[str] = set(entity_data.keys())
        unprocessed: list[str] = list(all_entities - processed_entities)

        self.logger.info("Total entities: %s", len(all_entities))
        self.logger.info("Already processed: %s", len(processed_entities))
        self.logger.info("Remaining to process: %s", len(unprocessed))

        unprocesed_identifier_data = {entity: entity_data[entity] for entity in unprocessed}
        return unprocesed_identifier_data

    def filter_stale_entities(self, existing_results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
        """
        Identify and process stale entities based on threshold.

        Args:
            existing_results: List of existing results

        Returns:
            Tuple of (filtered_results, stale_entities_set)
        """
        if self.threshold_days is None:
            return existing_results, set()

        self.logger.info(f"Checking for entities not updated in last {self.threshold_days} days")
        stale_entities, stale_dates = self._get_stale_entities(existing_results)

        if not stale_entities:
            return existing_results, set()

        self.logger.info(f"Found {len(stale_entities)} stale entity(ies) to re-process")

        # Remove stale entity records so they can be re-processed
        filtered_results = self._remove_stale_records(existing_results, stale_entities)

        # Remove stale entities from batch tracking
        self._remove_stale_from_batch_tracking(stale_dates)

        return filtered_results, stale_entities

    def _get_stale_entities(self, existing_results: list[dict[str, Any]]) -> tuple[set[str], list[datetime]]:
        """
        Get stale entities based on threshold.

        Args:
            existing_results: List of company info records

        Returns:
            Tuple of (set of stale entity names, list of stale dates)
        """
        if self.threshold_days is None:
            return set(), []

        threshold_date = datetime.now() - timedelta(days=self.threshold_days)
        old_entities: set[str] = set()
        new_entities: set[str] = set()
        stale_dates: list[str] = []

        for batch_key, batch_info in self.batch_tracking.items():
            try:
                batch_dt = datetime.strptime(batch_key, "%Y%m%dT%H%M%S")
            except ValueError:
                continue
            entities = batch_info.get("processed_entities", [])
            if batch_dt < threshold_date:
                old_entities.update(entities)
                stale_dates.append(batch_key)
            else:
                new_entities.update(entities)

        stale_entries = old_entities - new_entities
        stale_entries_data = {entity: {"identifiers": existing_results[entity]["identifiers"]} for entity in stale_entries}
        self.logger.info("Located %s stale entities", len(stale_entries))
        return stale_entries_data, stale_dates

    def _remove_stale_records(self, existing_results: list[dict[str, Any]], stale_entities: set[str]) -> list[dict[str, Any]]:
        """
        Remove records for stale entities so they can be re-processed.

        Args:
            existing_results: List of company info records
            stale_entities: Set of entity names to remove

        Returns:
            Filtered list without stale entity records
        """
        if not stale_entities:
            return existing_results

        stale_keys = stale_entities.keys()
        filtered_results = [
            record for record in existing_results
            if record and record.get("original_entity_name") not in stale_keys
        ]

        removed_count = len(existing_results) - len(filtered_results)
        self.logger.info(f"Removed {removed_count} stale record(s) for re-processing")

        return filtered_results

    def _remove_stale_from_batch_tracking(self, stale_dates: list[datetime]) -> None:
        """
        Remove stale entities from batch tracking so they're treated as unprocessed.

        Args:
            stale_dates: List of stale dates to remove
        """
        for key in stale_dates:
            del self.batch_tracking[key]

    def update_batch_tracking(self, processed_entities: list[str], batch_stats: dict):
        """Update batch tracking with processed entities.

        Writes results to the batch tracking file.
        """
        batch_timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
        self.batch_tracking[batch_timestamp] = {
            "processed_entities": processed_entities,
            "batch_size": len(processed_entities),
            "stats": asdict(batch_stats)
        }
        self.save_batch_tracking()

    def save_batch_tracking(self):
        """Save batch tracking data to file."""
        save_json(str(self.batch_file), self.batch_tracking)
        self.logger.info(f"Saved batch tracking to: {self.batch_file}")