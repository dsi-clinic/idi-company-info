"""Retrieve company information from PermID entity lookup."""

# Standard library imports
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

# Third party imports
from idi_ftm2j_shared.failures import FailureRegistry
from idi_ftm2j_shared.storage import load_json

# Application imports
from idi_company_info.buffer import Buffer
from idi_company_info.failures import CompanyInfoFailureClassifier
from idi_company_info.retrieval import Retrieval
from idi_company_info.types import (
    BatchConfig,
    BatchStats,
    FilePaths,
    MergeStrategy,
    PermidResponse,
    ResultEntry,
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
    ) -> None:
        """Initialize the CompInfoRetrieval.

        Args:
            file_paths: The file paths.
            batch_config: The batch configuration.
            api_clients: The constructed API client instances.
            identifier_type: The identifier type (e.g. 'cik', 'cusip').
            failure_registry: Optional registry for permanent failures.
        """
        super().__init__(file_paths, batch_config, api_clients, failure_registry)
        self.identifier_type = identifier_type

    def retrieve(
        self,
        permid_data: dict[str, Any],
        num_existing_entities: int,
        batch_stats: BatchStats,
    ) -> None:
        """Retrieve company information for each PermID and write to the result buffer.

        result_file is keyed purely by permid_url; the input (name, identifier) linkage
        lives in permid_file. Each PermID is fetched once.

        Args:
            permid_data: The permid cache: {cache_key: {search, result: [permid_url, ...]}}.
            num_existing_entities: Count of records already present before this run.
            batch_stats: Accumulator for run-level statistics.
        """
        result_data = load_json(self.file_paths.result_file, return_type="dict")

        # Map permid_url -> [(Name, LocalID), ...] for failure registration only.
        failure_pairs = self._build_failure_pairs(permid_data)

        # Batch = unique permid URLs not already fetched, capped at batch_size.
        batch = self._build_company_info_batch(permid_data, result_data)
        self.logger.info("Generating company info for %d permid urls", len(batch))

        buffer = Buffer(
            file_path=self.file_paths.result_file,
            merge=MergeStrategy.COMPANY_INFO,
            buffer_size=self.batch_config.buffer_size,
        )

        for idx, permid_url in enumerate(batch, 1):
            self.logger.info("[%d/%d] Processing: %s", idx, len(batch), permid_url)
            company = self._retrieve_entity_company_info(
                permid_url, failure_pairs.get(permid_url, []), batch_stats
            )
            if company:
                buffer.add(data=company)
                batch_stats.total_entities += 1

        batch_stats.total_records = len(buffer.load_all()) - num_existing_entities

    @staticmethod
    def _build_failure_pairs(permid_data: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
        """Map each permid_url to the (Name, LocalID) input rows that resolved to it.

        Used only to key the failure registry by the domain tuple when an entity-lookup
        fails; never stored in the result file.
        """
        pairs: dict[str, list[tuple[str, str]]] = {}
        for entry in permid_data.values():
            pair = (entry["search"]["Name"], entry["search"]["LocalID"])
            for permid_url in entry["result"]:
                pairs.setdefault(permid_url, []).append(pair)
        return pairs

    def _build_company_info_batch(
        self, permid_data: dict[str, Any], result_data: dict[str, Any]
    ) -> list[str]:
        """Select the unique permid URLs to fetch, capped at batch_size API calls.

        Every entity-lookup call costs one request, so one company == one request. URLs
        already present in result_data are skipped (self-heal / backlog drain).

        Args:
            permid_data: The permid cache: {cache_key: {search, result: [permid_url, ...]}}.
            result_data: Existing result_file, keyed by permid_url.

        Returns:
            Unique permid URLs to fetch this run.
        """
        unique_urls = list(
            dict.fromkeys(
                permid_url
                for permid_value in permid_data.values()
                for permid_url in permid_value["result"]
                if permid_url not in result_data
            )
        )

        batch_permids = unique_urls[: self.batch_config.batch_size]
        remaining = len(unique_urls) - len(batch_permids)
        self.logger.info(
            "Will process %d permid urls, remaining: %d", len(batch_permids), remaining
        )
        return batch_permids

    def _retrieve_entity_company_info(
        self,
        permid_url: str,
        failure_pairs: list[tuple[str, str]],
        batch_stats: BatchStats,
    ) -> dict[str, ResultEntry]:
        """Fetch company info for a single PermID and build its result entry.

        Args:
            permid_url: URL to query to retrieve company info.
            failure_pairs: (Name, LocalID) rows that resolved to this PermID, used to key
                the failure registry if the lookup fails.
            batch_stats: Accumulator for run-level statistics.

        Returns:
            {permid_url: result_entry} on success, or {} on failure.
        """
        response = self.api_clients.entity_lookup.query_endpoint(permid_url=permid_url)

        # Handle the response from the entity-lookup API
        if response.get("status_code") != self._HTTP_OK:
            self.logger.error(
                "Company info query error for PermID %s: %s",
                permid_url,
                response.get("error"),
            )
            batch_stats.total_company_info_failed += 1
            self._handle_failures(response, failure_pairs)
            return {}

        data: PermidResponse = response.get("data")
        if not data:
            self.logger.warning("No company data found for PermID %s", permid_url)
            batch_stats.total_company_info_failed += 1
            self._handle_failures(response, failure_pairs)
            return {}

        company_data = self._parse_company_info(permid_url, data)
        batch_stats.total_company_info += 1
        return {permid_url: company_data}

    def _parse_company_info(self, permid_url: str, response: dict[str, Any]) -> ResultEntry:
        """Map a raw entity-lookup response to a pure permid_url-keyed result entry.

        Args:
            permid_url: The PermID URL used in the request.
            response: The ``data`` payload from the entity-lookup response.

        Returns:
            A flat company-info entry (API fields + last_processed). The permid_url is
            the dict key — there is no ``search``/``result`` envelope.
        """
        return {
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

    def _handle_failures(self, response: dict, failure_pairs: list[tuple[str, str]]) -> None:
        """Classify a failed company-info lookup and register the affected input rows.

        A PermID lookup failure affects every input row that resolved to it, so each
        (name, identifier) pair is registered under the same domain-tuple key convention
        used by the shared failure registry.

        Args:
            response: The API response dict.
            failure_pairs: (name, identifier) rows that resolved to the failed PermID.
        """
        if not self.failure_registry or not failure_pairs:
            return

        failure_type = CompanyInfoFailureClassifier.classify_from_response(
            response, empty_data=True, category="company_info"
        )
        if CompanyInfoFailureClassifier.is_retryable(failure_type):
            return

        for name, identifier in failure_pairs:
            self.failure_registry.add(key=(name, identifier), failure_type=failure_type)
