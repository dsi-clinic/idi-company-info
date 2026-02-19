#!/usr/bin/env python3
"""
Unit tests for query_permid.py
"""

import json
import pathlib
from datetime import datetime
from unittest.mock import Mock, patch, mock_open

import pytest
import requests

from idi_company_info import query_permid


class TestCreateSession:
    """Tests for create_session function"""

    def test_create_session_returns_session(self):
        """Test that create_session returns a configured Session"""
        session = query_permid.create_session()

        assert isinstance(session, requests.Session)
        # Verify adapters are mounted
        assert "http://" in session.adapters
        assert "https://" in session.adapters


class TestQueryPermidByCik:
    """Tests for query_permid_by_cik function"""

    def test_query_permid_success(self):
        """Test successful PermID query"""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "organizations": {
                    "entities": [
                        {"@id": "https://permid.org/1-5000051854"}
                    ]
                }
            }
        }
        mock_session.get.return_value = mock_response

        result = query_permid.query_permid_by_cik(
            mock_session, "0001234567", "test-api-key"
        )

        assert result == "https://permid.org/1-5000051854"
        mock_session.get.assert_called_once()

        # Verify request parameters
        call_args = mock_session.get.call_args
        assert call_args.kwargs["params"]["q"] == "cik:0001234567"
        assert call_args.kwargs["headers"]["X-AG-Access-Token"] == "test-api-key"

    def test_query_permid_no_results(self):
        """Test query with no results"""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "organizations": {
                    "entities": []
                }
            }
        }
        mock_session.get.return_value = mock_response

        result = query_permid.query_permid_by_cik(
            mock_session, "0001234567", "test-api-key"
        )

        assert result is None

    def test_query_permid_request_exception(self):
        """Test handling of request exceptions"""
        mock_session = Mock()
        mock_session.get.side_effect = requests.exceptions.RequestException("API Error")

        result = query_permid.query_permid_by_cik(
            mock_session, "0001234567", "test-api-key"
        )

        assert result is None

    def test_query_permid_http_error(self):
        """Test handling of HTTP errors"""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404")
        mock_session.get.return_value = mock_response

        result = query_permid.query_permid_by_cik(
            mock_session, "0001234567", "test-api-key"
        )

        assert result is None


class TestLoadCikData:
    """Tests for load_cik_data function"""

    def test_load_cik_data_success(self):
        """Test successful loading of CIK data"""
        mock_data = {
            "Company A": ["0001234567", "0001234568"],
            "Company B": ["0001234569"]
        }

        m = mock_open(read_data=json.dumps(mock_data))
        with patch("builtins.open", m):
            result = query_permid.load_cik_data(pathlib.Path("/fake/input.json"))

        assert result == mock_data
        m.assert_called_once_with(pathlib.Path("/fake/input.json"))


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
                result = query_permid.load_batch_tracking(
                    pathlib.Path("/fake/batch.json")
                )

        assert result == mock_data

    def test_load_batch_tracking_new(self):
        """Test creating new batch tracking"""
        with patch.object(pathlib.Path, "exists", return_value=False):
            result = query_permid.load_batch_tracking(pathlib.Path("/fake/batch.json"))

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
            query_permid.save_batch_tracking(
                pathlib.Path("/fake/batch.json"), tracking_data
            )

        m.assert_called_once_with(pathlib.Path("/fake/batch.json"), "w")


class TestLoadExistingResults:
    """Tests for load_existing_results function"""

    def test_load_existing_results_file_exists(self):
        """Test loading existing results"""
        mock_data = {"Company A": ["https://permid.org/1-5000051854"]}

        m = mock_open(read_data=json.dumps(mock_data))
        with patch("builtins.open", m):
            with patch.object(pathlib.Path, "exists", return_value=True):
                result = query_permid.load_existing_results(
                    pathlib.Path("/fake/output.json")
                )

        assert result == mock_data

    def test_load_existing_results_no_file(self):
        """Test loading when no results file exists"""
        with patch.object(pathlib.Path, "exists", return_value=False):
            result = query_permid.load_existing_results(
                pathlib.Path("/fake/output.json")
            )

        assert result == {}


