#!/usr/bin/env python3
"""
Unit tests for retrieve_identifiers.py
"""

import json
import pathlib
from unittest.mock import Mock, mock_open, patch

import pandas as pd
import pytest

from idi_company_info import retrieve_identifiers


# ============================================================================
# Tests for Common Functions
# ============================================================================

class TestReadParquet:
    """Tests for read_parquet function"""

    def test_read_parquet_success(self):
        """Test successful parquet file reading"""
        mock_df = pd.DataFrame({
            "investor_name": ["Company A", "Company B"],
            "investor_cik": ["0001234567", "0001234568"]
        })

        with patch("pandas.read_parquet", return_value=mock_df) as mock_read:
            result = retrieve_identifiers.read_parquet(
                pathlib.Path("/fake/path.parquet"),
                required_columns=["investor_name", "investor_cik"]
            )

            mock_read.assert_called_once_with(pathlib.Path("/fake/path.parquet"))
            assert len(result) == 2
            assert "investor_name" in result.columns
            assert "investor_cik" in result.columns

    def test_read_parquet_missing_columns(self):
        """Test error when required columns are missing"""
        mock_df = pd.DataFrame({
            "wrong_column": ["value1", "value2"]
        })

        with patch("pandas.read_parquet", return_value=mock_df):
            with pytest.raises(ValueError, match="Required columns .* not found"):
                retrieve_identifiers.read_parquet(
                    pathlib.Path("/fake/path.parquet"),
                    required_columns=["investor_name", "investor_cik"]
                )


class TestIsBondSecurity:
    """Tests for is_bond_security function"""

    def test_equity_single_part(self):
        """Test single-part ticker (US equity)"""
        assert retrieve_identifiers.is_bond_security("AAPL") is False

    def test_equity_with_exchange(self):
        """Test equity with exchange suffix"""
        assert retrieve_identifiers.is_bond_security("ACTI SS") is False

    def test_bond_with_coupon(self):
        """Test bond with coupon rate"""
        assert retrieve_identifiers.is_bond_security("WEC 4.375 06/01/29") is True

    def test_bond_with_perp(self):
        """Test perpetual bond"""
        assert retrieve_identifiers.is_bond_security("MET F PERP A") is True

    def test_bond_with_integer_coupon(self):
        """Test bond with integer coupon rate"""
        assert retrieve_identifiers.is_bond_security("BAC 6 12/15/66") is True

    def test_bond_zero_coupon(self):
        """Test zero-coupon bond"""
        assert retrieve_identifiers.is_bond_security("AFRM 0 11/15/26") is True

    def test_empty_string(self):
        """Test empty string"""
        assert retrieve_identifiers.is_bond_security("") is False

    def test_none_value(self):
        """Test None value"""
        assert retrieve_identifiers.is_bond_security(None) is False


class TestParseTickerAndMic:
    """Tests for parse_ticker_and_mic function"""

    def test_us_ticker_no_suffix(self):
        """Test US ticker with no exchange suffix"""
        ticker, mic = retrieve_identifiers.parse_ticker_and_mic("AAPL")
        assert ticker == "AAPL"
        assert mic is None  # No MIC = let PermID API determine exchange

    def test_stockholm_ticker(self):
        """Test Stockholm ticker with SS suffix"""
        ticker, mic = retrieve_identifiers.parse_ticker_and_mic("ACTI SS")
        assert ticker == "ACTI"
        assert mic == "XSTO"

    def test_unknown_exchange_defaults(self):
        """Test unknown exchange suffix returns None (let API determine)"""
        ticker, mic = retrieve_identifiers.parse_ticker_and_mic("TEST XYZ")
        assert ticker == "TEST"
        assert mic is None  # Unknown exchange = let API determine

    def test_bond_filtered_out(self):
        """Test that bond securities return None, None"""
        ticker, mic = retrieve_identifiers.parse_ticker_and_mic("WEC 4.375 06/01/29")
        assert ticker is None
        assert mic is None

    def test_bond_perp_filtered_out(self):
        """Test that perpetual bonds are filtered out"""
        ticker, mic = retrieve_identifiers.parse_ticker_and_mic("MET F PERP A")
        assert ticker is None
        assert mic is None

    def test_empty_string(self):
        """Test empty string returns None, None"""
        ticker, mic = retrieve_identifiers.parse_ticker_and_mic("")
        assert ticker is None
        assert mic is None

    def test_none_value(self):
        """Test None value returns None, None"""
        ticker, mic = retrieve_identifiers.parse_ticker_and_mic(None)
        assert ticker is None
        assert mic is None

    def test_three_part_ticker_skipped(self):
        """Test that three-part tickers (non-bonds) are skipped"""
        # This might be a complex ticker format we don't handle
        ticker, mic = retrieve_identifiers.parse_ticker_and_mic("FOO BAR BAZ")
        assert ticker is None
        assert mic is None


# ============================================================================
# Tests for CIK Mode
# ============================================================================

