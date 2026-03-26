#!/usr/bin/env python3
"""Unit tests for idi_company_info.processors.identifier_cusip."""

from unittest.mock import MagicMock

import pandas as pd

from idi_company_info.processors.identifier_cusip import IdentifierCusip


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
    """Tests for IdentifierCusip._extract_filter_parquet_ticker.

    The method now returns {issuer_name: [cusip, ...]} (not tickers).
    It also sets self._raw_ticker_map and self._std_ticker_map as side effects.
    All input DataFrames must include issuer_name, security_cusip, and stock_ticker.
    """

    def test_groups_cusips_by_issuer_name(self):
        """CUSIPs are grouped by issuer_name, not by ticker."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp A", "Corp B"],
                "security_cusip": ["037833100", "037833101", "594918104"],
                "stock_ticker": ["AAPL", "MSFT", "GOOG"],
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert set(result.keys()) == {"Corp A", "Corp B"}
        assert set(result["Corp A"]) == {"037833100", "037833101"}
        assert result["Corp B"] == ["594918104"]

    def test_result_values_are_cusip_strings_not_tickers(self):
        """Values in the result dict are CUSIP strings, not formatted ticker strings."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A"],
                "security_cusip": ["037833100"],
                "stock_ticker": ["AAPL"],
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert result == {"Corp A": ["037833100"]}

    def test_builds_raw_ticker_map_as_side_effect(self):
        """_raw_ticker_map is populated with CUSIP -> raw ticker."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A"],
                "security_cusip": ["037833100"],
                "stock_ticker": ["AAPL"],
            }
        )
        instance._extract_filter_parquet_ticker(df)
        assert instance._raw_ticker_map == {"037833100": "AAPL"}

    def test_builds_std_ticker_map_as_side_effect(self):
        """_std_ticker_map is populated with CUSIP -> formatted Standard Identifier."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp B"],
                "security_cusip": ["037833100", "037833101"],
                "stock_ticker": ["AAPL", "ACTI SS"],
            }
        )
        instance._extract_filter_parquet_ticker(df)
        assert instance._std_ticker_map["037833100"] == "ticker:AAPL"
        assert instance._std_ticker_map["037833101"] == "ticker:ACTI&&mic:XSTO"

    def test_filters_out_null_tickers(self):
        """Rows with null stock_ticker are dropped; their CUSIPs are excluded."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp B"],
                "security_cusip": ["037833100", "594918104"],
                "stock_ticker": [None, "GOOG"],
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert "Corp A" not in result
        assert "Corp B" in result

    def test_filters_out_empty_tickers(self):
        """Rows with empty stock_ticker are dropped."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp B"],
                "security_cusip": ["037833100", "594918104"],
                "stock_ticker": ["", "GOOG"],
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert "Corp A" not in result
        assert "Corp B" in result

    def test_filters_out_null_cusips(self):
        """Rows with null security_cusip are dropped."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp B"],
                "security_cusip": [None, "594918104"],
                "stock_ticker": ["AAPL", "GOOG"],
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert "Corp A" not in result
        assert "Corp B" in result

    def test_filters_out_null_issuer_name(self):
        """Rows with null issuer_name are dropped."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": [None, "Corp B"],
                "security_cusip": ["037833100", "594918104"],
                "stock_ticker": ["AAPL", "GOOG"],
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert None not in result
        assert "Corp B" in result

    def test_filters_out_bond_securities(self):
        """CUSIPs whose tickers resolve to bond securities are excluded from the result."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp A"],
                "security_cusip": ["037833100", "037833101"],
                "stock_ticker": ["AAPL", "WEC 4.375 06/01/29"],
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        # Bond CUSIP is excluded from the result (no valid ticker)
        assert "037833100" in result["Corp A"]
        assert "037833101" not in result["Corp A"]

    def test_deduplicates_issuer_cusip_ticker_triples(self):
        """Duplicate (issuer_name, security_cusip, stock_ticker) triples are removed."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp A"],
                "security_cusip": ["037833100", "037833100"],
                "stock_ticker": ["AAPL", "AAPL"],
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert result["Corp A"] == ["037833100"]

    def test_warns_when_cusip_maps_to_multiple_tickers(self):
        """A warning is emitted if the same CUSIP appears with different tickers."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp B"],
                "security_cusip": ["037833100", "037833100"],
                "stock_ticker": ["AAPL", "MSFT"],
            }
        )
        instance._extract_filter_parquet_ticker(df)
        instance.logger.warning.assert_called_once()

    def test_returns_empty_dict_for_empty_dataframe(self):
        """An empty dataframe returns an empty dict."""
        instance = make_cusip_instance()
        df = pd.DataFrame({"issuer_name": [], "security_cusip": [], "stock_ticker": []})
        result = instance._extract_filter_parquet_ticker(df)
        assert result == {}

    def test_multiple_cusips_per_issuer_all_included(self):
        """All valid CUSIPs for an issuer are returned."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp A", "Corp A"],
                "security_cusip": ["037833100", "037833101", "037833102"],
                "stock_ticker": ["AAPL", "ACTI SS", "MSFT"],
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert set(result["Corp A"]) == {"037833100", "037833101", "037833102"}


