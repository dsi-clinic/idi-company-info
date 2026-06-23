"""A cross-host file lock for serialising writes to the final aggregated output.

Selects an implementation by path scheme:

- Local paths use ``fcntl.flock`` on a sidecar ``.lock`` file (covers multiple
  processes on one host).
- ``s3://`` paths use an S3 conditional ``PutObject`` (``If-None-Match: *``) to
  create a lock object atomically, with a TTL so a lock left behind by a crashed
  process can be stolen (covers multiple hosts writing to the same bucket).

Used by the Output aggregator so concurrent processor runs do not corrupt the
single final parquet via interleaved read-modify-write.
"""

# Standard library imports
import fcntl
import json
import os
import pathlib
import socket
import time
from types import TracebackType

# Third party imports
import boto3
from botocore.exceptions import ClientError
from idi_ftm2j_shared.logs import get_logger

logger = get_logger("FileLock")

# A lock older than this is presumed abandoned (owner crashed) and may be stolen.
_DEFAULT_TTL_SECONDS = 600.0
# Delay between acquisition attempts while another holder owns the lock.
_POLL_INTERVAL_SECONDS = 0.5
# HTTP status returned by S3 when a conditional PutObject (If-None-Match) is rejected.
_PRECONDITION_FAILED = 412


def _owner_id() -> str:
    """Return a best-effort identifier for the current process, for lock diagnostics."""
    return f"{socket.gethostname()}:{os.getpid()}"


def _parse_s3_url(file_path: str) -> tuple[str, str]:
    """Parse an ``s3://bucket/key`` URL into ``(bucket, key)``."""
    without_scheme = file_path[5:]
    bucket, _, key = without_scheme.partition("/")
    return bucket, key


class FileLock:
    """Context manager that acquires an exclusive lock for ``target`` before writing it.

    The lock path is ``{target}.lock``. ``acquire`` blocks (polling) until the lock is
    obtained or ``timeout`` seconds elapse, in which case it raises ``TimeoutError``.
    """

    def __init__(
        self,
        target: str,
        timeout: float = 60.0,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
    ) -> None:
        """Initialise the lock.

        Args:
            target: The path being protected (local path or ``s3://`` URL). The lock
                object/file lives at ``{target}.lock``.
            timeout: Max seconds to wait to acquire the lock before raising ``TimeoutError``.
            ttl_seconds: Age beyond which an existing S3 lock is considered abandoned and
                may be stolen. Ignored for local locks.
        """
        self.target = target
        self.lock_path = f"{target}.lock"
        self.timeout = timeout
        self.ttl_seconds = ttl_seconds
        self._is_s3 = self.lock_path.startswith("s3://")
        self._local_handle = None
        self._s3_client = None

    def __enter__(self) -> "FileLock":
        """Acquire the lock on context entry."""
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release the lock on context exit."""
        self.release()

    def acquire(self) -> None:
        """Acquire the lock, polling until ``timeout`` elapses.

        Raises:
            TimeoutError: If the lock cannot be acquired within ``timeout`` seconds.
        """
        if self._is_s3:
            self._acquire_s3()
        else:
            self._acquire_local()

    def release(self) -> None:
        """Release the lock if held. Safe to call when the lock was never acquired."""
        if self._is_s3:
            self._release_s3()
        else:
            self._release_local()

    # --- Local (fcntl) -------------------------------------------------------

    def _acquire_local(self) -> None:
        """Acquire an exclusive ``fcntl`` lock on the sidecar ``.lock`` file.

        Opens (or creates) ``lock_path``, then polls with ``LOCK_EX | LOCK_NB`` until the
        lock is granted or ``timeout`` expires. Writes the owner ID into the file for
        diagnostics once the lock is held.

        Raises:
            TimeoutError: If the lock cannot be acquired within ``timeout`` seconds.
        """
        pathlib.Path(self.lock_path).parent.mkdir(parents=True, exist_ok=True)
        handle = pathlib.Path(self.lock_path).open("w")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                handle.write(_owner_id())
                handle.flush()
                self._local_handle = handle
                return
            except OSError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise TimeoutError(
                        f"Could not acquire lock {self.lock_path} within {self.timeout}s"
                    ) from None
                time.sleep(_POLL_INTERVAL_SECONDS)

    def _release_local(self) -> None:
        """Unlock and remove the sidecar ``.lock`` file. No-op if never acquired."""
        if self._local_handle is None:
            return
        try:
            fcntl.flock(self._local_handle, fcntl.LOCK_UN)
            self._local_handle.close()
            pathlib.Path(self.lock_path).unlink(missing_ok=True)
        finally:
            self._local_handle = None

    # --- S3 (conditional put) ------------------------------------------------

    def _client(self) -> "boto3.client":
        """Return a lazily-created boto3 S3 client (one per ``FileLock`` instance)."""
        if self._s3_client is None:
            self._s3_client = boto3.client("s3")
        return self._s3_client

    def _acquire_s3(self) -> None:
        """Acquire the lock by atomically creating an S3 object with ``If-None-Match: *``.

        Polls on ``PreconditionFailed`` (412) — the lock is held by another process.
        On each failed attempt, checks whether the existing lock is older than
        ``ttl_seconds`` and steals it if so (owner is presumed crashed). Raises
        ``TimeoutError`` if the lock cannot be acquired within ``timeout`` seconds.

        Raises:
            TimeoutError: If the lock cannot be acquired within ``timeout`` seconds.
            botocore.exceptions.ClientError: On any S3 error other than a failed
                precondition check.
        """
        bucket, key = _parse_s3_url(self.lock_path)
        deadline = time.monotonic() + self.timeout
        body = json.dumps({"owner": _owner_id(), "ts": time.time()}).encode()
        while True:
            try:
                self._client().put_object(Bucket=bucket, Key=key, Body=body, IfNoneMatch="*")
                return
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if code != "PreconditionFailed" and status != _PRECONDITION_FAILED:
                    raise
                # Lock is held by someone else; steal it if it is stale.
                if self._steal_if_stale_s3(bucket, key):
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Could not acquire lock {self.lock_path} within {self.timeout}s"
                    ) from None
                time.sleep(_POLL_INTERVAL_SECONDS)

    def _steal_if_stale_s3(self, bucket: str, key: str) -> bool:
        """Delete an abandoned (older than TTL) lock object so it can be re-acquired.

        Returns:
            True if a stale lock was deleted (caller should retry), False otherwise.
        """
        try:
            obj = self._client().get_object(Bucket=bucket, Key=key)
            meta = json.loads(obj["Body"].read())
            age = time.time() - float(meta.get("ts", 0))
            if age > self.ttl_seconds:
                logger.warning(
                    "Stealing stale lock %s (held by %s, age %.0fs)",
                    self.lock_path,
                    meta.get("owner"),
                    age,
                )
                self._client().delete_object(Bucket=bucket, Key=key)
                return True
        except (ClientError, ValueError, KeyError):
            # Lock vanished or was unreadable; let the caller retry the put.
            return True
        return False

    def _release_s3(self) -> None:
        """Release the S3 lock by deleting the lock object.

        Logs a warning on failure rather than raising — a release error after a successful
        write is non-fatal; the TTL mechanism will clean up an abandoned lock eventually.
        """
        bucket, key = _parse_s3_url(self.lock_path)
        try:
            self._client().delete_object(Bucket=bucket, Key=key)
        except ClientError:
            logger.warning("Failed to delete lock object %s on release", self.lock_path)
