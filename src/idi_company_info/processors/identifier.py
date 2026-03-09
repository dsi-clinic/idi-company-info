"""Processes identifiers for company information."""

# Standard library imports
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# Third party imports
import pandas as pd

# Application imports
from idi_company_info.common.api import (
    GeonamesApi,
    LSEGEntityLookup,
    LsegEntitySearch,
    LsegRecordMatch,
)
from idi_company_info.common.batch import BatchProcessing
from idi_company_info.common.buffer import Buffer
from idi_company_info.common.failures import FailureClassifier, FailureRegistry
from idi_company_info.common.logs import get_logger
from idi_company_info.common.storage import load_json, save_json
from idi_company_info.processors.permid_retriever import (
    EntitySearchRetriever,
    PermidRetriever,
    RecordMatchRetriever,
)


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
class ApiClients:
    entity_search: LsegEntitySearch
    record_match: LsegRecordMatch
    entity_lookup: LSEGEntityLookup
    geonames_api: GeonamesApi


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


class Identifier(ABC):
    """Base class for identifier types."""

    def __init__(self, file_paths: FilePaths, batch_config: BatchConfig, api_credentials: ApiCredentials, query_type: QueryType = QueryType.ENTITY_SEARCH, match_score_threshold: int = 1):
        """Initialize the Identifier.

        Args:
            file_paths: The file paths.
            batch_config: The batch config.
            api_credentials: The API credentials.
            identifier_type: The identifier type.
            query_type: The query type.
            match_score_threshold: The match score threshold.
        """
        self.file_paths = file_paths
        self._init_dirs()

        self.batch_config = batch_config

        self.failure_registry: FailureRegistry | None = (
            FailureRegistry(file_paths.failure_file) if file_paths.failure_file else None
        )

        self.api_credentials = api_credentials
        self.api_clients = ApiClients(
            entity_search=LsegEntitySearch(api_key=api_credentials.api_key),
            record_match=LsegRecordMatch(api_key=api_credentials.api_key),
            entity_lookup=LSEGEntityLookup(api_key=api_credentials.api_key),
            geonames_api=GeonamesApi(api_key=api_credentials.api_key, geonames_user=api_credentials.geonames_user)
        )

        self.query_type = query_type
        self.permid_retriever: PermidRetriever = self._create_permid_retriever(query_type, match_score_threshold)  # Strategy pattern

        self.logger = get_logger("Identifier")

    def _init_dirs(self) -> None:
        """Initialize the directories."""
        os.makedirs(os.path.dirname(self.file_paths.result_file), exist_ok=True)
        os.makedirs(os.path.dirname(self.file_paths.permid_file), exist_ok=True)
        if self.file_paths.failure_file:
            os.makedirs(os.path.dirname(self.file_paths.failure_file), exist_ok=True)

    def _create_permid_retriever(self, query_type: QueryType, match_score_threshold: int = 1) -> PermidRetriever:
        """Create the PermID retriever.

        Args:
            query_type: The query type.
            match_score_threshold: The match score threshold.
        Returns:
            The PermID retriever.
        """
        return {
            QueryType.ENTITY_SEARCH: EntitySearchRetriever(context=self),
            QueryType.RECORD_MATCH: RecordMatchRetriever(context=self, match_score_threshold=match_score_threshold),
        }[query_type]

    @property
    @abstractmethod
    def identifier_type(self) -> str:
        """Get the identifier type."""
        ...

    @abstractmethod
    def load_data(self) -> dict[str, Any]:
        """Load the data from the input file.

        Returns:
            The data from the input file.
        """
        ...

    @staticmethod
    def read_parquet(input_file: str, required_columns: list[str]) -> pd.DataFrame:
        """Read parquet file and validate required columns exist.

        Args:
            input_file: The input file to read.
            required_columns: The required columns to validate.

        Returns:
            The dataframe with the required columns.
        """
        df = pd.read_parquet(input_file)

        # Validate required columns
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise ValueError(
                f"Required columns {missing_columns} not found in dataframe"
            )

        return df

    @abstractmethod
    def _build_query_params(self, identifier: str) -> dict[str, Any]:
        """Build the query parameters.

        Args:
            identifier: The identifier.

        Returns:
            The query parameters.
        """
        ...

    def process_entities(self, entities_to_process: dict[str, Any], num_existing_entities: int) -> list[dict[str, Any]]:
        """Process the entities.

        For entities that already have PermIDs stored from a previous run, skip the
        PermID retrieval step entirely and proceed directly to company info lookup.
        This allows the company info stage to drain its backlog without burning
        PermID API quota on identifiers that are already resolved.

        Args:
            entities_to_process: The entities to process.
            num_existing_entities: The number of existing entities.

        Returns:
            The batch stats.
        """
        batch_stats = BatchStats()

        # Partition: entities that already have PermIDs vs those that still need retrieval
        existing_permid_data = load_json(self.file_paths.permid_file, return_type="dict")
        needs_permid = {k: v for k, v in entities_to_process.items() if k not in existing_permid_data}
        has_permid_count = len(entities_to_process) - len(needs_permid)

        # Retrieve the PermIDs for the entities that need them
        if needs_permid:
            retrieval_count = min(len(needs_permid), self.batch_config.batch_size)
            self.logger.info(
                "PermID retrieval: %d queued, %d will be retrieved this run, %d already resolved",
                len(needs_permid), retrieval_count, has_permid_count,
            )
            self.permid_retriever.retrieve(needs_permid, self.batch_config.batch_size, batch_stats)
        else:
            self.logger.info(
                "All %d entities already have PermIDs — skipping PermID retrieval",
                has_permid_count,
            )

        # Reload after retrieval so newly resolved PermIDs are included
        permid_data = load_json(self.file_paths.permid_file, return_type="dict")

        # Log how many of the queued entities were successfully resolved
        if needs_permid:
            self._log_permid_retrieval_stats(needs_permid, permid_data)

        # Pass all entities (both groups) — _build_company_info_batch will filter by batch threshold
        all_entities = list(entities_to_process.keys())
        self.generate_company_info(permid_data, all_entities, num_existing_entities, batch_stats)
        return batch_stats

    def _log_permid_retrieval_stats(self, needs_permid: dict[str, Any], permid_data: dict[str, Any]) -> None:
        """Log the PermID retrieval stats.

        Args:
            needs_permid: The entities that need PermIDs.
            permid_data: The PermID data.
        """
        retrieved_count = min(len(needs_permid), self.batch_config.batch_size)
        resolved_this_run = sum(
            1 for k in list(needs_permid.keys())[:retrieved_count]
            if k in permid_data
        )
        self.logger.info(
            "PermID retrieval complete: %d/%d entities resolved this run",
            resolved_this_run, retrieved_count,
        )

    def generate_company_info(self, permid_data: dict[str, Any], entities_to_process: list[dict[str, Any]], num_existing_entities: int, batch_stats: BatchStats) -> None:
        """Generate the company information.

        Args:
            permid_data: The PermID data.
            entities_to_process: The entities to process.
            num_existing_entities: The number of existing entities.
            batch_stats: The batch stats.
        """
        batch = self._build_company_info_batch(entities_to_process, permid_data)
        total_lookups = sum(
            len(permids)
            for e in batch
            for item in permid_data[e]
            for permids in item.values()
            if permids
        )
        self.logger.info("Generating company info for %d entities (%d entity-lookup requests)", len(batch), total_lookups)

        buffer = Buffer(
            file_path=self.file_paths.result_file,
            buffer_size=self.batch_config.buffer_size,
            mode="list"
        )

        for idx, entity_name in enumerate(batch, 1):
            self.logger.info("[%d/%d] Processing: %s (%d)", idx, len(batch), entity_name, len(permid_data[entity_name]))
            company = self.retrieve_company_info(entity_name, permid_data[entity_name], batch_stats)

            buffer.add(data=company)    # LO troubleshooting this line
            batch_stats.total_entities += 1

        batch_stats.total_records = len(buffer.load_all()) - num_existing_entities

    def _build_company_info_batch(self, entities_to_process: list[str], permid_data: dict[str, Any]) -> list[str]:
        """Select entities to process, capped at batch_size total entity-lookup API calls.

        Each entity can have multiple PermIDs; every PermID costs one API request.
        We stop adding entities as soon as the next entity would push the total over
        batch_size, ensuring we never exceed the API request budget.

        Args:
            entities_to_process: Ordered list of entity names to consider.
            permid_data: Mapping of entity name → list of {identifier: [permid, ...]} items.

        Returns:
            The subset of entities that fits within the request budget.
        """
        batch: list[str] = []
        remaining = self.batch_config.batch_size
        for entity in entities_to_process:
            if entity not in permid_data:
                continue
            permid_count = sum(
                len(permids)
                for item in permid_data[entity]
                for permids in item.values()
                if permids
            )
            if permid_count == 0:
                continue
            if permid_count > remaining:
                self.logger.info(
                    "Stopping company info batch: adding '%s' (%d PermID(s)) would exceed the "
                    "%d-request limit (%d remaining)",
                    entity, permid_count, self.batch_config.batch_size, remaining,
                )
                break
            batch.append(entity)
            remaining -= permid_count
        return batch

    def retrieve_company_info(self, entity_name: str, permid_data: dict[str, Any], batch_stats: BatchStats) -> list[dict[str, Any]]:
        """Retrieve the company information.

        Args:
            entity_name: The entity name.
            permid_data: The PermID data.
            batch_stats: The batch stats.

        Returns:
            A list of company information.
        """
        company_info = []
        for list_item in permid_data:
            for identifier, permid_list in list_item.items():
                for permid in permid_list:
                    response = self.api_clients.entity_lookup.query_endpoint(permid_url=permid)

                    success, company_data = self._handle_api_response(
                        response,
                        entity_name,
                        permid,
                        parse_fn=self._parse_company_data(entity_name, identifier, permid),
                        error_msg="Company info query error for entity %s with PermID %s: %s",
                        no_match_msg="No company data found for entity %s with PermID %s",
                    )

                    if success:
                        company_info.append(company_data)
                        batch_stats.total_company_info += 1
                    else:
                        batch_stats.total_company_info_failed += 1
                        if self.failure_registry:
                            self._handle_failures(response, entity_name, identifier, company_data)

        return company_info

    def _handle_api_response(
        self,
        response: dict,
        entity_name: str,
        identifier: str,
        parse_fn: Callable[[dict], Any],
        error_msg: str = "API error for entity %s with %s: %s",
        no_match_msg: str = "No data found for entity %s with %s",
    ) -> tuple[bool, Any]:
        """
        Parse API response and handle success/failure logging.

        Args:
            response: The API response.
            entity_name: The entity name.
            identifier: The identifier.
            parse_fn: The parse function.
            error_msg: The error message.
            no_match_msg: The no match message.

        Returns:
            A tuple of (success, data).
                success: True if the API response is successful, False otherwise.
                data: The data from the API response.
        """
        if response.get("status_code") != 200:
            self.logger.error(error_msg, entity_name, identifier, response.get("error"))
            return False, None

        data = parse_fn(response)
        if not data:
            self.logger.warning(no_match_msg, entity_name, identifier)
            return False, None

        return True, data

    def _parse_permid_entities(self, response: dict) -> list[str]:
        """Parse the PermID entities.

        Args:
            response: The API response.

        Returns:
            The PermID entities.
        """
        entities = response.get("data", {}).get("result", {}).get("organizations", {}).get("entities", [])
        return [e.get("@id") for e in entities if e.get("@id")]

    def _parse_company_data(self, entity_name: str, identifier: str, permid: str) -> Callable[[dict], dict]:
        """Return a parse fn that closes over entity_name, cik, permid.

        Args:
            entity_name: The entity name.
            identifier: The identifier.
            permid: The PermID.

        Returns:
            The company data.
        """
        def _parse(response: dict) -> dict:
            data = response.get("data")
            if not data:
                return None
            return asdict(self._parse_company_info(entity_name, identifier, self.identifier_type, permid, data))
        return _parse

    def _parse_company_info(self, entity_name: str,
                            identifier: list[str],
                            identifier_type: str,
                            permid_id: str,
                            response: dict[str, Any]) -> dict[str, Any]:
        """Parse the company information.

        Args:
            entity_name: The entity name.
            identifier: The identifier.
            identifier_type: The identifier type.
            permid_id: The PermID.
            response: The API response.

        Returns:
            A CompanyInfo object.
        """
        company_info = CompanyInfo(
            investor_name=response.get("vcard:organization-name"),
            original_entity_name=entity_name,
            identifier=identifier,
            identifier_type=identifier_type,
            permid_id=response.get("tr-common:hasPermId") if response.get("tr-common:hasPermId") else permid_id.split("/")[-1],
            permid_url=response.get("@id") if response.get("@id") else permid_id,
            hq_address=response.get("mdaas:HeadquartersAddress"),
            registered_address=response.get("mdaas:RegisteredAddress"),
            fax_number=response.get("tr-org:hasHeadquartersFaxNumber"),
            phone_number=response.get("tr-org:hasHeadquartersPhoneNumber"),
            lei=response.get("tr-org:hasLEI"),
            founded_date=response.get("hasLatestOrganizationFoundedDate"),
            incorporated_in=self._query_geonames_location(response.get("isIncorporatedIn")),
            domiciled_in=self._query_geonames_location(response.get("isDomiciledIn")),
            url=response.get("hasURL"),
            activity_status=response.get("hasActivityStatus"),
            last_processed=datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S"),
        )
        return company_info

    def _query_geonames_location(self, url: str) -> str:
        """Query the Geonames API to get the location information.

        Args:
            url: The URL to query.

        Returns:
            The location information.
        """
        if not url:
            return None

        response = self.api_clients.geonames_api.query_endpoint(url)
        if response.get("status_code") == 200:
            return response.get("data").get("name") or response.get("data").get("asciiName") or response.get("data").get("countryName")
        else:
            return None

    def _handle_failures(self, response: dict, entity_name: str, identifier: str, company_data: dict[str, Any]) -> None:
        """Handle the failures.

        Args:
            response: The response.
            entity_name: The entity name.
            identifier: The identifier.
            company_data: The company data.
        """
        empty_data = company_data is None
        failure_type = FailureClassifier.classify_from_response(
            response, empty_data=empty_data, category="company_info"
        )
        if not FailureClassifier.is_retryable(failure_type):
            self.failure_registry.add(
                entity_name, identifier, reason=str(failure_type)
            )

    def print_stats(self, batch_stats: BatchStats) -> None:
        """Print the stats.

        Args:
            batch_stats: The batch stats.
        """
        stats = asdict(batch_stats)

        # Compute rates (avoid division by zero)
        permid_total = stats["total_permids"] + stats["total_permid_failed"]
        permid_rate = (stats["total_permids"] / permid_total * 100) if permid_total else 0

        company_total = stats["total_company_info"] + stats["total_company_info_failed"]
        company_rate = (stats["total_company_info"] / company_total * 100) if company_total else 0

        self.logger.info("=" * 50)
        self.logger.info("BATCH PROCESSING STATS")
        self.logger.info("=" * 50)
        self.logger.info("PermID retrieval:     %d found, %d failed (%.1f%% success)",
                        stats["total_permids"], stats["total_permid_failed"], permid_rate)
        self.logger.info("Company info lookup:  %d fetched, %d failed (%.1f%% success)",
                        stats["total_company_info"], stats["total_company_info_failed"], company_rate)
        self.logger.info("Processed:   Entities: %d | Records: %d | Duplicates removed: %d",
                        stats["total_entities"], stats["total_records"], stats["duplicates_ids_removed"])
        self.logger.info("=" * 50)

    def save_company_info(self, company_info: list[dict[str, Any]]) -> None:
        """Save the company information.

        Args:
            company_info: The company information.
        """
        save_json(self.file_paths.result_file, company_info)

    def run(self):
        """Run the identifier pipeline."""

        # Load identifier data
        identifier_data = self.load_data()

        # Load existing results
        existing_results = load_json(self.file_paths.result_file, return_type="list")

        # Load previous batch processing data
        batch_processing = BatchProcessing(
            existing_results,
            self.batch_config.threshold_days,
            failure_registry=self.failure_registry,
        )
        unprocessed_entities = batch_processing.get_unprocessed_entities(identifier_data)
        filtered_results, stale_identifiers = batch_processing.filter_stale_entities()
        unprocessed_entities.update(stale_identifiers)
        to_process = sum(len(v) for v in unprocessed_entities.values())
        self.logger.info("To process: %d | Not to process: %d", to_process, len(filtered_results))

        # If stale entities were removed, persist the pruned list so the buffer
        # appends fresh results without duplicating the old stale records.
        if stale_identifiers:
            self.logger.info("Removing %d stale record(s) from result file", len(stale_identifiers))
            save_json(self.file_paths.result_file, filtered_results)

        # Process entities
        batch_stats = self.process_entities(unprocessed_entities, len(filtered_results))

        # Print stats
        self.print_stats(batch_stats)
