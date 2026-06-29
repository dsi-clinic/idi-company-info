#!/usr/bin/env python3
"""Unit tests for idi_company_info.retrieval_company_info metadata enrichment."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from idi_company_info.retrieval_company_info import CompInfoRetrieval
from idi_company_info.types import BatchConfig, BatchStats, FilePaths

# Linked permid URLs referenced (bare keys) in the entity-lookup response.
_BUSINESS_SECTOR_URL = "https://permid.org/1-business"
_ECONOMIC_SECTOR_URL = "https://permid.org/1-economic"
_INDUSTRY_GROUP_URL = "https://permid.org/1-industry"
_QUOTE_URL = "https://permid.org/1-quote"

_PERMID_URL = "https://permid.org/1-4295904307"

# Entity response carrying the four linked URLs (bare org-level keys per JSON-LD context).
_ENTITY_DATA = {
    "vcard:organization-name": "Test Corp Inc.",
    "tr-common:hasPermId": "4295904307",
    "@id": _PERMID_URL,
    "hasActivityStatus": "Active",
    "hasPrimaryBusinessSector": _BUSINESS_SECTOR_URL,
    "hasPrimaryEconomicSector": _ECONOMIC_SECTOR_URL,
    "hasPrimaryIndustryGroup": _INDUSTRY_GROUP_URL,
    "hasOrganizationPrimaryQuote": _QUOTE_URL,
}

# Linked records returned by the follow-up calls, keyed by the URL queried. Sector
# records expose a bare ``prefLabel`` (with ``rdfs:comment``); the quote record carries
# ticker and exchange identifiers inline (no further lookup) — modelled on a real
# tr-fin:Quote (e.g. AAL on LSE: ticker "AAL", code "LSE", MIC "XLON", RIC "AAL.L").
_LINKED_RECORDS = {
    _BUSINESS_SECTOR_URL: {"prefLabel": "Technology", "rdfs:comment": "Tech sector"},
    _ECONOMIC_SECTOR_URL: {"prefLabel": "Information Technology"},
    _INDUSTRY_GROUP_URL: {"prefLabel": "Software & IT Services"},
    _QUOTE_URL: {
        "tr-fin:hasExchangeTicker": "TEST",
        "tr-fin:hasRic": "TEST.O",
        "tr-fin:hasExchangeCode": "NSM",
        "tr-fin:hasMic": "XNGS",
    },
}


def _make_retriever(enrich_metadata: bool = True) -> CompInfoRetrieval:
    """Build a CompInfoRetrieval whose entity_lookup resolves the linked records above."""
    api_clients = MagicMock()

    def fake_query(permid_url: str) -> dict:
        data = _LINKED_RECORDS.get(permid_url)
        return {"status_code": 200, "data": data} if data else {"status_code": 404}

    api_clients.entity_lookup.query_endpoint.side_effect = fake_query

    # Pin sector_cache_file to "" so the SectorCache is deterministically in-memory only —
    # a bare MagicMock attribute would be truthy and make load()/flush() hit a Mock path.
    file_paths = MagicMock()
    file_paths.sector_cache_file = ""
    return CompInfoRetrieval(
        file_paths=file_paths,
        batch_config=BatchConfig(enrich_metadata=enrich_metadata),
        api_clients=api_clients,
        identifier_type="cik",
    )


class TestParseCompanyInfoEnrichment:
    """_parse_company_info resolves sector and quote links when enrichment is on."""

    def test_populates_all_enrichment_fields(self):
        retriever = _make_retriever(enrich_metadata=True)
        stats = BatchStats()

        entry = retriever._parse_company_info(_PERMID_URL, _ENTITY_DATA, stats)

        assert entry["primary_business_sector_label"] == "Technology"
        assert entry["primary_economic_sector_label"] == "Information Technology"
        assert entry["primary_industry_group_label"] == "Software & IT Services"
        assert entry["primary_business_sector_comment"] == "Tech sector"
        assert entry["primary_economic_sector_comment"] is None
        # exchange is the MIC; exchange_code and ric come from their own quote fields.
        assert entry["ticker"] == "TEST"
        assert entry["exchange"] == "XNGS"
        assert entry["exchange_code"] == "NSM"
        assert entry["ric"] == "TEST.O"

    def test_counts_follow_up_calls(self):
        retriever = _make_retriever(enrich_metadata=True)
        stats = BatchStats()

        retriever._parse_company_info(_PERMID_URL, _ENTITY_DATA, stats)

        # 3 sectors + 1 quote = 4 follow-up calls (exchange is inline in the quote).
        assert stats.total_follow_up_calls == 4

    def test_disabled_makes_no_calls_and_leaves_fields_none(self):
        retriever = _make_retriever(enrich_metadata=False)
        stats = BatchStats()

        entry = retriever._parse_company_info(_PERMID_URL, _ENTITY_DATA, stats)

        assert stats.total_follow_up_calls == 0
        retriever.api_clients.entity_lookup.query_endpoint.assert_not_called()
        for field in (
            "primary_business_sector_label",
            "primary_economic_sector_label",
            "primary_industry_group_label",
            "primary_business_sector_comment",
            "primary_economic_sector_comment",
            "primary_industry_group_comment",
            "ticker",
            "exchange",
            "exchange_code",
            "ric",
        ):
            assert entry[field] is None

    def test_missing_links_leave_fields_none_without_calls(self):
        retriever = _make_retriever(enrich_metadata=True)
        stats = BatchStats()

        entry = retriever._parse_company_info(
            _PERMID_URL,
            {"@id": _PERMID_URL, "vcard:organization-name": "No Links Inc."},
            stats,
        )

        assert stats.total_follow_up_calls == 0
        assert entry["primary_business_sector_label"] is None
        assert entry["ticker"] is None
        assert entry["exchange"] is None

    def test_failed_follow_up_degrades_to_none(self):
        retriever = _make_retriever(enrich_metadata=True)
        stats = BatchStats()

        entry = retriever._parse_company_info(
            _PERMID_URL,
            {"@id": _PERMID_URL, "hasPrimaryBusinessSector": "https://permid.org/1-missing"},
            stats,
        )

        # The call was attempted (and counted) but returned a non-200.
        assert stats.total_follow_up_calls == 1
        assert entry["primary_business_sector_label"] is None


class TestScalarText:
    """_scalar_text flattens a list-valued JSON-LD label/comment to a single string.

    PermID intermittently returns ``prefLabel`` as a list of spelling variants; a raw list
    reaching ``to_parquet`` raises ``ArrowTypeError: Expected bytes, got a 'list'``.
    """

    def test_passes_through_plain_string(self):
        assert (
            _make_retriever()._scalar_text("Software & IT Services", None)
            == "Software & IT Services"
        )

    def test_returns_first_item_of_list(self):
        assert _make_retriever()._scalar_text(["Software & IT Services", "Other"], None) == (
            "Software & IT Services"
        )

    def test_none_and_empty_collapse_to_none(self):
        retriever = _make_retriever()
        assert retriever._scalar_text(None, None) is None
        assert retriever._scalar_text([], None) is None
        assert retriever._scalar_text("", None) is None

    def test_string_value_is_not_logged(self, caplog):
        retriever = _make_retriever()
        with caplog.at_level("WARNING"):
            retriever._scalar_text("Software & IT Services", "https://permid.org/1-x")
        assert caplog.text == ""

    def test_list_value_logs_url_and_full_list(self, caplog):
        retriever = _make_retriever()
        url = "https://permid.org/1-4294952757"
        with caplog.at_level("WARNING"):
            result = retriever._scalar_text(
                ["Freight&Logistics Services", "Freight & Logistics Services"], url
            )
        assert result == "Freight&Logistics Services"
        assert "Multi-value label" in caplog.text
        assert url in caplog.text
        # The full list is logged (both variants), not the collapsed scalar's characters.
        assert "Freight&Logistics Services, Freight & Logistics Services" in caplog.text

    def test_empty_list_logs_without_crashing(self, caplog):
        retriever = _make_retriever()
        with caplog.at_level("WARNING"):
            assert retriever._scalar_text([], "https://permid.org/1-x") is None

    def test_single_item_list_collapses_to_scalar_without_warning(self, caplog):
        retriever = _make_retriever()
        with caplog.at_level("WARNING"):
            # A single-element list must still be flattened to its scalar (it would
            # otherwise reach to_parquet as a list), but it is not worth a warning.
            result = retriever._scalar_text(["Software & IT Services"], "https://permid.org/1-x")
        assert result == "Software & IT Services"
        assert caplog.text == ""

    def test_list_valued_label_does_not_leak_into_entry(self):
        retriever = _make_retriever(enrich_metadata=True)
        stats = BatchStats()
        url = "https://permid.org/1-listlabel"
        retriever.api_clients.entity_lookup.query_endpoint.side_effect = lambda permid_url: {
            "status_code": 200,
            "data": {"prefLabel": ["A", "B"]},
        }

        entry = retriever._parse_company_info(
            _PERMID_URL,
            {"@id": _PERMID_URL, "hasPrimaryIndustryGroup": url},
            stats,
        )

        assert entry["primary_industry_group_label"] == "A"


class TestSectorMemoization:
    """A sector URL shared across companies is resolved once, then served from cache."""

    def test_shared_sector_resolved_once_across_companies(self):
        retriever = _make_retriever(enrich_metadata=True)
        stats = BatchStats()
        entity_a = {
            "@id": "https://permid.org/1-a",
            "hasPrimaryBusinessSector": _BUSINESS_SECTOR_URL,
        }
        entity_b = {
            "@id": "https://permid.org/1-b",
            "hasPrimaryBusinessSector": _BUSINESS_SECTOR_URL,
        }

        a = retriever._parse_company_info("https://permid.org/1-a", entity_a, stats)
        b = retriever._parse_company_info("https://permid.org/1-b", entity_b, stats)

        # Both companies get the same resolved label...
        assert a["primary_business_sector_label"] == "Technology"
        assert b["primary_business_sector_label"] == "Technology"
        # ...but the sector URL was only fetched once (the second was a cache hit).
        assert stats.total_follow_up_calls == 1
        assert stats.total_sector_cache_hits == 1
        sector_calls = [
            call
            for call in retriever.api_clients.entity_lookup.query_endpoint.call_args_list
            if call.kwargs.get("permid_url") == _BUSINESS_SECTOR_URL
        ]
        assert len(sector_calls) == 1


# Records for the retrieve()-loop budget test: 3 companies, each with its own sector URL
# so every company costs 2 PermID requests (1 entity + 1 sector) with no cache sharing.
_C1, _C2, _C3 = (f"https://permid.org/1-company{n}" for n in (1, 2, 3))
_S1, _S2, _S3 = (f"https://permid.org/1-sector{n}" for n in (1, 2, 3))
_BUDGET_RECORDS = {
    _C1: {"@id": _C1, "vcard:organization-name": "C1", "hasPrimaryBusinessSector": _S1},
    _C2: {"@id": _C2, "vcard:organization-name": "C2", "hasPrimaryBusinessSector": _S2},
    _C3: {"@id": _C3, "vcard:organization-name": "C3", "hasPrimaryBusinessSector": _S3},
    _S1: {"prefLabel": "Sector One"},
    _S2: {"prefLabel": "Sector Two"},
    _S3: {"prefLabel": "Sector Three"},
}


def _make_retrieve_retriever(
    tmp_path: Path,
    max_requests: int,
    sector_cache_file: str = "",
    enrich_metadata: bool = True,
) -> CompInfoRetrieval:
    """Build a CompInfoRetrieval backed by real tmp files for driving retrieve()."""
    api_clients = MagicMock()
    api_clients.entity_lookup.query_endpoint.side_effect = lambda permid_url: (
        {"status_code": 200, "data": _BUDGET_RECORDS[permid_url]}
        if permid_url in _BUDGET_RECORDS
        else {"status_code": 404}
    )
    file_paths = FilePaths(
        result_file=str(tmp_path / "result.json"),
        permid_file=str(tmp_path / "permid.json"),
        failure_file="",
        sector_cache_file=sector_cache_file,
    )
    return CompInfoRetrieval(
        file_paths=file_paths,
        batch_config=BatchConfig(
            enrich_metadata=enrich_metadata, max_requests=max_requests, buffer_size=1
        ),
        api_clients=api_clients,
        identifier_type="cik",
    )


def _budget_permid_data() -> dict:
    """permid_data with three companies, one permid URL each."""
    return {
        f"key{i}": {"search": {"Name": f"C{i}", "LocalID": f"cik_{i}"}, "result": [company]}
        for i, company in enumerate((_C1, _C2, _C3), 1)
    }


class TestRequestBudget:
    """retrieve() stops at the max_requests budget without cutting a company off."""

    def test_stops_at_budget_with_each_started_company_fully_resolved(self, tmp_path):
        retriever = _make_retrieve_retriever(tmp_path, max_requests=3)
        stats = BatchStats()

        retriever.retrieve(_budget_permid_data(), num_existing_entities=0, batch_stats=stats)

        results = json.loads((tmp_path / "result.json").read_text())
        # Each company costs 2 requests; a budget of 3 admits C1 (->2) and C2 (->4), then
        # stops before C3. The check is only at the top of the loop, so both admitted
        # companies finish their sector follow-up — no company is cut off mid-resolution.
        assert set(results) == {_C1, _C2}
        assert results[_C1]["primary_business_sector_label"] == "Sector One"
        assert results[_C2]["primary_business_sector_label"] == "Sector Two"
        assert _C3 not in results

    def test_budget_above_total_cost_processes_all_candidates(self, tmp_path):
        retriever = _make_retrieve_retriever(tmp_path, max_requests=100)
        stats = BatchStats()

        retriever.retrieve(_budget_permid_data(), num_existing_entities=0, batch_stats=stats)

        results = json.loads((tmp_path / "result.json").read_text())
        assert set(results) == {_C1, _C2, _C3}

    def test_record_match_calls_count_against_the_shared_budget(self, tmp_path):
        retriever = _make_retrieve_retriever(tmp_path, max_requests=3)
        # Simulate one Record Match call already spent in the PermID-retrieval stage.
        stats = BatchStats(total_record_match_calls=1)

        retriever.retrieve(_budget_permid_data(), num_existing_entities=0, batch_stats=stats)

        results = json.loads((tmp_path / "result.json").read_text())
        # Budget 3, but 1 is already spent on Record Match, leaving room for one company
        # (1 entity + 1 sector = 2). C1 is admitted (->3), then the budget stops C2 — one
        # fewer than when the same budget started clean (see the test above).
        assert set(results) == {_C1}


class TestSectorCachePersistence:
    """The sector memo is flushed to and re-seeded from the shared on-disk cache."""

    def test_run_flushes_resolved_sectors_to_disk(self, tmp_path):
        cache_file = str(tmp_path / "sector_cache.json")
        retriever = _make_retrieve_retriever(
            tmp_path, max_requests=100, sector_cache_file=cache_file
        )

        retriever.retrieve(_budget_permid_data(), num_existing_entities=0, batch_stats=BatchStats())

        cached = json.loads((tmp_path / "sector_cache.json").read_text())
        # JSON stores each value as a [label, comment] list; comment is null when absent.
        assert cached[_S1] == ["Sector One", None]
        assert cached[_S2] == ["Sector Two", None]
        assert cached[_S3] == ["Sector Three", None]

    def test_disk_cache_seeds_a_fresh_run_and_avoids_the_api(self, tmp_path):
        cache_file = str(tmp_path / "sector_cache.json")
        # First run resolves and flushes the sectors.
        _make_retrieve_retriever(tmp_path, max_requests=100, sector_cache_file=cache_file).retrieve(
            _budget_permid_data(), num_existing_entities=0, batch_stats=BatchStats()
        )

        # A brand-new retriever (separate process / sibling source) seeds from that file.
        fresh = _make_retrieve_retriever(tmp_path, max_requests=100, sector_cache_file=cache_file)
        fresh._sector_cache.load()
        stats = BatchStats()

        label, comment = fresh._resolve_sector_name(_S1, stats)

        assert (label, comment) == ("Sector One", None)
        # Served from the on-disk cache: no follow-up call, no entity-lookup hit for _S1.
        assert stats.total_follow_up_calls == 0
        sector_calls = [
            call
            for call in fresh.api_clients.entity_lookup.query_endpoint.call_args_list
            if call.kwargs.get("permid_url") == _S1
        ]
        assert sector_calls == []

    def test_enrichment_disabled_writes_no_cache_file(self, tmp_path):
        cache_file = str(tmp_path / "sector_cache.json")
        retriever = _make_retrieve_retriever(
            tmp_path, max_requests=100, sector_cache_file=cache_file, enrich_metadata=False
        )

        retriever.retrieve(_budget_permid_data(), num_existing_entities=0, batch_stats=BatchStats())

        # With enrichment off, no sectors are resolved, so persistence is disabled entirely.
        assert not (tmp_path / "sector_cache.json").exists()
