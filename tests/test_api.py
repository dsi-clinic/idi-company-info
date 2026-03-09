#!/usr/bin/env python3
"""
Unit tests for idi_company_info.common.api
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from idi_company_info.common.api import (
    ApiClient,
    GeonamesApi,
    LSEGEntityLookup,
    LsegEntitySearch,
    LsegRecordMatch,
)


# Concrete implementation for testing ApiClient base class methods
class ConcreteApiClient(ApiClient):
    """Concrete ApiClient for testing base class methods."""

    def query_endpoint(self):
        """Required abstract method implementation."""
        return self.get("https://example.com")


class TestApiClient:
    """Tests for ApiClient base class."""

    def test_init_stores_api_key_and_max_retries(self):
        """Test that __init__ stores api_key and max_retries."""
        with patch("idi_company_info.common.api.get_logger"):
            client = ConcreteApiClient(api_key="test-key", max_retries=5)
            assert client.api_key == "test-key"
            assert client.max_retries == 5

    def test_init_uses_default_max_retries_when_none(self):
        """Test that max_retries defaults when None."""
        with patch("idi_company_info.common.api.get_logger"):
            client = ConcreteApiClient(api_key="test-key", max_retries=None)
            assert client.max_retries == ApiClient.DEFAULT_MAX_RETRIES

    def test_init_creates_logger_when_none_provided(self):
        """Test that get_logger is called when logger is None."""
        with patch("idi_company_info.common.api.get_logger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger
            client = ConcreteApiClient(api_key="key")
            mock_get_logger.assert_called_once()
            assert client.logger is mock_logger

    def test_session_is_cached(self):
        """Test that session is created once and cached."""
        with patch("idi_company_info.common.api.get_logger"):
            client = ConcreteApiClient(api_key="key")
            session1 = client.session
            session2 = client.session
            assert session1 is session2

    def test_session_has_retry_adapter_mounted(self):
        """Test that session has HTTPAdapter with retry strategy mounted."""
        with patch("idi_company_info.common.api.get_logger"):
            client = ConcreteApiClient(api_key="key")
            session = client.session
            assert "https://" in session.adapters
            assert "http://" in session.adapters

    def test_get_calls_session_get_with_params(self):
        """Test that get() calls session.get with correct arguments."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        with patch("idi_company_info.common.api.get_logger"):
            client = ConcreteApiClient(api_key="key")
            with patch.object(client, "session") as mock_session:
                mock_session.get.return_value = mock_response
                result = client.get(
                    "https://example.com",
                    params={"q": "test"},
                    headers={"X-Custom": "value"},
                )
                mock_session.get.assert_called_once_with(
                    "https://example.com",
                    params={"q": "test"},
                    headers={"X-Custom": "value"},
                    timeout=ApiClient.REQUEST_TIMEOUT,
                )
                assert result is mock_response

    def test_get_raises_on_http_error(self):
        """Test that get() raises when response has error status."""
        with patch("idi_company_info.common.api.get_logger"):
            client = ConcreteApiClient(api_key="key")
            with patch.object(client, "session") as mock_session:
                mock_response = MagicMock()
                mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404")
                mock_session.get.return_value = mock_response
                with pytest.raises(requests.exceptions.HTTPError):
                    client.get("https://example.com")

    def test_post_calls_session_post_with_params(self):
        """Test that post() calls session.post with correct arguments."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("idi_company_info.common.api.get_logger"):
            client = ConcreteApiClient(api_key="key")
            with patch.object(client, "session") as mock_session:
                mock_session.post.return_value = mock_response
                result = client.post(
                    "https://example.com",
                    data="csv,data",
                    headers={"Content-Type": "text/plain"},
                )
                mock_session.post.assert_called_once_with(
                    "https://example.com",
                    headers={"Content-Type": "text/plain"},
                    data="csv,data",
                    timeout=ApiClient.REQUEST_TIMEOUT,
                )
                assert result is mock_response


class TestLsegEntitySearch:
    """Tests for LsegEntitySearch."""

    def test_query_endpoint_success_returns_data_dict(self):
        """Test that query_endpoint returns data dict on success."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"results": [{"id": "1"}]}
        mock_response.status_code = 200
        mock_response.url = "https://api-eit.refinitiv.com/permid/search?q=test"

        with patch("idi_company_info.common.api.get_logger"):
            client = LsegEntitySearch(api_key="test-key")
            with patch.object(client, "get", return_value=mock_response):
                result = client.query_endpoint(params={"q": "cik:0001234567"})

        assert result["data"] == {"results": [{"id": "1"}]}
        assert result["status_code"] == 200
        assert "permid/search" in result["url"]

    def test_query_endpoint_success_calls_get_with_correct_args(self):
        """Test that query_endpoint calls get with correct URL and headers."""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.status_code = 200
        mock_response.url = "https://example.com"

        with patch("idi_company_info.common.api.get_logger"):
            client = LsegEntitySearch(api_key="api-key-123")
            with patch.object(client, "get", return_value=mock_response) as mock_get:
                client.query_endpoint(params={"q": "cik:0001234567", "format": "json"})
                mock_get.assert_called_once_with(
                    url=LsegEntitySearch.ENTITY_SEARCH_URL,
                    params={"q": "cik:0001234567", "format": "json"},
                    headers={
                        "X-AG-Access-Token": "api-key-123",
                        "Accept": "application/json",
                        "User-Agent": ApiClient.USER_AGENT,
                    },
                )

    def test_query_endpoint_failure_returns_error_dict(self):
        """Test that query_endpoint returns error dict on failure."""
        with patch("idi_company_info.common.api.get_logger"):
            client = LsegEntitySearch(api_key="key")
            with patch.object(
                client,
                "get",
                side_effect=requests.exceptions.RequestException("400 Bad Request"),
            ):
                result = client.query_endpoint(params={"q": ""})

        assert "error" in result
        assert "400 Bad Request" in result["error"]


