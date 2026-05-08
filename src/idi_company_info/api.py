"""Provides API utilities for use across the application."""

# Third party imports
from idi_ftm2j_shared.api import ApiClient


class LsegEntitySearch(ApiClient):
    """API client for the LSEG Entity Search API."""

    ENTITY_SEARCH_URL = "https://api-eit.refinitiv.com/permid/search"

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


class LsegRecordMatch(ApiClient):
    """API client for the LSEG Record Match API."""

    RECORD_MATCH_URL = "https://api-eit.refinitiv.com/permid/match"

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


class LSEGEntityLookup(ApiClient):
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


class GeonamesApi(ApiClient):
    """API client for the Geonames API."""

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
