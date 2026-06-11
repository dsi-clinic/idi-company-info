"""Identifier type registry mapping identifier types to their pipeline classes."""

# Standard library imports
from dataclasses import dataclass

# Application imports
from idi_company_info.input import CdtInput, Input, ShareholderInputCik, ShareholderInputCusip, SubsidiaryInput
from idi_company_info.types import InputSource


@dataclass
class InputSpec:
    """Pipeline class and per-type filenames for a single input type.

    Filenames are placed inside the output and failure directories supplied at
    runtime — see `IdentifierFactory.build`. Adding a new identifier type
    requires only a new entry here.
    """

    cls: type[Input]


INPUT_REGISTRY: dict[InputSource, InputSpec] = {
    InputSource.SHAREHOLDER_TRACKER_CIK: InputSpec(
        cls=ShareholderInputCik,
    ),
    InputSource.SHAREHOLDER_TRACKER_CUSIP: InputSpec(
        cls=ShareholderInputCusip,
    ),
    InputSource.COMMERCIAL_DEBT_TRACKER: InputSpec(
        cls=CdtInput
    ),
    InputSource.CORPORATE_SUBSIDIARIES: InputSpec(
        cls=SubsidiaryInput
    )
}
