"""Identifier type registry mapping identifier types to their pipeline classes."""

# Standard library imports
from dataclasses import dataclass

from idi_company_info.processors.identifier import IdentifierPipeline
from idi_company_info.processors.identifier_cik import IdentifierCik
from idi_company_info.processors.identifier_cusip import IdentifierCusip

# Application imports
from idi_company_info.processors.types import IdentifierType


@dataclass
class IdentifierSpec:
    """Specification for a single identifier type.

    Adding a new type requires only a new entry in IDENTIFIER_REGISTRY —
    no other code needs to change.
    """

    cls: type[IdentifierPipeline]
    permid_filename: str
    result_filename: str
    failure_filename: str


IDENTIFIER_REGISTRY: dict[IdentifierType, IdentifierSpec] = {
    IdentifierType.CIK: IdentifierSpec(
        cls=IdentifierCik,
        permid_filename="permid_tracking_cik.json",
        result_filename="company_info_cik.json",
        failure_filename="failures_cik.json",
    ),
    IdentifierType.CUSIP: IdentifierSpec(
        cls=IdentifierCusip,
        permid_filename="permid_tracking_cusip.json",
        result_filename="company_info_cusip.json",
        failure_filename="failures_cusip.json",
    )
}
