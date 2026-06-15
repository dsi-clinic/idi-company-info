"""Path helpers shared across the pipeline (local filesystem and ``s3://`` URLs)."""

# Standard library imports
import pathlib


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
