"""Buffer for storing data that flushes to file when threshold is reached."""

# Standard library imports
from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

# Application imports
from ftm2j.common.storage import load_json, save_json
from ftm2j.common.logs import get_logger

class Buffer:
    """PermidBuffer for storing permid data in a sync buffer."""

    def __init__(self, file_path: str, buffer_size: int = 500, mode: str = "dict"):
        """Initialize the PermidBuffer.

        Args:
            file_path: The path to the file.
            buffer_size: The size of the buffer.
            mode: The mode of the buffer.
        """
        self.file_path = file_path
        self.buffer_size = buffer_size
        self.logger = get_logger("Buffer")
        self.mode = mode
        if mode == "dict":
            self._buffer: dict = {}
        elif mode == "list":
            self._buffer: list = []
        else:
            raise ValueError(f"Invalid mode: {mode}")

    def add(self, data: dict[str, list[tuple[str, list[str]]]]) -> None:
        """Merge permid_data into sync buffer; flush if threshold reached.

        Args:
            data: The data to merge.
        """
        if self.mode == "dict":
            self._buffer.update(data)
        else:
            self._buffer.extend(data)

        if self._should_flush(len(self._buffer)):
            self.flush()

    def flush(self) -> None:
        """Merge sync buffer into file and clear buffer.

        Returns:
            The merged data.
        """
        existing = load_json(self.file_path, return_type=self.mode)
        if self.mode == "dict":
            self._merge_dict(existing, self._buffer)
        else:
            existing.extend(self._buffer)

        save_json(self.file_path, existing)
        self.logger.info("Saved %s data to %s", len(self._buffer), self.file_path)

        self._buffer.clear()

    def _merge_dict(self, existing: dict, new_data: dict) -> dict:
        """Merge the new data into the existing data.

        Args:
            existing: The existing data.
            new_data: The new data.

        Returns:
            The merged data.
        """
        for entity_name, new_data in self._buffer.items():
            if entity_name in existing:
                existing_ids = {k for item in existing[entity_name] for k in item}
                for item in new_data:
                    for identifier in item.keys():
                        if identifier not in existing_ids:
                            existing[entity_name].append(item)
                            existing_ids.add(identifier)
            else:
                existing[entity_name] = new_data

    def _should_flush(self, current_size: int) -> bool:
        return current_size >= self.buffer_size

    def load_all(self) -> dict:
        """Load full merged data from file + remaining sync buffer.

        Returns:
            The merged data.
        """
        self.flush()
        return load_json(self.file_path, return_type="dict") or {}
