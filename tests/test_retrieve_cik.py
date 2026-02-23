#!/usr/bin/env python3
"""
Unit tests for retrieve_cik.py
"""

import json
import pathlib
from unittest.mock import Mock, mock_open, patch

import pandas as pd
import pytest

from idi_company_info import retrieve_cik


class TestReadParquet:
    """Tests for read_parquet function"""

    def test_read_parquet_success(self):
        """Test successful parquet file reading"""
        mock_df = pd.DataFrame({
            "investor_name": ["Company A", "Company B"],
            "investor_cik": ["0001234567", "0001234568"]
        })

        with patch("pandas.read_parquet", return_value=mock_df) as mock_read:
            result = retrieve_cik.read_parquet(pathlib.Path("/fake/path.parquet"))

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
                retrieve_cik.read_parquet(pathlib.Path("/fake/path.parquet"))


class TestExtractFilterParquet:
    """Tests for extract_filter_parquet function"""

    def test_extract_filter_basic(self):
        """Test basic extraction and filtering"""
        df = pd.DataFrame({
            "investor_name": ["Company A", "Company B", "Company A"],
            "investor_cik": ["CIK0001234567", "CIK0001234568", "CIK0001234569"],
            "other_column": ["x", "y", "z"]
        })

        result = retrieve_cik.extract_filter_parquet(df)

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

        result = retrieve_cik.extract_filter_parquet(df)

        assert "Company A" in result
        assert "Company B" not in result
        assert "Company C" not in result

    def test_extract_filter_removes_duplicates(self):
        """Test that duplicate investor_name/CIK pairs are removed"""
        df = pd.DataFrame({
            "investor_name": ["Company A", "Company A", "Company A"],
            "investor_cik": ["CIK0001234567", "CIK0001234567", "CIK0001234568"]
        })

        result = retrieve_cik.extract_filter_parquet(df)

        assert len(result["Company A"]) == 2
        assert "0001234567" in result["Company A"]
        assert "0001234568" in result["Company A"]

    def test_extract_filter_removes_cik_prefix(self):
        """Test that CIK prefix is removed"""
        df = pd.DataFrame({
            "investor_name": ["Company A"],
            "investor_cik": ["CIK0001234567"]
        })

        result = retrieve_cik.extract_filter_parquet(df)

        assert result["Company A"] == ["0001234567"]


class TestSaveResult:
    """Tests for save_result function"""

    def test_save_result_success(self):
        """Test successful result saving"""
        result = {
            "Company A": ["0001234567", "0001234568"],
            "Company B": ["0001234569"]
        }

        m = mock_open()
        with patch("builtins.open", m):
            retrieve_cik.save_result(result, pathlib.Path("/fake/output.json"))

        m.assert_called_once_with(pathlib.Path("/fake/output.json"), "w")

        # Verify JSON was written
        handle = m()
        written_data = "".join(call.args[0] for call in handle.write.call_args_list)
        written_json = json.loads(written_data)

        assert written_json == result


class TestMain:
    """Tests for main function"""

    @patch("idi_company_info.retrieve_cik.save_result")
    @patch("idi_company_info.retrieve_cik.extract_filter_parquet")
    @patch("idi_company_info.retrieve_cik.read_parquet")
    @patch("idi_company_info.retrieve_cik.get_args")
    def test_main_integration(
        self, mock_get_args, mock_read_parquet, mock_extract, mock_save
    ):
        """Test main function integration"""
        # Setup mocks
        mock_args = Mock()
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
        retrieve_cik.main()

        # Verify calls
        mock_get_args.assert_called_once()
        mock_read_parquet.assert_called_once_with(pathlib.Path("/fake/input.parquet"))
        mock_extract.assert_called_once_with(mock_df)
        mock_save.assert_called_once_with(mock_result, pathlib.Path("/fake/output.json"))
