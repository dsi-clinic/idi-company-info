"""Utilities for the creation of cache keys."""

def permid_cache_key(entity_name: str, identifier: str) -> str:
    """Build the flat permid_file key for an input row.

    Args:
        entity_name: Name of entity to search cache for
        identifier: Identifier to search for

    Returns:
        Full string cache key for permid retrieval
    """
    return f"{entity_name}_{identifier}"

def parse_permid_cache_key(key: str) -> tuple[str, str, str]:
    """Parse the flat permid_file key into entity_name, identifier_type, identifier.

    Args:
        key: The flat permid_file key.

    Returns:
        Tuple of (entity_name, identifier_type, identifier).
    """
    parts = key.rsplit("_", 1)
    if len(parts) != 2:
        raise ValueError(f"Malformed permid cache key: {key!r}")
    entity_name, identifier_type, identifier = parts
    return entity_name, identifier_type, identifier