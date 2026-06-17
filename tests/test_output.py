#!/usr/bin/env python3
"""Unit tests for idi_company_info.output.Output.

Output joins each processor's permid_url.json against its permid_data.json on the PermID
URL and concatenates across processors into a single parquet — one row per identifier→PermID
link, company info left-joined.
"""

import json

import pandas as pd

from idi_company_info.output import OUTPUT_COLUMNS, Output
from idi_company_info.types import InputSource


def _write_processor(output_dir, input_type: InputSource, permid: dict, result: dict) -> None:
    """Write permid_url.json and permid_data.json for one processor subdir."""
    subdir = output_dir / str(input_type).lower()
    subdir.mkdir(parents=True)
    (subdir / "permid_url.json").write_text(json.dumps(permid))
    (subdir / "permid_data.json").write_text(json.dumps(result))


def _permid_entry(name: str, local_id: str, std_id: str, urls: list[str]) -> dict:
    return {
        "search": {"Name": name, "LocalID": local_id, "Standard Identifier": std_id},
        "result": urls,
    }


def _company(url: str, name: str) -> dict:
    return {
        "investor_name": name,
        "permid_id": url.rsplit("-", 1)[-1],
        "permid_url": url,
        "hq_address": "1 Main St",
        "registered_address": None,
        "fax_number": None,
        "phone_number": None,
        "lei": None,
        "founded_date": None,
        "incorporated_in": None,
        "domiciled_in": None,
        "url": None,
        "activity_status": "Active",
        "primary_business_sector_label": None,
        "primary_economic_sector_label": None,
        "primary_industry_group_label": None,
        "primary_business_sector_comment": None,
        "primary_economic_sector_comment": None,
        "primary_industry_group_comment": None,
        "ticker": None,
        "exchange": None,
        "exchange_code": None,
        "ric": None,
        "last_processed": "20260611T101530",
    }


class TestAggregate:
    """Output.aggregate joins and combines processor caches."""

    def test_mixes_processors_and_left_joins_company_info(self, tmp_path):
        out = tmp_path / "out"
        url_a = "https://permid.org/1-1"
        url_unresolved = "https://permid.org/1-2"

        _write_processor(
            out,
            InputSource.SHAREHOLDER_TRACKER_CIK,
            {
                "Firm A_cik_0000000001": _permid_entry(
                    "Firm A", "cik_0000000001", "Cik:0000000001", [url_a]
                )
            },
            {url_a: _company(url_a, "Firm A Inc")},
        )
        _write_processor(
            out,
            InputSource.COMMERCIAL_DEBT_TRACKER,
            {
                "Debt Co_cik_0000000002": _permid_entry(
                    "Debt Co", "cik_0000000002", "Cik:0000000002", [url_unresolved]
                )
            },
            {},  # company info not resolved yet -> left join keeps the row with nulls
        )

        path = Output(str(out)).aggregate()
        df = pd.read_parquet(path)

        assert list(df.columns) == OUTPUT_COLUMNS
        assert set(df["input_source"]) == {
            "shareholder_tracker_cik",
            "commercial_debt_tracker",
        }
        assert len(df) == 2

        resolved = df[df["permid_url"] == url_a].iloc[0]
        assert resolved["entity_name"] == "Firm A"
        assert resolved["identifier_type"] == "cik"
        assert resolved["identifier"] == "0000000001"
        assert resolved["investor_name"] == "Firm A Inc"

        unresolved = df[df["permid_url"] == url_unresolved].iloc[0]
        assert unresolved["entity_name"] == "Debt Co"
        assert pd.isna(unresolved["investor_name"])

    def test_multi_url_entry_fans_out_to_multiple_rows(self, tmp_path):
        out = tmp_path / "out"
        urls = ["https://permid.org/1-10", "https://permid.org/1-11"]
        _write_processor(
            out,
            InputSource.SHAREHOLDER_TRACKER_CIK,
            {"Firm A_cik_0000000001": _permid_entry("Firm A", "cik_0000000001", "Cik:1", urls)},
            {u: _company(u, f"Co {u}") for u in urls},
        )

        df = pd.read_parquet(Output(str(out)).aggregate())
        assert len(df) == 2
        assert set(df["permid_url"]) == set(urls)

    def test_empty_output_dir_writes_empty_parquet_with_schema(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        df = pd.read_parquet(Output(str(out)).aggregate())
        assert list(df.columns) == OUTPUT_COLUMNS
        assert len(df) == 0

    def test_default_output_path(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        path = Output(str(out)).aggregate()
        assert path.endswith("latest.parquet")
        assert (out / "latest.parquet").exists()

    def test_skips_processor_with_unparseable_cache(self, tmp_path):
        """A malformed cache (e.g. mid-write) is skipped; valid processors still aggregate."""
        out = tmp_path / "out"
        url = "https://permid.org/1-1"
        _write_processor(
            out,
            InputSource.SHAREHOLDER_TRACKER_CIK,
            {"Firm A_cik_0000000001": _permid_entry("Firm A", "cik_0000000001", "Cik:1", [url])},
            {url: _company(url, "Firm A Inc")},
        )
        # Write a truncated/invalid permid_url.json for a second processor.
        bad_subdir = out / str(InputSource.COMMERCIAL_DEBT_TRACKER).lower()
        bad_subdir.mkdir(parents=True)
        (bad_subdir / "permid_url.json").write_text('{"Debt Co": {"search": ')  # truncated
        (bad_subdir / "permid_data.json").write_text("{}")

        df = pd.read_parquet(Output(str(out)).aggregate())

        # Bad processor skipped; good processor's row present.
        assert set(df["input_source"]) == {"shareholder_tracker_cik"}
        assert len(df) == 1
