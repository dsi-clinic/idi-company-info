"""Integration tests for orchestrator.py.

These tests exercise the full pipeline for several input sources using real
temporary files and mocked API clients. They verify:
  - PipelineFactory composes the correct Input with the correct configuration
  - PipelineOrchestrator handles success, missing input, and failures correctly
  - Each input source produces the expected company info output file
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
from idi_company_info.company_pipeline import CompanyPipeline
from idi_company_info.factory import PipelineFactory
from idi_company_info.input import (
    CdtInput,
    ShareholderInputCik,
    ShareholderInputCusip,
    SubsidiaryInput,
)
from idi_company_info.orchestrator import PipelineOrchestrator
from idi_company_info.output import Output
from idi_company_info.types import InputSource, OrchestratorConfig

# Filenames the factory writes under output_dir/<input_type>/ — keep in sync
# with PipelineFactory.build.
_RESULT_FILENAME = "permid_data.json"
_PERMID_FILENAME = "permid_url.json"
_FAILURE_FILENAME = "failure.json"


def _paths_for(
    output_dir: pathlib.Path,
    input_type: InputSource,
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Build (result, permid, failure) paths from the output directory.

    Mirrors PipelineFactory.build: all three files live under a per-source
    subdirectory (the input-source slug) inside output_dir.
    """
    subdir = output_dir / str(input_type)
    return (
        subdir / _RESULT_FILENAME,
        subdir / _PERMID_FILENAME,
        subdir / _FAILURE_FILENAME,
    )


# ---------------------------------------------------------------------------
# Shared mock API responses
# ---------------------------------------------------------------------------

_PERMID_URL = "https://permid.org/1-4295904307"

_ENTITY_LOOKUP_HIT = {
    "status_code": 200,
    "data": {
        "vcard:organization-name": "Test Corp Inc.",
        "tr-common:hasPermId": "4295904307",
        "@id": _PERMID_URL,
        "hasActivityStatus": "Active",
    },
}

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
    input_type: InputSource,
    **kwargs,
) -> OrchestratorConfig:
    """Build a minimal OrchestratorConfig for testing."""
    defaults = {"batch_size": 10, "buffer_size": 5, "threshold_days": None}
    defaults.update(kwargs)
    return OrchestratorConfig(
        input_file=input_file,
        output_dir=output_dir,
        input_type=input_type,
        api_key="test-api-key",
        geonames_user="test-geonames-user",
        **defaults,
    )


def _read_output(output_file: pathlib.Path) -> dict:
    """Read the company info output file, returning {} if it does not exist."""
    return json.loads(output_file.read_text()) if output_file.exists() else {}


# ---------------------------------------------------------------------------
# TestPipelineFactory
# ---------------------------------------------------------------------------


