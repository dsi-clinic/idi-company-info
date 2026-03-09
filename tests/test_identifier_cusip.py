#!/usr/bin/env python3
"""
Unit tests for idi_company_info.processors.IdentifierCusip
"""

from unittest.mock import MagicMock

import pandas as pd

from idi_company_info.processors.IdentifierCusip import IdentifierCusip


def make_cusip_instance():
    """Create an IdentifierCusip with mocked dependencies."""
    instance = IdentifierCusip.__new__(IdentifierCusip)
    instance.logger = MagicMock()
    return instance


class TestIsBondSecurity:
    """Tests for IdentifierCusip._is_bond_security (static method)."""

    def test_single_ticker_is_not_bond(self):
        """A simple ticker with no suffix is not a bond."""
        assert IdentifierCusip._is_bond_security("AAPL") is False

    def test_ticker_with_exchange_suffix_is_not_bond(self):
        """Ticker with a two-letter exchange code is not a bond."""
        assert IdentifierCusip._is_bond_security("ACTI SS") is False

    def test_coupon_rate_is_bond(self):
        """Ticker with a decimal coupon rate is a bond."""
        assert IdentifierCusip._is_bond_security("WEC 4.375 06/01/29") is True

    def test_integer_number_after_ticker_is_bond(self):
        """Ticker followed by an integer is treated as a bond."""
        assert IdentifierCusip._is_bond_security("XYZ 7") is True

    def test_date_pattern_is_bond(self):
        """Ticker followed by a MM/DD/YY date is a bond."""
        assert IdentifierCusip._is_bond_security("ABC 06/01/29") is True

    def test_perp_suffix_is_bond(self):
        """Ticker with PERP suffix is a bond."""
        assert IdentifierCusip._is_bond_security("MET F PERP A") is True

    def test_perp_case_insensitive(self):
        """PERP detection is case-insensitive."""
        assert IdentifierCusip._is_bond_security("MET perp") is True

    def test_empty_string_is_not_bond(self):
        """Empty string returns False."""
        assert IdentifierCusip._is_bond_security("") is False

    def test_none_is_not_bond(self):
        """None returns False."""
        assert IdentifierCusip._is_bond_security(None) is False


class TestParseTickerAndMic:
    """Tests for IdentifierCusip._parse_ticker_and_mic (static method)."""

    def test_plain_us_ticker(self):
        """A single-part ticker returns ticker:<symbol>."""
        assert IdentifierCusip._parse_ticker_and_mic("AAPL") == "ticker:AAPL"

    def test_ticker_with_known_exchange(self):
        """Ticker with a known exchange code returns ticker and mic."""
        assert IdentifierCusip._parse_ticker_and_mic("ACTI SS") == "ticker:ACTI&&mic:XSTO"

    def test_ticker_with_unknown_exchange(self):
        """Ticker with an unknown exchange code returns only ticker."""
        assert IdentifierCusip._parse_ticker_and_mic("FOO NY") == "ticker:FOO"

    def test_bond_returns_empty_string(self):
        """A bond security string returns empty string."""
        assert IdentifierCusip._parse_ticker_and_mic("WEC 4.375 06/01/29") == ""

    def test_more_than_two_parts_returns_empty(self):
        """More than two non-bond parts returns empty string."""
        assert IdentifierCusip._parse_ticker_and_mic("A B C") == ""

    def test_empty_string_returns_empty(self):
        """Empty string returns empty string."""
        assert IdentifierCusip._parse_ticker_and_mic("") == ""

    def test_none_returns_empty(self):
        """None returns empty string."""
        assert IdentifierCusip._parse_ticker_and_mic(None) == ""

    def test_whitespace_is_stripped(self):
        """Leading/trailing whitespace is handled."""
        assert IdentifierCusip._parse_ticker_and_mic("  AAPL  ") == "ticker:AAPL"


