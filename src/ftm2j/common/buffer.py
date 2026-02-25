"""Buffer for storing data that flushes to file when threshold is reached."""

# Standard library imports
from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

# Application imports
from ftm2j.common.storage import load_json, save_json
from ftm2j.common.logs import get_logger
if TYPE_CHECKING:
    from ftm2j.common.batch import BatchProcessing
    from ftm2j.processors.idi_company_info.identifier import BatchStats

class Buffer(ABC):
    """Base class for buffered data that flushes to file when threshold is reached."""

    def __init__(self, file_path: str, buffer_size: int = 500):
        self.file_path = file_path
        self.buffer_size = buffer_size
        self.logger = get_logger(__name__)

    @abstractmethod
    def add(self, data: Any) -> None:
        """Add data to buffer; flush if threshold reached."""
        ...

    @abstractmethod
    def flush(self) -> None:
        """Write buffer to file and clear."""
        ...

    def _should_flush(self, current_size: int) -> bool:
        return current_size >= self.buffer_size

class PermidBuffer(Buffer):
    """PermidBuffer for storing permid data in a sync buffer."""

    def __init__(self, file_path: str, buffer_size: int = 500):
        """Initialize the PermidBuffer.

        Args:
            file_path: The path to the file.
            buffer_size: The size of the buffer.
        """
        super().__init__(file_path, buffer_size)
        self._buffer: dict = {}

    def add(self, permid_data: dict[str, list[tuple[str, list[str]]]]) -> None:
        """Merge permid_data into sync buffer; flush if threshold reached.

        Args:
            permid_data: The permid data to merge.
        """
        self._buffer.update(permid_data)
        if self._should_flush(len(self._buffer)):
            self.flush()

    def flush(self) -> None:
        """Merge sync buffer into file and clear buffer.

        Returns:
            The merged data.
        """
        existing = load_json(self.file_path, return_type="dict") or {}
        existing.update(self._buffer)

        save_json(self.file_path, existing)
        self.logger.info("Saved %s permid data to %s", len(self._buffer), self.file_path)

        self._buffer.clear()

    def load_all(self) -> dict:
        """Load full merged data from file + remaining sync buffer.

        Returns:
            The merged data.
        """
        self.flush()
        return load_json(self.file_path, return_type="dict") or {}

class CompanyInfoBuffer(Buffer):
    """Buffer for company info. Saves full list and updates batch tracking on flush."""

    def __init__(
        self,
        file_path: str,
        buffer_size: int,
        batch_processing: "BatchProcessing",
        batch_stats: "BatchStats",
        existing_results: list[dict],
    ):
        super().__init__(file_path, buffer_size)
        self._batch_processing = batch_processing
        self._batch_stats = batch_stats
        self._company_info: list[dict] = list(existing_results)
        self._buffer: list[str] = []

    def add(self, company_records: list[dict], entity_names: list[str]) -> None:
        """Add company records and entity names to buffer; flush if threshold reached.

        Args:
            company_records: The company records to add.
            entity_names: The entity names to add.
        """
        self._company_info.extend(company_records)
        self._buffer.extend(entity_names)
        if self._should_flush(len(self._buffer)):
            self.flush()

    def flush(self) -> None:
        """Write buffer to file and clear."""
        if not self._buffer:
            return

        save_json(self.file_path, self._company_info)
        self.logger.info("Saved %s company info data to %s", len(self._buffer), self.file_path)

        self._batch_processing.update_batch_tracking(self._buffer, self._batch_stats)
        self.logger.info("Updated batch tracking for %s entities", len(self._buffer))

        self._buffer.clear()

    def finalize(self) -> int:
        """Flush remaining and return total records count."""
        self.flush()
        return len(self._company_info)