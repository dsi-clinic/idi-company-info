#!/usr/bin/env python3
"""Unit tests for idi_company_info.input.CdtInput.

CdtInput reads a single CDT debt-instruments parquet file, projects company_name/cik,
drops null and literal ``"nan"`` company names, zero-pads CIKs to 10 digits, and groups
by company_name.
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from idi_company_info.input import CdtInput


def make_instance(input_file: str = "") -> CdtInput:
    """Create a CdtInput with a mocked logger and the given input path."""
    instance = CdtInput(input_file)
    instance.logger = MagicMock()
    return instance


class TestIdentifierType:
    """CdtInput reports the cik identifier type."""

    def test_identifier_type_is_cik(self):
        assert make_instance().identifier_type == "cik"


class TestExtractFilterParquetCik:
    """Tests for CdtInput's CIK filter/group logic (Input._filter_group_cik)."""

    def test_groups_ciks_by_company_name(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "company_name": ["Co A", "Co A", "Co B"],
                "cik": ["0001111111", "0002222222", "0003333333"],
            }
        )
        result = instance._filter_group_cik(df, "company_name", "cik")
        assert set(result.keys()) == {"Co A", "Co B"}
        assert set(result["Co A"]) == {"0001111111", "0002222222"}
        assert result["Co B"] == ["0003333333"]

    def test_drops_null_company_name(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "company_name": [None, "Co B"],
                "cik": ["0001111111", "0002222222"],
            }
        )
        result = instance._filter_group_cik(df, "company_name", "cik")
        assert list(result.keys()) == ["Co B"]

    def test_drops_literal_nan_company_name(self):
        """Rows whose company_name is the literal string 'nan' are dropped."""
        instance = make_instance()
        df = pd.DataFrame(
            {
                "company_name": ["nan", "Co B"],
                "cik": ["0001461755", "0002222222"],
            }
        )
        result = instance._filter_group_cik(df, "company_name", "cik")
        assert "nan" not in result
        assert list(result.keys()) == ["Co B"]

    def test_zero_pads_ciks_to_ten_digits(self):
        """Raw integer-like CIKs are left-padded to 10 digits."""
        instance = make_instance()
        df = pd.DataFrame(
            {
                "company_name": ["Co A"],
                "cik": ["1002910"],
            }
        )
        result = instance._filter_group_cik(df, "company_name", "cik")
        assert result["Co A"] == ["0001002910"]

    def test_deduplicates_name_cik_pairs(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "company_name": ["Co A", "Co A"],
                "cik": ["0001111111", "0001111111"],
            }
        )
        result = instance._filter_group_cik(df, "company_name", "cik")
        assert result["Co A"] == ["0001111111"]

    def test_returns_empty_dict_for_empty_dataframe(self):
        instance = make_instance()
        df = pd.DataFrame({"company_name": [], "cik": []})
        result = instance._filter_group_cik(df, "company_name", "cik")
        assert result == {}


class TestReadParquetAndLoad:
    """CdtInput.read_parquet / load_data read a single parquet file."""

    def test_reads_single_parquet_file(self, tmp_path):
        input_file = tmp_path / "latest.parquet"
        pd.DataFrame(
            {
                "company_name": ["Co A", "Co B", "nan"],
                "cik": ["1002910", "2078008", "1461755"],
            }
        ).to_parquet(input_file)
        instance = make_instance(str(input_file))
        result = instance.load_data()

        # Both real companies appear, the literal "nan" row is dropped, CIKs padded.
        assert result == {"Co A": ["0001002910"], "Co B": ["0002078008"]}

    def test_ignores_extra_columns(self, tmp_path):
        """Wide schemas (e.g. lenders_json) are fine — only company_name/cik are used."""
        input_file = tmp_path / "latest.parquet"
        pd.DataFrame(
            {
                "debt_instrument_id": ["dim::1"],
                "company_name": ["Co A"],
                "cik": ["1002910"],
                "lenders_json": ["[]"],
            }
        ).to_parquet(input_file)
        result = make_instance(str(input_file)).load_data()
        assert result == {"Co A": ["0001002910"]}

    def test_raises_when_required_column_missing(self, tmp_path):
        input_file = tmp_path / "latest.parquet"
        pd.DataFrame({"company_name": ["Co A"]}).to_parquet(input_file)
        with pytest.raises(ValueError):
            make_instance(str(input_file)).load_data()
