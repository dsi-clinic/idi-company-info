#!/usr/bin/env python3
"""
Unit tests for query_company_info.py
"""

import json
import pathlib
from datetime import datetime
from unittest.mock import Mock, mock_open, patch

import pytest
import requests

from idi_company_info import query_company_info


class TestCreateSession:
    """Tests for create_session function"""

    def test_create_session_returns_session(self):
        """Test that create_session returns a configured Session"""
        session = query_company_info.create_session()

        assert isinstance(session, requests.Session)
        # Verify adapters are mounted
        assert "http://" in session.adapters
        assert "https://" in session.adapters


class TestQueryGeonamesLocation:
    """Tests for query_geonames_location function"""

    def test_query_geonames_success(self):
        """Test successful geonames query"""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "United States",
            "asciiName": "United States",
            "countryName": "United States"
        }
        mock_session.get.return_value = mock_response

        result = query_company_info.query_geonames_location(
            mock_session,
            "http://sws.geonames.org/6252001/",
            "test-username"
        )

        assert result == "United States"
        mock_session.get.assert_called_once()

        # Verify request parameters
        call_args = mock_session.get.call_args
        assert call_args.kwargs["params"]["geonameId"] == "6252001"
        assert call_args.kwargs["params"]["username"] == "test-username"

    def test_query_geonames_fallback_to_ascii(self):
        """Test geonames query falls back to asciiName"""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "asciiName": "United States",
            "countryName": "United States"
        }
        mock_session.get.return_value = mock_response

        result = query_company_info.query_geonames_location(
            mock_session,
            "http://sws.geonames.org/6252001/",
            "test-username"
        )

        assert result == "United States"

    def test_query_geonames_fallback_to_country(self):
        """Test geonames query falls back to countryName"""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "countryName": "United States"
        }
        mock_session.get.return_value = mock_response

        result = query_company_info.query_geonames_location(
            mock_session,
            "http://sws.geonames.org/6252001/",
            "test-username"
        )

        assert result == "United States"

    def test_query_geonames_strips_trailing_slash(self):
        """Test that trailing slash is handled correctly"""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"name": "United States"}
        mock_session.get.return_value = mock_response

        query_company_info.query_geonames_location(
            mock_session,
            "http://sws.geonames.org/6252001/",
            "test-username"
        )

        call_args = mock_session.get.call_args
        assert call_args.kwargs["params"]["geonameId"] == "6252001"


class TestExtractPermidFields:
    """Tests for extract_permid_fields function"""

    def test_extract_fields_basic(self):
        """Test basic field extraction"""
        data = {
            "vcard:organization-name": "Test Company",
            "tr-common:hasPermId": "1-5000051854",
            "mdaas:HeadquartersAddress": "123 Main St",
            "tr-org:hasLEI": "ABC123DEF456",
            "@id": "https://permid.org/1-5000051854"
        }

        mock_session = Mock()

        result = query_company_info.extract_permid_fields(
            data, mock_session, "test-username", resolve_urls=False
        )

        assert result["investor_name"] == "Test Company"
        assert result["permid"] == "1-5000051854"
        assert result["hq_address"] == "123 Main St"
        assert result["lei"] == "ABC123DEF456"
        assert result["id"] == "https://permid.org/1-5000051854"

    def test_extract_fields_with_url_resolution(self):
        """Test field extraction with URL resolution"""
        data = {
            "vcard:organization-name": "Test Company",
            "isIncorporatedIn": "http://sws.geonames.org/6252001/"
        }

        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"name": "United States"}
        mock_session.get.return_value = mock_response

        result = query_company_info.extract_permid_fields(
            data, mock_session, "test-username", resolve_urls=True
        )

        assert result["investor_name"] == "Test Company"
        assert result["incorporated_in"] == "United States"
        mock_session.get.assert_called_once()

    def test_extract_fields_extracts_permid_from_id(self):
        """Test that PermID is extracted from @id when not present"""
        data = {
            "vcard:organization-name": "Test Company",
            "@id": "https://permid.org/1-5000051854"
        }

        mock_session = Mock()

        result = query_company_info.extract_permid_fields(
            data, mock_session, "test-username", resolve_urls=False
        )

        assert result["permid"] == "1-5000051854"


