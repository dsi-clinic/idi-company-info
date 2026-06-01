"""Retrieve company information from PermID entity lookup."""

# Standard library imports
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

# Third party imports
from idi_ftm2j_shared.failures import FailureRegistry

# Application imports
from idi_company_info.buffer import Buffer, CacheBuffer
from idi_company_info.failures import CompanyInfoFailureClassifier
from idi_company_info.retrieval import Retrieval
from idi_company_info.types import BatchConfig, BatchStats, FilePaths, PermidResponse

if TYPE_CHECKING:
    from idi_company_info.company_pipeline import ApiClients


class CompInfoRetrieval(Retrieval):
    """Retrieve company information from the PermID entity lookup API."""

    def __init__(
        self,
        file_paths: FilePaths,
        batch_config: BatchConfig,
        api_clients: "ApiClients",
        identifier_type: str,
        failure_registry: FailureRegistry | None = None,
        raw_ticker_map: dict[str, str] | None = None,
    ) -> None:
        """Initialize the CompInfoRetrieval.

        Args:
            file_paths: The file paths.
            batch_config: The batch configuration.
            api_clients: The constructed API client instances.
            identifier_type: The identifier type (e.g. 'cik', 'cusip').
            failure_registry: Optional registry for permanent failures.
            raw_ticker_map: Optional CUSIP → raw ticker symbol map.  When supplied
                (CUSIP mode), the raw ticker is stored in ``CompanyInfo.ticker`` so
                the search term used for the Record Match call is retained in output.
        """
        super().__init__(file_paths, batch_config, api_clients, failure_registry)
        self.identifier_type = identifier_type
        self._raw_ticker_map: dict[str, str] = raw_ticker_map or {}

    def retrieve(
        self,
        permid_data: dict[str, Any],
        entities_to_process: list[str],
        num_existing_entities: int,
        batch_stats: BatchStats,
    ) -> None:
        """Retrieve company information for all entities and write to the result buffer.

        Args:
            permid_data: Mapping of entity name → list of {identifier: [permid_url, ...]} items.
            entities_to_process: Ordered list of entity names to consider.
            num_existing_entities: Count of records already present before this run.
            batch_stats: Accumulator for run-level statistics.
        """
        # Build the batch of entities to process
        batch = self._build_company_info_batch(permid_data)
        self.logger.info(
            "Generating company info for %d permid urls",
            len(batch),
        )

        # Re-org data to make it easier to retrieve by permid_url
        batch_url = {
            permid_url: entity_value["search"]
            for entity_key, entity_value in permid_data.items()
            for permid_url in entity_value["result"]
        }

        # Create the buffer to store the company info
        buffer = Buffer(
            file_path=self.file_paths.result_file,
            buffer_size=self.batch_config.buffer_size,
        )

        # Retrieve the company info for each entity in the batch
        for idx, permid_url in enumerate(batch, 1):
            self.logger.info("[%d/%d] Processing: %s", idx, len(batch), permid_url)
            company = self._retrieve_entity_company_info(
                permid_url,
                batch_url[permid_url]["Name"],
                batch_url[permid_url]["LocalID"],
                batch_stats,
            )
            buffer.add(data=company)
            batch_stats.total_entities += 1

        batch_stats.total_records = len(buffer.load_all()) - num_existing_entities

    def _build_company_info_batch(self, permid_data: dict[str, Any]) -> list[str]:
        """Select entities to process, capped at batch_size total entity-lookup API calls.

        Each entity can have multiple PermIDs; every PermID costs one API request.
        We stop adding entities as soon as the next entity would push the total over
        batch_size, ensuring we never exceed the API request budget.

        Args:
            permid_data: Mapping of entity name → list of {identifier: [permid, ...]} items.

        Returns:
            The subset of entities that fits within the request budget.
        """
        permid_list = [
            permid_url for values in permid_data.values() for permid_url in values["result"]
        ]

        batch_permids = permid_list[: self.batch_config.batch_size]
        remaining = len(permid_list) - len(batch_permids)

        self.logger.info(
            "Will process %d permid urls, remaining: %d", len(batch_permids), remaining
        )
        return batch_permids

    def _retrieve_entity_company_info(
        self, permid_url: str, entity_name: str, entity_identifier: str, batch_stats: BatchStats
    ) -> CacheBuffer:
        """Fetch company info for every PermID associated with a single entity.

        Args:
            permid_url: URL to query to retrieve company info.
            entity_name: Name for entity searched via permid.
            entity_id: Identifier for entity searched via permid.
            batch_stats: Accumulator for run-level statistics.

        Returns:
            List of company info dicts for this entity.
        """
        response = self.api_clients.entity_lookup.query_endpoint(permid_url=permid_url)

        # Handle the response from the entity-lookup API
        if response.get("status_code") != self._HTTP_OK:
            self.logger.error(
                "Company info query error for entity %s with PermID %s: %s",
                entity_name,
                permid_url,
                response.get("error"),
            )
            batch_stats.total_company_info_failed += 1
            if self.failure_registry:
                self._handle_failures(
                    response=response,
                    entity_name=entity_name,
                    identifier=entity_identifier,
                    company_data=None,
                )

        # Parse the company info from the response
        data: PermidResponse = response.get("data")
        if not data:
            self.logger.warning(
                "No company data found for entity %s with PermID %s",
                entity_name,
                permid_url,
            )
            batch_stats.total_company_info_failed += 1
            if self.failure_registry:
                self._handle_failures(
                    response=response,
                    entity_name=entity_name,
                    identifier=entity_identifier,
                    company_data=None,
                )

        # Parse the company info from the response
        company_data = self._parse_company_info(
            entity_name,
            entity_identifier,
            self.identifier_type,
            permid_url,
            data,
            ticker=self._raw_ticker_map.get(entity_identifier),
        )
        batch_stats.total_company_info += 1

        return {permid_url: company_data}

    def _parse_company_info(
        self,
        entity_name: str,
        identifier: str,
        identifier_type: str,
        permid_url: str,
        response: dict[str, Any],
        ticker: str | None = None,
    ) -> dict[str, dict]:
        """Map a raw entity-lookup response to a CompanyInfo dataclass.

        Args:
            entity_name: The entity name.
            identifier: The identifier (CIK, CUSIP, etc.).
            identifier_type: The identifier type string.
            permid_url: The PermID URL used in the request.
            response: The ``data`` payload from the entity-lookup response.
            ticker: Raw ticker symbol used during the Record Match search (CUSIP
                mode only).  Stored verbatim in the output; ``None`` for CIK mode.

        Returns:
            A populated CompanyInfo dataclass instance.
        """
        return {
            "search": {"permid_url": permid_url},
            "result": {
                "investor_name": response.get("vcard:organization-name"),
                "permid_id": response.get("tr-common:hasPermId") or permid_url.split("/")[-1],
                "permid_url": response.get("@id") or permid_url,
                "hq_address": response.get("mdaas:HeadquartersAddress"),
                "registered_address": response.get("mdaas:RegisteredAddress"),
                "fax_number": response.get("tr-org:hasHeadquartersFaxNumber"),
                "phone_number": response.get("tr-org:hasHeadquartersPhoneNumber"),
                "lei": response.get("tr-org:hasLEI"),
                "founded_date": response.get("hasLatestOrganizationFoundedDate"),
                "incorporated_in": self._query_geonames_location(response.get("isIncorporatedIn")),
                "domiciled_in": self._query_geonames_location(response.get("isDomiciledIn")),
                "url": response.get("hasURL"),
                "activity_status": response.get("hasActivityStatus"),
                "last_processed": datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S"),
            },
            "identifier": {
                "name": entity_name,
                "identifier": identifier,
                "identifier_type": identifier_type,
                "ticker": ticker,
            },
        }

    def _query_geonames_location(self, url: str | None) -> str | None:
        """Query the Geonames API for a human-readable location name.

        Args:
            url: The Geonames resource URL to resolve, or None.

        Returns:
            The location name string, or None if the URL is absent or the request fails.
        """
        if not url:
            return None

        response = self.api_clients.geonames_api.query_endpoint(url)
        if response.get("status_code") == self._HTTP_OK:
            return (
                response.get("data", {}).get("name")
                or response.get("data", {}).get("asciiName")
                or response.get("data", {}).get("countryName")
            )
        return None

    def _handle_failures(
        self, response: dict, entity_name: str, identifier: str, company_data: dict | None
    ) -> None:
        """Classify a failed company-info lookup and add it to the failure registry.

        Args:
            response: The API response dict.
            entity_name: The entity name.
            identifier: The identifier used in the request.
            company_data: The parsed company data (None when lookup returned nothing).
        """
        empty_data = company_data is None
        failure_type = CompanyInfoFailureClassifier.classify_from_response(
            response, empty_data=empty_data, category="company_info"
        )
        if not CompanyInfoFailureClassifier.is_retryable(failure_type):
            self.failure_registry.add(key=(entity_name, identifier), failure_type=failure_type)
