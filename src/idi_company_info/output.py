#!/usr/bin/env python3
"""Aggregate every processor's caches into a single, lock-protected parquet file.

Each input source (see :class:`InputSource`) runs independently and writes two JSON
caches under ``{output_dir}/{input_type}/``: ``permid_url.json`` (input rows linked to
their resolved PermID URLs) and ``permid_data.json`` (company info keyed by PermID URL).

``Output.aggregate`` joins those two files per processor on the PermID URL and concatenates
the result across all processors into one parquet — one row per identifier→PermID link,
mixing processors and identifiers together. The write is serialised with a
:class:`~idi_company_info.lock.FileLock` so concurrent processor runs do not clobber the
final file.
"""

# Standard library imports
import argparse
import pathlib
import sys
import uuid

# Third party imports
import pandas as pd
from idi_ftm2j_shared.logs import get_logger
from idi_ftm2j_shared.storage import key_exists, load_json

# Application imports
from idi_company_info.lock import FileLock
from idi_company_info.paths import join_path
from idi_company_info.types import InputSource

# Company-info fields carried over from each result entry (CompanyResult), excluding
# permid_url which we take from the join key so it is always populated.
COMPANY_FIELDS: list[str] = [
    "investor_name",
    "permid_id",
    "hq_address",
    "registered_address",
    "fax_number",
    "phone_number",
    "lei",
    "founded_date",
    "incorporated_in",
    "domiciled_in",
    "url",
    "activity_status",
    "last_processed",
]

# Explicit, stable column order so the parquet schema is deterministic even when a
# processor (or the whole aggregate) is empty.
OUTPUT_COLUMNS: list[str] = [
    "input_source",
    "entity_name",
    "identifier_type",
    "identifier",
    "standard_identifier",
    "permid_url",
    *COMPANY_FIELDS,
]

DEFAULT_OUTPUT_NAME = "latest.parquet"


class Output:
    """Aggregates per-processor caches into a single final parquet."""

    def __init__(
        self,
        output_dir: str,
        final_output_file: str | None = None,
        lock_timeout: float = 60.0,
    ) -> None:
        """Initialise the aggregator.

        Args:
            output_dir: Root output directory holding one subdir per input source
                (local path or ``s3://`` URL).
            final_output_file: Destination parquet path. Defaults to
                ``{output_dir}/company_info.parquet``.
            lock_timeout: Max seconds to wait for the write lock.
        """
        self.output_dir = str(output_dir)
        self.final_output_file = final_output_file or join_path(
            self.output_dir, DEFAULT_OUTPUT_NAME
        )
        self.lock_timeout = lock_timeout
        self.logger = get_logger(type(self).__name__)

    def aggregate(self) -> str:
        """Build the combined DataFrame and write it atomically under a lock.

        Returns:
            The path the parquet was written to.
        """
        rows: list[dict] = []
        for input_type in InputSource:
            subdir = join_path(self.output_dir, str(input_type).lower())
            permid_file = join_path(subdir, "permid_url.json")
            if not key_exists(permid_file):
                continue
            result_file = join_path(subdir, "permid_data.json")
            permid_data = load_json(permid_file, return_type="dict")
            result_data = load_json(result_file, return_type="dict")
            rows.extend(self._build_rows(str(input_type), permid_data, result_data))

        output_df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)

        with FileLock(self.final_output_file, timeout=self.lock_timeout):
            self._write_parquet(output_df)

        self.logger.info(
            "Aggregated %d rows from %d processor(s) -> %s",
            len(output_df),
            output_df["input_source"].nunique() if not output_df.empty else 0,
            self.final_output_file,
        )
        return self.final_output_file

    def _build_rows(self, input_source: str, permid_data: dict, result_data: dict) -> list[dict]:
        """Join one processor's permid_url entries against its company-info results.

        Args:
            input_source: The processor's InputSource value (becomes the row's source).
            permid_data: ``{cache_key: {"search": {...}, "result": [permid_url, ...]}}``.
            result_data: ``{permid_url: {company info fields}}``.

        Returns:
            One row dict per (entity, identifier, permid_url) link. Company info is
            left-joined: rows are kept even when the PermID has no result yet (null fields).
        """
        rows: list[dict] = []
        for entry in permid_data.values():
            search = entry.get("search", {})
            entity_name = search.get("Name")
            identifier_type, _, identifier = str(search.get("LocalID", "")).partition("_")
            standard_identifier = search.get("Standard Identifier")

            for permid_url in entry.get("result", []):
                company = result_data.get(permid_url, {})
                row = {
                    "input_source": input_source,
                    "entity_name": entity_name,
                    "identifier_type": identifier_type,
                    "identifier": identifier,
                    "standard_identifier": standard_identifier,
                    "permid_url": permid_url,
                }
                for field in COMPANY_FIELDS:
                    row[field] = company.get(field)
                rows.append(row)
        return rows

    def _write_parquet(self, df: pd.DataFrame) -> None:
        """Write ``df`` to the final path. Local writes go via a temp file + atomic rename."""
        if self.final_output_file.startswith("s3://"):
            df.to_parquet(self.final_output_file, index=False)
            return
        tmp_path = f"{self.final_output_file}.tmp.{uuid.uuid4().hex}"
        df.to_parquet(tmp_path, index=False)
        pathlib.Path(tmp_path).replace(self.final_output_file)


def get_args() -> argparse.Namespace:
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(
        description=("Aggregate all company-info processor caches into a single final parquet.")
    )
    parser.add_argument(
        "--output-directory",
        type=str,
        required=True,
        help="Root output directory holding one subdir per input source (local or s3:// URL)",
    )
    parser.add_argument(
        "--final-output-file",
        type=str,
        default=None,
        help="Destination parquet path (default: <output-directory>/company_info.parquet)",
    )
    parser.add_argument(
        "--lock-timeout",
        type=float,
        default=60.0,
        help="Max seconds to wait for the write lock (default: 60)",
    )
    return parser.parse_args()


def main() -> None:
    """Standalone entry point for on-demand aggregation."""
    args = get_args()
    output = Output(
        output_dir=args.output_directory,
        final_output_file=args.final_output_file,
        lock_timeout=args.lock_timeout,
    )
    try:
        output.aggregate()
    except Exception:
        get_logger("aggregate").exception("Aggregation failed")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
