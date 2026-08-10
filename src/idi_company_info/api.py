"""Provides API utilities for use across the application."""

# Third party imports
from idi_ftm2j_shared.api import ApiClient


class QuotaSafeApiClient(ApiClient):
    """Base for clients that must never sleep inside urllib3 on a 429.

    Narrows the inherited retry forcelist to 5xx only, deliberately excluding 429. The
    shared ``ApiClient`` builds its session with ``respect_retry_after_header=True`` and
    leaves ``retry_after_max`` at urllib3's default of 21600s, so a 429 carrying a long
    ``Retry-After`` puts urllib3 into a bare ``time.sleep()`` of up to 6 hours per
    attempt — inside the adapter and below ``requests``, where nothing on our logging
    path runs. With ``total=3`` that is up to ~18h on a single request, and the task sits
    in ``RUNNING`` looking frozen rather than failing (GitHub issue #34).

    Excluding 429 makes a rejection surface immediately as a classifiable
    ``RATE_LIMIT`` failure that the retrieval stages can act on. Transient 5xx codes are
    still retried, so nothing legitimately retryable is lost.
    """

    # 5xx only — see the class docstring for why 429 is deliberately absent.
    RETRY_STATUS_FORCELIST: list[int] = [500, 502, 503, 504]


class LsegApiClient(QuotaSafeApiClient):
    """Base for the LSEG PermID clients, which all draw on one shared daily quota.

    Beyond the sleep exposure that :class:`QuotaSafeApiClient` removes, retrying a
    PermID 429 is pointless on its own terms: it means the shared daily request quota is
    spent, not that we hit a transient per-second throttle (call spacing is already
    handled by ``rate_limit()``), so no retry can succeed until the quota resets.
    """


class LsegEntitySearch(LsegApiClient):
    """API client for the LSEG Entity Search API."""

    ENTITY_SEARCH_URL = "https://api-eit.refinitiv.com/permid/search"
    USER_AGENT: str = "idi-company-info"

    def query_endpoint(self, params: dict) -> dict:
        """Query the LSEG Entity Search API.

        Args:
            params: The parameters to pass to the API.

        Returns:
            The data from the API.
        """
        headers = {
            "X-AG-Access-Token": self.api_key,
            "Accept": "application/json",
            "User-Agent": self.USER_AGENT,
        }

        self.rate_limit()
        return self._query_with_error_handling(
            url=self.ENTITY_SEARCH_URL, params=params, headers=headers, method="get"
        )


class LsegRecordMatch(LsegApiClient):
    """API client for the LSEG Record Match API."""

    RECORD_MATCH_URL = "https://api-eit.refinitiv.com/permid/match"
    USER_AGENT: str = "idi-company-info"

    def query_endpoint(self, csv_data: str) -> dict:
        """Query the LSEG Record Match API.

        Args:
            csv_data: The CSV data to search for.

        Returns:
            The data from the API.
        """
        headers = {
            "accept": "application/json",
            "Content-Type": "text/plain",
            "x-ag-access-token": self.api_key,
            "x-openmatch-numberOfMatchesPerRecord": "1",
            "x-openmatch-dataType": "Organization",
            "User-Agent": self.USER_AGENT,
        }

        self.rate_limit()
        return self._query_with_error_handling(
            url=self.RECORD_MATCH_URL, data=csv_data, headers=headers, method="post"
        )


class LSEGEntityLookup(LsegApiClient):
    """API client for the LSEG Entity Lookup API."""

    def query_endpoint(self, permid_url: str) -> dict:
        """Query the LSEG Entity Lookup API.

        Args:
            permid_url: The PermID URL to lookup.

        Returns:
            The data from the API.
        """
        headers = {
            "X-AG-Access-Token": self.api_key,
            "Accept": "application/ld+json",
        }
        params = {"format": "json-ld"}

        self.rate_limit()
        return self._query_with_error_handling(
            url=permid_url, params=params, headers=headers, method="get"
        )


class GeonamesApi(QuotaSafeApiClient):
    """API client for the Geonames API.

    Geonames is a separate API with its own quota — usage is metered in credits, capped
    daily and hourly (https://www.geonames.org/export/credits.html) — and its documented
    error codes do not promise a 429 when a cap is reached
    (https://www.geonames.org/export/webservice-exception.html). Either way, a 429 here
    carries the same unbounded-``Retry-After``-sleep exposure as the LSEG clients, so it
    is excluded from the retry forcelist too: a location field is optional enrichment and
    is never worth a silent multi-hour stall. Unlike a PermID quota rejection, it does not
    abort the run — the caller logs it and leaves the field null.
    """

    GEONAMES_API_URL = "http://api.geonames.org/getJSON"

    def __init__(self, api_key: str, geonames_user: str, rate_limit: float | None = None) -> None:
        """Initialize the GeonamesApi.

        Args:
            api_key: The API key.
            geonames_user: The Geonames user.
            rate_limit: The rate limit.
        """
        super().__init__(api_key=api_key, rate_limit=rate_limit)
        self.geonames_user = geonames_user

    def query_endpoint(self, geoname_url: str) -> dict:
        """Query the Geonames API.

        Args:
            geoname_url: The Geonames URL to look up (e.g. http://sws.geonames.org/6252001/).
        """
        # Extract geoname ID from URL (e.g., http://sws.geonames.org/6252001/)
        geoname_id = geoname_url.rstrip("/").split("/")[-1]

        # Query Geonames API with credentials (per https://www.geonames.org/export/web-services.html)
        params = {"geonameId": geoname_id, "username": self.geonames_user}

        self.rate_limit()
        return self._query_with_error_handling(
            url=self.GEONAMES_API_URL, params=params, method="get"
        )
