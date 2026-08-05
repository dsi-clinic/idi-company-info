"""Classifies failures as retryable or permanent (do-not-retry)."""

# Standard library imports
from enum import StrEnum
from typing import Literal

# Third party imports
from idi_ftm2j_shared.failures import FailureClassifier


class FailureType(StrEnum):
    """Failure type for classification."""

    NO_PERMID = "no_permid"  # Empty response, identifier not in DB
    LOW_MATCH_SCORE = "low_match_score"  # Match score less than threshold
    NO_COMPANY_INFO = "no_company_info"  # Entity lookup empty/404
    API_ERROR = "api_error"  # 5xx, timeout, network
    RATE_LIMIT = "rate_limit"  # 429


class QuotaExhaustedError(Exception):
    """The shared daily PermID request quota is spent.

    Raised by the retrieval stages on a 429 so the caller can stop the run early rather
    than spend one doomed request per remaining candidate. Retryable in the sense that a
    later run picks the work back up — nothing is added to the do-not-retry registry.
    """


_HTTP_RATE_LIMIT = 429
_HTTP_OK = 200
_HTTP_CLIENT_ERROR_MIN = 400
_HTTP_SERVER_ERROR_MIN = 500


class CompanyInfoFailureClassifier(FailureClassifier):
    """Classifies failures as retryable or permanent."""

    _DO_NOT_RETRY = frozenset(
        {FailureType.NO_PERMID, FailureType.NO_COMPANY_INFO, FailureType.LOW_MATCH_SCORE}
    )

    @property
    def do_not_retry(self) -> frozenset:
        """Return the set of failure types that should not be retried."""
        return self._DO_NOT_RETRY

    @classmethod
    def is_retryable(cls, failure_type: FailureType) -> bool:
        """Check if a failure type should be retried.

        Args:
            failure_type: The type of failure.

        Returns:
            True if the failure is transient and should be retried.
        """
        return failure_type not in cls._DO_NOT_RETRY

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

        # 429 is tested before the generic error branch. `raise_for_status` means a
        # rate-limited response always carries an `error` key too, so checking has_error
        # first would classify every quota rejection as API_ERROR and lose the distinction
        # callers need to stop the run early. Both types are retryable, so the ordering
        # does not change what lands in the do-not-retry registry.
        if status_code == _HTTP_RATE_LIMIT:
            return FailureType.RATE_LIMIT

        if has_error or status_code is None:
            return FailureType.API_ERROR

        if status_code == _HTTP_OK and empty_data:
            return FailureType.NO_PERMID if category == "permid" else FailureType.NO_COMPANY_INFO

        if _HTTP_CLIENT_ERROR_MIN <= status_code < _HTTP_SERVER_ERROR_MIN:
            return FailureType.NO_PERMID if category == "permid" else FailureType.NO_COMPANY_INFO

        return FailureType.API_ERROR