class TestSaveResults:
    """Tests for save_results function"""

    def test_save_results(self):
        """Test saving results"""
        results = {"Company A": ["https://permid.org/1-5000051854"]}

        m = mock_open()
        with patch("builtins.open", m):
            query_permid.save_results(pathlib.Path("/fake/output.json"), results)

        m.assert_called_once_with(pathlib.Path("/fake/output.json"), "w")


class TestGetUnprocessedInvestors:
    """Tests for get_unprocessed_investors function"""

    def test_get_unprocessed_investors_none_processed(self):
        """Test getting unprocessed investors when none are processed"""
        cik_data = {
            "Company A": ["0001234567"],
            "Company B": ["0001234568"],
            "Company C": ["0001234569"]
        }
        batch_tracking = {}

        result = query_permid.get_unprocessed_investors(cik_data, batch_tracking)

        assert len(result) == 3
        assert set(result) == {"Company A", "Company B", "Company C"}

    def test_get_unprocessed_investors_some_processed(self):
        """Test getting unprocessed investors when some are processed"""
        cik_data = {
            "Company A": ["0001234567"],
            "Company B": ["0001234568"],
            "Company C": ["0001234569"]
        }
        batch_tracking = {
            "20240101T120000": {
                "processed_investors": ["Company A"]
            }
        }

        result = query_permid.get_unprocessed_investors(cik_data, batch_tracking)

        assert len(result) == 2
        assert set(result) == {"Company B", "Company C"}

    def test_get_unprocessed_investors_all_processed(self):
        """Test getting unprocessed investors when all are processed"""
        cik_data = {
            "Company A": ["0001234567"],
            "Company B": ["0001234568"]
        }
        batch_tracking = {
            "20240101T120000": {
                "processed_investors": ["Company A", "Company B"]
            }
        }

        result = query_permid.get_unprocessed_investors(cik_data, batch_tracking)

        assert len(result) == 0


class TestProcessBatch:
    """Tests for process_batch function"""

    @patch("time.sleep")
    @patch("idi_company_info.query_permid.query_permid_by_cik")
    def test_process_batch_success(self, mock_query, mock_sleep):
        """Test successful batch processing"""
        mock_session = Mock()
        cik_data = {
            "Company A": ["0001234567", "0001234568"],
            "Company B": ["0001234569"]
        }
        investors_to_process = ["Company A", "Company B"]

        mock_query.side_effect = [
            "https://permid.org/1-5000051854",
            "https://permid.org/1-5000051855",
            "https://permid.org/1-5000051856"
        ]

        results, processed, stats = query_permid.process_cik_batch(
            mock_session,
            cik_data,
            investors_to_process,
            batch_size=2,
            api_key="test-api-key"
        )

        assert len(results) == 2
        assert "Company A" in results
        assert "Company B" in results
        assert len(results["Company A"]) == 2
        assert len(results["Company B"]) == 1
        assert processed == ["Company A", "Company B"]
        assert stats["total_investors"] == 2
        assert stats["total_ciks_queried"] == 3
        assert stats["successful_queries"] == 3

    @patch("time.sleep")
    @patch("idi_company_info.query_permid.query_permid_by_cik")
    def test_process_batch_with_failures(self, mock_query, mock_sleep):
        """Test batch processing with some failed queries"""
        mock_session = Mock()
        cik_data = {
            "Company A": ["0001234567", "0001234568"]
        }
        investors_to_process = ["Company A"]

        mock_query.side_effect = [
            "https://permid.org/1-5000051854",
            None  # Failed query
        ]

        results, processed, stats = query_permid.process_cik_batch(
            mock_session,
            cik_data,
            investors_to_process,
            batch_size=1,
            api_key="test-api-key"
        )

        # With the new structure, results are grouped by PermID
        # Only the successful query creates an entry
        assert len(results["Company A"]) == 1
        assert results["Company A"][0]["ciks"] == ["0001234567"]
        assert results["Company A"][0]["permid"] == "https://permid.org/1-5000051854"
        assert stats["successful_queries"] == 1
        assert stats["failed_queries"] == 1

    @patch("time.sleep")
    @patch("idi_company_info.query_permid.query_permid_by_cik")
    def test_process_batch_removes_duplicates(self, mock_query, mock_sleep):
        """Test that duplicate PermIDs are removed"""
        mock_session = Mock()
        cik_data = {
            "Company A": ["0001234567", "0001234568"]
        }
        investors_to_process = ["Company A"]

        # Return same PermID for both CIKs
        mock_query.side_effect = [
            "https://permid.org/1-5000051854",
            "https://permid.org/1-5000051854"
        ]

        results, processed, stats = query_permid.process_cik_batch(
            mock_session,
            cik_data,
            investors_to_process,
            batch_size=1,
            api_key="test-api-key"
        )

        assert len(results["Company A"]) == 1
        assert stats["duplicates_removed"] == 1


