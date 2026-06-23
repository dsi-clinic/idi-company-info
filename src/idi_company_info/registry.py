"""Input-source registry mapping each InputSource to its Input loader class."""

# Standard library imports
from dataclasses import dataclass

# Application imports
from idi_company_info.input import (
    CdtInput,
    Input,
    ShareholderInputCik,
    ShareholderInputCusip,
    SubsidiaryInput,
)
from idi_company_info.types import InputSource


@dataclass
class InputSpec:
    """The Input loader class for a single input source.

    Output paths are derived at runtime from the output directory and the input
    source slug — see `PipelineFactory.build`. Adding a new input source requires
    only a new entry here.
    """

    cls: type[Input]


INPUT_REGISTRY: dict[InputSource, InputSpec] = {
    InputSource.SHAREHOLDER_TRACKER_CIK: InputSpec(
        cls=ShareholderInputCik,
    ),
    InputSource.SHAREHOLDER_TRACKER_CUSIP: InputSpec(
        cls=ShareholderInputCusip,
    ),
    InputSource.COMMERCIAL_DEBT_TRACKER: InputSpec(cls=CdtInput),
    InputSource.CORPORATE_SUBSIDIARIES: InputSpec(cls=SubsidiaryInput),
}
