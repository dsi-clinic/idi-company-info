"""Retrieve company information from PermID entity lookup."""

# Standard library imports
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

# Third party imports
from idi_ftm2j_shared.failures import FailureRegistry

# Application imports
from idi_company_info.buffer import Buffer
from idi_company_info.failures import CompanyInfoFailureClassifier
from idi_company_info.retrieval import Retrieval
from idi_company_info.types import (
    BatchConfig,
    BatchStats,
    CompanyInfo,
    FilePaths,
)

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
        batch = self._build_company_info_batch(entities_to_process, permid_data)
        total_lookups = sum(
            len(permids)
            for e in batch
            for item in permid_data.get(e, [])
            for permids in item.values()
            if permids
        )
        self.logger.info(
            "Generating company info for %d entities (%d entity-lookup requests)",
            len(batch),
            total_lookups,
        )

        # Create the buffer to store the company info
        buffer = Buffer(
            file_path=self.file_paths.result_file,
            buffer_size=self.batch_config.buffer_size,
            mode="list",
        )

        # Retrieve the company info for each entity in the batch
        for idx, entity_name in enumerate(batch, 1):
            self.logger.info(
                "[%d/%d] Processing: %s (%d)",
                idx,
                len(batch),
                entity_name,
                len(permid_data.get(entity_name, [])),
            )
            company = self._retrieve_entity_company_info(
                entity_name, permid_data.get(entity_name, []), batch_stats
            )
            buffer.add(data=company)
            batch_stats.total_entities += 1

        batch_stats.total_records = len(buffer.load_all()) - num_existing_entities

    def _build_company_info_batch(
        self, entities_to_process: list[str], permid_data: dict[str, Any]
    ) -> list[str]:
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
            # Count the number of PermIDs for the entity
            permid_count = sum(
                len(permids) for item in permid_data.get(entity, []) for permids in item.values()
            )
            if permid_count == 0:
                continue

            # If the entity has more PermIDs than the remaining request budget, stop adding entities
            if permid_count > remaining:
                self.logger.info(
                    "Stopping company info batch: adding '%s' (%d PermID(s)) would exceed the "
                    "%d-request limit (%d remaining)",
                    entity,
                    permid_count,
                    self.batch_config.batch_size,
                    remaining,
                )
                break
            batch.append(entity)
            remaining -= permid_count
        return batch

    def _retrieve_entity_company_info(
        self, entity_name: str, permid_data: list[dict[str, Any]], batch_stats: BatchStats
    ) -> list[dict[str, Any]]:
        """Fetch company info for every PermID associated with a single entity.

        Args:
            entity_name: The entity name.
            permid_data: List of {identifier: [permid_url, ...]} items for this entity.
            batch_stats: Accumulator for run-level statistics.

        Returns:
            List of company info dicts for this entity.
        """
        company_info = []
        for list_item in permid_data:
            for identifier, permid_list in list_item.items():
                for permid in permid_list:
                    response = self.api_clients.entity_lookup.query_endpoint(permid_url=permid)

                    # Handle the response from the entity-lookup API
                    if response.get("status_code") != self._HTTP_OK:
                        self.logger.error(
                            "Company info query error for entity %s with PermID %s: %s",
                            entity_name,
                            permid,
                            response.get("error"),
                        )
                        batch_stats.total_company_info_failed += 1
                        if self.failure_registry:
                            self._handle_failures(
                                response=response,
                                entity_name=entity_name,
                                identifier=identifier,
                                company_data=None,
                            )
                        continue

                    # Parse the company info from the response
                    data = response.get("data")
                    if not data:
                        self.logger.warning(
                            "No company data found for entity %s with PermID %s",
                            entity_name,
                            permid,
                        )
                        batch_stats.total_company_info_failed += 1
                        if self.failure_registry:
                            self._handle_failures(
                                response=response,
                                entity_name=entity_name,
                                identifier=identifier,
                                company_data=None,
                            )
                        continue

                    # Parse the company info from the response
                    company_data = asdict(
                        self._parse_company_info(
                            entity_name,
                            identifier,
                            self.identifier_type,
                            permid,
                            data,
                            ticker=self._raw_ticker_map.get(identifier),
                        )
                    )
                    company_info.append(company_data)
                    batch_stats.total_company_info += 1

        return company_info

    def _parse_company_info(
        self,
        entity_name: str,
        identifier: str,
        identifier_type: str,
        permid_id: str,
        response: dict[str, Any],
        ticker: str | None = None,
    ) -> CompanyInfo:
        """Map a raw entity-lookup response to a CompanyInfo dataclass.

        Args:
            entity_name: The entity name.
            identifier: The identifier (CIK, CUSIP, etc.).
            identifier_type: The identifier type string.
            permid_id: The PermID URL used in the request.
            response: The ``data`` payload from the entity-lookup response.
            ticker: Raw ticker symbol used during the Record Match search (CUSIP
                mode only).  Stored verbatim in the output; ``None`` for CIK mode.

        Returns:
            A populated CompanyInfo dataclass instance.
        """
        return CompanyInfo(
            investor_name=response.get("vcard:organization-name"),
            original_entity_name=entity_name,
            identifier=identifier,
            identifier_type=identifier_type,
            ticker=ticker,
            permid_id=response.get("tr-common:hasPermId") or permid_id.split("/")[-1],
            permid_url=response.get("@id") or permid_id,
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
            self.failure_registry.add(entity_name, identifier, reason=str(failure_type))
