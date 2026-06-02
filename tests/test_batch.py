#!/usr/bin/env python3
"""Unit tests for idi_company_info.batch.BatchProcessing."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from idi_company_info.batch import BatchProcessing, find_cusip_collisions

_PERMID_URL = "https://permid.org/1-test"
_PERMID_URL_2 = "https://permid.org/1-test2"


def make_result(permid_url: str, days_ago: int = 0) -> dict:
    """Build a result_file entry (keyed by permid_url) — no identifiers block."""
    ts = datetime.now() - timedelta(days=days_ago)
    return {
        "search": {"permid_url": permid_url},
        "result": {"last_processed": ts.strftime("%Y%m%dT%H%M%S")},
    }


def make_permid(
    entity_name: str, identifier: str, permid_url: str, identifier_type: str = "cik"
) -> dict:
    """Build a one-entry permid_file dict for a single (name, identifier) -> permid_url."""
    key = f"{entity_name}_{identifier_type}_{identifier}"
    return {
        key: {
            "search": {
                "Name": entity_name,
                "LocalID": f"{identifier_type}_{identifier}",
                "Standard Identifier": f"Cik:{identifier}",
            },
            "result": [permid_url],
        }
    }


def write_files(tmp_path: Path, result_data: dict, permid_data: dict) -> tuple[Path, Path]:
    """Write result and permid dicts to temp files; return their paths."""
    result_file = tmp_path / "result.json"
    permid_file = tmp_path / "permid.json"
    result_file.write_text(json.dumps(result_data))
    permid_file.write_text(json.dumps(permid_data))
    return result_file, permid_file


class TestGetUnprocessedEntities:
    """Tests for BatchProcessing.get_unprocessed_entities.

    A (name, identifier) is processed iff its permid_cache_key is in permid_data AND every
    permid_url it resolved to is present in result_data.
    """

    def test_returns_all_entities_when_none_processed(self):
        """Empty caches → all input rows are unprocessed."""
        bp = BatchProcessing(result_data={}, permid_data={}, identifier_type="cik")
        result = bp.get_unprocessed_entities({"Firm A": ["id1", "id2"]})
        assert result == {"Firm A": ["id1", "id2"]}

    def test_excludes_already_processed_entities(self):
        """A row whose permid is resolved and result present is excluded."""
        permid_data = make_permid("Firm A", "id1", _PERMID_URL)
        result_data = {_PERMID_URL: make_result(_PERMID_URL)}
        bp = BatchProcessing(
            result_data=result_data, permid_data=permid_data, identifier_type="cik"
        )
        result = bp.get_unprocessed_entities({"Firm A": ["id1", "id2"]})
        assert result == {"Firm A": ["id2"]}

    def test_permid_resolved_but_result_missing_is_unprocessed(self):
        """Permid resolved but its url not yet in result_data → still unprocessed."""
        permid_data = make_permid("Firm A", "id1", _PERMID_URL)
        bp = BatchProcessing(result_data={}, permid_data=permid_data, identifier_type="cik")
        result = bp.get_unprocessed_entities({"Firm A": ["id1"]})
        assert result == {"Firm A": ["id1"]}

    def test_returns_empty_when_all_processed(self):
        """All rows resolved + results present → nothing to process."""
        permid_data = {
            **make_permid("Firm A", "id1", _PERMID_URL),
            **make_permid("Firm A", "id2", _PERMID_URL_2),
        }
        result_data = {
            _PERMID_URL: make_result(_PERMID_URL),
            _PERMID_URL_2: make_result(_PERMID_URL_2),
        }
        bp = BatchProcessing(
            result_data=result_data, permid_data=permid_data, identifier_type="cik"
        )
        result = bp.get_unprocessed_entities({"Firm A": ["id1", "id2"]})
        assert result == {}

    def test_multiple_entities_partial_processing(self):
        """Mix of processed and new rows across entities."""
        permid_data = make_permid("Firm A", "id1", _PERMID_URL)
        result_data = {_PERMID_URL: make_result(_PERMID_URL)}
        bp = BatchProcessing(
            result_data=result_data, permid_data=permid_data, identifier_type="cik"
        )
        result = bp.get_unprocessed_entities({"Firm A": ["id1", "id2"], "Firm B": ["id3"]})
        assert result == {"Firm A": ["id2"], "Firm B": ["id3"]}

    def test_excludes_failure_registry_entries(self):
        """Rows in the do-not-retry registry (keyed by prefixed LocalID) are excluded."""
        failure_registry = MagicMock()
        failure_registry.__contains__ = lambda self, key: key == ("Firm A", "cik_id1")
        bp = BatchProcessing(
            result_data={}, permid_data={}, identifier_type="cik", failure_registry=failure_registry
        )
        result = bp.get_unprocessed_entities({"Firm A": ["id1", "id2"]})
        assert "id1" not in result.get("Firm A", [])
        assert "id2" in result.get("Firm A", [])

    def test_no_failure_registry_returns_all_unprocessed(self):
        """No registry → all unprocessed rows are returned."""
        bp = BatchProcessing(
            result_data={}, permid_data={}, identifier_type="cik", failure_registry=None
        )
        result = bp.get_unprocessed_entities({"Firm A": ["id1"]})
        assert result == {"Firm A": ["id1"]}


class TestFilterStaleEntities:
    """Tests for BatchProcessing.filter_stale_entities (prunes both caches in sync)."""

    def test_returns_count_when_no_stale(self, tmp_path):
        """No stale results → both caches untouched, returns remaining count."""
        result_data = {_PERMID_URL: make_result(_PERMID_URL, days_ago=1)}
        permid_data = make_permid("Firm A", "id1", _PERMID_URL)
        result_file, permid_file = write_files(tmp_path, result_data, permid_data)
        bp = BatchProcessing(result_data, permid_data, "cik", threshold_days=30)
        remaining = bp.filter_stale_entities(result_file, permid_file)
        assert remaining == 1
        assert _PERMID_URL in bp.result_data

    def test_prunes_stale_result_and_permid_key(self, tmp_path):
        """A stale result is dropped, and its reverse-indexed permid key is dropped too."""
        result_data = {_PERMID_URL: make_result(_PERMID_URL, days_ago=60)}
        permid_data = make_permid("Firm A", "id1", _PERMID_URL)
        result_file, permid_file = write_files(tmp_path, result_data, permid_data)
        bp = BatchProcessing(result_data, permid_data, "cik", threshold_days=30)
        remaining = bp.filter_stale_entities(result_file, permid_file)
        assert remaining == 0
        assert bp.result_data == {}
        assert bp.permid_data == {}
        # Both files persisted pruned.
        assert json.loads(result_file.read_text()) == {}
        assert json.loads(permid_file.read_text()) == {}

    def test_keeps_fresh_prunes_only_stale(self, tmp_path):
        """Only the stale entry and its key are removed; fresh ones remain."""
        result_data = {
            _PERMID_URL: make_result(_PERMID_URL, days_ago=60),
            _PERMID_URL_2: make_result(_PERMID_URL_2, days_ago=1),
        }
        permid_data = {
            **make_permid("Firm A", "id1", _PERMID_URL),
            **make_permid("Firm B", "id2", _PERMID_URL_2),
        }
        result_file, permid_file = write_files(tmp_path, result_data, permid_data)
        bp = BatchProcessing(result_data, permid_data, "cik", threshold_days=30)
        remaining = bp.filter_stale_entities(result_file, permid_file)
        assert remaining == 1
        assert _PERMID_URL_2 in bp.result_data
        assert _PERMID_URL not in bp.result_data
        assert "Firm B_cik_id2" in bp.permid_data
        assert "Firm A_cik_id1" not in bp.permid_data

    def test_returns_all_when_threshold_is_none(self, tmp_path):
        """threshold_days None disables staleness."""
        result_data = {_PERMID_URL: make_result(_PERMID_URL, days_ago=999)}
        permid_data = make_permid("Firm A", "id1", _PERMID_URL)
        result_file, permid_file = write_files(tmp_path, result_data, permid_data)
        bp = BatchProcessing(result_data, permid_data, "cik", threshold_days=None)
        remaining = bp.filter_stale_entities(result_file, permid_file)
        assert remaining == 1
        assert _PERMID_URL in bp.result_data

    def test_prunes_all_keys_sharing_a_stale_url(self, tmp_path):
        """When several cache keys share one stale permid_url, ALL of them are pruned."""
        # Two CUSIPs (share classes) resolving to the same permid_url.
        permid_data = {
            **make_permid("ALPHA A", "id1", _PERMID_URL),
            **make_permid("ALPHA B", "id2", _PERMID_URL),
        }
        result_data = {_PERMID_URL: make_result(_PERMID_URL, days_ago=60)}
        result_file, permid_file = write_files(tmp_path, result_data, permid_data)
        bp = BatchProcessing(result_data, permid_data, "cik", threshold_days=30)
        bp.filter_stale_entities(result_file, permid_file)
        # Both keys pointing at the stale url are gone — not just one.
        assert bp.permid_data == {}
        assert json.loads(permid_file.read_text()) == {}

    def test_orphan_stale_url_is_noop_on_permid(self, tmp_path):
        """A stale result whose url isn't in any permid entry prunes the result only."""
        result_data = {_PERMID_URL: make_result(_PERMID_URL, days_ago=60)}
        permid_data = {}  # no permid entry references the url
        result_file, permid_file = write_files(tmp_path, result_data, permid_data)
        bp = BatchProcessing(result_data, permid_data, "cik", threshold_days=30)
        remaining = bp.filter_stale_entities(result_file, permid_file)
        assert remaining == 0
        assert bp.result_data == {}

    def test_empty_result_data_returns_zero(self, tmp_path):
        """Empty result_data → nothing stale, zero remaining."""
        result_file, permid_file = write_files(tmp_path, {}, {})
        bp = BatchProcessing({}, {}, "cik", threshold_days=30)
        remaining = bp.filter_stale_entities(result_file, permid_file)
        assert remaining == 0


