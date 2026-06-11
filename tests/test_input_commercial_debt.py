#!/usr/bin/env python3
"""Unit tests for idi_company_info.input.CdtInput.

CdtInput reads a *directory* of CDT debt-instrument shards
(``cik_shard=*/part-0000.parquet``), projects company_name/cik, drops null and
literal ``"nan"`` company names, zero-pads CIKs to 10 digits, and groups by
company_name.
"""

from unittest.mock import MagicMock

import pandas as pd

from idi_company_info.input import CdtInput


def make_instance(input_file: str = "") -> CdtInput:
    """Create a CdtInput with a mocked logger and the given input path."""
    instance = CdtInput(input_file)
    instance.logger = MagicMock()
    return instance


def _write_shards(root, shards: dict[str, pd.DataFrame]) -> None:
    """Write each DataFrame to ``root/cik_shard=<key>/part-0000.parquet``."""
    for shard, df in shards.items():
        part_dir = root / f"cik_shard={shard}"
        part_dir.mkdir(parents=True)
        df.to_parquet(part_dir / "part-0000.parquet")


class TestIdentifierType:
    """CdtInput reports the cik identifier type."""

    def test_identifier_type_is_cik(self):
        assert make_instance().identifier_type == "cik"


class TestExtractFilterParquetCik:
    """Tests for CdtInput._extract_filter_parquet_cik."""

    def test_groups_ciks_by_company_name(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "company_name": ["Co A", "Co A", "Co B"],
                "cik": ["0001111111", "0002222222", "0003333333"],
            }
        )
        result = instance._extract_filter_parquet_cik(df)
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
        result = instance._extract_filter_parquet_cik(df)
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
        result = instance._extract_filter_parquet_cik(df)
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
        result = instance._extract_filter_parquet_cik(df)
        assert result["Co A"] == ["0001002910"]

    def test_deduplicates_name_cik_pairs(self):
        instance = make_instance()
        df = pd.DataFrame(
            {
                "company_name": ["Co A", "Co A"],
                "cik": ["0001111111", "0001111111"],
            }
        )
        result = instance._extract_filter_parquet_cik(df)
        assert result["Co A"] == ["0001111111"]

    def test_returns_empty_dict_for_empty_dataframe(self):
        instance = make_instance()
        df = pd.DataFrame({"company_name": [], "cik": []})
        result = instance._extract_filter_parquet_cik(df)
        assert result == {}


class TestReadParquetAndLoad:
    """CdtInput.read_parquet / load_data sweep an entire shard directory."""

    def test_reads_all_shards_in_directory(self, tmp_path):
        _write_shards(
            tmp_path,
            {
                "0000": pd.DataFrame({"company_name": ["Co A"], "cik": ["1002910"]}),
                "0001": pd.DataFrame(
                    {"company_name": ["Co B", "nan"], "cik": ["2078008", "1461755"]}
                ),
            },
        )
        instance = make_instance(str(tmp_path))
        result = instance.load_data()

        # Both real companies appear, the literal "nan" row is dropped, CIKs padded.
        assert result == {"Co A": ["0001002910"], "Co B": ["0002078008"]}
