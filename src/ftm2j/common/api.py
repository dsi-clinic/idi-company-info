"""Provides API utilities for use across the application."""

# Standard library imports
from abc import ABC, abstractmethod
from functools import cached_property
import logging

# Third party imports
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Application imports
from .logs import get_logger


class ApiClient(ABC):
    """Base class for API clients."""

    DEFAULT_MAX_RETRIES: int = 3
    REQUEST_TIMEOUT: tuple[int, int] = (10, 30)
    RETRY_BACKOFF_FACTOR: int = 2  # Wait 1, 2, 4 seconds between retries
    RETRY_STATUS_FORCELIST: list[int] = [429, 500, 502, 503, 504]
    USER_AGENT: str = "idi-ftm2j"

    def __init__(self, api_key: str, max_retries: int = DEFAULT_MAX_RETRIES, logger: logging.Logger = None):
        """
        Initialize the ApiClient.

        Args:
            api_key: The API key.
            max_retries: The maximum number of retries.
            logger: The logger to use.
        """
        self.api_key: str = api_key
        self.max_retries: int = max_retries if max_retries is not None else self.DEFAULT_MAX_RETRIES
        self.logger: logging.Logger = logger if logger is not None else get_logger(__name__)

    @cached_property
    def session(self) -> requests.Session:
        """
        Create a requests Session with retry strategy.

        Returns:
            Configured requests.Session with retry logic
        """
        session = requests.Session()

        # Configure retry strategy
        retry_strategy = Retry(
            total=self.max_retries,
            backoff_factor=self.RETRY_BACKOFF_FACTOR,  # Wait 1, 2, 4 seconds between retries
            status_forcelist=self.RETRY_STATUS_FORCELIST,
            allowed_methods=["GET"]
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        return session

    def get(self, url: str, params: dict = None, headers: dict = None) -> requests.Response:
        """Get a resource from the API.

        Args:
            params: The parameters to pass to the API.
            headers: The headers to pass to the API.

        Returns:
            The response from the API.
        """
        response = self.session.get(
            url,
            params=params,
            headers=headers,
            timeout=self.REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response


    def post(self, url: str, data: dict = None, headers: dict = None) -> requests.Response:
        """Post a resource to the API.

        Args:
            data: The data to post to the API.
            headers: The headers to post to the API.

        Returns:
            The response from the API.
        """
        response = self.session.post(
            url,
            headers=headers,
            data=data,
            timeout=self.REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response


    @abstractmethod
    def query_endpoint(self) -> requests.Response:
        """Query an endpoint."""
        ...


class LsegEntitySearch(ApiClient):
    """API client for the LSEG Entity Search API."""

    ENTITY_SEARCH_URL = "https://api-eit.refinitiv.com/permid/search"

    def __init__(self, api_key: str, max_retries: int = 3, logger: logging.Logger = None):
        """
        Initialize the LsegEntitySearch.

        Args:
            api_key: The API key.
            max_retries: The maximum number of retries.
            logger: The logger to use.
        """
        super().__init__(api_key=api_key, max_retries=max_retries, logger=logger)

    def query_endpoint(self, cik:str) -> dict:
        """Query the LSEG Entity Search API.

        Args:
            cik: The CIK to search for.

        Returns:
            The data from the API.
        """
        params = {
            "q": f"cik:{cik}",
            "format": "json",
        }

        headers = {
            "X-AG-Access-Token": self.api_key,
            "Accept": "application/json",
            "User-Agent": self.USER_AGENT,
        }

        try:
            response = self.get(url=self.ENTITY_SEARCH_URL, params=params, headers=headers)
            data = {"data": response.json()}
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error querying LSEG Entity Search API: {e}")
            data = {"error": str(e)}

        data.update({
            "status_code": response.status_code,
            "url": response.url
        })

        return data


class LsegRecordMatch(ApiClient):
    """API client for the LSEG Record Match API."""

    RECORD_MATCH_URL = "https://api-eit.refinitiv.com/permid/match"

    def __init__(self, api_key: str, max_retries: int = 3, logger: logging.Logger = None):
        """
        Initialize the LsegRecordMatch.

        Args:
            api_key: The API key.
            max_retries: The maximum number of retries.
            logger: The logger to use.
        """
        super().__init__(api_key=api_key, max_retries=max_retries, logger=logger)

    def query_endpoint(self, csv_data: str) -> dict:
        """Query the LSEG Record Match API.

        Args:
            csv_data: The CSV data to search for.
        """
        headers = {
            "accept": "application/json",
            "Content-Type": "text/plain",
            "x-ag-access-token": self.api_key,
            "x-openmatch-numberOfMatchesPerRecord": "1",
            "x-openmatch-dataType": "Organization",
            "User-Agent": self.USER_AGENT,
        }

        try:
            response = self.post(url=self.RECORD_MATCH_URL, data=csv_data, headers=headers)
            data = {"data": response.json()}
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error querying LSEG Record Match API: {e}")
            data = {"error": str(e)}

        data.update({
            "status_code": response.status_code,
            "url": response.url
        })

        return data
