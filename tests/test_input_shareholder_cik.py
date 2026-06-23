#!/usr/bin/env python3
"""Unit tests for idi_company_info.input.ShareholderInputCik."""

from unittest.mock import MagicMock

import pandas as pd

from idi_company_info.input import ShareholderInputCik


def make_instance() -> ShareholderInputCik:
    """Create a ShareholderInputCik with a mocked logger and dummy input path."""
    instance = ShareholderInputCik("")
    instance.logger = MagicMock()
    return instance


class TestIdentifierType:
    """The CIK shareholder input reports the cik identifier type."""

    def test_identifier_type_is_cik(self):
        assert make_instance().identifier_type == "cik"


class TestExtractFilterParquetCik:
    """Tests for ShareholderInputCik's CIK filter/group logic (Input._filter_group_cik)."""

    def test_returns_dict_grouped_by_investor_name(self):
        """The result groups ciks by investor_name."""
        instance = make_instance()
        df = pd.DataFrame(
            {
                "investor_name": ["Firm A", "Firm A", "Firm B"],
                "investor_cik": ["0001111111", "0002222222", "0003333333"],
            }
        )
        result = instance._filter_group_cik(
            df, "investor_name", "investor_cik", strip_cik_prefix=True
        )
        assert set(result.keys()) == {"Firm A", "Firm B"}
        assert set(result["Firm A"]) == {"0001111111", "0002222222"}
        assert result["Firm B"] == ["0003333333"]

    def test_filters_out_null_ciks(self):
        """Rows with null investor_cik are dropped."""
        instance = make_instance()
        df = pd.DataFrame(
            {
                "investor_name": ["Firm A", "Firm B"],
                "investor_cik": [None, "0001234567"],
            }
        )
        result = instance._filter_group_cik(
            df, "investor_name", "investor_cik", strip_cik_prefix=True
        )
        assert "Firm A" not in result
        assert "Firm B" in result

    def test_filters_out_empty_string_ciks(self):
        """Rows with empty-string investor_cik are dropped."""
        instance = make_instance()
        df = pd.DataFrame(
            {
                "investor_name": ["Firm A", "Firm B"],
                "investor_cik": ["", "0001234567"],
            }
        )
        result = instance._filter_group_cik(
            df, "investor_name", "investor_cik", strip_cik_prefix=True
        )
        assert "Firm A" not in result
        assert "Firm B" in result

    def test_removes_cik_prefix(self):
        """The CIK prefix is stripped from values."""
        instance = make_instance()
        df = pd.DataFrame(
            {
                "investor_name": ["Firm A"],
                "investor_cik": ["CIK0001546531"],
            }
        )
        result = instance._filter_group_cik(
            df, "investor_name", "investor_cik", strip_cik_prefix=True
        )
        assert result["Firm A"] == ["0001546531"]

    def test_deduplicates_name_cik_pairs(self):
        """Duplicate investor_name/investor_cik pairs are removed."""
        instance = make_instance()
        df = pd.DataFrame(
            {
                "investor_name": ["Firm A", "Firm A"],
                "investor_cik": ["0001111111", "0001111111"],
            }
        )
        result = instance._filter_group_cik(
            df, "investor_name", "investor_cik", strip_cik_prefix=True
        )
        assert result["Firm A"] == ["0001111111"]

    def test_returns_empty_dict_for_empty_dataframe(self):
        """An empty dataframe returns an empty dict."""
        instance = make_instance()
        df = pd.DataFrame({"investor_name": [], "investor_cik": []})
        result = instance._filter_group_cik(
            df, "investor_name", "investor_cik", strip_cik_prefix=True
        )
        assert result == {}

    def test_cik_values_are_strings(self):
        """CIK values are cast to string."""
        instance = make_instance()
        df = pd.DataFrame(
            {
                "investor_name": ["Firm A"],
                "investor_cik": [1234567],
            }
        )
        result = instance._filter_group_cik(
            df, "investor_name", "investor_cik", strip_cik_prefix=True
        )
        assert all(isinstance(v, str) for v in result["Firm A"])
