#!/usr/bin/env python3
"""Unit tests for idi_company_info.retrieval_company_info metadata enrichment."""

from unittest.mock import MagicMock

from idi_company_info.retrieval_company_info import CompInfoRetrieval
from idi_company_info.types import BatchConfig, BatchStats

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

# Linked records returned by the follow-up calls, keyed by the URL queried. The quote
# record carries ticker AND exchange inline (no further lookup) — modelled on a real
# tr-fin:Quote (e.g. AAL on LSE: ticker "AAL", code "LSE", MIC "XLON", RIC "AAL.L").
_LINKED_RECORDS = {
    _BUSINESS_SECTOR_URL: {"skos:prefLabel": "Technology"},
    _ECONOMIC_SECTOR_URL: {"skos:prefLabel": "Information Technology"},
    _INDUSTRY_GROUP_URL: {"skos:prefLabel": "Software & IT Services"},
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

    return CompInfoRetrieval(
        file_paths=MagicMock(),
        batch_config=BatchConfig(enrich_metadata=enrich_metadata),
        api_clients=api_clients,
        identifier_type="cik",
    )


class TestParseCompanyInfoEnrichment:
    """_parse_company_info resolves sector and quote links when enrichment is on."""

    def test_populates_all_five_fields(self):
        retriever = _make_retriever(enrich_metadata=True)
        stats = BatchStats()

        entry = retriever._parse_company_info(_PERMID_URL, _ENTITY_DATA, stats)

        assert entry["primary_business_sector"] == "Technology"
        assert entry["primary_economic_sector"] == "Information Technology"
        assert entry["primary_industry_group"] == "Software & IT Services"
        assert entry["ticker"] == "TEST"
        assert entry["exchange"] == "NSM"

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
            "primary_business_sector",
            "primary_economic_sector",
            "primary_industry_group",
            "ticker",
            "exchange",
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
        assert entry["primary_business_sector"] is None
        assert entry["ticker"] is None
        assert entry["exchange"] is None

    def test_failed_follow_up_degrades_to_none(self):
        retriever = _make_retriever(enrich_metadata=True)
        stats = BatchStats()

        entry = retriever._parse_company_info(
            _PERMID_URL,
            {"@id": _PERMID_URL, "tr-org:hasPrimaryBusinessSector": "https://permid.org/1-missing"},
            stats,
        )

        # The call was attempted (and counted) but returned a non-200.
        assert stats.total_follow_up_calls == 1
        assert entry["primary_business_sector"] is None