class TestQueryPermidEntity:
    """Tests for query_permid_entity function"""

    def test_query_permid_entity_success(self):
        """Test successful PermID entity query"""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "vcard:organization-name": "Test Company",
            "tr-common:hasPermId": "1-5000051854",
            "@id": "https://permid.org/1-5000051854"
        }
        mock_session.get.return_value = mock_response

        result = query_company_info.query_permid_entity(
            mock_session,
            "https://permid.org/1-5000051854",
            "test-api-key",
            "test-username",
            resolve_urls=False
        )

        assert result["investor_name"] == "Test Company"
        assert result["permid"] == "1-5000051854"

        # Verify API call
        call_args = mock_session.get.call_args
        assert call_args.kwargs["headers"]["X-AG-Access-Token"] == "test-api-key"
        assert call_args.kwargs["headers"]["Accept"] == "application/ld+json"

    def test_query_permid_entity_request_exception(self):
        """Test handling of request exceptions"""
        mock_session = Mock()
        mock_session.get.side_effect = requests.exceptions.RequestException("API Error")

        result = query_company_info.query_permid_entity(
            mock_session,
            "https://permid.org/1-5000051854",
            "test-api-key",
            "test-username"
        )

        assert result is None

    def test_query_permid_entity_http_error(self):
        """Test handling of HTTP errors"""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404")
        mock_session.get.return_value = mock_response

        result = query_company_info.query_permid_entity(
            mock_session,
            "https://permid.org/1-5000051854",
            "test-api-key",
            "test-username"
        )

        assert result is None


class TestLoadData:
    """Tests for load_data function"""

    @patch("idi_company_info.query_company_info.get_unprocessed_investors")
    @patch("idi_company_info.query_company_info.load_existing_results")
    @patch("idi_company_info.query_company_info.load_batch_tracking")
    @patch("idi_company_info.query_company_info.load_permid_data")
    def test_load_data_success(
        self,
        mock_load_permid,
        mock_load_batch,
        mock_load_results,
        mock_get_unprocessed
    ):
        """Test successful data loading"""
        mock_permid_data = {"Company A": ["https://permid.org/1-5000051854"]}
        mock_batch_tracking = {}
        mock_existing_results = []
        mock_unprocessed = ["Company A"]

        mock_load_permid.return_value = mock_permid_data
        mock_load_batch.return_value = mock_batch_tracking
        mock_load_results.return_value = mock_existing_results
        mock_get_unprocessed.return_value = mock_unprocessed

        result = query_company_info.load_data(
            pathlib.Path("/fake/input.json"),
            pathlib.Path("/fake/batch.json"),
            pathlib.Path("/fake/output.json"),
            batch_size=1
        )

        assert result == (mock_permid_data, mock_existing_results, mock_unprocessed)

    @patch("idi_company_info.query_company_info.get_unprocessed_investors")
    @patch("idi_company_info.query_company_info.load_existing_results")
    @patch("idi_company_info.query_company_info.load_batch_tracking")
    @patch("idi_company_info.query_company_info.load_permid_data")
    def test_load_data_no_unprocessed(
        self,
        mock_load_permid,
        mock_load_batch,
        mock_load_results,
        mock_get_unprocessed
    ):
        """Test data loading when all investors are processed"""
        mock_load_permid.return_value = {"Company A": ["https://permid.org/1-5000051854"]}
        mock_load_batch.return_value = {}
        mock_load_results.return_value = []
        mock_get_unprocessed.return_value = []  # All processed

        result = query_company_info.load_data(
            pathlib.Path("/fake/input.json"),
            pathlib.Path("/fake/batch.json"),
            pathlib.Path("/fake/output.json"),
            batch_size=1
        )

        assert result is None


