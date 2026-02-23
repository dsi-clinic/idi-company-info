"""Processes identifiers for company information."""

# Standard library imports
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable
from dataclasses import asdict
from datetime import datetime, timezone

# Application imports
from ftm2j.common.api import LsegEntitySearch, LsegRecordMatch, LSEGEntityLookup, GeonamesApi
from ftm2j.common.logs import get_logger
from ftm2j.common.batch import BatchProcessing
from ftm2j.common.storage import load_json


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
class BatchStatsPermid:
    total_entities: int = 0
    total_records: int = 0
    total_ids: int = 0
    total_permids: int = 0
    total_permid_failed: int = 0
    total_company_info: int = 0
    total_company_info_failed: int = 0
    duplicates_ids_removed: int = 0


class Identifier(ABC):
    """Base class for identifier types."""

    def __init__(self, file_paths: FilePaths, batch_config: BatchConfig, api_credentials: ApiCredentials):
        """Initialize the Identifier.

        Args:
            file_paths: The file paths.
            batch_config: The batch config.
            api_credentials: The API credentials.
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

    @abstractmethod
    def load_data(self) -> dict[str, Any]:
        """Load the data from the input file."""
        ...

    @abstractmethod
    def retrieve_permid(self) -> dict[str, Any]:
        """Retrieve the PermID for the company."""
        ...

    @abstractmethod
    def retrieve_company_info(self) -> list[dict[str, Any]]:
        """Retrieve the company information."""
        ...

    @abstractmethod
    def save_company_info(self) -> list[str]:
        """Save the company information."""
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
        buffer = []
        new_results = []

        batch_stats = BatchStatsPermid()
        batch = list(entities_to_process.keys())[:self.batch_config.batch_size]

        for idx, entity_name in enumerate(batch, 1):
            permid_data = None
            company = None
            try:
                # Retrieve the PermID for the company
                entity_data = entities_to_process[entity_name]
                self.logger.info(f"[{idx}/{len(batch)}] Processing: {entity_name} ({len(entity_data)})")
                permid_data = self.retrieve_permid(entity_name,entity_data, batch_stats)

                # Retrieve the company information (if PermID is found)
                permid_data_count = sum(len(permid_data[i]) for i in permid_data.keys())
                if permid_data_count > 0:
                    company = self.retrieve_company_info(entity_name,permid_data, batch_stats)
                    new_results.extend(company)

                # Add the company information to the buffer
                if company:
                    batch_stats.total_entities += 1
                    batch_stats.total_records += len(company)
                    buffer.extend(c["original_entity_name"] for c in company)

                # Check if buffer is full
                if len(buffer) >= self.batch_config.buffer_size:
                    # Save the company information to the file and update the batch tracking
                    self.save_company_info(new_results + existing_results)
                    batch_processing.update_batch_tracking(buffer, batch_stats)
                    buffer = []

            except Exception as e:
                self.logger.error(f"Error processing entity {entity_name}: {e}")
                if permid_data is not None:
                    batch_stats.total_company_info_failed += 1
                else:
                    batch_stats.total_permid_failed += 1
                continue

        # Save company info and batch tracking
        if buffer:
            self.save_company_info(new_results + existing_results)
            batch_processing.update_batch_tracking(buffer, batch_stats)

        return batch_stats

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
        Returns (success, data). Caller updates stats.
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

    def _parse_company_data(self, entity_name: str, cik: str, permid: str) -> Callable[[dict], dict]:
        """Return a parse fn that closes over entity_name, cik, permid.

        Args:
            entity_name: The entity name.
            cik: The CIK.
            permid: The PermID.

        Returns:
            The company data.
        """
        def _parse(response: dict) -> dict:
            data = response.get("data")
            if not data:
                return None
            return asdict(self._parse_company_info(entity_name, cik, "cik", permid, data))
        return _parse

    def _parse_company_info(self, entity_name: str, identifier: list[str], identifier_type: str, permid_id: dict[str, str], response: dict[str, Any]) -> dict[str, Any]:
        """Parse the company information."""
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
        """Query the Geonames API to get the location information."""
        response = self.api_clients.geonames_api.query_endpoint(url)
        if response.get("status_code") == 200:
            return response.get("data").get("name") or response.get("data").get("asciiName") or response.get("data").get("countryName")
        else:
            return None

    def print_stats(self, batch_stats: BatchStatsPermid) -> None:
        """Print the stats.

        Args:
            batch_stats: The batch stats.
        """
        self.logger.info(f"Batch stats: {asdict(batch_stats)}")

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