#!/usr/bin/env python3
"""Unit tests for idi_company_info.input.SubsidiaryInput.

SubsidiaryInput reads parent_name/parent_cik, filters/dedups, and groups by
parent_name. (It is fully implemented — not a stub.)
"""

from unittest.mock import MagicMock

import pandas as pd

from idi_company_info.input import SubsidiaryInput


def make_instance(input_file: str = "") -> SubsidiaryInput:
    """Create a SubsidiaryInput with a mocked logger and dummy input path."""
    instance = SubsidiaryInput(input_file)
    instance.logger = MagicMock()
    return instance


class TestIdentifierType:
    """SubsidiaryInput reports the cik identifier type."""

    def test_identifier_type_is_cik(self):
        assert make_instance().identifier_type == "cik"


class TestExtractFilterParquetCik:
    """Tests for SubsidiaryInput._extract_filter_parquet_cik."""

    def test_groups_ciks_by_parent_name(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "parent_name": ["Parent A", "Parent A", "Parent B"],
                "parent_cik": ["0001111111", "0002222222", "0003333333"],
            }
        )
        result = instance._extract_filter_parquet_cik(df)
        assert set(result.keys()) == {"Parent A", "Parent B"}
        assert set(result["Parent A"]) == {"0001111111", "0002222222"}
        assert result["Parent B"] == ["0003333333"]

    def test_drops_null_parent_name(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "parent_name": [None, "Parent B"],
                "parent_cik": ["0001111111", "0002222222"],
            }
        )
        result = instance._extract_filter_parquet_cik(df)
        assert list(result.keys()) == ["Parent B"]

    def test_deduplicates_name_cik_pairs(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "parent_name": ["Parent A", "Parent A"],
                "parent_cik": ["0001111111", "0001111111"],
            }
        )
        result = instance._extract_filter_parquet_cik(df)
        assert result["Parent A"] == ["0001111111"]

    def test_cik_values_are_strings(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "parent_name": ["Parent A"],
                "parent_cik": [1234567],
            }
        )
        result = instance._extract_filter_parquet_cik(df)
        assert all(isinstance(v, str) for v in result["Parent A"])

    def test_returns_empty_dict_for_empty_dataframe(self):
        instance = make_instance()
        df = pd.DataFrame({"parent_name": [], "parent_cik": []})
        result = instance._extract_filter_parquet_cik(df)
        assert result == {}