def _permid_entry(name: str, cusip: str, permid_url: str) -> dict:
    """A permid_file entry for a CUSIP row resolving to permid_url."""
    return {
        f"{name}_cusip_{cusip}": {
            "search": {
                "Name": name,
                "LocalID": f"cusip_{cusip}",
                "Standard Identifier": "ticker:X",
            },
            "result": [permid_url],
        }
    }


class TestFindCusipCollisions:
    """Tests for find_cusip_collisions (detection-only, no API calls)."""

    def test_flags_different_issuers_on_one_permid(self):
        """CUSIPs with different issuer prefixes sharing a PermID is a suspect."""
        permid_data = {
            **_permid_entry("ABBOTT", "002824100", _PERMID_URL),  # issuer 002824
            **_permid_entry("ABACUS", "00258Y104", _PERMID_URL),  # issuer 00258Y
        }
        collisions = find_cusip_collisions(permid_data)
        # Returns the submitted name per CUSIP so the warning is legible.
        assert collisions == {_PERMID_URL: {"002824100": "ABBOTT", "00258Y104": "ABACUS"}}

    def test_share_classes_same_issuer_not_flagged(self):
        """Same issuer prefix (share classes) collapsing to one PermID is legitimate."""
        permid_data = {
            **_permid_entry("ALPHA A", "00510M104", _PERMID_URL),  # issuer 00510M
            **_permid_entry("ALPHA B", "00510M203", _PERMID_URL),  # issuer 00510M
        }
        assert find_cusip_collisions(permid_data) == {}

    def test_distinct_permids_not_flagged(self):
        """Different CUSIPs resolving to different PermIDs is fine."""
        permid_data = {
            **_permid_entry("ABBOTT", "002824100", _PERMID_URL),
            **_permid_entry("ADOBE", "00724F101", _PERMID_URL_2),
        }
        assert find_cusip_collisions(permid_data) == {}