class TestMain:
    """Tests for main function"""

    @patch("idi_company_info.query_permid.print_cik_stats")
    @patch("idi_company_info.query_permid.save_batch_tracking")
    @patch("idi_company_info.query_permid.save_results")
    @patch("idi_company_info.query_permid.process_cik_batch")
    @patch("idi_company_info.query_permid.create_session")
    @patch("idi_company_info.query_permid.get_unprocessed_investors")
    @patch("idi_company_info.query_permid.load_existing_results")
    @patch("idi_company_info.query_permid.load_batch_tracking")
    @patch("idi_company_info.query_permid.load_cik_data")
    @patch("idi_company_info.query_permid.get_args")
    def test_main_integration(
        self,
        mock_get_args,
        mock_load_cik,
        mock_load_batch,
        mock_load_results,
        mock_get_unprocessed,
        mock_create_session,
        mock_process_batch,
        mock_save_results,
        mock_save_batch,
        mock_print_stats
    ):
        """Test main function integration"""
        # Setup mocks
        mock_args = Mock()
        mock_args.type = "cik"
        mock_args.api_key = "test-api-key"
        mock_args.input_file = pathlib.Path("/fake/input.json")
        mock_args.output_file = pathlib.Path("/fake/output.json")
        mock_args.batch_file = pathlib.Path("/fake/batch.json")
        mock_args.batch_size = 2
        mock_get_args.return_value = mock_args

        mock_load_cik.return_value = {"Company A": ["0001234567"]}
        mock_load_batch.return_value = {}
        mock_load_results.return_value = {}
        mock_get_unprocessed.return_value = ["Company A"]

        mock_session = Mock()
        mock_create_session.return_value = mock_session

        mock_batch_results = {"Company A": ["https://permid.org/1-5000051854"]}
        mock_processed = ["Company A"]
        mock_stats = {"total_investors": 1}
        mock_process_batch.return_value = (mock_batch_results, mock_processed, mock_stats)

        # Run main
        query_permid.main()

        # Verify calls
        mock_get_args.assert_called_once()
        mock_load_cik.assert_called_once()
        mock_load_batch.assert_called_once()
        mock_load_results.assert_called_once()
        mock_get_unprocessed.assert_called_once()
        mock_create_session.assert_called_once()
        mock_process_batch.assert_called_once()
        mock_save_results.assert_called_once()
        mock_save_batch.assert_called_once()
        mock_print_stats.assert_called_once()

    @patch("idi_company_info.query_permid.create_session")
    @patch("idi_company_info.query_permid.get_unprocessed_investors")
    @patch("idi_company_info.query_permid.load_existing_results")
    @patch("idi_company_info.query_permid.load_batch_tracking")
    @patch("idi_company_info.query_permid.load_cik_data")
    @patch("idi_company_info.query_permid.get_args")
    def test_main_all_processed(
        self,
        mock_get_args,
        mock_load_cik,
        mock_load_batch,
        mock_load_results,
        mock_get_unprocessed,
        mock_create_session
    ):
        """Test main when all investors are already processed"""
        # Setup mocks
        mock_args = Mock()
        mock_args.type = "cik"
        mock_get_args.return_value = mock_args

        mock_load_cik.return_value = {"Company A": ["0001234567"]}
        mock_load_batch.return_value = {}
        mock_load_results.return_value = {}
        mock_get_unprocessed.return_value = []  # All processed

        # Run main
        query_permid.main()

        # Should return early without processing
        mock_get_args.assert_called_once()
        mock_get_unprocessed.assert_called_once()