class TestDetectInputFormat:
    """Tests for detect_input_format function"""

    def test_detect_cik_format(self):
        """Test detection of CIK format"""
        cik_data = {
            "Company A": [
                {"ciks": ["0001234567"], "permid": "https://permid.org/1-5000051854"}
            ]
        }
        result = query_company_info.detect_input_format(cik_data)
        assert result == "cik"

    def test_detect_record_format(self):
        """Test detection of Record format"""
        record_data = {
            "Company A": {
                "ticker": "AAPL",
                "mic": "XNAS",
                "permid": "https://permid.org/1-5000051854"
            }
        }
        result = query_company_info.detect_input_format(record_data)
        assert result == "record"

    def test_detect_empty_data(self):
        """Test detection with empty data"""
        with pytest.raises(ValueError, match="Empty input data"):
            query_company_info.detect_input_format({})


class TestNormalizeToUnifiedFormat:
    """Tests for normalize_to_unified_format function"""

    def test_normalize_cik_format(self):
        """Test normalization of CIK format"""
        cik_data = {
            "Company A": [
                {"ciks": ["0001234567"], "permid": "https://permid.org/1-5000051854"}
            ]
        }
        result = query_company_info.normalize_to_unified_format(cik_data, "cik")

        assert "Company A" in result
        assert len(result["Company A"]) == 1
        assert result["Company A"][0]["permid"] == "https://permid.org/1-5000051854"
        assert result["Company A"][0]["ciks"] == ["0001234567"]
        assert result["Company A"][0]["ticker"] is None
        assert result["Company A"][0]["mic"] is None
        assert result["Company A"][0]["match_org_name"] is None

    def test_normalize_record_format(self):
        """Test normalization of Record format"""
        record_data = {
            "Company A": {
                "ticker": "AAPL",
                "mic": "XNAS",
                "permid": "https://permid.org/1-5000051854",
                "match_org_name": "Apple Inc",
                "match_score": "100%",
                "match_level": "Excellent",
                "input_standard_identifier": "Ticker:AAPL",
                "input_name": "APPLE INC"
            }
        }
        result = query_company_info.normalize_to_unified_format(record_data, "record")

        assert "Company A" in result
        assert len(result["Company A"]) == 1
        assert result["Company A"][0]["permid"] == "https://permid.org/1-5000051854"
        assert result["Company A"][0]["ticker"] == "AAPL"
        assert result["Company A"][0]["mic"] == "XNAS"
        assert result["Company A"][0]["match_org_name"] == "Apple Inc"
        assert result["Company A"][0]["ciks"] is None


class TestLoadPermidData:
    """Tests for load_permid_data function"""

    def test_load_permid_data_cik_format(self):
        """Test successful loading of CIK format PermID data"""
        mock_data = {
            "Company A": [
                {"ciks": ["0001234567"], "permid": "https://permid.org/1-5000051854"}
            ]
        }

        m = mock_open(read_data=json.dumps(mock_data))
        with patch("builtins.open", m):
            result = query_company_info.load_permid_data(
                pathlib.Path("/fake/input.json")
            )

        # Should be normalized to unified format
        assert "Company A" in result
        assert result["Company A"][0]["ciks"] == ["0001234567"]
        assert result["Company A"][0]["ticker"] is None

    def test_load_permid_data_record_format(self):
        """Test successful loading of Record format PermID data"""
        mock_data = {
            "Company A": {
                "ticker": "AAPL",
                "mic": "XNAS",
                "permid": "https://permid.org/1-5000051854",
                "match_org_name": "Apple Inc",
                "match_score": "100%",
                "match_level": "Excellent",
                "input_standard_identifier": "Ticker:AAPL",
                "input_name": "APPLE INC"
            }
        }

        m = mock_open(read_data=json.dumps(mock_data))
        with patch("builtins.open", m):
            result = query_company_info.load_permid_data(
                pathlib.Path("/fake/input.json")
            )

        # Should be normalized to unified format
        assert "Company A" in result
        assert result["Company A"][0]["ticker"] == "AAPL"
        assert result["Company A"][0]["ciks"] is None


