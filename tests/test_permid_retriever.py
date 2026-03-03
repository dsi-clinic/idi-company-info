#!/usr/bin/env python3
"""
Unit tests for idi_company_info.processors.permid_retriever
"""

from unittest.mock import MagicMock

import pytest

from idi_company_info.processors.permid_retriever import RecordMatchRetriever


def make_retriever(match_score_threshold=1, identifier_type="ticker"):
    """Create a RecordMatchRetriever with a mocked context."""
    context = MagicMock()
    context.identifier_type = identifier_type
    retriever = RecordMatchRetriever(context=context, match_score_threshold=match_score_threshold)
    return retriever


class TestParseScore:
    """Tests for RecordMatchRetriever._parse_score (static method)."""

    def test_parses_percentage_string(self):
        """A percentage string like '100%' returns 1.0."""
        assert RecordMatchRetriever._parse_score({"Match Score": "100%"}) == pytest.approx(1.0)

    def test_parses_partial_percentage(self):
        """'75%' returns 0.75."""
        assert RecordMatchRetriever._parse_score({"Match Score": "75%"}) == pytest.approx(0.75)

    def test_returns_zero_when_no_score(self):
        """Missing Match Score returns 0."""
        assert RecordMatchRetriever._parse_score({}) == 0

    def test_returns_zero_for_none_score(self):
        """None Match Score returns 0."""
        assert RecordMatchRetriever._parse_score({"Match Score": None}) == 0


class TestFilterRecordMatchResponse:
    """Tests for RecordMatchRetriever._filter_record_match_response."""

    def test_keeps_records_at_or_above_threshold(self):
        """Records with score >= threshold are kept."""
        retriever = make_retriever(match_score_threshold=1)
        response = [
            {"Match Score": "100%", "Input_Name": "Corp A"},
            {"Match Score": "50%", "Input_Name": "Corp B"},
        ]
        result = retriever._filter_record_match_response(response)
        assert len(result) == 1
        assert result[0]["Input_Name"] == "Corp A"

    def test_filters_out_records_below_threshold(self):
        """Records below threshold are excluded."""
        retriever = make_retriever(match_score_threshold=1)
        response = [{"Match Score": "90%", "Input_Name": "Corp A"}]
        result = retriever._filter_record_match_response(response)
        assert result == []

    def test_all_pass_when_threshold_is_zero(self):
        """With threshold=0, all records pass."""
        retriever = make_retriever(match_score_threshold=0)
        response = [
            {"Match Score": "0%", "Input_Name": "Corp A"},
            {"Match Score": "100%", "Input_Name": "Corp B"},
        ]
        result = retriever._filter_record_match_response(response)
        assert len(result) == 2

    def test_empty_response_returns_empty(self):
        """Empty response returns empty list."""
        retriever = make_retriever()
        assert retriever._filter_record_match_response([]) == []


class TestParseRecordMatchResponse:
    """Tests for RecordMatchRetriever._parse_record_match_response."""

    def test_maps_name_to_identifier_and_permid(self):
        """Test that response maps Input_Name -> [{Input_LocalID: [permid]}]."""
        retriever = make_retriever()
        response = [
            {
                "Input_Name": "Corp A",
                "Input_LocalID": "AAPL",
                "Match OpenPermID": "https://permid.org/1-4297529501",
            }
        ]
        result = retriever._parse_record_match_response(response)
        assert "Corp A" in result
        assert result["Corp A"] == [{"AAPL": ["https://permid.org/1-4297529501"]}]

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
    """Tests for RecordMatchRetriever._build_records."""

    def test_builds_records_for_ticker_identifier(self):
        """Ticker identifiers are used as-is in Standard Identifier."""
        retriever = make_retriever(identifier_type="ticker")
        batch_entities = [("Corp A", ["ticker:AAPL"])]
        records = retriever._build_records(batch_entities)
        assert len(records) == 1
        assert records[0]["LocalID"] == "ticker:AAPL"
        assert records[0]["Standard Identifier"] == "ticker:AAPL"
        assert records[0]["Name"] == "Corp A"

    def test_builds_records_for_cik_identifier(self):
        """CIK identifiers are prefixed with 'Cik:' in Standard Identifier."""
        retriever = make_retriever(identifier_type="cik")
        batch_entities = [("Investor B", ["0001234567"])]
        records = retriever._build_records(batch_entities)
        assert records[0]["LocalID"] == "0001234567"
        assert records[0]["Standard Identifier"] == "Cik:0001234567"
        assert records[0]["Name"] == "Investor B"

    def test_raises_for_unknown_identifier_type(self):
        """Unknown identifier_type raises ValueError."""
        retriever = make_retriever(identifier_type="unknown")
        with pytest.raises(ValueError, match="Invalid identifier type"):
            retriever._build_records([("Corp A", ["X"])])

    def test_multiple_identifiers_per_entity(self):
        """Multiple identifiers for one entity produce one record each."""
        retriever = make_retriever(identifier_type="ticker")
        batch_entities = [("Corp A", ["ticker:AAPL", "ticker:MSFT"])]
        records = retriever._build_records(batch_entities)
        assert len(records) == 2
        ids = {r["LocalID"] for r in records}
        assert ids == {"ticker:AAPL", "ticker:MSFT"}

    def test_multiple_entities(self):
        """Multiple entities each produce their own records."""
        retriever = make_retriever(identifier_type="ticker")
        batch_entities = [
            ("Corp A", ["ticker:AAPL"]),
            ("Corp B", ["ticker:GOOG"]),
        ]
        records = retriever._build_records(batch_entities)
        assert len(records) == 2
        names = {r["Name"] for r in records}
        assert names == {"Corp A", "Corp B"}