class TestLsegRecordMatch:
    """Tests for LsegRecordMatch."""

    def test_query_endpoint_success_returns_data_dict(self):
        """Test that query_endpoint returns data dict on success."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"matches": []}
        mock_response.status_code = 200
        mock_response.url = "https://api-eit.refinitiv.com/permid/match"

        with patch("idi_company_info.common.api.get_logger"):
            client = LsegRecordMatch(api_key="test-key")
            with patch.object(client, "post", return_value=mock_response):
                result = client.query_endpoint(csv_data="LocalID,Name,Ticker\n100001,Test Inc,TEST")

        assert result["data"] == {"matches": []}
        assert result["status_code"] == 200

    def test_query_endpoint_success_calls_post_with_correct_args(self):
        """Test that query_endpoint calls post with correct URL and headers."""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.status_code = 200
        mock_response.url = "https://example.com"

        csv_data = "LocalID,Name,Ticker\n100001,S&P Global,SPGI"
        with patch("idi_company_info.common.api.get_logger"):
            client = LsegRecordMatch(api_key="api-key-456")
            with patch.object(client, "post", return_value=mock_response) as mock_post:
                client.query_endpoint(csv_data=csv_data)
                mock_post.assert_called_once_with(
                    url=LsegRecordMatch.RECORD_MATCH_URL,
                    data=csv_data,
                    headers={
                        "accept": "application/json",
                        "Content-Type": "text/plain",
                        "x-ag-access-token": "api-key-456",
                        "x-openmatch-numberOfMatchesPerRecord": "1",
                        "x-openmatch-dataType": "Organization",
                        "User-Agent": ApiClient.USER_AGENT,
                    },
                )

    def test_query_endpoint_failure_returns_error_dict(self):
        """Test that query_endpoint returns error dict on failure."""
        with patch("idi_company_info.common.api.get_logger"):
            client = LsegRecordMatch(api_key="key")
            with patch.object(
                client,
                "post",
                side_effect=requests.exceptions.RequestException("500 Server Error"),
            ):
                result = client.query_endpoint(csv_data="invalid")

        assert "error" in result
        assert "500 Server Error" in result["error"]


class TestLSEGEntityLookup:
    """Tests for LSEGEntityLookup."""

    def test_query_endpoint_success_returns_data_dict(self):
        """Test that query_endpoint returns data dict on success."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"@id": "https://permid.org/1-123"}
        mock_response.status_code = 200
        mock_response.url = "https://permid.org/1-123"

        with patch("idi_company_info.common.api.get_logger"):
            client = LSEGEntityLookup(api_key="test-key")
            with patch.object(client, "get", return_value=mock_response):
                result = client.query_endpoint(permid_url="https://permid.org/1-123")

        assert result["data"] == {"@id": "https://permid.org/1-123"}
        assert result["status_code"] == 200

    def test_query_endpoint_success_calls_get_with_correct_args(self):
        """Test that query_endpoint calls get with correct URL and params."""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.status_code = 200
        mock_response.url = "https://permid.org/1-123"

        permid_url = "https://permid.org/1-4295904495"
        with patch("idi_company_info.common.api.get_logger"):
            client = LSEGEntityLookup(api_key="api-key-789")
            with patch.object(client, "get", return_value=mock_response) as mock_get:
                client.query_endpoint(permid_url=permid_url)
                mock_get.assert_called_once_with(
                    url=permid_url,
                    headers={
                        "X-AG-Access-Token": "api-key-789",
                        "Accept": "application/ld+json",
                    },
                    params={"format": "json-ld"},
                )

    def test_query_endpoint_failure_returns_error_dict(self):
        """Test that query_endpoint returns error dict on failure."""
        with patch("idi_company_info.common.api.get_logger"):
            client = LSEGEntityLookup(api_key="key")
            with patch.object(
                client,
                "get",
                side_effect=requests.exceptions.RequestException("404 Not Found"),
            ):
                result = client.query_endpoint(permid_url="https://permid.org/1-invalid")

        assert "error" in result
        assert "404 Not Found" in result["error"]


