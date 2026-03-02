#!/usr/bin/env python3
"""
Unit tests for ftm2j.common.storage
"""

import io
import json
from unittest.mock import MagicMock, patch

from ftm2j.common.storage import load_json, save_json


class TestLoadJson:
    """Tests for load_json function."""

    def test_loads_dict_from_local_file(self):
        """Test loading a JSON object (dict) from a local file path."""
        data = {"key": "value", "nested": {"a": 1}}
        mock_stream = io.StringIO(json.dumps(data))
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)

        with patch("ftm2j.common.storage.pathlib.Path.exists", return_value=True):
            with patch("ftm2j.common.storage.smart_open.open", return_value=mock_stream) as mock_open:
                result = load_json("/fake/path/data.json")
                assert result == data
                mock_open.assert_called_once_with("/fake/path/data.json", mode="r")

    def test_loads_list_from_local_file(self):
        """Test loading a JSON array (list) from a local file path."""
        data = [1, 2, {"a": "b"}]
        mock_stream = io.StringIO(json.dumps(data))
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)

        with patch("ftm2j.common.storage.pathlib.Path.exists", return_value=True):
            with patch("ftm2j.common.storage.smart_open.open", return_value=mock_stream):
                result = load_json("/fake/path/list.json")
                assert result == data

    def test_passes_file_path_to_smart_open(self):
        """Test that the file path is passed correctly to smart_open."""
        mock_stream = io.StringIO(json.dumps({}))
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)

        with patch("ftm2j.common.storage.pathlib.Path.exists", return_value=True):
            with patch("ftm2j.common.storage.smart_open.open", return_value=mock_stream) as mock_open:
                load_json("/my/custom/path.json")
                mock_open.assert_called_once_with("/my/custom/path.json", mode="r")

    def test_returns_empty_dict_when_file_does_not_exist(self):
        """Test that load_json returns empty dict when file does not exist."""
        with patch("ftm2j.common.storage.pathlib.Path.exists", return_value=False):
            result = load_json("/nonexistent/path.json", return_type="dict")
            assert result == {}

    def test_returns_empty_list_when_file_does_not_exist(self):
        """Test that load_json returns empty list when file does not exist."""
        with patch("ftm2j.common.storage.pathlib.Path.exists", return_value=False):
            result = load_json("/nonexistent/path.json", return_type="list")
            assert result == []


class TestSaveJson:
    """Tests for save_json function."""

    def test_saves_dict_to_local_file(self):
        """Test saving a JSON object to a local file path."""
        data = {"key": "value"}
        written = []

        class CaptureWriter:
            def write(self, s):
                written.append(s)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with patch("ftm2j.common.storage.smart_open.open", return_value=CaptureWriter()) as mock_open:
            save_json("/fake/local/path.json", data)
            mock_open.assert_called_once_with("/fake/local/path.json", "w")
            assert json.loads("".join(written)) == data

    def test_saves_list_to_local_file(self):
        """Test saving a JSON array to a local file path."""
        data = [1, 2, 3]
        written = []

        class CaptureWriter:
            def write(self, s):
                written.append(s)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with patch("ftm2j.common.storage.smart_open.open", return_value=CaptureWriter()):
            save_json("/fake/local/array.json", data)
            assert json.loads("".join(written)) == data

    def test_uses_simple_open_for_non_s3_path(self):
        """Test that non-S3 paths use smart_open without transport_params."""
        data = {"x": 1}
        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)

        with patch("ftm2j.common.storage.smart_open.open", return_value=mock_stream) as mock_open:
            with patch("ftm2j.common.storage.json.dump") as mock_dump:
                save_json("/local/path.json", data)
                mock_open.assert_called_once_with("/local/path.json", "w")
                mock_dump.assert_called_once_with(data, mock_stream, indent=2)

    def test_uses_transport_params_for_s3_path(self):
        """Test that S3 paths use smart_open with transport_params and temp file."""
        data = {"x": 1}
        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)

        with patch("ftm2j.common.storage.smart_open.open", return_value=mock_stream) as mock_open:
            with patch("ftm2j.common.storage.tempfile.NamedTemporaryFile") as mock_tmp:
                mock_tmp_file = MagicMock()
                mock_tmp.return_value.__enter__ = MagicMock(return_value=mock_tmp_file)
                mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
                with patch("ftm2j.common.storage.json.dump"):
                    save_json("s3://bucket/key.json", data)
                    mock_open.assert_called_once()
                    call_kwargs = mock_open.call_args[1]
                    assert "transport_params" in call_kwargs
                    assert "writebuffer" in call_kwargs["transport_params"]
                    assert call_kwargs["transport_params"]["writebuffer"] == mock_tmp_file
