"""Processes identifiers for company information."""

# Standard library imports
from abc import ABC, abstractmethod
from dataclasses import asdict,dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable

# Third party imports
import pandas as pd

# Application imports
from ftm2j.common.api import LsegEntitySearch, LsegRecordMatch, LSEGEntityLookup, GeonamesApi
from ftm2j.common.logs import get_logger
from ftm2j.common.batch import BatchProcessing
from ftm2j.common.storage import load_json, save_json
from ftm2j.processors.idi_company_info.permid_retriever import PermidRetriever, EntitySearchRetriever, RecordMatchRetriever


@dataclass
class FilePaths:
    input_file: str
    result_file: str
    batch_file: str


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
    identifier: list[str]
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

    def __init__(self, file_paths: FilePaths, batch_config: BatchConfig, api_credentials: ApiCredentials, query_type: QueryType = QueryType.ENTITY_SEARCH):
        """Initialize the Identifier.

        Args:
            file_paths: The file paths.
            batch_config: The batch config.
            api_credentials: The API credentials.
            identifier_type: The identifier type.
            query_type: The query type.
        """
        self.file_paths = file_paths
        self.batch_config = batch_config
        self.api_credentials = api_credentials
        self.api_clients = ApiClients(
            entity_search=LsegEntitySearch(api_key=api_credentials.api_key),
            record_match=LsegRecordMatch(api_key=api_credentials.api_key),
            entity_lookup=LSEGEntityLookup(api_key=api_credentials.api_key),
            geonames_api=GeonamesApi(api_key=api_credentials.api_key, geonames_user=api_credentials.geonames_user)
        )
        self.logger = get_logger(__name__)
        self.permid_retriever: PermidRetriever = self._create_permid_retriever(query_type)  # Strategy pattern

    def _create_permid_retriever(self, query_type: QueryType) -> PermidRetriever:
        """Create the PermID retriever.

        Args:
            query_type: The query type.

        Returns:
            The PermID retriever.
        """
        return {
            QueryType.ENTITY_SEARCH: EntitySearchRetriever(context=self),
            QueryType.RECORD_MATCH: RecordMatchRetriever(context=self),
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

    def process_entities(self, batch_processing: BatchProcessing, entities_to_process: dict[str, Any], existing_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Process the entities.

        Args:
            batch_processing: The batch processing.
            entities_to_process: The entities to process.
            existing_results: The existing results.

        Returns:
            The batch stats.
        """
        batch_stats = BatchStats()
        permid_data = self.permid_retriever.retrieve(entities_to_process, self.batch_config.batch_size, batch_stats)

        self.generate_company_info(permid_data, existing_results, batch_processing, batch_stats)
        return batch_stats

    def generate_company_info(self, permid_data: dict[str, Any], existing_results: list[dict[str, Any]], batch_processing: BatchProcessing, batch_stats: BatchStats) -> None:
        """Generate the company information.

        Args:
            permid_data: The PermID data.
            existing_results: The existing results.
            batch_processing: The batch processing.
            batch_stats: The batch stats.
        """
        batch = list(permid_data.keys())[:self.batch_config.batch_size]
        self.logger.info(f"Generating company info for {len(batch)} entities")

        existing_length = len(existing_results)
        company_info = existing_results

        buffer = []
        for idx, entity_name in enumerate(batch, 1):
            self.logger.info(f"[{idx}/{len(batch)}] Processing: {entity_name} ({len(permid_data[entity_name])})")
            company = self.retrieve_company_info(entity_name, permid_data[entity_name], batch_stats)

            company_info.extend(company)
            buffer.extend([c["original_entity_name"] for c in company])

            if len(buffer) >= self.batch_config.buffer_size:
                self.save_company_info(company_info)
                batch_processing.update_batch_tracking(buffer, batch_stats)
                buffer = []

            batch_stats.total_entities += 1

        if buffer:
            self.save_company_info(company_info)
            batch_processing.update_batch_tracking(buffer, batch_stats)

        batch_stats.total_records = len(company_info) - existing_length

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
        for identifier, permid_list in permid_data:
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

    def _parse_company_info(self, entity_name: str, identifier: list[str], identifier_type: str, permid_id: dict[str, str], response: dict[str, Any]) -> dict[str, Any]:
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
            last_processed=datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S"),
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

    def save_company_info(self, company_info: list[dict[str, Any]]) -> list[str]:
        """Save the company information.

        Args:
            company_info: The company information.

        Returns:
            The company information.
        """
        save_json(self.file_paths.result_file, company_info)

    def run(self):
        """Run the identifier pipeline."""

        # Load identifier data
        identifier_data = self.load_data()

        # Load existing results
        existing_results = load_json(self.file_paths.result_file, return_type="list")

        # Load previous batch processing data
        batch_processing = BatchProcessing(self.file_paths.batch_file, self.batch_config.threshold_days)
        unprocessed_entities = batch_processing.get_unprocessed_entities(identifier_data)
        filtered_results, stale_entities = batch_processing.filter_stale_entities(existing_results)
        self.logger.info(f"To process: %d | Not to process: %d", len(unprocessed_entities) + len(stale_entities), len(filtered_results))

        # Process entities
        unprocessed_entities.update(stale_entities)
        batch_stats = self.process_entities(batch_processing, unprocessed_entities, existing_results)

        # Print stats
        self.print_stats(batch_stats)