class TestPipelineFactory:
    """Verify the factory composes the correct Input subclass and configuration."""

    def test_cik_source_composes_shareholder_cik_input(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame({"investor_name": ["Firm A"], "investor_cik": ["123"]}).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", InputSource.SHAREHOLDER_TRACKER_CIK)

        pipeline = PipelineFactory.build(config)

        assert isinstance(pipeline, CompanyPipeline)
        assert isinstance(pipeline.input_source, ShareholderInputCik)

    def test_cusip_source_composes_shareholder_cusip_input(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame(
            {
                "issuer_name": ["Corp A"],
                "security_cusip": ["037833100"],
                "stock_ticker": ["AAPL"],
            }
        ).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", InputSource.SHAREHOLDER_TRACKER_CUSIP)

        pipeline = PipelineFactory.build(config)

        assert isinstance(pipeline, CompanyPipeline)
        assert isinstance(pipeline.input_source, ShareholderInputCusip)

    def test_commercial_debt_source_composes_cdt_input(self, tmp_path):
        config = _make_config(tmp_path, tmp_path / "out", InputSource.COMMERCIAL_DEBT_TRACKER)

        pipeline = PipelineFactory.build(config)

        assert isinstance(pipeline.input_source, CdtInput)

    def test_corporate_subsidiaries_source_composes_subsidiary_input(self, tmp_path):
        config = _make_config(tmp_path, tmp_path / "out", InputSource.CORPORATE_SUBSIDIARIES)

        pipeline = PipelineFactory.build(config)

        assert isinstance(pipeline.input_source, SubsidiaryInput)

    def test_file_paths_derived_from_output_dir(self, tmp_path):
        out = tmp_path / "output"
        parquet = tmp_path / "data.parquet"
        pd.DataFrame({"investor_name": ["Firm A"], "investor_cik": ["123"]}).to_parquet(parquet)
        config = _make_config(parquet, out, InputSource.SHAREHOLDER_TRACKER_CIK)
        expected_result, expected_permid, expected_failure = _paths_for(
            out, InputSource.SHAREHOLDER_TRACKER_CIK
        )

        pipeline = PipelineFactory.build(config)

        assert pipeline.file_paths.result_file == str(expected_result)
        assert pipeline.file_paths.permid_file == str(expected_permid)
        assert pipeline.file_paths.failure_file == str(expected_failure)

    def test_batch_config_passed_through(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame({"investor_name": ["Firm A"], "investor_cik": ["123"]}).to_parquet(parquet)
        config = _make_config(
            parquet,
            tmp_path / "out",
            InputSource.SHAREHOLDER_TRACKER_CIK,
            batch_size=42,
            buffer_size=7,
        )

        pipeline = PipelineFactory.build(config)

        assert pipeline.batch_config.batch_size == 42
        assert pipeline.batch_config.buffer_size == 7

    def test_each_source_has_distinct_output_filenames(self, tmp_path):
        result_files = {s: _paths_for(tmp_path, s)[0] for s in InputSource}
        assert len(set(result_files.values())) == len(InputSource), (
            "Each input source must write to a distinct result file"
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
            input_type=InputSource.SHAREHOLDER_TRACKER_CIK,
        )

        result = PipelineOrchestrator(config).run()

        assert result is False

    def test_returns_true_when_pipeline_run_succeeds(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame({"investor_name": ["A"], "investor_cik": ["1"]}).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", InputSource.SHAREHOLDER_TRACKER_CIK)

        with patch("idi_company_info.orchestrator.PipelineFactory.build") as mock_build:
            mock_build.return_value.run.return_value = None

            result = PipelineOrchestrator(config).run()

        assert result is True
        mock_build.return_value.run.assert_called_once()

    def test_returns_false_when_pipeline_run_raises(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame({"investor_name": ["A"], "investor_cik": ["1"]}).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", InputSource.SHAREHOLDER_TRACKER_CIK)

        with patch("idi_company_info.orchestrator.PipelineFactory.build") as mock_build:
            mock_build.return_value.run.side_effect = RuntimeError("simulated API failure")

            result = PipelineOrchestrator(config).run()

        assert result is False

    def test_returns_false_on_keyboard_interrupt(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame({"investor_name": ["A"], "investor_cik": ["1"]}).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", InputSource.SHAREHOLDER_TRACKER_CIK)

        with patch("idi_company_info.orchestrator.PipelineFactory.build") as mock_build:
            mock_build.return_value.run.side_effect = KeyboardInterrupt

            result = PipelineOrchestrator(config).run()

        assert result is False


# ---------------------------------------------------------------------------
# TestCikPipelineIntegration
# ---------------------------------------------------------------------------


class TestCikPipelineIntegration:
    """End-to-end integration tests for the shareholder CIK (Record Match) pipeline."""

    _SOURCE = InputSource.SHAREHOLDER_TRACKER_CIK

    def test_writes_company_info_for_matched_cik(self, tmp_path):
        entity_name = "Firm Alpha"
        cik = "0001234567"
        parquet = tmp_path / "data.parquet"
        pd.DataFrame({"investor_name": [entity_name], "investor_cik": [cik]}).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", self._SOURCE)

        with (
            patch.object(
                LsegRecordMatch, "query_endpoint", return_value=_record_match_hit(entity_name, cik)
            ),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        result_file, permid_file, _ = _paths_for(tmp_path / "out", self._SOURCE)
        records = _read_output(result_file)

        assert len(records) == 1
        assert _PERMID_URL in records
        assert records[_PERMID_URL]["permid_id"] == "4295904307"

        permid = _read_output(permid_file)
        assert permid[f"{entity_name}_cik_{cik}"]["result"] == [_PERMID_URL]

    def test_aggregation_failure_does_not_fail_the_run(self, tmp_path):
        """A failing final-output aggregation is logged but the run still succeeds.

        The scraped caches are durable once pipeline.run() returns, so aggregation errors
        must not flip the exit code (which would trigger an ECS retry).
        """
        entity_name = "Firm Alpha"
        cik = "0001234567"
        parquet = tmp_path / "data.parquet"
        pd.DataFrame({"investor_name": [entity_name], "investor_cik": [cik]}).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", self._SOURCE)

        with (
            patch.object(
                LsegRecordMatch, "query_endpoint", return_value=_record_match_hit(entity_name, cik)
            ),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
            patch.object(Output, "aggregate", side_effect=RuntimeError("boom")),
        ):
            result = PipelineOrchestrator(config).run()

        # Run reports success despite the aggregation failure, and caches are written.
        assert result is True
        result_file, _, _ = _paths_for(tmp_path / "out", self._SOURCE)
        assert _PERMID_URL in _read_output(result_file)

    def test_produces_empty_output_when_no_permid_match(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame(
            {"investor_name": ["Unknown Corp"], "investor_cik": ["0009999999"]}
        ).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", self._SOURCE)

        with (
            patch.object(LsegRecordMatch, "query_endpoint", return_value=_RECORD_MATCH_MISS),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        result_file, _, _ = _paths_for(tmp_path / "out", self._SOURCE)
        assert _read_output(result_file) == {}

    def test_creates_separate_output_from_cusip_pipeline(self, tmp_path):
        """CIK output files are distinct from CUSIP output files."""
        cik_result, _, _ = _paths_for(tmp_path, InputSource.SHAREHOLDER_TRACKER_CIK)
        cusip_result, _, _ = _paths_for(tmp_path, InputSource.SHAREHOLDER_TRACKER_CUSIP)
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
        config = _make_config(parquet, tmp_path / "out", self._SOURCE)

        with (
            patch.object(
                LsegRecordMatch, "query_endpoint", return_value=_record_match_hit(entity_name, cik)
            ),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        result_file, permid_file, _ = _paths_for(tmp_path / "out", self._SOURCE)
        records = _read_output(result_file)

        assert len(records) == 1
        assert _PERMID_URL in records
        permid = _read_output(permid_file)
        assert permid[f"{entity_name}_cik_{cik}"]["result"] == [_PERMID_URL]


# ---------------------------------------------------------------------------
# TestCommercialDebtPipelineIntegration
# ---------------------------------------------------------------------------


class TestCommercialDebtPipelineIntegration:
    """End-to-end integration for the CDT pipeline (single-file parquet input)."""

    _SOURCE = InputSource.COMMERCIAL_DEBT_TRACKER

    def test_writes_company_info_for_matched_cik(self, tmp_path):
        entity_name = "Debt Co"
        raw_cik = "1234567"
        padded_cik = "0001234567"  # CdtInput zero-pads to 10 digits

        # CDT input is a single parquet file.
        input_file = tmp_path / "debt_instruments.parquet"
        pd.DataFrame({"company_name": [entity_name], "cik": [raw_cik]}).to_parquet(input_file)
        config = _make_config(input_file, tmp_path / "out", self._SOURCE)

        with (
            patch.object(
                LsegRecordMatch,
                "query_endpoint",
                return_value=_record_match_hit(entity_name, padded_cik),
            ),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        result_file, permid_file, _ = _paths_for(tmp_path / "out", self._SOURCE)
        records = _read_output(result_file)

        assert len(records) == 1
        assert _PERMID_URL in records
        permid = _read_output(permid_file)
        assert permid[f"{entity_name}_cik_{padded_cik}"]["result"] == [_PERMID_URL]


# ---------------------------------------------------------------------------
# TestCusipPipelineIntegration
# ---------------------------------------------------------------------------


class TestCusipPipelineIntegration:
    """End-to-end integration tests for the shareholder CUSIP (Record Match) pipeline.

    The CUSIP pipeline uses CUSIP as the LocalID (stable identifier) and the
    formatted ticker as the Standard Identifier in Record Match API calls.
    """

    _SOURCE = InputSource.SHAREHOLDER_TRACKER_CUSIP

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
        config = _make_config(parquet, tmp_path / "out", self._SOURCE)

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
        result_file, permid_file, _ = _paths_for(tmp_path / "out", self._SOURCE)
        records = _read_output(result_file)

        assert len(records) == 1
        assert _PERMID_URL in records
        assert records[_PERMID_URL]["permid_id"] == "4295904307"
        permid = _read_output(permid_file)
        assert permid[f"{entity_name}_cusip_{cusip}"]["result"] == [_PERMID_URL]

    def test_result_file_is_pure_company_info(self, tmp_path):
        """result_file entries are flat company info — no search/result envelope.

        The input ``stock_ticker`` must not leak in: ``ticker`` is a company-info field
        sourced from the entity's primary quote (None here, as the mock has no quote link).
        """
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
        config = _make_config(parquet, tmp_path / "out", self._SOURCE)

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

        result_file, _, _ = _paths_for(tmp_path / "out", self._SOURCE)
        records = _read_output(result_file)
        record = list(records.values())[0]
        assert "search" not in record
        assert "result" not in record
        assert "permid_id" in record
        assert "last_processed" in record
        assert "identifiers" not in record
        # ticker is a company-info field, not the input stock_ticker that resolved the entity.
        assert record.get("ticker") != "AAPL"
        assert record["ticker"] is None

    def test_produces_empty_output_when_no_permid_match(self, tmp_path):
        parquet = tmp_path / "data.parquet"
        pd.DataFrame(
            {
                "issuer_name": ["Unknown Corp"],
                "security_cusip": ["000000000"],
                "stock_ticker": ["XYZ"],
            }
        ).to_parquet(parquet)
        config = _make_config(parquet, tmp_path / "out", self._SOURCE)

        with (
            patch.object(LsegRecordMatch, "query_endpoint", return_value=_RECORD_MATCH_MISS),
            patch.object(LSEGEntityLookup, "query_endpoint", return_value=_ENTITY_LOOKUP_HIT),
            patch.object(GeonamesApi, "query_endpoint", return_value=_GEONAMES_MISS),
        ):
            result = PipelineOrchestrator(config).run()

        assert result is True
        result_file, _, _ = _paths_for(tmp_path / "out", self._SOURCE)
        assert _read_output(result_file) == {}

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
        config = _make_config(parquet, tmp_path / "out", self._SOURCE)

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
        config = _make_config(parquet, tmp_path / "out", self._SOURCE)

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
        result_file, permid_file, _ = _paths_for(tmp_path / "out", self._SOURCE)
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
        config = _make_config(parquet, tmp_path / "out", self._SOURCE)

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
        config = _make_config(parquet, tmp_path / "out", self._SOURCE, match_score_threshold=1)

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
        result_file, _, _ = _paths_for(tmp_path / "out", self._SOURCE)
        assert _read_output(result_file) == {}

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
        config = _make_config(parquet, tmp_path / "out", self._SOURCE)

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
        result_file, permid_file, _ = _paths_for(tmp_path / "out", self._SOURCE)
        records = _read_output(result_file)

        assert set(records.keys()) == {_PERMID_URL_A, _PERMID_URL_B}
        permid = _read_output(permid_file)
        assert permid[f"{entity_name}_cusip_{cusip_a}"]["result"] == [_PERMID_URL_A]
        assert permid[f"{entity_name}_cusip_{cusip_b}"]["result"] == [_PERMID_URL_B]
