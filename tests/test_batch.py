#!/usr/bin/env python3
"""Unit tests for idi_company_info.common.batch.BatchProcessing."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from idi_company_info.batch import BatchProcessing

_PERMID_URL = "https://permid.org/1-test"
_PERMID_URL_2 = "https://permid.org/1-test2"


def make_record(
    entity_name: str,
    identifier: str,
    days_ago: int = 0,
    permid_url: str = _PERMID_URL,
) -> dict:
    """Build a result_data entry for a single permid_url."""
    ts = datetime.now() - timedelta(days=days_ago)
    return {
        "search": {"permid_url": permid_url},
        "result": {"last_processed": ts.strftime("%Y%m%dT%H%M%S")},
        "identifier": {
            "name": entity_name,
            "identifier": identifier,
            "identifier_type": "cik",
        },
    }


def make_permid_cache(entity_name: str, identifier: str, permid_url: str = _PERMID_URL) -> dict:
    """Build a minimal permid_file dict matching a single entity/identifier."""
    key = f"{entity_name}_cik_{identifier}"
    return {key: {"search": {"Name": entity_name, "LocalID": f"cik_{identifier}"}, "result": [permid_url]}}


def write_files(tmp_path: Path, result_data: dict, permid_data: dict) -> tuple[Path, Path]:
    """Write result and permid dicts to temp files; return their paths."""
    result_file = tmp_path / "result.json"
    permid_file = tmp_path / "permid.json"
    result_file.write_text(json.dumps(result_data))
    permid_file.write_text(json.dumps(permid_data))
    return result_file, permid_file


class TestGetUnprocessedEntities:
    """Tests for BatchProcessing.get_unprocessed_entities."""

    def test_returns_all_entities_when_none_processed(self):
        """When result_data is empty, all entities are returned."""
        bp = BatchProcessing(result_data={})
        entity_data = {"Firm A": ["id1", "id2"]}
        result = bp.get_unprocessed_entities(entity_data)
        assert result == {"Firm A": ["id1", "id2"]}

    def test_excludes_already_processed_entities(self):
        """Entities already in result_data are excluded."""
        result_data = {_PERMID_URL: make_record("Firm A", "id1")}
        bp = BatchProcessing(result_data=result_data)
        entity_data = {"Firm A": ["id1", "id2"]}
        result = bp.get_unprocessed_entities(entity_data)
        assert result == {"Firm A": ["id2"]}

    def test_returns_empty_when_all_processed(self):
        """Returns empty dict when all entities have been processed."""
        result_data = {
            _PERMID_URL: make_record("Firm A", "id1"),
            _PERMID_URL_2: make_record("Firm A", "id2"),
        }
        bp = BatchProcessing(result_data=result_data)
        entity_data = {"Firm A": ["id1", "id2"]}
        result = bp.get_unprocessed_entities(entity_data)
        assert result == {}

    def test_multiple_entities_partial_processing(self):
        """Handles multiple entities where some are partially processed."""
        result_data = {_PERMID_URL: make_record("Firm A", "id1")}
        bp = BatchProcessing(result_data=result_data)
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
        bp = BatchProcessing(result_data={}, failure_registry=failure_registry)
        entity_data = {"Firm A": ["id1", "id2"]}
        result = bp.get_unprocessed_entities(entity_data)
        assert "id1" not in result.get("Firm A", [])
        assert "id2" in result.get("Firm A", [])

    def test_no_failure_registry_returns_all_unprocessed(self):
        """When no failure registry is set, all unprocessed entities are returned."""
        bp = BatchProcessing(result_data={}, failure_registry=None)
        entity_data = {"Firm A": ["id1"]}
        result = bp.get_unprocessed_entities(entity_data)
        assert result == {"Firm A": ["id1"]}


class TestFilterStaleEntities:
    """Tests for BatchProcessing.filter_stale_entities."""

    def test_returns_empty_stale_when_no_stale(self, tmp_path):
        """When no records are stale, returns empty stale dict and full count."""
        result_data = {_PERMID_URL: make_record("Firm A", "id1", days_ago=1)}
        permid_data = make_permid_cache("Firm A", "id1")
        result_file, permid_file = write_files(tmp_path, result_data, permid_data)
        bp = BatchProcessing(result_data=result_data, threshold_days=30)
        stale, count = bp.filter_stale_entities(result_file, permid_file)
        assert stale == {}
        assert count == 1

    def test_identifies_stale_records(self, tmp_path):
        """Records older than threshold_days are identified as stale."""
        result_data = {_PERMID_URL: make_record("Firm A", "id1", days_ago=60)}
        permid_data = make_permid_cache("Firm A", "id1")
        result_file, permid_file = write_files(tmp_path, result_data, permid_data)
        bp = BatchProcessing(result_data=result_data, threshold_days=30)
        stale, _ = bp.filter_stale_entities(result_file, permid_file)
        assert _PERMID_URL in stale
        assert stale[_PERMID_URL]["identifier"]["name"] == "Firm A"

    def test_stale_records_pruned_from_result_file(self, tmp_path):
        """Stale records are removed from the saved result file."""
        stale_data = make_record("Firm A", "id1", days_ago=60, permid_url=_PERMID_URL)
        fresh_data = make_record("Firm B", "id2", days_ago=1, permid_url=_PERMID_URL_2)
        result_data = {_PERMID_URL: stale_data, _PERMID_URL_2: fresh_data}
        permid_data = {
            **make_permid_cache("Firm A", "id1", _PERMID_URL),
            **make_permid_cache("Firm B", "id2", _PERMID_URL_2),
        }
        result_file, permid_file = write_files(tmp_path, result_data, permid_data)
        bp = BatchProcessing(result_data=result_data, threshold_days=30)
        stale, count = bp.filter_stale_entities(result_file, permid_file)
        saved = json.loads(result_file.read_text())
        assert _PERMID_URL_2 in saved
        assert _PERMID_URL not in saved
        assert count == 1

    def test_returns_all_when_threshold_is_none(self, tmp_path):
        """When threshold_days is None, no staleness check is done."""
        result_data = {_PERMID_URL: make_record("Firm A", "id1", days_ago=999)}
        permid_data = make_permid_cache("Firm A", "id1")
        result_file, permid_file = write_files(tmp_path, result_data, permid_data)
        bp = BatchProcessing(result_data=result_data, threshold_days=None)
        stale, count = bp.filter_stale_entities(result_file, permid_file)
        assert stale == {}
        assert count == 1

    def test_stale_permid_cache_pruned(self, tmp_path):
        """Permid cache entries for stale entities are removed from the saved permid file."""
        result_data = {_PERMID_URL: make_record("Firm A", "id1", days_ago=60)}
        permid_data = make_permid_cache("Firm A", "id1")
        result_file, permid_file = write_files(tmp_path, result_data, permid_data)
        bp = BatchProcessing(result_data=result_data, threshold_days=30)
        bp.filter_stale_entities(result_file, permid_file)
        saved_permid = json.loads(permid_file.read_text())
        assert saved_permid == {}

    def test_empty_result_data_returns_empty_stale(self, tmp_path):
        """Empty result_data returns empty stale dict."""
        result_file, permid_file = write_files(tmp_path, {}, {})
        bp = BatchProcessing(result_data={}, threshold_days=30)
        stale, count = bp.filter_stale_entities(result_file, permid_file)
        assert stale == {}
        assert count == 0