class TestExtractFilterParquetTicker:
    """Tests for IdentifierCusip._extract_filter_parquet_ticker."""

    def test_groups_tickers_by_issuer_name(self):
        """Test that tickers are grouped by issuer_name."""
        instance = make_cusip_instance()
        df = pd.DataFrame({
            "issuer_name": ["Corp A", "Corp A", "Corp B"],
            "stock_ticker": ["AAPL", "MSFT", "GOOG"],
        })
        result = instance._extract_filter_parquet_ticker(df)
        assert set(result.keys()) == {"Corp A", "Corp B"}
        assert set(result["Corp A"]) == {"ticker:AAPL", "ticker:MSFT"}

    def test_record_data_has_issuer_name_and_list_of_tickers(self):
        """Record data is created correctly: issuer_name as key, list of parsed tickers as value."""
        instance = make_cusip_instance()
        df = pd.DataFrame({
            "issuer_name": ["Active Biotech AB", "Active Biotech AB", "Active Biotech AB", "Apple Inc"],
            "stock_ticker": ["AAPL", "ACTI SS", "AAPL", "MSFT"],  # duplicate (Active Biotech AB, AAPL)
        })
        result = instance._extract_filter_parquet_ticker(df)
        # Structure: {issuer_name: [ticker1, ticker2, ...]}
        assert isinstance(result, dict)
        assert "Active Biotech AB" in result
        assert "Apple Inc" in result
        assert isinstance(result["Active Biotech AB"], list)
        assert len(result["Active Biotech AB"]) == 2
        assert "ticker:AAPL" in result["Active Biotech AB"]
        assert "ticker:ACTI&&mic:XSTO" in result["Active Biotech AB"]
        assert result["Apple Inc"] == ["ticker:MSFT"]

    def test_filters_out_null_tickers(self):
        """Test that rows with null stock_ticker are dropped."""
        instance = make_cusip_instance()
        df = pd.DataFrame({
            "issuer_name": ["Corp A", "Corp B"],
            "stock_ticker": [None, "GOOG"],
        })
        result = instance._extract_filter_parquet_ticker(df)
        assert "Corp A" not in result
        assert "Corp B" in result

    def test_filters_out_empty_tickers(self):
        """Test that rows with empty stock_ticker are dropped."""
        instance = make_cusip_instance()
        df = pd.DataFrame({
            "issuer_name": ["Corp A", "Corp B"],
            "stock_ticker": ["", "GOOG"],
        })
        result = instance._extract_filter_parquet_ticker(df)
        assert "Corp A" not in result

    def test_filters_out_null_issuer_name(self):
        """Test that rows with null issuer_name are dropped."""
        instance = make_cusip_instance()
        df = pd.DataFrame({
            "issuer_name": [None, "Corp B"],
            "stock_ticker": ["AAPL", "GOOG"],
        })
        result = instance._extract_filter_parquet_ticker(df)
        assert None not in result
        assert "Corp B" in result

    def test_filters_out_bond_securities(self):
        """Test that bond security tickers are removed."""
        instance = make_cusip_instance()
        df = pd.DataFrame({
            "issuer_name": ["Corp A", "Corp A"],
            "stock_ticker": ["AAPL", "WEC 4.375 06/01/29"],
        })
        result = instance._extract_filter_parquet_ticker(df)
        assert result["Corp A"] == ["ticker:AAPL"]

    def test_deduplicates_name_ticker_pairs(self):
        """Test that duplicate issuer_name/stock_ticker pairs are removed."""
        instance = make_cusip_instance()
        df = pd.DataFrame({
            "issuer_name": ["Corp A", "Corp A"],
            "stock_ticker": ["AAPL", "AAPL"],
        })
        result = instance._extract_filter_parquet_ticker(df)
        assert result["Corp A"] == ["ticker:AAPL"]

    def test_returns_empty_dict_for_empty_dataframe(self):
        """Test that an empty dataframe returns an empty dict."""
        instance = make_cusip_instance()
        df = pd.DataFrame({"issuer_name": [], "stock_ticker": []})
        result = instance._extract_filter_parquet_ticker(df)
        assert result == {}


class TestExtractFilterParquetCusip:
    """Tests for IdentifierCusip._extract_filter_parquet_cusip."""

    def test_groups_cusips_by_issuer_name(self):
        """Test that CUSIPs are grouped by issuer_name."""
        instance = make_cusip_instance()
        df = pd.DataFrame({
            "issuer_name": ["Corp A", "Corp A", "Corp B"],
            "security_cusip": ["037833100", "037833101", "594918104"],
        })
        result = instance._extract_filter_parquet_cusip(df)
        assert set(result["Corp A"]) == {"037833100", "037833101"}
        assert result["Corp B"] == ["594918104"]

    def test_filters_out_null_cusips(self):
        """Test that rows with null security_cusip are dropped."""
        instance = make_cusip_instance()
        df = pd.DataFrame({
            "issuer_name": ["Corp A", "Corp B"],
            "security_cusip": [None, "037833100"],
        })
        result = instance._extract_filter_parquet_cusip(df)
        assert "Corp A" not in result

    def test_deduplicates_name_cusip_pairs(self):
        """Test that duplicate issuer_name/security_cusip pairs are removed."""
        instance = make_cusip_instance()
        df = pd.DataFrame({
            "issuer_name": ["Corp A", "Corp A"],
            "security_cusip": ["037833100", "037833100"],
        })
        result = instance._extract_filter_parquet_cusip(df)
        assert result["Corp A"] == ["037833100"]

    def test_cusip_values_are_strings(self):
        """Test that CUSIP values are cast to string."""
        instance = make_cusip_instance()
        df = pd.DataFrame({
            "issuer_name": ["Corp A"],
            "security_cusip": [37833100],
        })
        result = instance._extract_filter_parquet_cusip(df)
        assert all(isinstance(v, str) for v in result["Corp A"])


class TestBuildQueryParamsCusip:
    """Tests for IdentifierCusip._build_query_params."""

    def test_returns_identifier_as_query(self):
        """Test that the identifier is used as the query value."""
        instance = make_cusip_instance()
        result = instance._build_query_params("037833100")
        assert result == {"q": "037833100", "format": "json"}
