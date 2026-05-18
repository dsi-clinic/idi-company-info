"""Identifier type registry mapping identifier types to their pipeline classes."""

# Standard library imports
from dataclasses import dataclass

# Application imports
from idi_company_info.company_by_cik_pipeline import CompanyByCikPipeline
from idi_company_info.company_by_cusip_pipeline import CompanyByCusipPipeline
from idi_company_info.company_pipeline import CompanyPipeline
from idi_company_info.types import IdentifierType


@dataclass
class IdentifierSpec:
    """Pipeline class and per-type filenames for a single identifier type.

    Filenames are placed inside the output and failure directories supplied at
    runtime — see `IdentifierFactory.build`. Adding a new identifier type
    requires only a new entry here.
    """

    cls: type[CompanyPipeline]
    result_filename: str
    permid_filename: str
    failure_filename: str


IDENTIFIER_REGISTRY: dict[IdentifierType, IdentifierSpec] = {
    IdentifierType.CIK: IdentifierSpec(
        cls=CompanyByCikPipeline,
        result_filename="company_info_cik.json",
        permid_filename="permid_tracking_cik.json",
        failure_filename="failures_cik.json",
    ),
    IdentifierType.CUSIP: IdentifierSpec(
        cls=CompanyByCusipPipeline,
        result_filename="company_info_cusip.json",
        permid_filename="permid_tracking_cusip.json",
        failure_filename="failures_cusip.json",
    ),
}