class TestGeonamesApi:
    """Tests for GeonamesApi."""

    def test_query_endpoint_success_returns_data_dict(self):
        """Test that query_endpoint returns data dict on success."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "geonameId": 6252001,
            "name": "United States",
            "countryName": "United States",
        }
        mock_response.status_code = 200
        mock_response.url = "http://api.geonames.org/getJSON"

        with patch("idi_company_info.common.api.get_logger"):
            client = GeonamesApi(api_key="dummy", geonames_user="test-user")
            with patch.object(client, "get", return_value=mock_response):
                result = client.query_endpoint(
                    geoname_url="http://sws.geonames.org/6252001/",
                )

        assert result["data"] == {
            "geonameId": 6252001,
            "name": "United States",
            "countryName": "United States",
        }
        assert result["status_code"] == 200

    def test_query_endpoint_success_calls_get_with_correct_args(self):
        """Test that query_endpoint extracts geoname ID and calls get with correct params."""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.status_code = 200
        mock_response.url = "http://api.geonames.org/getJSON"

        with patch("idi_company_info.common.api.get_logger"):
            client = GeonamesApi(api_key="dummy", geonames_user="my-geonames-user")
            with patch.object(client, "get", return_value=mock_response) as mock_get:
                client.query_endpoint(
                    geoname_url="http://sws.geonames.org/6252001/",
                )
                mock_get.assert_called_once_with(
                    url=GeonamesApi.GEONAMES_API_URL,
                    params={
                        "geonameId": "6252001",
                        "username": "my-geonames-user",
                    },
                    headers=None,
                )

    def test_query_endpoint_strips_trailing_slash_from_url(self):
        """Test that geoname ID is correctly extracted from URL with trailing slash."""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.status_code = 200
        mock_response.url = "http://api.geonames.org/getJSON"

        with patch("idi_company_info.common.api.get_logger"):
            client = GeonamesApi(api_key="dummy", geonames_user="user")
            with patch.object(client, "get", return_value=mock_response) as mock_get:
                client.query_endpoint(
                    geoname_url="http://sws.geonames.org/6252001/",
                )
                mock_get.assert_called_once()
                assert mock_get.call_args[1]["params"]["geonameId"] == "6252001"

    def test_query_endpoint_failure_returns_error_dict(self):
        """Test that query_endpoint returns error dict on failure."""
        with patch("idi_company_info.common.api.get_logger"):
            client = GeonamesApi(api_key="dummy", geonames_user="invalid-user")
            with patch.object(
                client,
                "get",
                side_effect=requests.exceptions.RequestException("401 Unauthorized"),
            ):
                result = client.query_endpoint(
                    geoname_url="http://sws.geonames.org/6252001/",
                )

        assert "error" in result
        assert "401 Unauthorized" in result["error"]