class TestBatchTracking:
    """Tests for batch tracking functions"""

    def test_load_batch_tracking_existing(self):
        """Test loading existing batch tracking"""
        mock_data = {
            "20240101T120000": {
                "processed_investors": ["Company A"],
                "batch_size": 1
            }
        }

        m = mock_open(read_data=json.dumps(mock_data))
        with patch("builtins.open", m):
            with patch.object(pathlib.Path, "exists", return_value=True):
                result = query_company_info.load_batch_tracking(
                    pathlib.Path("/fake/batch.json")
                )

        assert result == mock_data

    def test_load_batch_tracking_new(self):
        """Test creating new batch tracking"""
        with patch.object(pathlib.Path, "exists", return_value=False):
            result = query_company_info.load_batch_tracking(
                pathlib.Path("/fake/batch.json")
            )

        assert result == {}

    def test_save_batch_tracking(self):
        """Test saving batch tracking"""
        tracking_data = {
            "20240101T120000": {
                "processed_investors": ["Company A"],
                "batch_size": 1
            }
        }

        m = mock_open()
        with patch("builtins.open", m):
            query_company_info.save_batch_tracking(
                pathlib.Path("/fake/batch.json"), tracking_data
            )

        m.assert_called_once_with(pathlib.Path("/fake/batch.json"), "w")


class TestLoadExistingResults:
    """Tests for load_existing_results function"""

    def test_load_existing_results_file_exists(self):
        """Test loading existing results"""
        mock_data = [{"investor_name": "Company A"}]

        m = mock_open(read_data=json.dumps(mock_data))
        with patch("builtins.open", m):
            with patch.object(pathlib.Path, "exists", return_value=True):
                result = query_company_info.load_existing_results(
                    pathlib.Path("/fake/output.json")
                )

        assert result == mock_data

    def test_load_existing_results_no_file(self):
        """Test loading when no results file exists"""
        with patch.object(pathlib.Path, "exists", return_value=False):
            result = query_company_info.load_existing_results(
                pathlib.Path("/fake/output.json"), default_type="list"
            )

        assert result == []


class TestSaveResults:
    """Tests for save_results function"""

    def test_save_results(self):
        """Test saving results"""
        results = [{"investor_name": "Company A"}]

        m = mock_open()
        with patch("builtins.open", m):
            query_company_info.save_results(pathlib.Path("/fake/output.json"), results)

        m.assert_called_once_with(pathlib.Path("/fake/output.json"), "w")


class TestGetUnprocessedInvestors:
    """Tests for get_unprocessed_investors function"""

    def test_get_unprocessed_investors_none_processed(self):
        """Test getting unprocessed investors when none are processed"""
        permid_data = {
            "Company A": ["https://permid.org/1-5000051854"],
            "Company B": ["https://permid.org/1-5000051855"]
        }
        batch_tracking = {}

        result = query_company_info.get_unprocessed_investors(
            permid_data, batch_tracking
        )

        assert len(result) == 2
        assert set(result) == {"Company A", "Company B"}

    def test_get_unprocessed_investors_some_processed(self):
        """Test getting unprocessed investors when some are processed"""
        permid_data = {
            "Company A": ["https://permid.org/1-5000051854"],
            "Company B": ["https://permid.org/1-5000051855"],
            "Company C": ["https://permid.org/1-5000051856"]
        }
        batch_tracking = {
            "20240101T120000": {
                "processed_investors": ["Company A"]
            }
        }

        result = query_company_info.get_unprocessed_investors(
            permid_data, batch_tracking
        )

        assert len(result) == 2
        assert set(result) == {"Company B", "Company C"}