# ============================================================================
# Tests for Record Matching Mode
# ============================================================================

class TestLoadRecordData:
    """Tests for load_record_data function"""

    def test_load_record_data_success(self):
        """Test successful loading of record data"""
        mock_data = {
            "APPLE INC": {"ticker": "AAPL", "mic": None},
            "ACTIVE BIOTECH AB": {"ticker": "ACTI", "mic": "XSTO"}
        }

        m = mock_open(read_data=json.dumps(mock_data))
        with patch("builtins.open", m):
            result = query_permid.load_record_data(pathlib.Path("/fake/records.json"))

        assert result == mock_data
        assert len(result) == 2


class TestBuildRecordMatchCsv:
    """Tests for _build_record_match_csv function"""

    def test_build_csv_with_ticker_and_mic(self):
        """Test CSV building with both ticker and MIC"""
        records = [
            {"local_id": "0", "name": "ACTIVE BIOTECH AB", "ticker": "ACTI", "mic": "XSTO"}
        ]

        csv_result = query_permid._build_record_match_csv(records)

        assert "LocalID" in csv_result
        assert "Name" in csv_result
        assert "Standard Identifier" in csv_result
        assert "ACTIVE BIOTECH AB" in csv_result
        assert "Ticker:ACTI&&MIC:XSTO" in csv_result

    def test_build_csv_with_ticker_only(self):
        """Test CSV building with ticker but no MIC"""
        records = [
            {"local_id": "0", "name": "APPLE INC", "ticker": "AAPL", "mic": None}
        ]

        csv_result = query_permid._build_record_match_csv(records)

        assert "Ticker:AAPL" in csv_result
        assert "&&MIC:" not in csv_result  # No MIC should be present

    def test_build_csv_multiple_records(self):
        """Test CSV building with multiple records"""
        records = [
            {"local_id": "0", "name": "APPLE INC", "ticker": "AAPL", "mic": None},
            {"local_id": "1", "name": "ACTIVE BIOTECH AB", "ticker": "ACTI", "mic": "XSTO"}
        ]

        csv_result = query_permid._build_record_match_csv(records)

        # Count rows (header + 2 data rows = 3 lines)
        lines = csv_result.strip().split('\n')
        assert len(lines) == 3
        assert "APPLE INC" in csv_result
        assert "ACTIVE BIOTECH AB" in csv_result


class TestParseRecordMatchResponse:
    """Tests for _parse_record_match_response function"""

    def test_parse_response_with_matches(self):
        """Test parsing response with successful matches"""
        json_response = {
            "outputContentResponse": [
                {
                    "Input_LocalID": "0",
                    "Match Level": "Excellent",
                    "Match InstrumentPermID": "https://permid.org/1-21523463320"
                },
                {
                    "Input_LocalID": "1",
                    "Match Level": "Good",
                    "Match InstrumentPermID": "https://permid.org/1-21475135515"
                }
            ]
        }

        results = query_permid._parse_record_match_response(json_response)

        assert len(results) == 2
        assert results[0]["local_id"] == "0"
        assert results[0]["permid"] == "https://permid.org/1-21523463320"
        assert results[0]["match_level"] == "Excellent"
        assert results[1]["permid"] == "https://permid.org/1-21475135515"

    def test_parse_response_with_no_match(self):
        """Test parsing response with No Match"""
        json_response = {
            "outputContentResponse": [
                {
                    "Input_LocalID": "0",
                    "Match Level": "No Match",
                    "Match InstrumentPermID": ""
                }
            ]
        }

        results = query_permid._parse_record_match_response(json_response)

        assert len(results) == 1
        assert results[0]["permid"] is None
        assert results[0]["match_level"] == "No Match"

    def test_parse_response_with_org_permid(self):
        """Test parsing response with OrgPermID instead of InstrumentPermID"""
        json_response = {
            "outputContentResponse": [
                {
                    "Input_LocalID": "0",
                    "Match Level": "Excellent",
                    "Match OrgPermID": "https://permid.org/1-5000051854",
                    "Match InstrumentPermID": ""
                }
            ]
        }

        results = query_permid._parse_record_match_response(json_response)

        assert results[0]["permid"] == "https://permid.org/1-5000051854"


