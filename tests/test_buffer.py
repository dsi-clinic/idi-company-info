#!/usr/bin/env python3
"""Unit tests for idi_company_info.buffer.SectorCache."""

import json

from idi_company_info.buffer import SectorCache


class TestSectorCacheInMemory:
    """Without a file path the cache dedupes within a run but never touches disk."""

    def test_set_get_contains_len(self):
        cache = SectorCache()
        assert "u" not in cache
        assert len(cache) == 0

        cache.set("u", ("Tech", "comment"))

        assert "u" in cache
        assert cache.get("u") == ("Tech", "comment")
        assert len(cache) == 1

    def test_flush_without_path_is_a_noop(self, tmp_path):
        cache = SectorCache()  # no file path -> in-memory only
        cache.set("u", ("Tech", None))

        cache.flush()  # must neither raise nor create any file

        assert list(tmp_path.iterdir()) == []

    def test_load_without_path_is_a_noop(self):
        cache = SectorCache()
        cache.load()
        assert len(cache) == 0


class TestSectorCachePersistence:
    """With a file path the cache seeds from and flushes to disk."""

    def test_load_missing_file_is_empty(self, tmp_path):
        cache = SectorCache(str(tmp_path / "cache.json"))
        cache.load()
        assert len(cache) == 0

    def test_set_then_flush_writes_lists(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = SectorCache(str(path))
        cache.set("u", ("Tech", "comment"))
        cache.set("v", ("Energy", None))

        cache.flush()

        # Tuples serialize as [label, comment] lists; a None comment -> null.
        assert json.loads(path.read_text()) == {"u": ["Tech", "comment"], "v": ["Energy", None]}

    def test_flush_is_noop_when_nothing_new(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = SectorCache(str(path))
        cache.load()  # missing file -> empty, not dirty

        cache.flush()

        assert not path.exists()  # nothing was added, so nothing is written

    def test_load_restores_tuples(self, tmp_path):
        path = tmp_path / "cache.json"
        path.write_text(json.dumps({"u": ["Tech", "comment"]}))
        cache = SectorCache(str(path))

        cache.load()

        assert cache.get("u") == ("Tech", "comment")

    def test_flush_merges_entries_written_after_load(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = SectorCache(str(path))
        cache.load()  # empty
        cache.set("mine", ("Mine", None))

        # A sibling source persists a different entry between our load and our flush.
        path.write_text(json.dumps({"theirs": ["Theirs", None]}))

        cache.flush()

        # The re-read-and-union on flush keeps both — neither writer is clobbered.
        assert json.loads(path.read_text()) == {
            "theirs": ["Theirs", None],
            "mine": ["Mine", None],
        }
