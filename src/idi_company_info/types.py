"""Shared dataclasses and enums for the identifier processing pipeline."""

# Standard library imports
import pathlib
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any

# Buffer records
type CacheEntry = dict[str, Any]
type CacheBuffer = dict[str, CacheEntry]

# LSEG permid response
type PermidResponse = dict[str, str | dict[str, str]] | None


@dataclass
class FilePaths:
    """File paths for pipeline input and output."""

    input_file: str
    result_file: str
    permid_file: str
    failure_file: str = ""


@dataclass
class BatchConfig:
    """Configuration for batch processing behaviour."""

    batch_size: int = 2450  # number of records to process in a single execution
    buffer_size: int = 500  # max size of buffer before data is written to disk
    threshold_days: int | None = None  # number of days to look for stale entities


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
    total_company_info: int = 0
    total_company_info_failed: int = 0
    duplicates_ids_removed: int = 0


class IdentifierType(StrEnum):
    """Supported identifier types."""

    CIK = "cik"
    CUSIP = "cusip"


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
    failure_dir: str | pathlib.Path
    identifier_type: IdentifierType
    api_key: str
    geonames_user: str
    batch_size: int = 2450
    buffer_size: int = 500
    threshold_days: int | None = None
    match_score_threshold: int = 1


class MergeStrategy(StrEnum):
    """Options for results merge operations."""

    PERMID = "permid"
    COMPANY_INFO = "company_info"
