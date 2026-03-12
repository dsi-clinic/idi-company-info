"""Identifier type registry mapping identifier types to their pipeline classes."""

# Standard library imports
from dataclasses import dataclass
from enum import StrEnum

from idi_company_info.processors.identifier import IdentifierPipeline
from idi_company_info.processors.IdentifierCik import IdentifierCik
from idi_company_info.processors.IdentifierCusip import IdentifierCusip

# Application imports
from idi_company_info.processors.types import QueryType


class IdentifierType(StrEnum):
    """Supported identifier types."""

    CIK = "cik"
    CIK_MATCH = "cik-match"
    CUSIP = "cusip"
    TICKER = "ticker"


@dataclass
class IdentifierSpec:
    """Specification for a single identifier type.

    Adding a new type requires only a new entry in IDENTIFIER_REGISTRY —
    no other code needs to change.
    """

    cls: type[IdentifierPipeline]
    query_type: QueryType
    permid_filename: str
    result_filename: str
    failure_filename: str


IDENTIFIER_REGISTRY: dict[IdentifierType, IdentifierSpec] = {
    IdentifierType.CIK: IdentifierSpec(
        cls=IdentifierCik,
        query_type=QueryType.ENTITY_SEARCH,
        permid_filename="permid_tracking_cik.json",
        result_filename="company_info_cik.json",
        failure_filename="failures_cik.json",
    ),
    IdentifierType.CUSIP: IdentifierSpec(
        cls=IdentifierCusip,
        query_type=QueryType.ENTITY_SEARCH,
        permid_filename="permid_tracking_cusip.json",
        result_filename="company_info_cusip.json",
        failure_filename="failures_cusip.json",
    ),
    IdentifierType.CIK_MATCH: IdentifierSpec(
        cls=IdentifierCik,
        query_type=QueryType.RECORD_MATCH,
        permid_filename="permid_tracking_cik_match.json",
        result_filename="company_info_cik_match.json",
        failure_filename="failures_cik_match.json",
    ),
    IdentifierType.TICKER: IdentifierSpec(
        cls=IdentifierCusip,
        query_type=QueryType.RECORD_MATCH,
        permid_filename="permid_tracking_ticker.json",
        result_filename="company_info_ticker.json",
        failure_filename="failures_ticker.json",
    ),
}
