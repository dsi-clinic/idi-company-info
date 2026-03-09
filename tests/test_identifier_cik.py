#!/usr/bin/env python3
"""
Unit tests for idi_company_info.processors.IdentifierCik
"""

from unittest.mock import MagicMock, patch

import pandas as pd

from idi_company_info.processors.IdentifierCik import IdentifierCik


def make_cik_instance():
    """Create an IdentifierCik with mocked dependencies."""
    with patch("idi_company_info.processors.identifier.os.makedirs"):
        with patch("idi_company_info.processors.identifier.FailureRegistry"):
            instance = IdentifierCik.__new__(IdentifierCik)
            instance.logger = MagicMock()
            return instance


class TestExtractFilterParquetCik:
    """Tests for IdentifierCik._extract_filter_parquet_cik."""

    def test_returns_dict_grouped_by_investor_name(self):
        """Test that the result groups ciks by investor_name."""
        instance = make_cik_instance()
        df = pd.DataFrame(
            {
                "investor_name": ["Firm A", "Firm A", "Firm B"],
                "investor_cik": ["0001111111", "0002222222", "0003333333"],
            }
        )
        result = instance._extract_filter_parquet_cik(df)
        assert set(result.keys()) == {"Firm A", "Firm B"}
        assert set(result["Firm A"]) == {"0001111111", "0002222222"}
        assert result["Firm B"] == ["0003333333"]

    def test_filters_out_null_ciks(self):
        """Test that rows with null investor_cik are dropped."""
        instance = make_cik_instance()
        df = pd.DataFrame(
            {
                "investor_name": ["Firm A", "Firm B"],
                "investor_cik": [None, "0001234567"],
            }
        )
        result = instance._extract_filter_parquet_cik(df)
        assert "Firm A" not in result
        assert "Firm B" in result

    def test_filters_out_empty_string_ciks(self):
        """Test that rows with empty-string investor_cik are dropped."""
        instance = make_cik_instance()
        df = pd.DataFrame(
            {
                "investor_name": ["Firm A", "Firm B"],
                "investor_cik": ["", "0001234567"],
            }
        )
        result = instance._extract_filter_parquet_cik(df)
        assert "Firm A" not in result
        assert "Firm B" in result

    def test_removes_cik_prefix(self):
        """Test that CIK prefix is stripped from values."""
        instance = make_cik_instance()
        df = pd.DataFrame(
            {
                "investor_name": ["Firm A"],
                "investor_cik": ["CIK0001546531"],
            }
        )
        result = instance._extract_filter_parquet_cik(df)
        assert result["Firm A"] == ["0001546531"]

    def test_deduplicates_name_cik_pairs(self):
        """Test that duplicate investor_name/investor_cik pairs are removed."""
        instance = make_cik_instance()
        df = pd.DataFrame(
            {
                "investor_name": ["Firm A", "Firm A"],
                "investor_cik": ["0001111111", "0001111111"],
            }
        )
        result = instance._extract_filter_parquet_cik(df)
        assert result["Firm A"] == ["0001111111"]

    def test_returns_empty_dict_for_empty_dataframe(self):
        """Test that an empty dataframe returns an empty dict."""
        instance = make_cik_instance()
        df = pd.DataFrame({"investor_name": [], "investor_cik": []})
        result = instance._extract_filter_parquet_cik(df)
        assert result == {}

    def test_cik_values_are_strings(self):
        """Test that CIK values are cast to string."""
        instance = make_cik_instance()
        df = pd.DataFrame(
            {
                "investor_name": ["Firm A"],
                "investor_cik": [1234567],
            }
        )
        result = instance._extract_filter_parquet_cik(df)
        assert all(isinstance(v, str) for v in result["Firm A"])


class TestBuildQueryParamsCik:
    """Tests for IdentifierCik._build_query_params."""

    def test_formats_cik_query(self):
        """Test that the query param is formatted with cik: prefix."""
        instance = make_cik_instance()
        result = instance._build_query_params("0001234567")
        assert result == {"q": "cik:0001234567", "format": "json"}

    def test_format_key_is_json(self):
        """Test that format is always json."""
        instance = make_cik_instance()
        result = instance._build_query_params("9876543210")
        assert result["format"] == "json"