class TestProcessInvestor:
    """Tests for process_investor function"""

    @patch("time.sleep")
    @patch("idi_company_info.query_company_info.query_permid_entity")
    def test_process_investor_success_cik_format(self, mock_query, mock_sleep):
        """Test successful investor processing with CIK format"""
        mock_session = Mock()
        investor_name = "Company A"
        unified_permid_data = [{
            "permid": "https://permid.org/1-5000051854",
            "ciks": ["0001234567"],
            "ticker": None,
            "mic": None,
            "match_org_name": None,
            "match_score": None,
            "match_level": None,
            "input_standard_identifier": None,
            "input_name": None
        }]
        stats = {
            "total_investors": 0,
            "investors_with_multiple_permids": 0,
            "total_permids_queried": 0,
            "successful_queries": 0,
            "failed_queries": 0
        }

        mock_query.return_value = {
            "investor_name": "Company A",
            "permid": "1-5000051854"
        }

        result = query_company_info.process_investor(
            mock_session,
            investor_name,
            unified_permid_data,
            "test-api-key",
            "test-username",
            stats
        )

        assert len(result) == 1
        assert result[0]["investor_name"] == "Company A"
        assert result[0]["original_investor_name"] == "Company A"
        assert result[0]["ciks"] == ["0001234567"]
        assert result[0]["ticker"] is None
        assert result[0]["match_org_name"] is None
        assert stats["total_investors"] == 1
        assert stats["successful_queries"] == 1

    @patch("time.sleep")
    @patch("idi_company_info.query_company_info.query_permid_entity")
    def test_process_investor_success_record_format(self, mock_query, mock_sleep):
        """Test successful investor processing with Record format"""
        mock_session = Mock()
        investor_name = "Company A"
        unified_permid_data = [{
            "permid": "https://permid.org/1-5000051854",
            "ciks": None,
            "ticker": "AAPL",
            "mic": "XNAS",
            "match_org_name": "Apple Inc",
            "match_score": "100%",
            "match_level": "Excellent",
            "input_standard_identifier": "Ticker:AAPL",
            "input_name": "APPLE INC"
        }]
        stats = {
            "total_investors": 0,
            "investors_with_multiple_permids": 0,
            "total_permids_queried": 0,
            "successful_queries": 0,
            "failed_queries": 0
        }

        mock_query.return_value = {
            "investor_name": "Apple Inc",
            "permid": "1-5000051854"
        }

        result = query_company_info.process_investor(
            mock_session,
            investor_name,
            unified_permid_data,
            "test-api-key",
            "test-username",
            stats
        )

        assert len(result) == 1
        assert result[0]["investor_name"] == "Apple Inc"
        assert result[0]["original_investor_name"] == "Company A"
        assert result[0]["ciks"] is None
        assert result[0]["ticker"] == "AAPL"
        assert result[0]["match_org_name"] == "Apple Inc"
        assert result[0]["match_score"] == "100%"
        assert stats["total_investors"] == 1
        assert stats["successful_queries"] == 1

    @patch("time.sleep")
    @patch("idi_company_info.query_company_info.query_permid_entity")
    def test_process_investor_multiple_permids(self, mock_query, mock_sleep):
        """Test processing investor with multiple PermIDs"""
        mock_session = Mock()
        investor_name = "Company A"
        unified_permid_data = [
            {
                "permid": "https://permid.org/1-5000051854",
                "ciks": ["0001234567"],
                "ticker": None,
                "mic": None,
                "match_org_name": None,
                "match_score": None,
                "match_level": None,
                "input_standard_identifier": None,
                "input_name": None
            },
            {
                "permid": "https://permid.org/1-5000051855",
                "ciks": ["0001234568"],
                "ticker": None,
                "mic": None,
                "match_org_name": None,
                "match_score": None,
                "match_level": None,
                "input_standard_identifier": None,
                "input_name": None
            }
        ]
        stats = {
            "total_investors": 0,
            "investors_with_multiple_permids": 0,
            "total_permids_queried": 0,
            "successful_queries": 0,
            "failed_queries": 0
        }

        mock_query.side_effect = [
            {"investor_name": "Company A", "permid": "1-5000051854"},
            {"investor_name": "Company A", "permid": "1-5000051855"}
        ]

        result = query_company_info.process_investor(
            mock_session,
            investor_name,
            unified_permid_data,
            "test-api-key",
            "test-username",
            stats
        )

        assert len(result) == 2
        assert result[0]["ciks"] == ["0001234567"]
        assert result[1]["ciks"] == ["0001234568"]
        assert stats["investors_with_multiple_permids"] == 1

    @patch("time.sleep")
    @patch("idi_company_info.query_company_info.query_permid_entity")
    def test_process_investor_with_failure(self, mock_query, mock_sleep):
        """Test processing investor with failed query"""
        mock_session = Mock()
        investor_name = "Company A"
        unified_permid_data = [{
            "permid": "https://permid.org/1-5000051854",
            "ciks": ["0001234567"],
            "ticker": None,
            "mic": None,
            "match_org_name": None,
            "match_score": None,
            "match_level": None,
            "input_standard_identifier": None,
            "input_name": None
        }]
        stats = {
            "total_investors": 0,
            "investors_with_multiple_permids": 0,
            "total_permids_queried": 0,
            "successful_queries": 0,
            "failed_queries": 0
        }

        mock_query.return_value = None

        result = query_company_info.process_investor(
            mock_session,
            investor_name,
            unified_permid_data,
            "test-api-key",
            "test-username",
            stats
        )

        assert len(result) == 1
        assert result[0] is None
        assert stats["failed_queries"] == 1


