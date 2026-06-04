#!/usr/bin/env python3
"""Unit tests for idi_ftm2j_shared.storage.

These exercise the public load_json/save_json contract by behavior — real temp
files for local paths and a mocked boto3 client for S3 — rather than patching
module internals, so they survive implementation refactors of the dependency.
"""

import json
from unittest.mock import MagicMock, patch

from idi_ftm2j_shared.storage import load_json, save_json


class TestLoadJson:
    """Tests for load_json on local filesystem paths."""

    def test_loads_dict_from_local_file(self, tmp_path):
        """A JSON object is loaded back as a dict."""
        data = {"key": "value", "nested": {"a": 1}}
        path = tmp_path / "data.json"
        path.write_text(json.dumps(data))
        assert load_json(str(path)) == data

    def test_loads_list_from_local_file(self, tmp_path):
        """A JSON array is loaded back as a list when return_type='list'."""
        data = [1, 2, {"a": "b"}]
        path = tmp_path / "list.json"
        path.write_text(json.dumps(data))
        assert load_json(str(path), return_type="list") == data

    def test_returns_empty_dict_when_file_does_not_exist(self, tmp_path):
        """A missing file yields an empty dict for return_type='dict'."""
        assert load_json(str(tmp_path / "nope.json"), return_type="dict") == {}

    def test_returns_empty_list_when_file_does_not_exist(self, tmp_path):
        """A missing file yields an empty list for return_type='list'."""
        assert load_json(str(tmp_path / "nope.json"), return_type="list") == []


class TestSaveJson:
    """Tests for save_json on local filesystem paths."""

    def test_saves_dict_to_local_file(self, tmp_path):
        """A dict is written as JSON to a local path."""
        data = {"key": "value"}
        path = tmp_path / "out.json"
        save_json(str(path), data)
        assert json.loads(path.read_text()) == data

    def test_saves_list_to_local_file(self, tmp_path):
        """A list is written as JSON to a local path."""
        data = [1, 2, 3]
        path = tmp_path / "array.json"
        save_json(str(path), data)
        assert json.loads(path.read_text()) == data

    def test_round_trips_local_file(self, tmp_path):
        """save_json followed by load_json returns the original data."""
        data = {"x": 1, "y": [1, 2, 3]}
        path = tmp_path / "round_trip.json"
        save_json(str(path), data)
        assert load_json(str(path)) == data


class TestS3Json:
    """Tests for the S3 code paths with the boto3 client mocked."""

    def test_save_json_puts_object_to_s3(self):
        """save_json to an s3:// path issues a put_object with the JSON body."""
        data = {"x": 1}
        client = MagicMock()
        with patch("idi_ftm2j_shared.storage._get_s3_client", return_value=client):
            save_json("s3://bucket/key.json", data)

        client.put_object.assert_called_once()
        kwargs = client.put_object.call_args.kwargs
        assert kwargs["Bucket"] == "bucket"
        assert kwargs["Key"] == "key.json"
        assert json.loads(kwargs["Body"].decode()) == data

    def test_load_json_gets_object_from_s3(self):
        """load_json from an s3:// path reads the object body and parses it."""
        data = {"x": 1}
        body = MagicMock()
        body.read.return_value = json.dumps(data).encode()
        client = MagicMock()
        client.get_object.return_value = {"Body": body}
        with patch("idi_ftm2j_shared.storage._get_s3_client", return_value=client):
            result = load_json("s3://bucket/key.json")

        assert result == data
        client.get_object.assert_called_once_with(Bucket="bucket", Key="key.json")
