"""Retrieve company information from PermID entity lookup."""

# Standard library imports
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

# Third party imports
from idi_ftm2j_shared.failures import FailureRegistry
from idi_ftm2j_shared.storage import load_json

# Application imports
from idi_company_info.buffer import Buffer, SectorCache
from idi_company_info.failures import CompanyInfoFailureClassifier
from idi_company_info.retrieval import Retrieval
from idi_company_info.types import (
    BatchConfig,
    BatchStats,
    FilePaths,
    MergeStrategy,
    PermidResponse,
    QuoteInfo,
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
        # Shared sector memo (in-memory + disk). Persistence is disabled when enrichment is
        # off, since no sectors are resolved in that case.
        self._sector_cache = SectorCache(
            file_paths.sector_cache_file if batch_config.enrich_metadata else ""
        )

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

        # Seed the sector memo from the shared on-disk cache (bypasses re-resolving)
        self._sector_cache.load()

        # Map permid_url -> [(Name, LocalID), ...] for failure registration only.
        failure_pairs = self._build_failure_pairs(permid_data)

        # Unique permid URLs not already fetched; the run is bounded by max_requests below.
        candidates = self._company_info_candidates(permid_data, result_data)
        max_requests = self.batch_config.max_requests
        self.logger.info(
            "Generating company info for up to %d permid urls (budget: %d PermID requests)",
            len(candidates),
            max_requests,
        )

        buffer = Buffer(
            file_path=self.file_paths.result_file,
            merge=MergeStrategy.COMPANY_INFO,
            buffer_size=self.batch_config.buffer_size,
        )

        processed = 0
        for idx, permid_url in enumerate(candidates, 1):
            # stop before starting a new company once the request budget is spent
            if self._permid_requests(batch_stats) >= max_requests:
                self.logger.info(
                    "Reached max_requests budget (%d PermID requests); stopping before "
                    "candidate %d of %d — %d left for a future run",
                    max_requests,
                    idx,
                    len(candidates),
                    len(candidates) - processed,
                )
                break

            self.logger.info("[%d/%d] Processing: %s", idx, len(candidates), permid_url)
            company = self._retrieve_entity_company_info(
                permid_url, failure_pairs.get(permid_url, []), batch_stats
            )
            processed += 1
            if company:
                buffer.add(data=company)
                batch_stats.total_entities += 1

        self.logger.info(
            "Company info done: %d/%d candidates processed, %d PermID requests used "
            "(budget %d), %d candidates remaining for a future run",
            processed,
            len(candidates),
            self._permid_requests(batch_stats),
            max_requests,
            len(candidates) - processed,
        )

        # Persist any newly discovered sectors; best effortas the cache is a non-critical
        try:
            self._sector_cache.flush()
        except Exception as error:  # noqa: BLE001
            self.logger.warning("Could not persist sector cache (non-fatal): %s", error)

        batch_stats.total_records = len(buffer.load_all()) - num_existing_entities

    @staticmethod
    def _permid_requests(batch_stats: BatchStats) -> int:
        """Count all PermID requests made this run against the shared daily quota.

        Record Match (PermID-retrieval stage), entity lookups (incl. failures), and sector/
        quote follow-ups all draw on the same per-key quota, so ``max_requests`` budgets the
        whole run, not just the enrichment stage. Record Match calls run before this stage,
        so they are already reflected here and shrink the enrichment headroom accordingly.
        Geonames is a separate API with its own quota and is intentionally excluded.
        """
        return (
            batch_stats.total_record_match_calls
            + batch_stats.total_company_info
            + batch_stats.total_company_info_failed
            + batch_stats.total_follow_up_calls
        )

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

    def _company_info_candidates(
        self, permid_data: dict[str, Any], result_data: dict[str, Any]
    ) -> list[str]:
        """Return the unique permid URLs eligible to fetch this run, in order.

        URLs already present in result_data are skipped (self-heal / backlog drain). This
        no longer caps the list — the run is bounded by the ``max_requests`` PermID-request
        budget enforced in :meth:`retrieve`, which counts the actual entity-lookup and
        follow-up calls each company costs rather than assuming a fixed per-company count.

        Args:
            permid_data: The permid cache: {cache_key: {search, result: [permid_url, ...]}}.
            result_data: Existing result_file, keyed by permid_url.

        Returns:
            Unique permid URLs not yet fetched, in first-seen order.
        """
        return list(
            dict.fromkeys(
                permid_url
                for permid_value in permid_data.values()
                for permid_url in permid_value["result"]
                if permid_url not in result_data
            )
        )

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

        company_data = self._parse_company_info(permid_url, data, batch_stats)
        batch_stats.total_company_info += 1
        return {permid_url: company_data}

    def _parse_company_info(
        self, permid_url: str, response: dict[str, Any], batch_stats: BatchStats
    ) -> ResultEntry:
        """Map a raw entity-lookup response to a pure permid_url-keyed result entry.

        Sectors arrive as linked permid.org URLs (resolved via follow-up entity-lookup
        calls into a label + comment each); the primary quote is one linked URL whose
        record carries the ticker and exchange identifiers inline. When
        ``batch_config.enrich_metadata`` is off these ten enrichment fields stay None and
        cost no extra calls. Worst case: 3 sector + 1 quote follow-ups.

        Note: organization-level link keys are read bare (``hasPrimaryBusinessSector``,
        ``hasOrganizationPrimaryQuote``) per the JSON-LD context — same convention as
        ``hasActivityStatus``/``hasURL`` above.

        Args:
            permid_url: The PermID URL used in the request.
            response: The ``data`` payload from the entity-lookup response.
            batch_stats: Accumulator for run-level statistics; follow-up calls are counted.

        Returns:
            A flat company-info entry (API fields + last_processed). The permid_url is
            the dict key — there is no ``search``/``result`` envelope.
        """
        quote_info = self._resolve_quote(response.get("hasOrganizationPrimaryQuote"), batch_stats)

        bus_label, bus_comment = self._resolve_sector_name(
            response.get("hasPrimaryBusinessSector"), batch_stats
        )
        eco_label, eco_comment = self._resolve_sector_name(
            response.get("hasPrimaryEconomicSector"), batch_stats
        )
        ind_label, ind_comment = self._resolve_sector_name(
            response.get("hasPrimaryIndustryGroup"), batch_stats
        )

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
            "primary_business_sector_label": bus_label,
            "primary_economic_sector_label": eco_label,
            "primary_industry_group_label": ind_label,
            "primary_business_sector_comment": bus_comment,
            "primary_economic_sector_comment": eco_comment,
            "primary_industry_group_comment": ind_comment,
            "ticker": quote_info.ticker,
            "exchange": quote_info.exchange,
            "exchange_code": quote_info.exchange_code,
            "ric": quote_info.ric,
            "last_processed": datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S"),
        }

    def _resolve_permid_link(
        self, url: str | None, batch_stats: BatchStats
    ) -> dict[str, Any] | None:
        """Resolve a linked permid.org URL via a follow-up entity-lookup call.

        Generalizes the follow-up-query pattern used by ``_query_geonames_location`` for
        the entity-lookup client. Each attempted call is counted against
        ``batch_stats.total_follow_up_calls`` so the added daily-call cost is observable.
        No-op (and no call) when enrichment is disabled or the URL is absent.

        Args:
            url: The linked permid.org URL to resolve, or None.
            batch_stats: Accumulator for run-level statistics.

        Returns:
            The ``data`` payload of the linked record, or None on absent URL / non-200.
        """
        if not self.batch_config.enrich_metadata or not url:
            return None

        batch_stats.total_follow_up_calls += 1
        response = self.api_clients.entity_lookup.query_endpoint(permid_url=url)
        if response.get("status_code") != self._HTTP_OK:
            self.logger.warning(
                "Follow-up lookup failed for linked PermID %s: %s",
                url,
                response.get("error"),
            )
            return None
        return response.get("data")

    def _resolve_quote(self, url: str | None, batch_stats: BatchStats) -> QuoteInfo:
        """Resolve the primary-quote permid URL to ``QuoteInfo``.

        A ``tr-fin:Quote`` record carries the ticker and the exchange identifiers inline,
        so a single follow-up call yields all of them — no further lookup is needed.

        Args:
            url: The primary-quote permid URL, or None.
            batch_stats: Accumulator for run-level statistics.

        Returns:
            A ``QuoteInfo`` (ticker, exchange, exchange_code, ric); each field is None
            when the quote is unresolved or the field is absent.
        """
        data = self._resolve_permid_link(url, batch_stats)
        if not data:
            return QuoteInfo()

        ticker = data.get("tr-fin:hasExchangeTicker")
        exchange = data.get("tr-fin:hasMic")
        exchange_code = data.get("tr-fin:hasExchangeCode")
        ric = data.get("tr-fin:hasRic")

        return QuoteInfo(ticker=ticker, exchange=exchange, exchange_code=exchange_code, ric=ric)

    def _resolve_sector_name(
        self, url: str | None, batch_stats: BatchStats
    ) -> tuple[str | None, str | None]:
        """Resolve a linked sector/industry permid URL to its human-readable label.

        Memoized via ``self._sector_cache`` (a shared :class:`SectorCache`): the same sector
        URL resolves to the same (label, comment) for every company, so a cache hit returns
        the stored value without an API call (and without incrementing
        ``total_follow_up_calls``). The memo is seeded from disk at the start of the run and
        flushed back, so hits span runs and sibling sources — not just the current run.
        Distinct sector types (business / economic / industry-group) carry distinct URLs, so
        a single URL-keyed cache is safe across all three.

        Args:
            url: The sector/industry permid URL, or None.
            batch_stats: Accumulator for run-level statistics.

        Returns:
            The sector label and comment, or None, None if unresolved.
        """
        if url and url in self._sector_cache:
            batch_stats.total_sector_cache_hits += 1
            return self._sector_cache.get(url)

        data = self._resolve_permid_link(url, batch_stats)
        if not data:
            return None, None

        label = self._scalar_text(data.get("prefLabel") or data.get("rdfs:label"), url)
        comment = self._scalar_text(data.get("rdfs:comment"), url)

        resolved = (label, comment)
        if url:
            self._sector_cache.set(url, resolved)
        return resolved

    def _scalar_text(self, value: object, url: str | None) -> str | None:
        """Coerce a sector label/comment to a single string.

        PermID occasionally returns ``prefLabel`` as a list of variant spellings of the
        same label (observed: ``["Freight&Logistics Services", "Freight & Logistics Services"]``)
        rather than a scalar. A list reaching ``to_parquet`` raises
        ``ArrowTypeError: Expected bytes, got a 'list'``, so the first non-empty value is returned.
        """
        if isinstance(value, list):
            if len(value) > 1:
                self.logger.warning(
                    "Multi-value label for %s: %s",
                    url or "sector",
                    ", ".join(str(item) for item in value),
                )
            value = next((item for item in value if item), None)
        return value or None

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
