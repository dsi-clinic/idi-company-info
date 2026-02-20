"""Provides storage utilities for use across the application."""

# Standard library imports
import json
import tempfile

# Third party imports
import smart_open


def load_json(file_path: str, mode: str = "r") -> dict | list:
    """Loads a JSON file from the given path.

    Args:
        file_path: The path to the JSON file.
        mode: The mode to open the file in.

    Returns:
        The JSON data loaded from the file as a dictionary or list.
    """
    with smart_open.open(file_path, mode=mode) as f:
        json_data = json.load(f)
    return json_data


def save_json(file_path: str, data: dict | list, mode: str = "w") -> None:
    """Saves a JSON file to the given path.

    Efficient writing: https://github.com/piskvorky/smart_open/blob/develop/howto.md#how-to-write-to-s3-efficiently

    Can write in append mode for local files, S3 files are always overwritten.

    Args:
        file_path: The path to the JSON file.
        data: The JSON data to save to the file as a dictionary or list.
    """
    if "s3://" in file_path:
        with tempfile.NamedTemporaryFile() as tmp:
            tp = {'writebuffer': tmp}
            with smart_open.open(file_path, "w", transport_params=tp) as fout:
                json.dump(data, fout, indent=2)
    else:
        with smart_open.open(file_path, mode) as fout:
            json.dump(data, fout, indent=2)