class TestWarnAmbiguousCusips:
    """Tests for IdentifierCusip._warn_ambiguous_cusips."""

    def test_no_warning_for_unambiguous_cusips(self):
        """No warning is emitted when each CUSIP maps to exactly one ticker."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp B"],
                "security_cusip": ["037833100", "594918104"],
                "stock_ticker": ["AAPL", "GOOG"],
            }
        )
        instance._warn_ambiguous_cusips(df)
        instance.logger.warning.assert_not_called()

    def test_warning_emitted_for_ambiguous_cusip(self):
        """A warning is emitted when one CUSIP maps to multiple tickers."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp B"],
                "security_cusip": ["037833100", "037833100"],
                "stock_ticker": ["AAPL", "MSFT"],
            }
        )
        instance._warn_ambiguous_cusips(df)
        instance.logger.warning.assert_called_once()

    def test_ambiguous_cusip_deterministically_keeps_first(self):
        """When a CUSIP maps to multiple tickers, _std_ticker_map keeps the first."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp B"],
                "security_cusip": ["037833100", "037833100"],
                "stock_ticker": ["AAPL", "MSFT"],
            }
        )
        instance._build_ticker_maps(df)
        assert instance._raw_ticker_map["037833100"] == "AAPL"
        assert instance._std_ticker_map["037833100"] == "ticker:AAPL"


class TestGroupByIssuer:
    """Tests for IdentifierCusip._group_by_issuer."""

    def test_returns_cusips_per_issuer(self):
        """Returns {issuer_name: [cusip, ...]} with no ticker values."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp A", "Corp B"],
                "security_cusip": ["037833100", "037833101", "594918104"],
                "stock_ticker": ["AAPL", "MSFT", "GOOG"],
            }
        )
        result = instance._group_by_issuer(df)
        assert set(result["Corp A"]) == {"037833100", "037833101"}
        assert result["Corp B"] == ["594918104"]

    def test_bond_cusips_are_excluded(self):
        """CUSIPs whose tickers are bonds are not returned."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp A"],
                "security_cusip": ["037833100", "037833101"],
                "stock_ticker": ["AAPL", "WEC 4.375 06/01/29"],
            }
        )
        result = instance._group_by_issuer(df)
        assert result["Corp A"] == ["037833100"]
        assert "037833101" not in result.get("Corp A", [])

    def test_empty_dataframe_returns_empty_dict(self):
        """Empty DataFrame produces empty dict."""
        instance = make_cusip_instance()
        df = pd.DataFrame({"issuer_name": [], "security_cusip": [], "stock_ticker": []})
        result = instance._group_by_issuer(df)
        assert result == {}


class TestStdAndRawTickerMapProperties:
    """Tests for IdentifierCusip.std_ticker_map and raw_ticker_map properties."""

    def test_std_ticker_map_returns_empty_before_load(self):
        """std_ticker_map returns {} when _std_ticker_map has not been set."""
        instance = make_cusip_instance()
        assert instance.std_ticker_map == {}

    def test_raw_ticker_map_returns_empty_before_load(self):
        """raw_ticker_map returns {} when _raw_ticker_map has not been set."""
        instance = make_cusip_instance()
        assert instance.raw_ticker_map == {}

    def test_std_ticker_map_returns_set_value(self):
        """std_ticker_map returns the value set by _build_ticker_maps."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A"],
                "security_cusip": ["037833100"],
                "stock_ticker": ["AAPL"],
            }
        )
        instance._extract_filter_parquet_ticker(df)
        assert instance.std_ticker_map == {"037833100": "ticker:AAPL"}

    def test_raw_ticker_map_returns_set_value(self):
        """raw_ticker_map returns the value set by _build_ticker_maps."""
        instance = make_cusip_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A"],
                "security_cusip": ["037833100"],
                "stock_ticker": ["AAPL"],
            }
        )
        instance._extract_filter_parquet_ticker(df)
        assert instance.raw_ticker_map == {"037833100": "AAPL"}
