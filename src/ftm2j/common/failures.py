"""Classifies failures as retryable or permanent (do-not-retry)."""

# Standard library imports
import json
import pathlib
from enum import StrEnum
from typing import Literal

# Application imports
from ftm2j.common.logs import get_logger
from ftm2j.common.storage import load_json, save_json


class FailureType(StrEnum):
    """Failure type for classification."""

    NO_PERMID = "no_permid"  # Empty response, identifier not in DB
    LOW_MATCH_SCORE = "low_match_score"  # Match score less than threshold
    NO_COMPANY_INFO = "no_company_info"  # Entity lookup empty/404
    API_ERROR = "api_error"  # 5xx, timeout, network
    RATE_LIMIT = "rate_limit"  # 429


class FailureClassifier:
    """Classifies failures as retryable or permanent."""

    DO_NOT_RETRY = frozenset({FailureType.NO_PERMID, FailureType.NO_COMPANY_INFO, FailureType.LOW_MATCH_SCORE})

    @classmethod
    def is_retryable(cls, failure_type: FailureType) -> bool:
        """Check if a failure type should be retried.

        Args:
            failure_type: The type of failure.

        Returns:
            True if the failure is transient and should be retried.
        """
        return failure_type not in cls.DO_NOT_RETRY

    @classmethod
    def classify_from_response(
        cls,
        response: dict,
        empty_data: bool,
        category: Literal["permid", "company_info"],
    ) -> FailureType:
        """Classify failure from API response.

        Args:
            response: API response dict with status_code and optional error.
            empty_data: True if parse returned empty (no permid or no company info).
            category: "permid" or "company_info" for permanent failure type.

        Returns:
            The classified FailureType.
        """
        status_code = response.get("status_code")
        has_error = "error" in response

        if has_error or status_code is None:
            return FailureType.API_ERROR

        if status_code == 429:
            return FailureType.RATE_LIMIT

        if status_code == 200 and empty_data:
            return FailureType.NO_PERMID if category == "permid" else FailureType.NO_COMPANY_INFO

        if 400 <= status_code < 500:
            return FailureType.NO_PERMID if category == "permid" else FailureType.NO_COMPANY_INFO

        return FailureType.API_ERROR

class FailureRegistry:
    """Persists permanent failures to avoid retrying entities that will always fail."""

    def __init__(self, file_path: str):
        """Initialize the FailureRegistry.

        Args:
            file_path: Path to the JSON file for persistence.
        """
        self.file_path = file_path
        self._entries: set[tuple[str, str]] = set()
        self._reasons: dict[tuple[str, str], str] = {}
        self.logger = get_logger("FailureRegistry")
        self.load()

    def load(self) -> None:
        """Load entries from the persistence file."""
        if not self.file_path or not pathlib.Path(self.file_path).exists():
            self._entries = set()    # Becomes the do-not-retry set
            self._reasons = {}
            return

        try:
            data = load_json(self.file_path, return_type="dict")
        except json.JSONDecodeError:
            self._entries = set()
            self._reasons = {}
            return
        entries_data = data.get("entries", [])
        reasons_data = data.get("reasons", {})

        self._entries = {tuple(e) for e in entries_data if len(e) >= 2}
        self._reasons = {}
        for entry in self._entries:
            key = f"{entry[0]}|{entry[1]}"
            if key in reasons_data:
                self._reasons[entry] = reasons_data[key]

    def save(self) -> None:
        """Persist entries to the file."""
        if not self.file_path:
            return

        entries_list = [list(e) for e in self._entries]
        reasons_dict = {f"{e[0]}|{e[1]}": self._reasons.get(e, "") for e in self._entries}
        save_json(self.file_path, {"entries": entries_list, "reasons": reasons_dict})

    def add(self, entity_name: str, identifier: str, reason: str = "") -> None:
        """Add a permanent failure entry.

        Args:
            entity_name: The entity name.
            identifier: The identifier (CUSIP, CIK, etc.).
            reason: Optional reason for debugging.
        """
        key = (entity_name, identifier)
        self._entries.add(key)
        if reason:
            self._reasons[key] = reason
        self.save()

    def contains(self, entity_name: str, identifier: str) -> bool:
        """Check if an entity/identifier is in the do-not-retry list.

        Args:
            entity_name: The entity name.
            identifier: The identifier.

        Returns:
            True if the entry should not be retried.
        """
        return (entity_name, identifier) in self._entries

    def __contains__(self, key: tuple[str, str]) -> bool:
        """Set-like membership check.

        Args:
            key: Tuple of (entity_name, identifier).

        Returns:
            True if the entry should not be retried.
        """
        return key in self._entries