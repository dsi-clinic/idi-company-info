#!/usr/bin/env python3
"""Build a CIK-mode investors parquet from CDT debt-instrument shards.

The Commercial Debt Tracker writes consolidated instruments to
``processors/cdt/debt-instruments/cik_shard=*/part-0000.parquet`` (the
``database/cdt/.../latest.parquet`` snapshot is only written when the pipeline
runs with ``--final-database-root``, which has not happened for this bucket).

This adapter reads every shard, projects ``company_name``/``cik`` onto the
``investor_name``/``investor_cik`` columns the Company Info processor expects,
and writes a single parquet. The processor handles CIK-prefix stripping,
deduplication, and grouping itself (see input.py:_extract_filter_parquet_cik),
so we only need the two columns plus a light dedup to keep the file small.

Usage:
    uv run python scripts/cdt_to_investors.py \\
        --output s3://<bucket>/cdt-companies/investors.parquet

Then feed it to the pipeline:
    uv run pipeline \\
        --input-file s3://<bucket>/cdt-companies/investors.parquet \\
        --output-directory s3://<bucket>/cdt-companies/output \\
        --failure-directory s3://<bucket>/cdt-companies/failures \\
        --type cik
"""

from __future__ import annotations

import argparse
import logging

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cdt_to_investors")

DEFAULT_SHARD_ROOT = (
    "s3://idi-dev-ftm2j-shared-processor-storage"
    "/processors/cdt/debt-instruments"
)


def build_investors(shard_root: str, output: str) -> int:
    """Read all CDT debt-instrument shards and write the investors parquet.

    Args:
        shard_root: Directory containing ``cik_shard=*/part-0000.parquet`` files
            (local path or ``s3://`` URI).
        output: Destination parquet path (local path or ``s3://`` URI).

    Returns:
        The number of unique ``(investor_name, investor_cik)`` rows written.
    """
    # A pyarrow dataset over the root reads every cik_shard partition in one go,
    # whether shard_root is local or on S3 (s3fs resolves the s3:// scheme).
    logger.info("Reading debt-instrument shards from %s", shard_root)
    dataset = ds.dataset(shard_root, format="parquet")

    table = dataset.to_table(columns=["company_name", "cik"])
    logger.info("Read %s instrument rows across all shards", table.num_rows)

    table = table.rename_columns(["investor_name", "investor_cik"])

    # Drop rows with no CIK, then dedup. The processor dedups too, but trimming
    # here keeps the handoff file small (one row per company instead of per
    # instrument).
    df = table.to_pandas()
    df = df[df["investor_cik"].notna() & (df["investor_cik"].astype(str) != "")]
    df["investor_cik"] = df["investor_cik"].astype(str)
    df = df.drop_duplicates(subset=["investor_name", "investor_cik"])
    logger.info("Writing %s unique investor_name/CIK rows to %s", len(df), output)

    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), output)
    return len(df)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shard-root",
        default=DEFAULT_SHARD_ROOT,
        help="Root of CDT debt-instrument shards (local path or s3:// URI).",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Destination investors parquet (local path or s3:// URI).",
    )
    args = parser.parse_args()
    build_investors(args.shard_root, args.output)


if __name__ == "__main__":
    main()
