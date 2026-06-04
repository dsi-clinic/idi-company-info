"""Integration tests for orchestrator.py.

These tests exercise the full pipeline for each identifier type (CIK, CUSIP)
using real temporary files and mocked API clients. They verify:
  - IdentifierFactory creates the correct class with the correct configuration
  - PipelineOrchestrator handles success, missing input, and failures correctly
  - Each identifier type produces the expected company info output file
"""

import json
import pathlib
from unittest.mock import patch

import pandas as pd

from idi_company_info.api import (
    GeonamesApi,
    LSEGEntityLookup,
    LsegRecordMatch,
)
from idi_company_info.company_by_cik_pipeline import CompanyByCikPipeline
from idi_company_info.company_by_cusip_pipeline import CompanyByCusipPipeline
from idi_company_info.factory import IdentifierFactory
from idi_company_info.orchestrator import PipelineOrchestrator
from idi_company_info.registry import IDENTIFIER_REGISTRY
from idi_company_info.types import IdentifierType, OrchestratorConfig


def _paths_for(
    output_dir: pathlib.Path,
    failure_dir: pathlib.Path,
    identifier_type: IdentifierType,
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Build (result, permid, failure) paths from the given directories.

    Mirrors the factory: result and permid files live under a per-type
    subdirectory inside output_dir; the failure file is written directly
    under failure_dir.
    """
    spec = IDENTIFIER_REGISTRY[identifier_type]
    type_subdir = str(identifier_type)
    return (
        output_dir / type_subdir / spec.result_filename,
        output_dir / type_subdir / spec.permid_filename,
        failure_dir / spec.failure_filename,
    )


# ---------------------------------------------------------------------------
# Shared mock API responses
# ---------------------------------------------------------------------------

_PERMID_URL = "https://permid.org/1-4295904307"

# Entity Lookup: returns company detail
_ENTITY_LOOKUP_HIT = {
    "status_code": 200,
    "data": {
        "vcard:organization-name": "Test Corp Inc.",
        "tr-common:hasPermId": "4295904307",
        "@id": _PERMID_URL,
        "hasActivityStatus": "Active",
    },
}

# Geonames: not found (avoids a second API call in _parse_company_info)
_GEONAMES_MISS = {"status_code": 404}

_RECORD_MATCH_MISS = {
    "status_code": 200,
    "data": {"outputContentResponse": []},
}


def _record_match_hit(entity_name: str, local_id: str, identifier_type: str = "cik") -> dict:
    """Build a Record Match response with a single 100% match.

    ``Input_LocalID`` is prefixed with the identifier type to match what
    ``_build_records`` sends (e.g. ``cik_0001234567`` for CIK mode).
    """
    return {
        "status_code": 200,
        "data": {
            "outputContentResponse": [
                {
                    "Input_Name": entity_name,
                    "Input_LocalID": f"{identifier_type}_{local_id}",
                    "Match OpenPermID": _PERMID_URL,
                    "Match Score": "100%",
                }
            ]
        },
    }


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_config(
    input_file: pathlib.Path,
    output_dir: pathlib.Path,
    identifier_type: IdentifierType,
    **kwargs,
) -> OrchestratorConfig:
    """Build a minimal OrchestratorConfig for testing.

    Tests share a single directory for output and failures — they don't need
    to be distinct, just the per-type filenames must not collide.
    """
    defaults = {"batch_size": 10, "buffer_size": 5, "threshold_days": None}
    defaults.update(kwargs)
    return OrchestratorConfig(
        input_file=input_file,
        output_dir=output_dir,
        failure_dir=output_dir,
        identifier_type=identifier_type,
        api_key="test-api-key",
        geonames_user="test-geonames-user",
        **defaults,
    )


def _read_output(output_file: pathlib.Path) -> dict:
    """Read the company info output file, returning {} if it does not exist.

    The result_file is a dict keyed by permid_url:
    ``{permid_url: {search: {...}, result: {...}, identifier: {...}}}``.
    """
    return json.loads(output_file.read_text()) if output_file.exists() else {}


# ---------------------------------------------------------------------------
# TestIdentifierFactory
# ---------------------------------------------------------------------------


class TestIdentifierFactory:
    """Verify the factory wires the correct Identifier subclass and configuration."""

    def test_cik_creates_identifier_cik(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame({"investor_name": ["Firm A"], "investor_cik": ["123"]}).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", IdentifierType.CIK)

        identifier = IdentifierFactory.build(config)

        assert isinstance(identifier, CompanyByCikPipeline)

    def test_cusip_creates_identifier_cusip(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame(
            {
                "issuer_name": ["Corp A"],
                "security_cusip": ["037833100"],
                "stock_ticker": ["AAPL"],
            }
        ).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", IdentifierType.CUSIP)

        identifier = IdentifierFactory.build(config)

        assert isinstance(identifier, CompanyByCusipPipeline)

    def test_file_paths_passed_through_explicitly(self, tmp_path):
        out = tmp_path / "output"
        parquet = tmp_path / "data.parquet"
        pd.DataFrame({"investor_name": ["Firm A"], "investor_cik": ["123"]}).to_parquet(parquet)
        config = _make_config(parquet, out, IdentifierType.CIK)
        expected_result, expected_permid, expected_failure = _paths_for(
            out, out, IdentifierType.CIK
        )

        identifier = IdentifierFactory.build(config)

        assert identifier.file_paths.result_file == str(expected_result)
        assert identifier.file_paths.permid_file == str(expected_permid)
        assert identifier.file_paths.failure_file == str(expected_failure)

    def test_batch_config_passed_through(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame({"investor_name": ["Firm A"], "investor_cik": ["123"]}).to_parquet(parquet)
        config = _make_config(
            parquet, tmp_path / "out", IdentifierType.CIK, batch_size=42, buffer_size=7
        )

        identifier = IdentifierFactory.build(config)

        assert identifier.batch_config.batch_size == 42
        assert identifier.batch_config.buffer_size == 7

    def test_each_type_has_distinct_output_filenames(self, tmp_path):
        result_files = {t: _paths_for(tmp_path, tmp_path, t)[0] for t in IdentifierType}
        assert len(set(result_files.values())) == len(IdentifierType), (
            "Each identifier type must write to a distinct result file"
        )


# ---------------------------------------------------------------------------
# TestPipelineOrchestratorFlow
# ---------------------------------------------------------------------------


class TestPipelineOrchestratorFlow:
    """Verify PipelineOrchestrator.run() flow control."""

    def test_returns_false_when_input_file_missing(self, tmp_path):
        config = _make_config(
            input_file=tmp_path / "does_not_exist.parquet",
            output_dir=tmp_path / "out",
            identifier_type=IdentifierType.CIK,
        )

        result = PipelineOrchestrator(config).run()

        assert result is False

    def test_returns_true_when_identifier_run_succeeds(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame({"investor_name": ["A"], "investor_cik": ["1"]}).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", IdentifierType.CIK)

        with patch("idi_company_info.orchestrator.IdentifierFactory.build") as mock_build:
            mock_build.return_value.run.return_value = None

            result = PipelineOrchestrator(config).run()

        assert result is True
        mock_build.return_value.run.assert_called_once()

    def test_returns_false_when_identifier_run_raises(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame({"investor_name": ["A"], "investor_cik": ["1"]}).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", IdentifierType.CIK)

        with patch("idi_company_info.orchestrator.IdentifierFactory.build") as mock_build:
            mock_build.return_value.run.side_effect = RuntimeError("simulated API failure")

            result = PipelineOrchestrator(config).run()

        assert result is False

    def test_returns_false_on_keyboard_interrupt(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame({"investor_name": ["A"], "investor_cik": ["1"]}).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", IdentifierType.CIK)

        with patch("idi_company_info.orchestrator.IdentifierFactory.build") as mock_build:
            mock_build.return_value.run.side_effect = KeyboardInterrupt

            result = PipelineOrchestrator(config).run()

        assert result is False


# ---------------------------------------------------------------------------
# TestCikPipelineIntegration
# ---------------------------------------------------------------------------


class TestCikPipelineIntegration:
    """End-to-end integration tests for the CIK (Record Match) pipeline."""

    def test_writes_company_info_for_matched_cik(self, tmp_path):
        entity_name = "Firm Alpha"
        cik = "0001234567"
        parquet = tmp_path / "data.parquet"
        pd.DataFrame(
            {
                "investor_name": [entity_name],
                "investor_cik": [cik],
            }
        ).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", IdentifierType.CIK)

        with (
            patch.object(
                LsegRecordMatch, "query_endpoint", return_value=_record_match_hit(entity_name, cik)
            ),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        result_file, permid_file, _ = _paths_for(
            tmp_path / "out", tmp_path / "out", IdentifierType.CIK
        )
        records = _read_output(result_file)

        # result_file is flat company info keyed by permid_url (no envelope)
        assert len(records) == 1
        assert _PERMID_URL in records
        assert records[_PERMID_URL]["permid_id"] == "4295904307"

        # linkage (name, identifier) -> permid_url lives in permid_file
        permid = _read_output(permid_file)
        assert permid[f"{entity_name}_cik_{cik}"]["result"] == [_PERMID_URL]

    def test_produces_empty_output_when_no_permid_match(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame(
            {
                "investor_name": ["Unknown Corp"],
                "investor_cik": ["0009999999"],
            }
        ).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", IdentifierType.CIK)

        with (
            patch.object(LsegRecordMatch, "query_endpoint", return_value=_RECORD_MATCH_MISS),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        result_file, _, _ = _paths_for(tmp_path / "out", tmp_path / "out", IdentifierType.CIK)
        records = _read_output(result_file)
        assert records == {}

    def test_creates_separate_output_from_cusip_pipeline(self, tmp_path):
        """CIK output files are distinct from CUSIP output files."""
        cik_result, _, _ = _paths_for(tmp_path, tmp_path, IdentifierType.CIK)
        cusip_result, _, _ = _paths_for(tmp_path, tmp_path, IdentifierType.CUSIP)
        assert cik_result != cusip_result

    def test_deduplicates_investor_cik_pairs(self, tmp_path):
        """Duplicate (investor_name, investor_cik) pairs produce only one record."""
        entity_name = "Firm Alpha"
        cik = "0001234567"
        parquet = tmp_path / "data.parquet"
        pd.DataFrame(
            {
                "investor_name": [entity_name, entity_name, entity_name],
                "investor_cik": [cik, cik, cik],
            }
        ).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", IdentifierType.CIK)

        with (
            patch.object(
                LsegRecordMatch, "query_endpoint", return_value=_record_match_hit(entity_name, cik)
            ),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        result_file, permid_file, _ = _paths_for(
            tmp_path / "out", tmp_path / "out", IdentifierType.CIK
        )
        records = _read_output(result_file)

        # Duplicate input rows collapse to one company (one permid_url).
        assert len(records) == 1
        assert _PERMID_URL in records
        permid = _read_output(permid_file)
        assert permid[f"{entity_name}_cik_{cik}"]["result"] == [_PERMID_URL]


# ---------------------------------------------------------------------------
# TestCusipPipelineIntegration
# ---------------------------------------------------------------------------


class TestCusipPipelineIntegration:
    """End-to-end integration tests for the CUSIP (Record Match) pipeline.

    The CUSIP pipeline uses CUSIP as the LocalID (stable identifier) and the
    formatted ticker as the Standard Identifier in Record Match API calls.
    Input parquet must include issuer_name, security_cusip, and stock_ticker.
    """

    def test_writes_company_info_for_matched_cusip(self, tmp_path):
        entity_name = "Corp Beta"
        cusip = "037833100"
        parquet = tmp_path / "data.parquet"
        pd.DataFrame(
            {
                "issuer_name": [entity_name],
                "security_cusip": [cusip],
                "stock_ticker": ["AAPL"],
            }
        ).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", IdentifierType.CUSIP)

        with (
            patch.object(
                LsegRecordMatch,
                "query_endpoint",
                return_value=_record_match_hit(entity_name, cusip, "cusip"),
            ),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        result_file, permid_file, _ = _paths_for(
            tmp_path / "out", tmp_path / "out", IdentifierType.CUSIP
        )
        records = _read_output(result_file)

        assert len(records) == 1
        assert _PERMID_URL in records
        assert records[_PERMID_URL]["permid_id"] == "4295904307"
        permid = _read_output(permid_file)
        assert permid[f"{entity_name}_cusip_{cusip}"]["result"] == [_PERMID_URL]

    def test_result_file_is_pure_company_info(self, tmp_path):
        """result_file entries are flat company info — no search/result envelope, no ticker."""
        entity_name = "Corp Beta"
        cusip = "037833100"
        parquet = tmp_path / "data.parquet"
        pd.DataFrame(
            {
                "issuer_name": [entity_name],
                "security_cusip": [cusip],
                "stock_ticker": ["AAPL"],
            }
        ).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", IdentifierType.CUSIP)

        with (
            patch.object(
                LsegRecordMatch,
                "query_endpoint",
                return_value=_record_match_hit(entity_name, cusip, "cusip"),
            ),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            PipelineOrchestrator(config).run()

        result_file, _, _ = _paths_for(tmp_path / "out", tmp_path / "out", IdentifierType.CUSIP)
        records = _read_output(result_file)
        record = list(records.values())[0]
        # Flat company info: no "search"/"result" envelope, fields sit at the top level.
        assert "search" not in record
        assert "result" not in record
        assert "permid_id" in record
        assert "last_processed" in record
        assert "identifiers" not in record
        assert "ticker" not in record

    def test_produces_empty_output_when_no_permid_match(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame(
            {
                "issuer_name": ["Unknown Corp"],
                "security_cusip": ["000000000"],
                "stock_ticker": ["XYZ"],
            }
        ).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", IdentifierType.CUSIP)

        with (
            patch.object(LsegRecordMatch, "query_endpoint", return_value=_RECORD_MATCH_MISS),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        result_file, _, _ = _paths_for(tmp_path / "out", tmp_path / "out", IdentifierType.CUSIP)
        records = _read_output(result_file)
        assert records == {}

    def test_bond_securities_are_filtered_before_api_call(self, tmp_path):
        """Bond tickers must be dropped before the Record Match API is called."""
        parquet = tmp_path / "data.parquet"
        pd.DataFrame(
            {
                "issuer_name": ["Corp Delta"],
                "security_cusip": ["037833100"],
                "stock_ticker": ["WEC 4.375 06/01/29"],
            }
        ).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", IdentifierType.CUSIP)

        with (
            patch.object(LsegRecordMatch, "query_endpoint") as mock_rm,
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        mock_rm.assert_not_called()

    def test_deduplicates_issuer_cusip_pairs(self, tmp_path):
        """Duplicate (issuer_name, security_cusip, stock_ticker) triples produce one record."""
        entity_name = "Corp Beta"
        cusip = "037833100"
        parquet = tmp_path / "data.parquet"
        pd.DataFrame(
            {
                "issuer_name": [entity_name, entity_name, entity_name],
                "security_cusip": [cusip, cusip, cusip],
                "stock_ticker": ["AAPL", "AAPL", "AAPL"],
            }
        ).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", IdentifierType.CUSIP)

        with (
            patch.object(
                LsegRecordMatch,
                "query_endpoint",
                return_value=_record_match_hit(entity_name, cusip, "cusip"),
            ),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        result_file, permid_file, _ = _paths_for(
            tmp_path / "out", tmp_path / "out", IdentifierType.CUSIP
        )
        records = _read_output(result_file)

        assert len(records) == 1
        assert _PERMID_URL in records
        permid = _read_output(permid_file)
        assert permid[f"{entity_name}_cusip_{cusip}"]["result"] == [_PERMID_URL]

    def test_record_match_receives_cusip_as_local_id(self, tmp_path):
        """Record Match CSV payload uses CUSIP as LocalID and ticker as Standard Identifier."""
        entity_name = "Corp Beta"
        cusip = "037833100"
        parquet = tmp_path / "data.parquet"
        pd.DataFrame(
            {
                "issuer_name": [entity_name],
                "security_cusip": [cusip],
                "stock_ticker": ["AAPL"],
            }
        ).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", IdentifierType.CUSIP)

        with (
            patch.object(
                LsegRecordMatch,
                "query_endpoint",
                return_value=_record_match_hit(entity_name, cusip, "cusip"),
            ) as mock_rm,
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            PipelineOrchestrator(config).run()

        mock_rm.assert_called_once()
        csv_payload = mock_rm.call_args.args[0]
        assert cusip in csv_payload
        assert "ticker:AAPL" in csv_payload

    def test_low_score_match_is_excluded(self, tmp_path):
        """A Record Match response below the score threshold should not produce output."""
        entity_name = "Corp Zeta"
        cusip = "037833100"
        parquet = tmp_path / "data.parquet"
        pd.DataFrame(
            {
                "issuer_name": [entity_name],
                "security_cusip": [cusip],
                "stock_ticker": ["AAPL"],
            }
        ).to_parquet(parquet)
        # match_score_threshold=1 means 100% required; return 50% match
        config = _make_config(
            parquet, tmp_path / "out", IdentifierType.CUSIP, match_score_threshold=1
        )

        low_score_response = {
            "status_code": 200,
            "data": {
                "outputContentResponse": [
                    {
                        "Input_Name": entity_name,
                        "Input_LocalID": f"cusip_{cusip}",
                        "Match OpenPermID": _PERMID_URL,
                        "Match Score": "50%",
                    }
                ]
            },
        }

        with (
            patch.object(LsegRecordMatch, "query_endpoint", return_value=low_score_response),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        result_file, _, _ = _paths_for(tmp_path / "out", tmp_path / "out", IdentifierType.CUSIP)
        records = _read_output(result_file)
        assert records == {}

    def test_keeps_distinct_issuer_cusip_pairs_for_same_issuer(self, tmp_path):
        """Same issuer with different CUSIPs produces both records."""
        entity_name = "Corp Beta"
        cusip_a, cusip_b = "037833100", "037833101"
        parquet = tmp_path / "data.parquet"
        pd.DataFrame(
            {
                "issuer_name": [entity_name, entity_name],
                "security_cusip": [cusip_a, cusip_b],
                "stock_ticker": ["AAPL", "MSFT"],
            }
        ).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", IdentifierType.CUSIP)

        _PERMID_URL_A = "https://permid.org/1-1111111111"
        _PERMID_URL_B = "https://permid.org/1-2222222222"

        def record_match_side_effect(*args, **kwargs):
            return {
                "status_code": 200,
                "data": {
                    "outputContentResponse": [
                        {
                            "Input_Name": entity_name,
                            "Input_LocalID": f"cusip_{cusip_a}",
                            "Match OpenPermID": _PERMID_URL_A,
                            "Match Score": "100%",
                        },
                        {
                            "Input_Name": entity_name,
                            "Input_LocalID": f"cusip_{cusip_b}",
                            "Match OpenPermID": _PERMID_URL_B,
                            "Match Score": "100%",
                        },
                    ]
                },
            }

        def entity_lookup_side_effect(*args, permid_url, **kwargs):
            permid_id = permid_url.split("/")[-1].split("-")[-1]
            return {
                "status_code": 200,
                "data": {
                    "vcard:organization-name": "Test Corp Inc.",
                    "tr-common:hasPermId": permid_id,
                    "@id": permid_url,
                    "hasActivityStatus": "Active",
                },
            }

        with (
            patch.object(LsegRecordMatch, "query_endpoint", side_effect=record_match_side_effect),
            patch.object(LSEGEntityLookup, "query_endpoint", side_effect=entity_lookup_side_effect),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        result_file, permid_file, _ = _paths_for(
            tmp_path / "out", tmp_path / "out", IdentifierType.CUSIP
        )
        records = _read_output(result_file)

        # Two distinct CUSIPs -> two distinct PermIDs -> two result entries.
        assert set(records.keys()) == {_PERMID_URL_A, _PERMID_URL_B}
        permid = _read_output(permid_file)
        assert permid[f"{entity_name}_cusip_{cusip_a}"]["result"] == [_PERMID_URL_A]
        assert permid[f"{entity_name}_cusip_{cusip_b}"]["result"] == [_PERMID_URL_B]