class TestProcessBatch:
    """Tests for process_batch function"""

    @patch("idi_company_info.query_company_info.process_investor")
    def test_process_batch_success(self, mock_process_investor):
        """Test successful batch processing"""
        mock_session = Mock()
        permid_data = {
            "Company A": [{
                "permid": "https://permid.org/1-5000051854",
                "ciks": ["0001234567"],
                "ticker": None,
                "mic": None,
                "match_org_name": None,
                "match_score": None,
                "match_level": None,
                "input_standard_identifier": None,
                "input_name": None
            }],
            "Company B": [{
                "permid": "https://permid.org/1-5000051855",
                "ciks": ["0001234568"],
                "ticker": None,
                "mic": None,
                "match_org_name": None,
                "match_score": None,
                "match_level": None,
                "input_standard_identifier": None,
                "input_name": None
            }]
        }
        investors_to_process = ["Company A", "Company B"]

        mock_process_investor.side_effect = [
            [{"investor_name": "Company A"}],
            [{"investor_name": "Company B"}]
        ]

        results, processed, stats = query_company_info.process_batch(
            mock_session,
            permid_data,
            investors_to_process,
            batch_size=2,
            api_key="test-api-key",
            geonames_user="test-username"
        )

        assert len(results) == 2
        assert len(processed) == 2
        assert "Company A" in processed
        assert "Company B" in processed

    @patch("idi_company_info.query_company_info.process_investor")
    def test_process_batch_filters_null_permids(self, mock_process_investor):
        """Test that null PermIDs are filtered out"""
        mock_session = Mock()
        permid_data = {
            "Company A": [
                {
                    "permid": "https://permid.org/1-5000051854",
                    "ciks": ["0001234567"],
                    "ticker": None,
                    "mic": None,
                    "match_org_name": None,
                    "match_score": None,
                    "match_level": None,
                    "input_standard_identifier": None,
                    "input_name": None
                },
                {
                    "permid": None,
                    "ciks": ["0001234568"],
                    "ticker": None,
                    "mic": None,
                    "match_org_name": None,
                    "match_score": None,
                    "match_level": None,
                    "input_standard_identifier": None,
                    "input_name": None
                }
            ]
        }
        investors_to_process = ["Company A"]

        mock_process_investor.return_value = [{"investor_name": "Company A"}]

        results, processed, stats = query_company_info.process_batch(
            mock_session,
            permid_data,
            investors_to_process,
            batch_size=1,
            api_key="test-api-key",
            geonames_user="test-username"
        )

        # Verify that process_investor was called with filtered unified data
        call_args = mock_process_investor.call_args
        unified_data_arg = call_args[0][2]
        # Should only have the first item (with valid PermID)
        assert len(unified_data_arg) == 1
        assert unified_data_arg[0]["permid"] is not None

    @patch("idi_company_info.query_company_info.process_investor")
    def test_process_batch_skips_all_null_permids(self, mock_process_investor):
        """Test that investors with all null PermIDs are skipped"""
        mock_session = Mock()
        permid_data = {
            "Company A": [
                {
                    "permid": None,
                    "ciks": ["0001234567"],
                    "ticker": None,
                    "mic": None,
                    "match_org_name": None,
                    "match_score": None,
                    "match_level": None,
                    "input_standard_identifier": None,
                    "input_name": None
                },
                {
                    "permid": None,
                    "ciks": ["0001234568"],
                    "ticker": None,
                    "mic": None,
                    "match_org_name": None,
                    "match_score": None,
                    "match_level": None,
                    "input_standard_identifier": None,
                    "input_name": None
                }
            ]
        }
        investors_to_process = ["Company A"]

        results, processed, stats = query_company_info.process_batch(
            mock_session,
            permid_data,
            investors_to_process,
            batch_size=1,
            api_key="test-api-key",
            geonames_user="test-username"
        )

        # Should not call process_investor
        mock_process_investor.assert_not_called()
        # Should still mark as processed
        assert "Company A" in processed