class TestQueryRecordBatch:
    """Tests for _query_record_batch function"""

    def test_query_batch_success(self):
        """Test successful batch query"""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "outputContentResponse": [
                {
                    "Input_LocalID": "0",
                    "Match Level": "Excellent",
                    "Match InstrumentPermID": "https://permid.org/1-21523463320"
                }
            ]
        }
        mock_session.post.return_value = mock_response

        batch_records = [
            {"local_id": "0", "name": "APPLE INC", "ticker": "AAPL", "mic": None}
        ]

        results = query_permid._query_record_batch(
            mock_session, batch_records, "test-api-key", attempt=1
        )

        assert results is not None
        assert len(results) == 1
        assert results[0]["permid"] == "https://permid.org/1-21523463320"
        mock_session.post.assert_called_once()

    def test_query_batch_request_exception(self):
        """Test handling of request exceptions"""
        mock_session = Mock()
        mock_session.post.side_effect = requests.exceptions.RequestException("API Error")

        batch_records = [
            {"local_id": "0", "name": "APPLE INC", "ticker": "AAPL", "mic": None}
        ]

        results = query_permid._query_record_batch(
            mock_session, batch_records, "test-api-key", attempt=1
        )

        assert results is None

    def test_query_batch_http_error(self):
        """Test handling of HTTP errors"""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
        mock_session.post.return_value = mock_response

        batch_records = [
            {"local_id": "0", "name": "APPLE INC", "ticker": "AAPL", "mic": None}
        ]

        results = query_permid._query_record_batch(
            mock_session, batch_records, "test-api-key", attempt=1
        )

        assert results is None


