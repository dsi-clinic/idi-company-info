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


def _record_match_hit(entity_name: str, local_id: str) -> dict:
    """Build a Record Match response with a single 100% match."""
    return {
        "status_code": 200,
        "data": {
            "outputContentResponse": [
                {
                    "Input_Name": entity_name,
                    "Input_LocalID": local_id,
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
    """Build a minimal OrchestratorConfig for testing."""
    defaults = {"batch_size": 10, "buffer_size": 5, "threshold_days": None}
    defaults.update(kwargs)
    return OrchestratorConfig(
        input_file=input_file,
        output_dir=output_dir,
        identifier_type=identifier_type,
        api_key="test-api-key",
        geonames_user="test-geonames-user",
        **defaults,
    )


def _read_output(output_file: pathlib.Path) -> list:
    """Read the company info output file, returning [] if it does not exist."""
    return json.loads(output_file.read_text()) if output_file.exists() else []


def _read_permid_tracking(output_dir: pathlib.Path, identifier_type: IdentifierType) -> dict:
    """Read the permid tracking file for the given identifier type."""
    spec = IDENTIFIER_REGISTRY[identifier_type]
    permid_file = output_dir / "permid_data" / spec.permid_filename
    return json.loads(permid_file.read_text()) if permid_file.exists() else {}


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

    def test_file_paths_derived_from_output_dir(self, tmp_path):
        out = tmp_path / "output"
        parquet = tmp_path / "data.parquet"
        pd.DataFrame({"investor_name": ["Firm A"], "investor_cik": ["123"]}).to_parquet(parquet)
        config = _make_config(parquet, out, IdentifierType.CIK)
        spec = IDENTIFIER_REGISTRY[IdentifierType.CIK]

        identifier = IdentifierFactory.build(config)

        assert identifier.file_paths.result_file == str(out / "company_info" / spec.result_filename)
        assert identifier.file_paths.permid_file == str(out / "permid_data" / spec.permid_filename)
        assert identifier.file_paths.failure_file == str(out / "failures" / spec.failure_filename)

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
        result_files = {t: IDENTIFIER_REGISTRY[t].result_filename for t in IdentifierType}
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
        spec = IDENTIFIER_REGISTRY[IdentifierType.CIK]
        records = _read_output(tmp_path / "out" / "company_info" / spec.result_filename)

        assert len(records) == 1
        assert records[0]["original_entity_name"] == entity_name
        assert records[0]["identifier_type"] == "cik"
        assert records[0]["identifier"] == cik
        assert records[0]["permid_id"] == "4295904307"

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
        spec = IDENTIFIER_REGISTRY[IdentifierType.CIK]
        records = _read_output(tmp_path / "out" / "company_info" / spec.result_filename)
        assert records == []

    def test_creates_separate_output_from_cusip_pipeline(self, tmp_path):
        """CIK output files are distinct from CUSIP output files."""
        cik_spec = IDENTIFIER_REGISTRY[IdentifierType.CIK]
        cusip_spec = IDENTIFIER_REGISTRY[IdentifierType.CUSIP]
        assert cik_spec.result_filename != cusip_spec.result_filename

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
        spec = IDENTIFIER_REGISTRY[IdentifierType.CIK]
        records = _read_output(tmp_path / "out" / "company_info" / spec.result_filename)

        assert len(records) == 1
        assert records[0]["original_entity_name"] == entity_name
        assert records[0]["identifier"] == cik


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
                return_value=_record_match_hit(entity_name, cusip),
            ),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        spec = IDENTIFIER_REGISTRY[IdentifierType.CUSIP]
        records = _read_output(tmp_path / "out" / "company_info" / spec.result_filename)

        assert len(records) == 1
        assert records[0]["original_entity_name"] == entity_name
        assert records[0]["identifier_type"] == "cusip"
        assert records[0]["identifier"] == cusip
        assert records[0]["permid_id"] == "4295904307"

    def test_cusip_output_contains_raw_ticker(self, tmp_path):
        """The ticker field in output is the raw ticker symbol, not the formatted form."""
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
                return_value=_record_match_hit(entity_name, cusip),
            ),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            PipelineOrchestrator(config).run()

        spec = IDENTIFIER_REGISTRY[IdentifierType.CUSIP]
        records = _read_output(tmp_path / "out" / "company_info" / spec.result_filename)
        assert records[0]["ticker"] == "AAPL"

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
        spec = IDENTIFIER_REGISTRY[IdentifierType.CUSIP]
        records = _read_output(tmp_path / "out" / "company_info" / spec.result_filename)
        assert records == []

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
                return_value=_record_match_hit(entity_name, cusip),
            ),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        spec = IDENTIFIER_REGISTRY[IdentifierType.CUSIP]
        records = _read_output(tmp_path / "out" / "company_info" / spec.result_filename)

        assert len(records) == 1
        assert records[0]["original_entity_name"] == entity_name
        assert records[0]["identifier"] == cusip

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
                return_value=_record_match_hit(entity_name, cusip),
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
                        "Input_LocalID": cusip,
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
        spec = IDENTIFIER_REGISTRY[IdentifierType.CUSIP]
        records = _read_output(tmp_path / "out" / "company_info" / spec.result_filename)
        assert records == []

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

        def record_match_side_effect(*args, **kwargs):
            return {
                "status_code": 200,
                "data": {
                    "outputContentResponse": [
                        {
                            "Input_Name": entity_name,
                            "Input_LocalID": cusip_a,
                            "Match OpenPermID": _PERMID_URL,
                            "Match Score": "100%",
                        },
                        {
                            "Input_Name": entity_name,
                            "Input_LocalID": cusip_b,
                            "Match OpenPermID": _PERMID_URL,
                            "Match Score": "100%",
                        },
                    ]
                },
            }

        with (
            patch.object(LsegRecordMatch, "query_endpoint", side_effect=record_match_side_effect),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        spec = IDENTIFIER_REGISTRY[IdentifierType.CUSIP]
        records = _read_output(tmp_path / "out" / "company_info" / spec.result_filename)

        assert len(records) == 2
        identifiers = {r["identifier"] for r in records}
        assert identifiers == {cusip_a, cusip_b}
