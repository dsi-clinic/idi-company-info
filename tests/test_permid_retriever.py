#!/usr/bin/env python3
"""Unit tests for idi_company_info.processors.retrieval_permid."""

from unittest.mock import MagicMock

import pytest

from idi_company_info.retrieval_permid import PermidRetrieval
from idi_company_info.types import BatchStats


def make_retriever(
    match_score_threshold: float = 1,
    identifier_type: str = "cik",
    std_ticker_map: dict | None = None,
) -> PermidRetrieval:
    """Create a PermidRetrieval with mocked dependencies."""
    return PermidRetrieval(
        file_paths=MagicMock(),
        batch_config=MagicMock(),
        api_clients=MagicMock(),
        identifier_type=identifier_type,
        match_score_threshold=match_score_threshold,
        std_ticker_map=std_ticker_map,
    )


class TestParseScore:
    """Tests for PermidRetrieval._parse_score (static method)."""

    def test_parses_percentage_string(self):
        """A percentage string like '100%' returns 1.0."""
        assert PermidRetrieval._parse_score({"Match Score": "100%"}) == pytest.approx(1.0)

    def test_parses_partial_percentage(self):
        """'75%' returns 0.75."""
        assert PermidRetrieval._parse_score({"Match Score": "75%"}) == pytest.approx(0.75)

    def test_returns_zero_when_no_score(self):
        """Missing Match Score returns 0."""
        assert PermidRetrieval._parse_score({}) == 0

    def test_returns_zero_for_none_score(self):
        """None Match Score returns 0."""
        assert PermidRetrieval._parse_score({"Match Score": None}) == 0


class TestParseRecordMatchResponse:
    """Tests for PermidRetrieval._parse_record_match_response.

    The new shape is a flat dict keyed by permid_cache_key(name, local_id):
    ``{f"{name}_{local_id}": {"search": {...}, "result": [permid_url, ...]}}``.
    """

    def test_maps_name_to_identifier_and_permid(self):
        """Response is keyed by cache key; result is a list of permid URLs."""
        retriever = make_retriever()
        response = [
            {
                "Input_Name": "Corp A",
                "Input_LocalID": "cik_037833100",
                "Match OpenPermID": "https://permid.org/1-4297529501",
            }
        ]
        result = retriever._parse_record_match_response(response)
        key = "Corp A_cik_037833100"
        assert key in result
        assert result[key]["result"] == ["https://permid.org/1-4297529501"]
        assert result[key]["search"]["Name"] == "Corp A"
        assert result[key]["search"]["LocalID"] == "cik_037833100"

    def test_multiple_records_for_same_entity_dedupes_result_urls(self):
        """Duplicate permid URLs for the same key are not added twice."""
        retriever = make_retriever()
        response = [
            {"Input_Name": "Corp A", "Input_LocalID": "cik_ID1", "Match OpenPermID": "permid_1"},
            {"Input_Name": "Corp A", "Input_LocalID": "cik_ID1", "Match OpenPermID": "permid_1"},
        ]
        result = retriever._parse_record_match_response(response)
        assert result["Corp A_cik_ID1"]["result"] == ["permid_1"]

    def test_multiple_different_entities_produce_separate_keys(self):
        """Different entity names produce separate cache-key entries."""
        retriever = make_retriever()
        response = [
            {"Input_Name": "Corp A", "Input_LocalID": "cik_A1", "Match OpenPermID": "p1"},
            {"Input_Name": "Corp B", "Input_LocalID": "cik_B1", "Match OpenPermID": "p2"},
        ]
        result = retriever._parse_record_match_response(response)
        assert "Corp A_cik_A1" in result
        assert "Corp B_cik_B1" in result

    def test_empty_response_returns_empty_dict(self):
        """Empty response returns empty dict."""
        retriever = make_retriever()
        assert retriever._parse_record_match_response([]) == {}


