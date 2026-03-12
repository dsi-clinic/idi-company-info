"""Shared dataclasses and enums for the identifier processing pipeline."""

# Standard library imports
from dataclasses import dataclass
from enum import Enum, StrEnum
import pathlib


@dataclass
class FilePaths:
    input_file: str
    result_file: str
    permid_file: str
    failure_file: str = ""


@dataclass
class BatchConfig:
    batch_size: int = 2450
    buffer_size: int = 500
    threshold_days: int = 30


@dataclass
class ApiCredentials:
    api_key: str
    geonames_user: str


@dataclass
class CompanyInfo:
    investor_name: str | None
    original_entity_name: str
    identifier: str
    identifier_type: str
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
    last_processed: str


@dataclass
class BatchStats:
    total_entities: int = 0
    total_records: int = 0
    total_ids: int = 0
    total_permids: int = 0
    total_permid_failed: int = 0
    total_company_info: int = 0
    total_company_info_failed: int = 0
    duplicates_ids_removed: int = 0


class QueryType(StrEnum):
    ENTITY_SEARCH = "entity_search"
    RECORD_MATCH = "record_match"


class StageStatus(Enum):
    """Execution status for pipeline stages."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