class TestProcessRecordBatch:
    """Tests for process_record_batch function"""

    @patch("idi_company_info.query_permid._query_record_batch")
    def test_process_record_batch_success(self, mock_query_batch):
        """Test successful record batch processing"""
        mock_session = Mock()
        mock_query_batch.return_value = [
            {"local_id": "0", "permid": "https://permid.org/1-21523463320", "match_level": "Excellent"},
            {"local_id": "1", "permid": "https://permid.org/1-21475135515", "match_level": "Good"}
        ]

        record_data = {
            "APPLE INC": {"ticker": "AAPL", "mic": None, "local_id": 0},
            "ACTIVE BIOTECH AB": {"ticker": "ACTI", "mic": "XSTO", "local_id": 1}
        }

        results, processed, stats = query_permid.process_record_batch(
            mock_session,
            record_data,
            list(record_data.keys()),
            "test-api-key"
        )

        assert len(results) == 2
        assert "APPLE INC" in results
        assert "ACTIVE BIOTECH AB" in results
        assert results["APPLE INC"]["ticker"] == "AAPL"
        assert results["APPLE INC"]["mic"] is None
        assert results["APPLE INC"]["permid"] == "https://permid.org/1-21523463320"
        assert results["ACTIVE BIOTECH AB"]["ticker"] == "ACTI"
        assert results["ACTIVE BIOTECH AB"]["mic"] == "XSTO"
        assert results["ACTIVE BIOTECH AB"]["permid"] == "https://permid.org/1-21475135515"
        assert stats["successful_matches"] == 2
        assert stats["no_matches"] == 0

    @patch("idi_company_info.query_permid._query_record_batch")
    def test_process_record_batch_with_no_matches(self, mock_query_batch):
        """Test processing with some no matches"""
        mock_session = Mock()
        mock_query_batch.return_value = [
            {"local_id": "0", "permid": "https://permid.org/1-21523463320", "match_level": "Excellent"},
            {"local_id": "1", "permid": None, "match_level": "No Match"}
        ]

        record_data = {
            "APPLE INC": {"ticker": "AAPL", "mic": None, "local_id": 0},
            "UNKNOWN CORP": {"ticker": "UNKN", "mic": None, "local_id": 1}
        }

        results, processed, stats = query_permid.process_record_batch(
            mock_session,
            record_data,
            list(record_data.keys()),
            "test-api-key"
        )

        assert len(results) == 1  # Only one match
        assert "APPLE INC" in results
        assert "UNKNOWN CORP" not in results
        assert stats["successful_matches"] == 1
        assert stats["no_matches"] == 1

    @patch("time.sleep")
    @patch("idi_company_info.query_permid._query_record_batch")
    def test_process_record_batch_with_retry(self, mock_query_batch, mock_sleep):
        """Test batch processing with retry logic"""
        mock_session = Mock()
        # First attempt fails, second succeeds
        mock_query_batch.side_effect = [
            None,  # First attempt fails
            [{"local_id": "0", "permid": "https://permid.org/1-21523463320", "match_level": "Excellent"}]
        ]

        record_data = {
            "APPLE INC": {"ticker": "AAPL", "mic": None, "local_id": 0}
        }

        results, processed, stats = query_permid.process_record_batch(
            mock_session,
            record_data,
            list(record_data.keys()),
            "test-api-key"
        )

        assert len(results) == 1
        assert stats["retries"] == 1
        assert mock_query_batch.call_count == 2