class TestRecordMatchCallCount:
    """Each Record Match HTTP call is counted against the shared PermID daily quota."""

    def test_call_is_counted_once_per_batch(self):
        retriever = make_retriever()
        retriever.api_clients.record_match.query_endpoint.return_value = {
            "status_code": 200,
            "data": {"outputContentResponse": []},
        }
        records = [{"Name": "Corp A", "Standard Identifier": "Cik:1", "LocalID": "cik_1"}]
        stats = BatchStats()

        retriever._retrieve_record_match(records, stats)

        assert stats.total_record_match_calls == 1
        assert retriever.api_clients.record_match.query_endpoint.call_count == 1

    def test_failed_call_is_still_counted(self):
        retriever = make_retriever()
        retriever.api_clients.record_match.query_endpoint.return_value = {"status_code": 500}
        records = [{"Name": "Corp A", "Standard Identifier": "Cik:1", "LocalID": "cik_1"}]
        stats = BatchStats()

        retriever._retrieve_record_match(records, stats)

        # The request hit the API (and the quota) even though it failed.
        assert stats.total_record_match_calls == 1


class TestBuildRecords:
    """Tests for PermidRetrieval._build_records.

    _build_records now takes a flat list of (entity_name, identifier) tuples
    and prefixes LocalID with the identifier type (e.g. 'cik_0001234567').
    """

    def test_builds_records_for_cik_identifier(self):
        """CIK LocalID is prefixed; Standard Identifier uses Cik: prefix."""
        retriever = make_retriever(identifier_type="cik")
        batch_entities = [("Investor B", "0001234567")]
        records = retriever._build_records(batch_entities)
        assert len(records) == 1
        assert records[0]["LocalID"] == "cik_0001234567"
        assert records[0]["Standard Identifier"] == "Cik:0001234567"
        assert records[0]["Name"] == "Investor B"

    def test_builds_records_for_cusip_identifier(self):
        """CUSIP LocalID is prefixed; Standard Identifier from std_ticker_map."""
        std_ticker_map = {"037833100": "ticker:AAPL"}
        retriever = make_retriever(identifier_type="cusip", std_ticker_map=std_ticker_map)
        batch_entities = [("Corp A", "037833100")]
        records = retriever._build_records(batch_entities)
        assert len(records) == 1
        assert records[0]["LocalID"] == "cusip_037833100"
        assert records[0]["Standard Identifier"] == "ticker:AAPL"
        assert records[0]["Name"] == "Corp A"

    def test_builds_records_for_cusip_with_mic(self):
        """CUSIP with exchange-qualified ticker formats correctly."""
        std_ticker_map = {"037833100": "ticker:ACTI&&mic:XSTO"}
        retriever = make_retriever(identifier_type="cusip", std_ticker_map=std_ticker_map)
        records = retriever._build_records([("Corp A", "037833100")])
        assert records[0]["Standard Identifier"] == "ticker:ACTI&&mic:XSTO"

    def test_cusip_missing_from_std_ticker_map_raises_key_error(self):
        """A CUSIP absent from std_ticker_map raises KeyError."""
        retriever = make_retriever(identifier_type="cusip", std_ticker_map={})
        with pytest.raises(KeyError):
            retriever._build_records([("Corp A", "037833100")])

    def test_raises_for_unknown_identifier_type(self):
        """Unknown identifier_type raises ValueError."""
        retriever = make_retriever(identifier_type="unknown")
        with pytest.raises(ValueError, match="Invalid identifier type"):
            retriever._build_records([("Corp A", "X")])

    def test_multiple_flat_tuples_produce_one_record_each(self):
        """Multiple (entity, identifier) tuples each produce one record."""
        retriever = make_retriever(identifier_type="cik")
        batch_entities = [("Corp A", "0001234567"), ("Corp A", "0009876543")]
        records = retriever._build_records(batch_entities)
        assert len(records) == 2
        ids = {r["LocalID"] for r in records}
        assert ids == {"cik_0001234567", "cik_0009876543"}

    def test_multiple_entities(self):
        """Multiple entities each produce their own records."""
        retriever = make_retriever(identifier_type="cik")
        batch_entities = [
            ("Firm A", "0001234567"),
            ("Firm B", "0009876543"),
        ]
        records = retriever._build_records(batch_entities)
        assert len(records) == 2
        names = {r["Name"] for r in records}
        assert names == {"Firm A", "Firm B"}
