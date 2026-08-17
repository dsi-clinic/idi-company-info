"""Shared dataclasses and enums for the identifier processing pipeline."""

# Standard library imports
import pathlib
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any, Literal, TypedDict

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
    # Shared across all sources (lives at the output root, not a per-source subdir): the
    # sector/industry-group taxonomy is global, so a hit resolved by one source serves the
    # others. Empty string disables disk persistence (in-memory cache only).
    sector_cache_file: str = ""


@dataclass
class BatchConfig:
    """Configuration for batch processing behaviour."""

    batch_size: int = 2450  # max NEW identifiers resolved to PermIDs per run (intake cap)
    # Total PermID-request budget for the run against the shared daily quota: Record Match
    # + entity-lookup + sector/quote follow-up calls all count. The company-info stage stops
    # starting new companies once this many requests are made. Memoized sectors mean most
    # companies cost ~2 live requests. This default sizes one ad-hoc run; every scheduled
    # source passes its own lower cap so that cap * enabled sources <= 5,000/day (dev runs
    # 4 sources at 1,240). See API Quota Budgeting in the README.
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


PermidCallKind = Literal["record_match", "entity_lookup", "follow_up"]


@dataclass
class BatchStats:
    """Counters accumulated during a single pipeline run for reporting."""

    total_entities: int = 0
    total_records: int = 0
    total_ids: int = 0
    total_permids: int = 0
    total_permid_failed: int = 0
    total_permid_lookups: int = 0  # every HTTP call against the shared PermID daily quota
    total_record_match_calls: int = 0  # Record Match HTTP calls (1 per <=1000 records)
    total_entity_lookup_calls: int = 0  # Entity Lookup calls for a candidate's own record
    total_company_info: int = 0  # complete company-info records built (not a call count)
    total_company_info_failed: int = 0
    total_follow_up_calls: int = 0
    total_sector_cache_hits: int = 0  # sector resolves served from the memo (no API call)
    duplicates_ids_removed: int = 0
    # True when a stage stopped early because the shared PermID daily quota was rejected
    # (429), as opposed to finishing its candidates or stopping at our own max_requests cap.
    # The run still succeeds and exits 0 — partial results are flushed and aggregated — so
    # without this flag an interrupted run is indistinguishable from a complete one in the
    # counters alone. Set once per run and never cleared.
    quota_exhausted: bool = False

    def record_permid_call(self, kind: PermidCallKind) -> None:
        """Count one HTTP call against the shared PermID daily quota.

        Both the running total and its per-stage component are incremented here so the two
        cannot drift apart. Call this at the request site, before the call is made and
        regardless of how it turns out — the quota is spent either way. Geonames is a
        separate API with its own quota and is deliberately not counted.

        Args:
            kind: Which PermID endpoint the call goes to.
        """
        self.total_permid_lookups += 1
        if kind == "record_match":
            self.total_record_match_calls += 1
        elif kind == "entity_lookup":
            self.total_entity_lookup_calls += 1
        else:
            self.total_follow_up_calls += 1


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
