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


def pairs(result: dict) -> list[tuple]:
    """Flatten ``{name: [cusip, ...]}`` into a list of (name, cusip) work-unit tuples."""
    return [(name, cusip) for name, cusips in result.items() for cusip in cusips]


class TestNormalizeNameForGrouping:
    """Tests for ShareholderInputCusip._normalize_name_for_grouping (static method)."""

    def test_uppercases(self):
        assert ShareholderInputCusip._normalize_name_for_grouping("Apple Inc") == "APPLE INC"

    def test_collapses_internal_whitespace(self):
        assert ShareholderInputCusip._normalize_name_for_grouping("SOFTBANK   CORP") == (
            "SOFTBANK CORP"
        )

    def test_strips_surrounding_whitespace(self):
        assert ShareholderInputCusip._normalize_name_for_grouping("  ACME  ") == "ACME"

    def test_strips_trailing_period(self):
        assert ShareholderInputCusip._normalize_name_for_grouping("SOFTBANK CORP.") == (
            "SOFTBANK CORP"
        )

    def test_strips_trailing_comma(self):
        assert ShareholderInputCusip._normalize_name_for_grouping("ACME INC,") == "ACME INC"

    def test_strips_repeated_trailing_punctuation(self):
        assert ShareholderInputCusip._normalize_name_for_grouping("ACME INC.,") == "ACME INC"

    def test_keeps_internal_punctuation(self):
        assert ShareholderInputCusip._normalize_name_for_grouping("AMAZON.COM INC") == (
            "AMAZON.COM INC"
        )

    def test_empty_string(self):
        assert ShareholderInputCusip._normalize_name_for_grouping("") == ""

    def test_non_string_input_is_coerced(self):
        assert ShareholderInputCusip._normalize_name_for_grouping(12345) == "12345"


class TestCanonicalNameCollapse:
    """Tests for the one-canonical-name-per-CUSIP collapse in _filter_and_deduplicate.

    Frequency is what selects the name, so these DataFrames deliberately repeat rows —
    counting happens before deduplication.
    """

    def test_modal_name_wins_over_minority_variant(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["APPLE INC", "APPLE INC", "APPLE INC", "APPLE COMPUTER"],
                "security_cusip": ["037833100"] * 4,
                "stock_ticker": ["AAPL"] * 4,
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert result == {"APPLE INC": ["037833100"]}

    def test_one_pair_per_cusip_across_many_variants(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["SPDR S&P 500 ETF TR"] * 3 + ["CSX CORP", "ASTERA LABS INC"],
                "security_cusip": ["78462F103"] * 5,
                "stock_ticker": ["SPY"] * 5,
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert pairs(result) == [("SPDR S&P 500 ETF TR", "78462F103")]

    def test_pair_count_equals_std_ticker_map_size(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp A2", "Corp B", "Corp C"],
                "security_cusip": ["037833100", "037833100", "594918104", "037833101"],
                "stock_ticker": ["AAPL", "AAPL", "GOOG", "WEC 4.375 06/01/29"],
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert len(pairs(result)) == len(instance.std_ticker_map)

    def test_tie_break_is_independent_of_row_order(self):
        rows = {
            "issuer_name": ["ZETA CORP", "ALPHA CORP"],
            "security_cusip": ["037833100", "037833100"],
            "stock_ticker": ["AAPL", "AAPL"],
        }
        forward = make_instance()._extract_filter_parquet_ticker(pd.DataFrame(rows))
        reversed_df = pd.DataFrame(rows).iloc[::-1].reset_index(drop=True)
        backward = make_instance()._extract_filter_parquet_ticker(reversed_df)
        assert forward == backward == {"ALPHA CORP": ["037833100"]}

    def test_modal_name_differs_from_first_occurrence(self):
        instance = make_instance()
        # Mirrors CUSIP 02079K305: 'ALPHABET CLASS A' appears first, 'ALPHABET INC' dominates.
        df = pd.DataFrame(
            {
                "issuer_name": ["ALPHABET CLASS A", "ALPHABET INC", "ALPHABET INC"],
                "security_cusip": ["02079K305"] * 3,
                "stock_ticker": ["GOOGL"] * 3,
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert result == {"ALPHABET INC": ["02079K305"]}

    def test_normalization_merges_variants_and_emits_common_raw_spelling(self):
        instance = make_instance()
        # 'SOFTBANK CORP.' x2 + 'softbank  corp' x1 is one group of 3, beating OTHER NAME's 2.
        # The emitted name is the most common raw spelling in the winning group, so the
        # trailing period survives even though it was stripped for counting.
        df = pd.DataFrame(
            {
                "issuer_name": [
                    "SOFTBANK CORP.",
                    "SOFTBANK CORP.",
                    "softbank  corp",
                    "OTHER NAME",
                    "OTHER NAME",
                ],
                "security_cusip": ["83405K102"] * 5,
                "stock_ticker": ["9434"] * 5,
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert result == {"SOFTBANK CORP.": ["83405K102"]}

    def test_cusip_kept_when_modal_name_row_has_bond_ticker(self):
        instance = make_instance()
        # The modal name travels with a bond-form ticker; the minority name has the parseable
        # one. The CUSIP must survive, and std_ticker_map must still hold its ticker.
        df = pd.DataFrame(
            {
                "issuer_name": ["MODAL NAME", "MODAL NAME", "MINORITY NAME"],
                "security_cusip": ["037833100"] * 3,
                "stock_ticker": ["WEC 4.375 06/01/29", "WEC 4.375 06/01/29", "WEC"],
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert pairs(result) == [("MODAL NAME", "037833100")]
        assert instance.std_ticker_map == {"037833100": "ticker:WEC"}

    def test_cusip_dropped_when_no_variant_has_a_parseable_ticker(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["MODAL NAME", "MINORITY NAME"],
                "security_cusip": ["037833100"] * 2,
                "stock_ticker": ["WEC 4.375 06/01/29", "ABC 06/01/29"],
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert result == {}
        assert len(pairs(result)) == len(instance.std_ticker_map)

    def test_distinct_cusips_keep_their_own_canonical_names(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp A", "Corp A-alt", "Corp B", "Corp B", "Corp B2"],
                "security_cusip": ["037833100"] * 3 + ["594918104"] * 3,
                "stock_ticker": ["AAPL"] * 3 + ["GOOG"] * 3,
            }
        )
        result = instance._extract_filter_parquet_ticker(df)
        assert result == {"Corp A": ["037833100"], "Corp B": ["594918104"]}

    def test_collapse_is_logged(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "issuer_name": ["Corp A", "Corp A-alt"],
                "security_cusip": ["037833100"] * 2,
                "stock_ticker": ["AAPL"] * 2,
            }
        )
        instance._extract_filter_parquet_ticker(df)
        logged = [call.args[0] for call in instance.logger.info.call_args_list]
        assert any("canonical pairs" in message for message in logged)

    def test_empty_dataframe_returns_empty_dict(self):
        instance = make_instance()
        df = pd.DataFrame({"issuer_name": [], "security_cusip": [], "stock_ticker": []})
        assert instance._extract_filter_parquet_ticker(df) == {}
        assert instance.std_ticker_map == {}


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
