#!/usr/bin/env python3
"""Unit tests for idi_company_info.lock.FileLock.

Covers the local (fcntl) path with real contention and timeout, and the S3 path with a
mocked boto3 client to assert the conditional-put / delete protocol.
"""

import threading
import time
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from idi_company_info.lock import FileLock


class TestLocalLock:
    """Local locks use fcntl on a sidecar .lock file."""

    def test_acquire_and_release_removes_sidecar(self, tmp_path):
        target = str(tmp_path / "out.parquet")
        lock = FileLock(target)
        lock.acquire()
        assert (tmp_path / "out.parquet.lock").exists()
        lock.release()
        assert not (tmp_path / "out.parquet.lock").exists()

    def test_second_holder_blocks_then_succeeds_after_release(self, tmp_path):
        target = str(tmp_path / "out.parquet")
        first = FileLock(target)
        first.acquire()

        acquired = threading.Event()

        def worker():
            with FileLock(target, timeout=5.0):
                acquired.set()

        t = threading.Thread(target=worker)
        t.start()
        # The second acquirer cannot proceed while the first holds the lock.
        assert not acquired.wait(timeout=0.5)
        first.release()
        assert acquired.wait(timeout=5.0)
        t.join()

    def test_times_out_when_held(self, tmp_path):
        target = str(tmp_path / "out.parquet")
        holder = FileLock(target)
        holder.acquire()
        try:
            with pytest.raises(TimeoutError):
                FileLock(target, timeout=0.5).acquire()
        finally:
            holder.release()


def _precondition_error() -> ClientError:
    return ClientError(
        {"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}},
        "PutObject",
    )


class TestS3Lock:
    """S3 locks use a conditional PutObject and delete on release."""

    def test_acquire_uses_if_none_match(self, monkeypatch):
        client = MagicMock()
        lock = FileLock("s3://bucket/out.parquet")
        monkeypatch.setattr(lock, "_client", lambda: client)

        lock.acquire()

        _, kwargs = client.put_object.call_args
        assert kwargs["IfNoneMatch"] == "*"
        assert kwargs["Bucket"] == "bucket"
        assert kwargs["Key"] == "out.parquet.lock"

    def test_release_deletes_lock_object(self, monkeypatch):
        client = MagicMock()
        lock = FileLock("s3://bucket/out.parquet")
        monkeypatch.setattr(lock, "_client", lambda: client)

        lock.acquire()
        lock.release()

        client.delete_object.assert_called_once_with(Bucket="bucket", Key="out.parquet.lock")

    def test_steals_stale_lock(self, monkeypatch):
        client = MagicMock()
        # First put fails (held); after stealing, second put succeeds.
        client.put_object.side_effect = [_precondition_error(), None]
        body = MagicMock()
        body.read.return_value = b'{"owner": "dead-host:1", "ts": 0}'
        client.get_object.return_value = {"Body": body}

        lock = FileLock("s3://bucket/out.parquet", timeout=5.0, ttl_seconds=1.0)
        monkeypatch.setattr(lock, "_client", lambda: client)

        lock.acquire()

        client.delete_object.assert_called_with(Bucket="bucket", Key="out.parquet.lock")
        assert client.put_object.call_count == 2

    def test_times_out_when_lock_fresh(self, monkeypatch):
        client = MagicMock()
        client.put_object.side_effect = _precondition_error()
        body = MagicMock()
        body.read.return_value = f'{{"owner": "live:1", "ts": {time.time()}}}'.encode()
        client.get_object.return_value = {"Body": body}

        lock = FileLock("s3://bucket/out.parquet", timeout=0.5, ttl_seconds=600.0)
        monkeypatch.setattr(lock, "_client", lambda: client)

        with pytest.raises(TimeoutError):
            lock.acquire()
