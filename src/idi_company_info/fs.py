"""Filesystem helpers for cache and output files (local filesystem and ``s3://``).

Both helpers encapsulate the same local-vs-S3 branching:

- ``join_path`` joins a filename onto a base directory for either backend.
- ``atomic_write`` writes a file so readers never observe a partial write — via a
  temp file + atomic rename locally, and directly on S3 (``put_object`` /
  ``CompleteMultipartUpload`` are already atomic per object).
"""

# Standard library imports
import pathlib
import uuid
from collections.abc import Callable


def join_path(base: str | pathlib.Path, name: str) -> str:
    """Join a filename onto a base directory, supporting local paths and ``s3://`` URLs.

    Args:
        base: Base directory — a local path or ``s3://bucket/prefix`` URL.
        name: Filename or sub-path to append.

    Returns:
        The joined path as a string.
    """
    base_str = str(base)
    if base_str.startswith("s3://"):
        return f"{base_str.rstrip('/')}/{name}"
    return str(pathlib.Path(base_str) / name)


def atomic_write(path: str, write: Callable[[str], None]) -> None:
    """Write to ``path`` so readers never observe a partial file.

    Local: ``write`` is directed at a sibling temp file (same directory → same filesystem),
    which is then atomically renamed onto ``path`` via ``Path.replace``. If ``write`` (or the
    rename) raises, the temp file is removed and the error re-raised.

    S3: ``write`` is called with ``path`` directly — ``put_object`` and multipart completion
    are atomic per object, so no temp file is needed.

    Args:
        path: Destination — a local filesystem path or ``s3://bucket/key`` URL.
        write: Callback that writes the full content to the path it is given.
    """
    if path.startswith("s3://"):
        write(path)
        return

    tmp_path = f"{path}.tmp.{uuid.uuid4().hex}"
    try:
        write(tmp_path)
        pathlib.Path(tmp_path).replace(path)
    except BaseException:
        pathlib.Path(tmp_path).unlink(missing_ok=True)
        raise