class TestExtractFilterParquetCik:
    """Tests for extract_filter_parquet_cik function"""

    def test_extract_filter_basic(self):
        """Test basic extraction and filtering"""
        df = pd.DataFrame({
            "investor_name": ["Company A", "Company B", "Company A"],
            "investor_cik": ["CIK0001234567", "CIK0001234568", "CIK0001234569"],
            "other_column": ["x", "y", "z"]
        })

        result = retrieve_identifiers.extract_filter_parquet_cik(df)

        assert "Company A" in result
        assert "Company B" in result
        assert len(result["Company A"]) == 2
        assert "0001234567" in result["Company A"]
        assert "0001234569" in result["Company A"]
        assert result["Company B"] == ["0001234568"]

    def test_extract_filter_removes_nulls(self):
        """Test that null CIKs are filtered out"""
        df = pd.DataFrame({
            "investor_name": ["Company A", "Company B", "Company C"],
            "investor_cik": ["CIK0001234567", None, ""]
        })

        result = retrieve_identifiers.extract_filter_parquet_cik(df)

        assert "Company A" in result
        assert "Company B" not in result
        assert "Company C" not in result

    def test_extract_filter_removes_duplicates(self):
        """Test that duplicate investor_name/CIK pairs are removed"""
        df = pd.DataFrame({
            "investor_name": ["Company A", "Company A", "Company A"],
            "investor_cik": ["CIK0001234567", "CIK0001234567", "CIK0001234568"]
        })

        result = retrieve_identifiers.extract_filter_parquet_cik(df)

        assert len(result["Company A"]) == 2
        assert "0001234567" in result["Company A"]
        assert "0001234568" in result["Company A"]

    def test_extract_filter_removes_cik_prefix(self):
        """Test that CIK prefix is removed"""
        df = pd.DataFrame({
            "investor_name": ["Company A"],
            "investor_cik": ["CIK0001234567"]
        })

        result = retrieve_identifiers.extract_filter_parquet_cik(df)

        assert result["Company A"] == ["0001234567"]


class TestSaveResultCik:
    """Tests for save_result_cik function"""

    def test_save_result_success(self):
        """Test successful result saving"""
        result = {
            "Company A": ["0001234567", "0001234568"],
            "Company B": ["0001234569"]
        }

        m = mock_open()
        with patch("builtins.open", m):
            retrieve_identifiers.save_result_cik(result, pathlib.Path("/fake/output.json"))

        m.assert_called_once_with(pathlib.Path("/fake/output.json"), "w")

        # Verify JSON was written
        handle = m()
        written_data = "".join(call.args[0] for call in handle.write.call_args_list)
        written_json = json.loads(written_data)

        assert written_json == result


# ============================================================================
# Tests for Record Mode
# ============================================================================

class TestExtractFilterParquetRecord:
    """Tests for extract_filter_parquet_record function"""

    def test_extract_filter_basic(self):
        """Test basic extraction for record matching"""
        df = pd.DataFrame({
            "issuer_name": ["ACTIVE BIOTECH AB", "APPLE INC", "TESLA INC"],
            "stock_ticker": ["ACTI SS", "AAPL", "TSLA"],
            "other_column": ["x", "y", "z"]
        })

        result = retrieve_identifiers.extract_filter_parquet_record(df)

        assert "ACTIVE BIOTECH AB" in result
        assert result["ACTIVE BIOTECH AB"]["ticker"] == "ACTI"
        assert result["ACTIVE BIOTECH AB"]["mic"] == "XSTO"

        assert "APPLE INC" in result
        assert result["APPLE INC"]["ticker"] == "AAPL"
        assert result["APPLE INC"]["mic"] is None  # US ticker, no MIC

        assert "TESLA INC" in result
        assert result["TESLA INC"]["ticker"] == "TSLA"
        assert result["TESLA INC"]["mic"] is None  # US ticker, no MIC

    def test_extract_filter_removes_nulls(self):
        """Test that null values are filtered out"""
        df = pd.DataFrame({
            "issuer_name": ["Company A", "Company B", "Company C", None],
            "stock_ticker": ["AAPL", None, "", "TSLA"]
        })

        result = retrieve_identifiers.extract_filter_parquet_record(df)

        assert "Company A" in result
        assert "Company B" not in result
        assert "Company C" not in result
        assert None not in result

    def test_extract_filter_removes_duplicates(self):
        """Test that duplicate issuer_name entries are removed (keeps first)"""
        df = pd.DataFrame({
            "issuer_name": ["APPLE INC", "APPLE INC", "TESLA INC"],
            "stock_ticker": ["AAPL", "AAPL2", "TSLA"]
        })

        result = retrieve_identifiers.extract_filter_parquet_record(df)

        # Should only have 2 entries (duplicates removed)
        assert len(result) == 2
        assert "APPLE INC" in result
        assert result["APPLE INC"]["ticker"] == "AAPL"  # First occurrence kept

    def test_extract_filter_removes_bonds(self):
        """Test that bond securities are filtered out"""
        df = pd.DataFrame({
            "issuer_name": ["WELLS FARGO BOND", "APPLE INC", "BOND CORP"],
            "stock_ticker": ["WEC 4.375 06/01/29", "AAPL", "BAC 6 12/15/66"]
        })

        result = retrieve_identifiers.extract_filter_parquet_record(df)

        # Only equity should remain
        assert len(result) == 1
        assert "APPLE INC" in result
        assert "WELLS FARGO BOND" not in result
        assert "BOND CORP" not in result

    def test_extract_filter_stockholm_exchange(self):
        """Test Stockholm exchange mapping"""
        df = pd.DataFrame({
            "issuer_name": ["ACTIVE BIOTECH AB", "ABB LTD"],
            "stock_ticker": ["ACTI SS", "ABB SS"]
        })

        result = retrieve_identifiers.extract_filter_parquet_record(df)

        assert result["ACTIVE BIOTECH AB"]["mic"] == "XSTO"
        assert result["ABB LTD"]["mic"] == "XSTO"

    def test_extract_filter_unknown_exchange(self):
        """Test unknown exchange returns None (let API determine)"""
        df = pd.DataFrame({
            "issuer_name": ["TEST COMPANY"],
            "stock_ticker": ["TEST XYZ"]
        })

        result = retrieve_identifiers.extract_filter_parquet_record(df)

        assert result["TEST COMPANY"]["ticker"] == "TEST"
        assert result["TEST COMPANY"]["mic"] is None  # Unknown exchange


