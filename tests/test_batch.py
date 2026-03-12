#!/usr/bin/env python3
"""
Unit tests for idi_company_info.common.batch.BatchProcessing
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from idi_company_info.common.batch import BatchProcessing


def make_record(entity_name, identifier, days_ago=0):
    """Helper to create a result_data record with a last_processed timestamp."""
    ts = datetime.now() - timedelta(days=days_ago)
    return {
        "original_entity_name": entity_name,
        "identifier": identifier,
        "last_processed": ts.strftime("%Y%m%dT%H%M%S"),
    }


class TestGetUnprocessedEntities:
    """Tests for BatchProcessing.get_unprocessed_entities."""

    def test_returns_all_entities_when_none_processed(self):
        """When result_data is empty, all entities are returned."""
        bp = BatchProcessing(result_data=[])
        entity_data = {"Firm A": ["id1", "id2"]}
        result = bp.get_unprocessed_entities(entity_data)
        assert result == {"Firm A": ["id1", "id2"]}

    def test_excludes_already_processed_entities(self):
        """Entities already in result_data are excluded."""
        existing = [make_record("Firm A", "id1")]
        bp = BatchProcessing(result_data=existing)
        entity_data = {"Firm A": ["id1", "id2"]}
        result = bp.get_unprocessed_entities(entity_data)
        assert result == {"Firm A": ["id2"]}

    def test_returns_empty_when_all_processed(self):
        """Returns empty dict when all entities have been processed."""
        existing = [make_record("Firm A", "id1"), make_record("Firm A", "id2")]
        bp = BatchProcessing(result_data=existing)
        entity_data = {"Firm A": ["id1", "id2"]}
        result = bp.get_unprocessed_entities(entity_data)
        assert result == {}

    def test_multiple_entities_partial_processing(self):
        """Handles multiple entities where some are partially processed."""
        existing = [make_record("Firm A", "id1")]
        bp = BatchProcessing(result_data=existing)
        entity_data = {
            "Firm A": ["id1", "id2"],
            "Firm B": ["id3"],
        }
        result = bp.get_unprocessed_entities(entity_data)
        assert result == {"Firm A": ["id2"], "Firm B": ["id3"]}

    def test_excludes_failure_registry_entries(self):
        """Entities in the failure registry are excluded."""
        failure_registry = MagicMock()
        failure_registry.__contains__ = lambda self, key: key == ("Firm A", "id1")

        bp = BatchProcessing(result_data=[], failure_registry=failure_registry)
        entity_data = {"Firm A": ["id1", "id2"]}
        result = bp.get_unprocessed_entities(entity_data)
        assert "id1" not in result.get("Firm A", [])
        assert "id2" in result.get("Firm A", [])

    def test_no_failure_registry_returns_all_unprocessed(self):
        """When no failure registry is set, all unprocessed entities are returned."""
        bp = BatchProcessing(result_data=[], failure_registry=None)
        entity_data = {"Firm A": ["id1"]}
        result = bp.get_unprocessed_entities(entity_data)
        assert result == {"Firm A": ["id1"]}


class TestFilterStaleEntities:
    """Tests for BatchProcessing.filter_stale_entities."""

    def test_returns_all_results_and_empty_stale_when_no_stale(self):
        """When no records are stale, returns original data and empty stale dict."""
        records = [make_record("Firm A", "id1", days_ago=1)]
        bp = BatchProcessing(result_data=records, threshold_days=30)
        filtered, stale = bp.filter_stale_entities()
        assert stale == {}

    def test_identifies_stale_records(self):
        """Records older than threshold_days are identified as stale."""
        records = [make_record("Firm A", "id1", days_ago=60)]
        bp = BatchProcessing(result_data=records, threshold_days=30)
        filtered, stale = bp.filter_stale_entities()
        assert "Firm A" in stale
        assert "id1" in stale["Firm A"]

    def test_stale_records_removed_from_filtered_results(self):
        """Stale records are removed from the filtered results list."""
        stale_record = make_record("Firm A", "id1", days_ago=60)
        fresh_record = make_record("Firm B", "id2", days_ago=1)
        bp = BatchProcessing(result_data=[stale_record, fresh_record], threshold_days=30)
        filtered, stale = bp.filter_stale_entities()
        entity_names = [r["original_entity_name"] for r in filtered]
        assert "Firm B" in entity_names
        assert "Firm A" not in entity_names

    def test_returns_all_when_threshold_is_none(self):
        """When threshold_days is None, no staleness check is done."""
        records = [make_record("Firm A", "id1", days_ago=999)]
        bp = BatchProcessing(result_data=records, threshold_days=None)
        filtered, stale = bp.filter_stale_entities()
        assert stale == {}
        assert filtered == records

    def test_entity_with_both_fresh_and_stale_records(self):
        """An entity with both fresh and old records is not treated as stale."""
        old_record = make_record("Firm A", "id1", days_ago=60)
        fresh_record = make_record("Firm A", "id1", days_ago=1)  # same entity+identifier, fresh
        bp = BatchProcessing(result_data=[old_record, fresh_record], threshold_days=30)
        filtered, stale = bp.filter_stale_entities()
        # Because there's a fresh record for the same (entity, identifier), it's not stale
        assert "Firm A" not in stale

    def test_empty_result_data_returns_empty_stale(self):
        """Empty result_data returns empty filtered and stale."""
        bp = BatchProcessing(result_data=[], threshold_days=30)
        filtered, stale = bp.filter_stale_entities()
        assert stale == {}