class TestMainRecordMode:
    """Tests for main function in record mode"""

    @patch("idi_company_info.query_permid.print_record_stats")
    @patch("idi_company_info.query_permid.save_batch_tracking")
    @patch("idi_company_info.query_permid.save_results")
    @patch("idi_company_info.query_permid.process_record_batch")
    @patch("idi_company_info.query_permid.create_session")
    @patch("idi_company_info.query_permid.get_unprocessed_investors")
    @patch("idi_company_info.query_permid.load_existing_results")
    @patch("idi_company_info.query_permid.load_batch_tracking")
    @patch("idi_company_info.query_permid.load_record_data")
    @patch("idi_company_info.query_permid.get_args")
    def test_main_record_mode(
        self,
        mock_get_args,
        mock_load_record,
        mock_load_batch,
        mock_load_results,
        mock_get_unprocessed,
        mock_create_session,
        mock_process_batch,
        mock_save_results,
        mock_save_batch,
        mock_print_stats
    ):
        """Test main function in record mode"""
        # Setup mocks
        mock_args = Mock()
        mock_args.type = "record"
        mock_args.api_key = "test-api-key"
        mock_args.input_file = pathlib.Path("/fake/records.json")
        mock_args.output_file = pathlib.Path("/fake/output.json")
        mock_args.batch_file = pathlib.Path("/fake/batch.json")
        mock_args.batch_size = 5000
        mock_get_args.return_value = mock_args

        mock_load_record.return_value = {"APPLE INC": {"ticker": "AAPL", "mic": None, "local_id": 0}}
        mock_load_batch.return_value = {}
        mock_load_results.return_value = {}
        mock_get_unprocessed.return_value = ["APPLE INC"]

        mock_session = Mock()
        mock_create_session.return_value = mock_session

        mock_batch_results = {"APPLE INC": "https://permid.org/1-21523463320"}
        mock_processed = ["APPLE INC"]
        mock_stats = {"total_issuers": 1, "successful_matches": 1}
        mock_process_batch.return_value = (mock_batch_results, mock_processed, mock_stats)

        # Run main
        query_permid.main()

        # Verify calls
        mock_get_args.assert_called_once()
        mock_load_record.assert_called_once()
        mock_load_batch.assert_called_once()
        mock_load_results.assert_called_once()
        mock_get_unprocessed.assert_called_once()
        mock_create_session.assert_called_once()
        mock_process_batch.assert_called_once()
        mock_save_results.assert_called_once()
        mock_save_batch.assert_called_once()
        mock_print_stats.assert_called_once()

    @patch("idi_company_info.query_permid.get_unprocessed_investors")
    @patch("idi_company_info.query_permid.load_existing_results")
    @patch("idi_company_info.query_permid.load_batch_tracking")
    @patch("idi_company_info.query_permid.load_record_data")
    @patch("idi_company_info.query_permid.get_args")
    def test_main_record_mode_all_processed(
        self,
        mock_get_args,
        mock_load_record,
        mock_load_batch,
        mock_load_results,
        mock_get_unprocessed
    ):
        """Test main in record mode when all issuers are already processed"""
        # Setup mocks
        mock_args = Mock()
        mock_args.type = "record"
        mock_args.batch_size = 5000
        mock_get_args.return_value = mock_args

        mock_load_record.return_value = {"APPLE INC": {"ticker": "AAPL", "mic": None, "local_id": 0}}
        mock_load_batch.return_value = {}
        mock_load_results.return_value = {}
        mock_get_unprocessed.return_value = []  # All processed

        # Run main
        query_permid.main()

        # Should return early without processing
        mock_get_args.assert_called_once()
        mock_get_unprocessed.assert_called_once()

    @patch("idi_company_info.query_permid.print_record_stats")
    @patch("idi_company_info.query_permid.save_batch_tracking")
    @patch("idi_company_info.query_permid.save_results")
    @patch("idi_company_info.query_permid.process_record_batch")
    @patch("idi_company_info.query_permid.create_session")
    @patch("idi_company_info.query_permid.get_unprocessed_investors")
    @patch("idi_company_info.query_permid.load_existing_results")
    @patch("idi_company_info.query_permid.load_batch_tracking")
    @patch("idi_company_info.query_permid.load_record_data")
    @patch("idi_company_info.query_permid.get_args")
    def test_main_record_mode_respects_batch_size(
        self,
        mock_get_args,
        mock_load_record,
        mock_load_batch,
        mock_load_results,
        mock_get_unprocessed,
        mock_create_session,
        mock_process_batch,
        mock_save_results,
        mock_save_batch,
        mock_print_stats
    ):
        """Test that batch_size limits the number of issuers processed"""
        # Setup mocks
        mock_args = Mock()
        mock_args.type = "record"
        mock_args.api_key = "test-api-key"
        mock_args.input_file = pathlib.Path("/fake/records.json")
        mock_args.output_file = pathlib.Path("/fake/output.json")
        mock_args.batch_file = pathlib.Path("/fake/batch.json")
        mock_args.batch_size = 10  # Only process 10 at a time
        mock_get_args.return_value = mock_args

        # 50 unprocessed issuers, but batch_size is 10
        record_data = {f"COMPANY_{i}": {"ticker": f"TKR{i}", "mic": None, "local_id": i} for i in range(50)}
        unprocessed = list(record_data.keys())

        mock_load_record.return_value = record_data
        mock_load_batch.return_value = {}
        mock_load_results.return_value = {}
        mock_get_unprocessed.return_value = unprocessed

        mock_session = Mock()
        mock_create_session.return_value = mock_session

        mock_batch_results = {f"COMPANY_{i}": f"https://permid.org/1-{i}" for i in range(10)}
        mock_processed = list(mock_batch_results.keys())
        mock_stats = {"total_issuers": 10, "successful_matches": 10}
        mock_process_batch.return_value = (mock_batch_results, mock_processed, mock_stats)

        # Run main
        query_permid.main()

        # Verify that process_record_batch was called with only 10 issuers
        mock_process_batch.assert_called_once()
        call_args = mock_process_batch.call_args
        issuers_to_process = call_args[0][2]  # Third positional argument
        assert len(issuers_to_process) == 10  # Should only process 10, not all 50
