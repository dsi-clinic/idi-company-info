#!/usr/bin/env python3
"""Unit tests for idi_company_info.fs (join_path + atomic_write)."""

import pathlib
from unittest.mock import MagicMock

import pytest

from idi_company_info.fs import atomic_write, join_path


class TestJoinPath:
    """join_path supports local paths and s3:// URLs."""

    def test_local_path(self):
        assert join_path("/data/out", "latest.parquet") == "/data/out/latest.parquet"

    def test_s3_url(self):
        assert join_path("s3://bucket/out", "latest.parquet") == "s3://bucket/out/latest.parquet"

    def test_s3_url_trailing_slash(self):
        assert join_path("s3://bucket/out/", "latest.parquet") == "s3://bucket/out/latest.parquet"


class TestAtomicWriteLocal:
    """Local writes go via a temp file then an atomic rename."""

    def test_writes_final_file_and_leaves_no_temp(self, tmp_path):
        target = tmp_path / "out.txt"
        atomic_write(str(target), lambda p: pathlib.Path(p).write_text("hello"))

        assert target.read_text() == "hello"
        # No leftover temp files in the directory.
        assert [p.name for p in tmp_path.iterdir()] == ["out.txt"]

    def test_write_target_is_not_the_final_path(self, tmp_path):
        """The callback is handed a temp path, not the destination, on local writes."""
        target = tmp_path / "out.txt"
        seen = {}

        def writer(p):
            seen["path"] = p
            pathlib.Path(p).write_text("x")

        atomic_write(str(target), writer)
        assert seen["path"] != str(target)
        assert seen["path"].startswith(str(target))

    def test_failure_removes_temp_and_reraises(self, tmp_path):
        target = tmp_path / "out.txt"

        def boom(p):
            pathlib.Path(p).write_text("partial")
            raise RuntimeError("write failed")

        with pytest.raises(RuntimeError, match="write failed"):
            atomic_write(str(target), boom)

        assert not target.exists()
        assert list(tmp_path.iterdir()) == []  # temp cleaned up


class TestAtomicWriteS3:
    """S3 writes go directly to the final key (put_object is already atomic)."""

    def test_writes_directly_without_temp(self):
        writer = MagicMock()
        atomic_write("s3://bucket/key/out.parquet", writer)
        writer.assert_called_once_with("s3://bucket/key/out.parquet")
