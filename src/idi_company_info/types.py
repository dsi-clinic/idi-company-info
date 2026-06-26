"""Shared dataclasses and enums for the identifier processing pipeline."""

# Standard library imports
import pathlib
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any, TypedDict

# Buffer records — the buffer is generic over both on-disk shapes below
type CacheEntry = dict[str, Any]
type CacheBuffer = dict[str, CacheEntry]

# LSEG permid response
type PermidResponse = dict[str, str | dict[str, str]] | None


# Functional syntax required because "Standard Identifier" contains a space.
PermidSearch = TypedDict(
    "PermidSearch",
    {"Name": str, "LocalID": str, "Standard Identifier": str | None},
)


class PermidEntry(TypedDict):
    """A permid_file entry: the input search block and the resolved PermID URLs."""

    search: PermidSearch
    result: list[str]


class CompanyResult(TypedDict):
    """Company info returned by the entity-lookup API for one PermID."""

    investor_name: str | None
    permid_id: str
    permid_url: str | None
    hq_address: str | None
    registered_address: str | None
    fax_number: str | None
    phone_number: str | None
    lei: str | None
    founded_date: str | None
    incorporated_in: str | None
    domiciled_in: str | None
    url: str | None
    activity_status: str | None
    primary_business_sector_label: str | None
    primary_economic_sector_label: str | None
    primary_industry_group_label: str | None
    primary_business_sector_comment: str | None
    primary_economic_sector_comment: str | None
    primary_industry_group_comment: str | None
    ticker: str | None
    exchange: str | None
    exchange_code: str | None
    ric: str | None
    last_processed: str


# A result_file entry IS the company info, flat and keyed by permid_url
ResultEntry = CompanyResult


@dataclass(frozen=True)
class QuoteInfo:
    """Resolved primary-quote fields from a tr-fin:Quote record."""

    ticker: str | None = None
    exchange: str | None = None
    exchange_code: str | None = None
    ric: str | None = None


@dataclass
class FilePaths:
    """File paths for pipeline input and output."""

    result_file: str
    permid_file: str
    failure_file: str = ""


@dataclass
class BatchConfig:
    """Configuration for batch processing behaviour."""

    batch_size: int = 2450  # max NEW identifiers resolved to PermIDs per run (intake cap)
    # Total PermID-request budget for the run against the shared daily quota: Record Match
    # + entity-lookup + sector/quote follow-up calls all count. The company-info stage stops
    # starting new companies once this many requests are made. Memoized sectors mean most
    # companies cost ~2 live requests. 3 daily sources * 1650 = 4,950 <= 5,000/day quota.
    max_requests: int = 1650
    buffer_size: int = 500  # max size of buffer before data is written to disk
    threshold_days: int | None = None  # number of days to look for stale entities
    enrich_metadata: bool = (
        True  # when True, resolve linked sector and quote (ticker/exchange) URLs
    )


@dataclass
class ApiCredentials:
    """API credentials required by pipeline clients."""

    api_key: str
    geonames_user: str


@dataclass
class APIRateLimits:
    """API rate limits for the pipeline."""

    permid: float = 0.5
    company_info: float = 0.5
    geonames: float = 0


@dataclass
class BatchStats:
    """Counters accumulated during a single pipeline run for reporting."""

    total_entities: int = 0
    total_records: int = 0
    total_ids: int = 0
    total_permids: int = 0
    total_permid_failed: int = 0
    total_record_match_calls: int = 0  # Record Match HTTP calls (1 per <=1000 records)
    total_company_info: int = 0
    total_company_info_failed: int = 0
    total_follow_up_calls: int = 0
    duplicates_ids_removed: int = 0


class InputSource(StrEnum):
    """Supported input sources."""

    SHAREHOLDER_TRACKER_CIK = "shareholder_tracker_cik"
    SHAREHOLDER_TRACKER_CUSIP = "shareholder_tracker_cusip"
    COMMERCIAL_DEBT_TRACKER = "commercial_debt_tracker"
    CORPORATE_SUBSIDIARIES = "corporate_subsidiaries"


class StageStatus(Enum):
    """Execution status for pipeline stages."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class OrchestratorConfig:
    """Configuration for a single orchestrator run."""

    input_file: str | pathlib.Path
    output_dir: str | pathlib.Path
    input_type: InputSource
    api_key: str
    geonames_user: str
    batch_size: int = 2450  # max NEW identifiers resolved to PermIDs per run (intake cap)
    max_requests: int = 1650  # PermID enrichment-request budget per run (company-info stage)
    buffer_size: int = 500
    threshold_days: int | None = None
    enrich_metadata: bool = True  # resolve sector + ticker/exchange links (extra API calls)
    match_score_threshold: int = 1
    final_output_file: str | None = None  # aggregated parquet; None = <output_dir>/latest.parquet
    skip_final_output: bool = False  # when True, do not aggregate after the pipeline run


class MergeStrategy(StrEnum):
    """Options for results merge operations."""

    PERMID = "permid"
    COMPANY_INFO = "company_info"
