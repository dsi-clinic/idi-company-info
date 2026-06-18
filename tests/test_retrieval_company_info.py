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

    return CompInfoRetrieval(
        file_paths=MagicMock(),
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
