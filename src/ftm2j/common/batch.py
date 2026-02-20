"""Batch processing utilities for tracking and managing batch operations."""

# Standard library imports
import pathlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

# Application imports
from ftm2j.common.logs import get_logger
from ftm2j.common.storage import load_json, save_json

class BatchProcessing:

    def __init__(self, batch_file: pathlib.Path, threshold_days: int = 30):
        self.batch_file = batch_file
        self.batch_tracking = self.load_batch_tracking(batch_file)
        self.logger = get_logger(__name__)
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

    def save_batch_tracking(self):
        """Save batch tracking data to file."""
        save_json(self.batch_file, self.batch_tracking)
        self.logger.info(f"Saved batch tracking to: {self.batch_file}")

    def get_unprocessed_entities(self, entity_data: dict[str, Any]) -> list[str]:
        """
        Get list of entities that haven't been processed yet.

        Args:
            entity_data: Dictionary mapping entity names to their data

        Returns:
            List of unprocessed entity names
        """
        processed_entities = set()

        # Collect all processed entities from all batches
        for batch_info in self.batch_tracking.values():
            entities = batch_info.get("processed_entities", [])
            processed_entities.update(entities)

        # Find unprocessed entities
        all_entities = set(entity_data.keys())
        unprocessed = list(all_entities - processed_entities)

        self.logger.info(f"Total entities: {len(all_entities)}")
        self.logger.info(f"Already processed: {len(processed_entities)}")
        self.logger.info(f"Remaining to process: {len(unprocessed)}")

        return unprocessed

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
        stale_entities = self._get_stale_entities(existing_results)

        if not stale_entities:
            return existing_results, set()

        self.logger.info(f"Found {len(stale_entities)} stale investor(s) to re-process")

        # Remove stale investor records so they can be re-processed
        filtered_results = self._remove_stale_records(existing_results, stale_entities)

        # Remove stale investors from batch tracking
        self._remove_stale_from_batch_tracking(stale_entities)

        return filtered_results, stale_entities

    def _get_stale_entities(self, existing_results: list[dict[str, Any]]) -> set[str]:
        """
        Get stale entities based on threshold.

        Args:
            existing_results: List of company info records

        Returns:
            Set of stale entity names
        """
        if self.threshold_days is None:
            return set()

        # Group by entity
        entity_records = defaultdict(list)
        for record in existing_results:
            if record and (name := record.get("original_entity_name")):
                entity_records[name].append(record)

        threshold_date = datetime.now() - timedelta(days=self.threshold_days)
        stale_entities = set()

        for entity_name, records in entity_records.items():
            timestamps = [self._parse_last_processed(r, entity_name) for r in records]
            if None in timestamps:
                most_recent = None
            else:
                most_recent = max(timestamps)

            if most_recent is None or most_recent < threshold_date:
                stale_entities.add(entity_name)
                msg = f"{(datetime.now() - most_recent).days} days old" if most_recent else "no timestamp"
                self.logger.info(f"  Marking {entity_name} as stale ({msg})")

        return stale_entities

    def _parse_last_processed(self, record: dict, entity_name: str) -> datetime | None:
        """Parse last_processed timestamp; return None if missing or invalid."""
        ts_str = record.get("last_processed")
        if not ts_str:
            return None
        try:
            return datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            self.logger.warning(f"Invalid timestamp for {entity_name}: {ts_str}")
            return None

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

        filtered_results = [
            record for record in existing_results
            if record and record.get("original_entity_name") not in stale_entities
        ]

        removed_count = len(existing_results) - len(filtered_results)
        self.logger.info(f"Removed {removed_count} stale record(s) for re-processing")

        return filtered_results

    def _remove_stale_from_batch_tracking(self, stale_entities: set[str]) -> None:
        """
        Remove stale entities from batch tracking so they're treated as unprocessed.

        Args:
            stale_entities: Set of entity names to remove
        """
        for batch_data in self.batch_tracking.values():
            processed_list = batch_data.get("processed_entities", [])
            batch_data["processed_entities"] = [
                ent for ent in processed_list if ent not in stale_entities
            ]

    def update_batch_tracking(self, processed_entities: list[str], batch_stats: dict):
        """Update batch tracking with processed entities.

        Writes results to the batch tracking file.
        """
        batch_timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
        self.batch_tracking[batch_timestamp] = {
            "processed_entities": processed_entities,
            "batch_size": len(processed_entities),
            "stats": batch_stats
        }
        self.save_batch_tracking()