class TestFinalizeBatch:
    """Tests for finalize_batch function"""

    @patch("idi_company_info.query_company_info.save_batch_tracking")
    @patch("idi_company_info.query_company_info.load_batch_tracking")
    @patch("idi_company_info.query_company_info.save_results")
    def test_finalize_batch(self, mock_save_results, mock_load_batch, mock_save_batch):
        """Test batch finalization"""
        output_file = pathlib.Path("/fake/output.json")
        batch_file = pathlib.Path("/fake/batch.json")
        existing_results = [{"investor_name": "Company A"}]
        batch_results = [{"investor_name": "Company B"}]
        processed_investors = ["Company B"]
        batch_stats = {"total_investors": 1}

        mock_load_batch.return_value = {}

        result = query_company_info.finalize_batch(
            output_file,
            batch_file,
            existing_results,
            batch_results,
            processed_investors,
            batch_stats
        )

        assert len(result) == 2
        mock_save_results.assert_called_once()
        mock_save_batch.assert_called_once()


class TestMain:
    """Tests for main function"""

    @patch("idi_company_info.query_company_info.print_stats")
    @patch("idi_company_info.query_company_info.finalize_batch")
    @patch("idi_company_info.query_company_info.process_batch")
    @patch("idi_company_info.query_company_info.create_session")
    @patch("idi_company_info.query_company_info.load_data")
    @patch("idi_company_info.query_company_info.get_args")
    def test_main_integration(
        self,
        mock_get_args,
        mock_load_data,
        mock_create_session,
        mock_process_batch,
        mock_finalize_batch,
        mock_print_stats
    ):
        """Test main function integration"""
        # Setup mocks
        mock_args = Mock()
        mock_args.api_key = "test-api-key"
        mock_args.geonames_user = "test-username"
        mock_args.input_file = pathlib.Path("/fake/input.json")
        mock_args.output_file = pathlib.Path("/fake/output.json")
        mock_args.batch_file = pathlib.Path("/fake/batch.json")
        mock_args.batch_size = 2
        mock_get_args.return_value = mock_args

        mock_permid_data = {"Company A": ["https://permid.org/1-5000051854"]}
        mock_existing_results = []
        mock_unprocessed = ["Company A"]
        mock_load_data.return_value = (
            mock_permid_data,
            mock_existing_results,
            mock_unprocessed
        )

        mock_session = Mock()
        mock_create_session.return_value = mock_session

        mock_batch_results = [{"investor_name": "Company A"}]
        mock_processed = ["Company A"]
        mock_stats = {"total_investors": 1}
        mock_process_batch.return_value = (
            mock_batch_results,
            mock_processed,
            mock_stats
        )

        mock_all_results = mock_existing_results + mock_batch_results
        mock_finalize_batch.return_value = mock_all_results

        # Mock mkdir
        with patch.object(pathlib.Path, "mkdir"):
            # Run main
            query_company_info.main()

        # Verify calls
        mock_get_args.assert_called_once()
        mock_load_data.assert_called_once()
        mock_create_session.assert_called_once()
        mock_process_batch.assert_called_once()
        mock_finalize_batch.assert_called_once()
        mock_print_stats.assert_called_once()
