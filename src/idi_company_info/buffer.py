"""Buffer for storing data that flushes to file when threshold is reached."""

# Standard library imports
from collections.abc import Mapping
from typing import Any

# Third party imports
from idi_ftm2j_shared.logs import get_logger
from idi_ftm2j_shared.storage import load_json, save_json

# Application imports
from idi_company_info.fs import atomic_write
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

        # Atomic write so a concurrent aggregation read never sees a partial file.
        atomic_write(self.file_path, lambda p: save_json(p, existing))
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


class SectorCache:
    """Memo of resolved sector/industry-group URLs, persisted to a shared JSON file.

    The TRBC taxonomy is tiny (~90 entries) and global, so a value resolved by one run or
    source serves all the others. Seed it from disk with :meth:`load`, record resolutions
    with :meth:`set`, and persist newly discovered entries with :meth:`flush`. An empty
    ``file_path`` keeps the memo in-memory only (no disk reads or writes).
    """

    def __init__(self, file_path: str = "") -> None:
        """Initialize the SectorCache.

        Args:
            file_path: Shared cache file (local path or ``s3://`` URL). Empty disables
                persistence — the memo still dedupes within a run, just not across runs.
        """
        self.file_path = file_path
        self.logger = get_logger(type(self).__name__)
        self._entries: dict[str, tuple[str | None, str | None]] = {}
        self._has_new_entries = False

    def __contains__(self, url: str) -> bool:
        """Whether a resolved value is memoized for this URL."""
        return url in self._entries

    def get(self, url: str) -> tuple[str | None, str | None]:
        """Return the memoized (label, comment) for a previously resolved URL."""
        return self._entries[url]

    def set(self, url: str, value: tuple[str | None, str | None]) -> None:
        """Memoize a freshly resolved (label, comment) and mark the cache for flushing."""
        self._entries[url] = value
        self._has_new_entries = True

    def __len__(self) -> int:
        """Number of memoized entries."""
        return len(self._entries)

    def load(self) -> None:
        """Seed the in-memory memo from the shared on-disk cache, if a file is configured.

        No-op without a file path (in-memory only). Missing files load as empty. JSON stores
        each value as a ``[label, comment]`` list; it is restored to a tuple to match the
        in-memory shape.
        """
        self._has_new_entries = False
        if not self.file_path:
            return
        raw = load_json(self.file_path, return_type="dict")
        self._entries = {url: tuple(value) for url, value in raw.items()}
        self.logger.info("Loaded %d cached sectors from %s", len(self._entries), self.file_path)

    def flush(self) -> None:
        """Persist newly discovered sectors to the shared cache. Best-effort, non-critical.

        Writes only when entries were added this run. Re-reads the file first and unions the
        in-memory entries on top to reduce loss when sources overlap. Concurrency guarantees,
        by design:

        - ``atomic_write`` means a concurrent *reader* never sees a partial file (each write
          is a whole-object replace).
        - It is NOT fully serializable: two writers that both read-then-write can still drop
          one's new entries (last-writer-wins). That is acceptable here — the cache is a
          pure optimization, the values are deterministic, and any dropped entry is simply
          re-fetched on a later run. No data the pipeline depends on lives only here.

        Tuples serialize as ``[label, comment]`` lists. Callers should treat a raised
        exception as non-fatal (this is just a cache write).
        """
        if not self._has_new_entries or not self.file_path:
            return
        merged = load_json(self.file_path, return_type="dict")
        merged.update({url: list(value) for url, value in self._entries.items()})
        atomic_write(self.file_path, lambda p: save_json(p, merged))
        self.logger.info("Flushed sector cache (%d entries) to %s", len(merged), self.file_path)
        self._has_new_entries = False


def permid_cache_key(entity_name: str, identifier: str) -> str:
    """Build the flat permid_file key for an input row.

    Args:
        entity_name: Name of entity to search cache for
        identifier: Identifier to search for

    Returns:
        Full string cache key for permid retrieval
    """
    return f"{entity_name}_{identifier}"
