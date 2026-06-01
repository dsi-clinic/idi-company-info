"""Buffer for storing data that flushes to file when threshold is reached."""

# Third party imports
from idi_ftm2j_shared.logs import get_logger
from idi_ftm2j_shared.storage import load_json, save_json

# Application imports
from idi_company_info.types import CacheBuffer


class Buffer:
    """PermidBuffer for storing permid data in a sync buffer."""

    def __init__(self, file_path: str, buffer_size: int = 500) -> None:
        """Initialize the PermidBuffer.

        Args:
            file_path: The path to the file.
            buffer_size: The size of the buffer.
            mode: The mode of the buffer.
        """
        self.file_path = file_path
        self.buffer_size = buffer_size
        self.logger = get_logger(type(self).__name__)
        self._buffer: CacheBuffer = {}

    def add(self, data: CacheBuffer) -> None:
        """Merge permid_data into sync buffer; flush if threshold reached.

        Args:
            data: The data to merge.
        """
        self._buffer.update(data)
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
        self._merge_dict(existing)

        save_json(self.file_path, existing)
        self.logger.info("Saved %s data to %s", len(self._buffer), self.file_path)

        self._buffer.clear()

    def _merge_dict(self, existing: dict) -> None:
        """Merge the entity data into the existing data.

        Args:
            existing: The existing data.
        """
        for key, value in self._buffer.items():
            # Merge old data with new
            if key not in existing:
                existing[key] = value
            # Merge old data with existing new
            elif isinstance(value.get("result"), list):
                # permid file: result is a list of URLs
                for url in value["result"]:
                    existing_urls = existing[key]["result"]
                    if url not in existing_urls:
                        existing_urls.append(url)
                existing[key]["search"] = value["search"]
            else:
                # company info: result is a dict
                self.logger.warning(
                    "Unexpected duplicate key in result buffer: %s - overwriting",
                key)
                existing[key] = value

    def _should_flush(self, current_size: int) -> bool:
        return current_size >= self.buffer_size

    def load_all(self) -> dict | list:
        """Load full merged data from file + remaining sync buffer.

        Returns:
            The merged data.
        """
        self.flush()
        return load_json(self.file_path, return_type="dict")