class TestSaveResultRecord:
    """Tests for save_result_record function"""

    def test_save_result_success(self):
        """Test successful result saving"""
        result = {
            "ACTIVE BIOTECH AB": {"ticker": "ACTI", "mic": "XSTO"},
            "APPLE INC": {"ticker": "AAPL", "mic": None}
        }

        m = mock_open()
        with patch("builtins.open", m):
            retrieve_identifiers.save_result_record(result, pathlib.Path("/fake/output.json"))

        m.assert_called_once_with(pathlib.Path("/fake/output.json"), "w")

        # Verify JSON was written
        handle = m()
        written_data = "".join(call.args[0] for call in handle.write.call_args_list)
        written_json = json.loads(written_data)

        assert written_json == result


# ============================================================================
# Tests for Main Function
# ============================================================================

class TestMain:
    """Tests for main function"""

    @patch("idi_company_info.retrieve_identifiers.save_result_cik")
    @patch("idi_company_info.retrieve_identifiers.extract_filter_parquet_cik")
    @patch("idi_company_info.retrieve_identifiers.read_parquet")
    @patch("idi_company_info.retrieve_identifiers.get_args")
    def test_main_cik_mode(
        self, mock_get_args, mock_read_parquet, mock_extract, mock_save
    ):
        """Test main function in CIK mode"""
        # Setup mocks
        mock_args = Mock()
        mock_args.type = "cik"
        mock_args.input_file = pathlib.Path("/fake/input.parquet")
        mock_args.output_file = pathlib.Path("/fake/output.json")
        mock_get_args.return_value = mock_args

        mock_df = pd.DataFrame({
            "investor_name": ["Company A"],
            "investor_cik": ["CIK0001234567"]
        })
        mock_read_parquet.return_value = mock_df

        mock_result = {"Company A": ["0001234567"]}
        mock_extract.return_value = mock_result

        # Run main
        retrieve_identifiers.main()

        # Verify calls
        mock_get_args.assert_called_once()
        mock_read_parquet.assert_called_once_with(
            pathlib.Path("/fake/input.parquet"),
            required_columns=["investor_name", "investor_cik"]
        )
        mock_extract.assert_called_once_with(mock_df)
        mock_save.assert_called_once_with(mock_result, pathlib.Path("/fake/output.json"))

    @patch("idi_company_info.retrieve_identifiers.save_result_record")
    @patch("idi_company_info.retrieve_identifiers.extract_filter_parquet_record")
    @patch("idi_company_info.retrieve_identifiers.read_parquet")
    @patch("idi_company_info.retrieve_identifiers.get_args")
    def test_main_record_mode(
        self, mock_get_args, mock_read_parquet, mock_extract, mock_save
    ):
        """Test main function in record mode"""
        # Setup mocks
        mock_args = Mock()
        mock_args.type = "record"
        mock_args.input_file = pathlib.Path("/fake/input.parquet")
        mock_args.output_file = pathlib.Path("/fake/output.json")
        mock_get_args.return_value = mock_args

        mock_df = pd.DataFrame({
            "issuer_name": ["APPLE INC"],
            "stock_ticker": ["AAPL"]
        })
        mock_read_parquet.return_value = mock_df

        mock_result = {"APPLE INC": {"ticker": "AAPL", "mic": None}}
        mock_extract.return_value = mock_result

        # Run main
        retrieve_identifiers.main()

        # Verify calls
        mock_get_args.assert_called_once()
        mock_read_parquet.assert_called_once_with(
            pathlib.Path("/fake/input.parquet"),
            required_columns=["issuer_name", "stock_ticker"]
        )
        mock_extract.assert_called_once_with(mock_df)
        mock_save.assert_called_once_with(mock_result, pathlib.Path("/fake/output.json"))
