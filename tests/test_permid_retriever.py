#!/usr/bin/env python3
"""Unit tests for idi_company_info.processors.retrieval_permid."""

from unittest.mock import MagicMock

import pytest

from idi_company_info.retrieval_permid import PermidRetrieval


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
    """Tests for PermidRetrieval._parse_record_match_response."""

    def test_maps_name_to_identifier_and_permid(self):
        """Test that response maps Input_Name -> [{Input_LocalID: [permid]}]."""
        retriever = make_retriever()
        response = [
            {
                "Input_Name": "Corp A",
                "Input_LocalID": "037833100",
                "Match OpenPermID": "https://permid.org/1-4297529501",
            }
        ]
        result = retriever._parse_record_match_response(response)
        assert "Corp A" in result
        assert result["Corp A"] == [{"037833100": ["https://permid.org/1-4297529501"]}]

    def test_multiple_records_for_same_entity_accumulates_all(self):
        """Multiple records for the same entity are all preserved."""
        retriever = make_retriever()
        response = [
            {"Input_Name": "Corp A", "Input_LocalID": "ID1", "Match OpenPermID": "permid_1"},
            {"Input_Name": "Corp A", "Input_LocalID": "ID2", "Match OpenPermID": "permid_2"},
        ]
        result = retriever._parse_record_match_response(response)
        assert len(result["Corp A"]) == 2
        assert {"ID1": ["permid_1"]} in result["Corp A"]
        assert {"ID2": ["permid_2"]} in result["Corp A"]

    def test_multiple_different_entities(self):
        """Different entity names produce separate keys."""
        retriever = make_retriever()
        response = [
            {"Input_Name": "Corp A", "Input_LocalID": "A1", "Match OpenPermID": "p1"},
            {"Input_Name": "Corp B", "Input_LocalID": "B1", "Match OpenPermID": "p2"},
        ]
        result = retriever._parse_record_match_response(response)
        assert set(result.keys()) == {"Corp A", "Corp B"}

    def test_empty_response_returns_empty_dict(self):
        """Empty response returns empty dict."""
        retriever = make_retriever()
        assert retriever._parse_record_match_response([]) == {}


class TestBuildRecords:
    """Tests for PermidRetrieval._build_records."""

    def test_builds_records_for_cik_identifier(self):
        """CIK identifiers are prefixed with 'Cik:' in Standard Identifier."""
        retriever = make_retriever(identifier_type="cik")
        batch_entities = [("Investor B", ["0001234567"])]
        records = retriever._build_records(batch_entities)
        assert len(records) == 1
        assert records[0]["LocalID"] == "0001234567"
        assert records[0]["Standard Identifier"] == "Cik:0001234567"
        assert records[0]["Name"] == "Investor B"

    def test_builds_records_for_cusip_identifier(self):
        """CUSIP is used as LocalID; formatted ticker from std_ticker_map is Standard Identifier."""
        std_ticker_map = {"037833100": "ticker:AAPL"}
        retriever = make_retriever(identifier_type="cusip", std_ticker_map=std_ticker_map)
        batch_entities = [("Corp A", ["037833100"])]
        records = retriever._build_records(batch_entities)
        assert len(records) == 1
        assert records[0]["LocalID"] == "037833100"
        assert records[0]["Standard Identifier"] == "ticker:AAPL"
        assert records[0]["Name"] == "Corp A"

    def test_builds_records_for_cusip_with_mic(self):
        """CUSIP with exchange-qualified ticker formats correctly."""
        std_ticker_map = {"037833100": "ticker:ACTI&&mic:XSTO"}
        retriever = make_retriever(identifier_type="cusip", std_ticker_map=std_ticker_map)
        records = retriever._build_records([("Corp A", ["037833100"])])
        assert records[0]["Standard Identifier"] == "ticker:ACTI&&mic:XSTO"

    def test_cusip_missing_from_std_ticker_map_raises_key_error(self):
        """A CUSIP absent from std_ticker_map raises KeyError."""
        retriever = make_retriever(identifier_type="cusip", std_ticker_map={})
        with pytest.raises(KeyError):
            retriever._build_records([("Corp A", ["037833100"])])

    def test_raises_for_unknown_identifier_type(self):
        """Unknown identifier_type raises ValueError."""
        retriever = make_retriever(identifier_type="unknown")
        with pytest.raises(ValueError, match="Invalid identifier type"):
            retriever._build_records([("Corp A", ["X"])])

    def test_multiple_identifiers_per_entity(self):
        """Multiple identifiers for one entity produce one record each."""
        retriever = make_retriever(identifier_type="cik")
        batch_entities = [("Corp A", ["0001234567", "0009876543"])]
        records = retriever._build_records(batch_entities)
        assert len(records) == 2
        ids = {r["LocalID"] for r in records}
        assert ids == {"0001234567", "0009876543"}

    def test_multiple_entities(self):
        """Multiple entities each produce their own records."""
        retriever = make_retriever(identifier_type="cik")
        batch_entities = [
            ("Firm A", ["0001234567"]),
            ("Firm B", ["0009876543"]),
        ]
        records = retriever._build_records(batch_entities)
        assert len(records) == 2
        names = {r["Name"] for r in records}
        assert names == {"Firm A", "Firm B"}
