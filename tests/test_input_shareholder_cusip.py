#!/usr/bin/env python3
"""Unit tests for idi_company_info.input.ShareholderInputCusip."""

from unittest.mock import MagicMock

import pandas as pd

from idi_company_info.input import ShareholderInputCusip


def make_instance() -> ShareholderInputCusip:
    """Create a ShareholderInputCusip with a mocked logger and dummy input path."""
    instance = ShareholderInputCusip("")
    instance.logger = MagicMock()
    return instance


class TestIdentifierType:
    """The CUSIP shareholder input reports the cusip identifier type."""

    def test_identifier_type_is_cusip(self):
        assert make_instance().identifier_type == "cusip"


class TestIsBondSecurity:
    """Tests for ShareholderInputCusip._is_bond_security (static method)."""

    def test_single_ticker_is_not_bond(self):
        assert ShareholderInputCusip._is_bond_security("AAPL") is False

    def test_ticker_with_exchange_suffix_is_not_bond(self):
        assert ShareholderInputCusip._is_bond_security("ACTI SS") is False

    def test_coupon_rate_is_bond(self):
        assert ShareholderInputCusip._is_bond_security("WEC 4.375 06/01/29") is True

    def test_integer_number_after_ticker_is_bond(self):
        assert ShareholderInputCusip._is_bond_security("XYZ 7") is True

    def test_date_pattern_is_bond(self):
        assert ShareholderInputCusip._is_bond_security("ABC 06/01/29") is True

    def test_perp_suffix_is_bond(self):
        assert ShareholderInputCusip._is_bond_security("MET F PERP A") is True

    def test_perp_case_insensitive(self):
        assert ShareholderInputCusip._is_bond_security("MET perp") is True

    def test_empty_string_is_not_bond(self):
        assert ShareholderInputCusip._is_bond_security("") is False

    def test_none_is_not_bond(self):
        assert ShareholderInputCusip._is_bond_security(None) is False


class TestParseTickerAndMic:
    """Tests for ShareholderInputCusip._parse_ticker_and_mic (static method)."""

    def test_plain_us_ticker(self):
        assert ShareholderInputCusip._parse_ticker_and_mic("AAPL") == "ticker:AAPL"

    def test_ticker_with_known_exchange(self):
        assert ShareholderInputCusip._parse_ticker_and_mic("ACTI SS") == "ticker:ACTI&&mic:XSTO"

    def test_ticker_with_unknown_exchange(self):
        assert ShareholderInputCusip._parse_ticker_and_mic("FOO NY") == "ticker:FOO"

    def test_bond_returns_empty_string(self):
        assert ShareholderInputCusip._parse_ticker_and_mic("WEC 4.375 06/01/29") == ""

    def test_more_than_two_parts_returns_empty(self):
        assert ShareholderInputCusip._parse_ticker_and_mic("A B C") == ""

    def test_empty_string_returns_empty(self):
        assert ShareholderInputCusip._parse_ticker_and_mic("") == ""

    def test_none_returns_empty(self):
        assert ShareholderInputCusip._parse_ticker_and_mic(None) == ""

    def test_whitespace_is_stripped(self):
        assert ShareholderInputCusip._parse_ticker_and_mic("  AAPL  ") == "ticker:AAPL"


class TestExtractFilterParquetTicker:
    """Tests for ShareholderInputCusip._extract_filter_parquet_ticker.

    The method returns {issuer_name: [cusip, ...]} and sets self._std_ticker_map
    as a side effect. All input DataFrames include issuer_name, security_cusip,
    and stock_ticker.
    """

    def test_groups_cusips_by_issuer_name(self):
        instance = make_instance()
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
        instance = make_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A"],
                "security_cusip": ["037833100"],
                "stock_ticker": ["AAPL"],
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert result == {"Corp A": ["037833100"]}

    def test_builds_std_ticker_map_as_side_effect(self):
        instance = make_instance()
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
        instance = make_instance()
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
        instance = make_instance()
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
        instance = make_instance()
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
        instance = make_instance()
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
        instance = make_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp A"],
                "security_cusip": ["037833100", "037833101"],
                "stock_ticker": ["AAPL", "WEC 4.375 06/01/29"],
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert "037833100" in result["Corp A"]
        assert "037833101" not in result["Corp A"]

    def test_deduplicates_issuer_cusip_ticker_triples(self):
        instance = make_instance()
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
        instance = make_instance()
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
        instance = make_instance()
        df = pd.DataFrame({"issuer_name": [], "security_cusip": [], "stock_ticker": []})
        result = instance._extract_filter_parquet_ticker(df)
        assert result == {}

    def test_multiple_cusips_per_issuer_all_included(self):
        instance = make_instance()
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
    """Tests for ShareholderInputCusip._warn_ambiguous_cusips."""

    def test_no_warning_for_unambiguous_cusips(self):
        instance = make_instance()
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
        instance = make_instance()
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
        instance = make_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp B"],
                "security_cusip": ["037833100", "037833100"],
                "stock_ticker": ["AAPL", "MSFT"],
            }
        )
        instance._build_ticker_maps(df)
        assert instance._std_ticker_map["037833100"] == "ticker:AAPL"


class TestGroupByIssuer:
    """Tests for ShareholderInputCusip._group_by_issuer."""

    def test_returns_cusips_per_issuer(self):
        instance = make_instance()
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
        instance = make_instance()
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
        instance = make_instance()
        df = pd.DataFrame({"issuer_name": [], "security_cusip": [], "stock_ticker": []})
        result = instance._group_by_issuer(df)
        assert result == {}


class TestStdTickerMapProperty:
    """Tests for the ShareholderInputCusip.std_ticker_map property."""

    def test_std_ticker_map_returns_empty_before_load(self):
        instance = make_instance()
        assert instance.std_ticker_map == {}

    def test_std_ticker_map_returns_set_value(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A"],
                "security_cusip": ["037833100"],
                "stock_ticker": ["AAPL"],
            }
        )
        instance._extract_filter_parquet_ticker(df)
        assert instance.std_ticker_map == {"037833100": "ticker:AAPL"}
