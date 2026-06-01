"""Buffer for storing data that flushes to file when threshold is reached."""

# Third party imports
from idi_ftm2j_shared.logs import get_logger
from idi_ftm2j_shared.storage import load_json, save_json

# Application imports
from idi_company_info.types import CacheBuffer, MergeStrategy


class Buffer:
    """PermidBuffer for storing permid data in a sync buffer."""

    def __init__(self, file_path: str, merge: MergeStrategy, buffer_size: int = 500) -> None:
        """Initialize the PermidBuffer.

        Args:
            file_path: The path to the file.
            merge: Which result type to merge
            buffer_size: The size of the buffer.
        """
        self.file_path = file_path
        self.merge = merge  # "permid" | "company_info"
        self.buffer_size = buffer_size
        self.logger = get_logger(type(self).__name__)
        self._buffer: CacheBuffer = {}

    def add(self, data: CacheBuffer) -> None:
        """Merge permid_data into sync buffer; flush if threshold reached.

        Args:
            data: The data to merge.
        """
        self._merge(data, self._buffer)        # was: self._buffer.update(data)
        # current_size = sum(len(value["result"]) for value in self._buffer.values())
        # print("CURRENT_SIZE", current_size)
        if self._should_flush(len(self._buffer)):
            self.flush()

    def flush(self) -> None:
        """Merge sync buffer into file and clear buffer.

        Returns:
            The merged data.
        """
        if not self._buffer:
            return

        existing = load_json(self.file_path, return_type="dict")
        self._merge(self._buffer, existing)    # merge buffer into file

        save_json(self.file_path, existing)
        self.logger.info("Saved %s data to %s", len(self._buffer), self.file_path)

        self._buffer = {}

    def _merge(self, source: dict, target: dict) -> None:
        """Merge source into target using this buffer's strategy."""
        for source_key, source_value in source.items():
            if source_key not in target:
                target[source_key] = source_value
            elif self.merge is MergeStrategy.PERMID:
                self._merge_permid(target[source_key], source_value)
            else:
                self._merge_company_info(target[source_key], source_value)

    @staticmethod
    def _merge_permid(existing_entry: dict, value: dict) -> None:
        """permid_file: result is a list of URLs — extend deduped, refresh search."""
        urls = existing_entry["result"]
        for url in value["result"]:
            if url not in urls:
                urls.append(url)
        existing_entry["search"] = value["search"]

    @staticmethod
    def _merge_company_info(target_entry: dict, source_entry: dict) -> None:
        """result_file: union identifiers, refresh company info."""
        target_ids = [ (value["name"], value["identifier"]) for value in target_entry["identifiers"] ]
        for source_id in source_entry["identifiers"]:
            if (source_id["name"], source_id["identifier"]) not in target_ids:
                target_entry["identifiers"].append(source_id)

    def _should_flush(self, current_size: int) -> bool:
        return current_size >= self.buffer_size

    def load_all(self) -> dict | list:
        """Load full merged data from file + remaining sync buffer.

        Returns:
            The merged data.
        """
        self.flush()
        return load_json(self.file_path, return_type="dict")
