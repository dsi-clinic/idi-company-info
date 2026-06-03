"""Buffer for storing data that flushes to file when threshold is reached."""

# Standard library imports
from collections.abc import Mapping
from typing import Any

# Third party imports
from idi_ftm2j_shared.logs import get_logger
from idi_ftm2j_shared.storage import load_json, save_json

# Application imports
from idi_company_info.types import CacheBuffer, MergeStrategy


class Buffer:
    """Accumulates cache entries in memory and flushes them to a JSON file.

    Serves both cache files; the merge strategy (permid vs company_info) selects how
    colliding keys are combined on flush.
    """

    def __init__(self, file_path: str, merge: MergeStrategy, buffer_size: int = 500) -> None:
        """Initialize the Buffer.

        Args:
            file_path: The path to the file.
            merge: The merge strategy for this file (permid extends URL lists; company_info
                overwrites the entry).
            buffer_size: The number of units to accumulate before flushing to disk.
        """
        self.file_path = file_path
        self.merge = merge
        self.buffer_size = buffer_size
        self.logger = get_logger(type(self).__name__)
        self._buffer: CacheBuffer = {}

    def add(self, data: Mapping[str, Any]) -> None:
        """Merge data into sync buffer; flush if threshold reached.

        Accepts any mapping of cache entries (Mapping is covariant in its value type, so
        callers can pass precisely-typed entries like dict[str, ResultEntry]).

        Args:
            data: The data to merge.
        """
        self._merge(data, self._buffer)
        if self._should_flush(self._buffer_size()):
            self.flush()

    def _buffer_size(self) -> int:
        """Count the unit that defines the flush threshold for this buffer.

        permid: total permid URLs across entries; company info: number of companies
        (one entry == one entity-lookup result).
        """
        if self.merge is MergeStrategy.PERMID:
            return sum(len(value["result"]) for value in self._buffer.values())
        return len(self._buffer.keys())

    def flush(self) -> None:
        """Merge sync buffer into file and clear buffer.

        Returns:
            The merged data.
        """
        if not self._buffer:
            return

        existing = load_json(self.file_path, return_type="dict")
        self._merge(self._buffer, existing)  # merge buffer into file

        save_json(self.file_path, existing)
        self.logger.info("Saved %s data to %s", len(self._buffer), self.file_path)

        self._buffer = {}

    def _merge(self, source: Mapping[str, Any], target: dict) -> None:
        """Merge source into target using this buffer's strategy.

        permid: extend the result URL list (deduped). company info: overwrite the entry —
        result_file is keyed by a unique permid_url and only written when absent or being
        refreshed after a stale prune, so there is nothing to union.
        """
        for source_key, source_value in source.items():
            if source_key not in target or self.merge is MergeStrategy.COMPANY_INFO:
                target[source_key] = source_value
            else:
                self._merge_permid(target[source_key], source_value)

    @staticmethod
    def _merge_permid(existing_entry: dict, value: dict) -> None:
        """permid_file: result is a list of URLs — extend deduped, refresh search."""
        urls = existing_entry["result"]
        for url in value["result"]:
            if url not in urls:
                urls.append(url)
        existing_entry["search"] = value["search"]

    def _should_flush(self, current_size: int) -> bool:
        return current_size >= self.buffer_size

    def load_all(self) -> dict | list:
        """Load full merged data from file + remaining sync buffer.

        Returns:
            The merged data.
        """
        self.flush()
        return load_json(self.file_path, return_type="dict")

def permid_cache_key(entity_name: str, identifier: str) -> str:
    """Build the flat permid_file key for an input row.

    Args:
        entity_name: Name of entity to search cache for
        identifier: Identifier to search for

    Returns:
        Full string cache key for permid retrieval
    """
    return f"{entity_name}_{identifier}"
