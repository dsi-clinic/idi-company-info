#!/usr/bin/env python3
"""Unit tests for idi_company_info.failures classification."""

import pytest

from idi_company_info.failures import CompanyInfoFailureClassifier, FailureType


class TestClassifyRateLimit:
    """Tests that a 429 classifies as RATE_LIMIT (see GitHub issue #34).

    `raise_for_status` means a rate-limited response always carries an `error` key as
    well as status_code 429, so the 429 branch must be tested before the generic error
    branch. Otherwise every quota rejection reads as API_ERROR and callers cannot tell
    quota exhaustion apart from an ordinary failure.
    """

    @pytest.mark.parametrize("category", ["permid", "company_info"])
    def test_429_with_error_key_is_rate_limit(self, category):
        """Test the real-world shape: 429 plus the error set by raise_for_status."""
        response = {"status_code": 429, "error": "429 Client Error: Too Many Requests"}
        result = CompanyInfoFailureClassifier.classify_from_response(
            response, empty_data=False, category=category
        )
        assert result is FailureType.RATE_LIMIT

    def test_429_without_error_key_is_rate_limit(self):
        """Test that a bare 429 also classifies as RATE_LIMIT."""
        result = CompanyInfoFailureClassifier.classify_from_response(
            {"status_code": 429}, empty_data=False, category="company_info"
        )
        assert result is FailureType.RATE_LIMIT

    def test_429_is_rate_limit_even_when_data_is_empty(self):
        """Test that empty_data does not downgrade a 429 to a permanent failure."""
        result = CompanyInfoFailureClassifier.classify_from_response(
            {"status_code": 429, "error": "rate limited"},
            empty_data=True,
            category="company_info",
        )
        assert result is FailureType.RATE_LIMIT


class TestClassifyOtherFailures:
    """Tests that reordering the 429 check left the other branches intact."""

    def test_error_without_status_code_is_api_error(self):
        """Test that a transport error with no status code is an API_ERROR."""
        result = CompanyInfoFailureClassifier.classify_from_response(
            {"error": "Timeout querying https://permid.org/1-1"},
            empty_data=False,
            category="company_info",
        )
        assert result is FailureType.API_ERROR

    def test_non_429_http_error_is_api_error(self):
        """Test that a 500 carrying an error key is an API_ERROR."""
        result = CompanyInfoFailureClassifier.classify_from_response(
            {"status_code": 500, "error": "500 Server Error"},
            empty_data=False,
            category="company_info",
        )
        assert result is FailureType.API_ERROR

    def test_missing_status_code_is_api_error(self):
        """Test that an empty response dict is an API_ERROR."""
        result = CompanyInfoFailureClassifier.classify_from_response(
            {}, empty_data=False, category="company_info"
        )
        assert result is FailureType.API_ERROR

    @pytest.mark.parametrize(
        ("category", "expected"),
        [("permid", FailureType.NO_PERMID), ("company_info", FailureType.NO_COMPANY_INFO)],
    )
    def test_200_with_empty_data_is_permanent(self, category, expected):
        """Test that a successful-but-empty response is a permanent failure."""
        result = CompanyInfoFailureClassifier.classify_from_response(
            {"status_code": 200}, empty_data=True, category=category
        )
        assert result is expected

    @pytest.mark.parametrize(
        ("category", "expected"),
        [("permid", FailureType.NO_PERMID), ("company_info", FailureType.NO_COMPANY_INFO)],
    )
    def test_404_is_permanent(self, category, expected):
        """Test that a 404 with no error key is a permanent failure."""
        result = CompanyInfoFailureClassifier.classify_from_response(
            {"status_code": 404}, empty_data=False, category=category
        )
        assert result is expected


class TestRetryability:
    """Tests which failure types are eligible for a later run."""

    @pytest.mark.parametrize("failure_type", [FailureType.RATE_LIMIT, FailureType.API_ERROR])
    def test_transient_types_are_retryable(self, failure_type):
        """Test that quota and transport failures are not blacklisted."""
        assert CompanyInfoFailureClassifier.is_retryable(failure_type)
        assert failure_type not in CompanyInfoFailureClassifier().do_not_retry

    @pytest.mark.parametrize(
        "failure_type",
        [FailureType.NO_PERMID, FailureType.NO_COMPANY_INFO, FailureType.LOW_MATCH_SCORE],
    )
    def test_permanent_types_are_not_retryable(self, failure_type):
        """Test that permanent failures stay in the do-not-retry set."""
        assert not CompanyInfoFailureClassifier.is_retryable(failure_type)
        assert failure_type in CompanyInfoFailureClassifier().do_not_retry
