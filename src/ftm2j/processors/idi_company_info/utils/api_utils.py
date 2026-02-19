"""API utilities for creating sessions and managing API configurations."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# API configuration constants
REQUEST_TIMEOUT = (10, 30)  # (connect timeout, read timeout)
RATE_LIMIT_DELAY = 1.0  # 1 request per second


def create_session() -> requests.Session:
    """
    Create a requests Session with retry strategy.

    Returns:
        Configured requests.Session with retry logic
    """
    session = requests.Session()

    # Configure retry strategy
    retry_strategy = Retry(
        total=3,
        backoff_factor=2,  # Wait 1, 2, 4 seconds between retries
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session
