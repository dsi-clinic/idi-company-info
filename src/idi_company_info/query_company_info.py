#!/usr/bin/env python3
"""
Job #3: Query PermID API to retrieve detailed company information.

Reads PermID data from JSON, queries the PermID API for each PermID,
and saves detailed company information as JSON.
Supports batch processing with rate limiting and retry logic.
"""

import argparse
import json
import logging
import pathlib
import time
from datetime import datetime
from typing import Any

import requests

from .utils import (
    create_session,
    REQUEST_TIMEOUT,
    RATE_LIMIT_DELAY,
    load_batch_tracking,
    save_batch_tracking,
    get_unprocessed_investors,
    load_existing_results,
    save_results,
)

logging.getLogger().setLevel(logging.INFO)
logging.basicConfig(
    format='%(asctime)s,%(msecs)d %(module)s:%(lineno)d %(levelname)s %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S',
    level=logging.INFO
)

# API configuration
PERMID_BASE_URL = "https://permid.org"
GEONAMES_API_URL = "http://api.geonames.org/getJSON"

# Field mapping from API response to our output format
FIELD_MAPPING = {
    "vcard:organization-name": "investor_name",
    "tr-common:hasPermId": "permid",
    "mdaas:HeadquartersAddress": "hq_address",
    "mdaas:RegisteredAddress": "registered_address",
    "tr-org:hasHeadquartersFaxNumber": "fax_number",
    "tr-org:hasHeadquartersPhoneNumber": "phone_number",
    "tr-org:hasLEI": "lei",
    "hasLatestOrganizationFoundedDate": "founded_date",
    "isIncorporatedIn": "incorporated_in",
    "isDomiciledIn": "domiciled_in",
    "hasURL": "url",
    "hasActivityStatus": "activity_status",
    "@id": "id"
}

# Fields that contain URLs that need to be resolved
URL_FIELDS = {
    "isIncorporatedIn",
    "isDomiciledIn"
}


def get_args():
    parser = argparse.ArgumentParser(
        description="Query PermID API to retrieve detailed company information"
    )
    parser.add_argument(
        "--api-key",
        type=str,
        required=True,
        help="PermID API access token"
    )
    parser.add_argument(
        "--input-file",
        type=pathlib.Path,
        required=True,
        help="Path to input JSON file with PermID data from Job 2"
    )
    parser.add_argument(
        "--output-file",
        type=pathlib.Path,
        required=True,
        help="Path to save JSON query results to"
    )
    parser.add_argument(
        "--batch-file",
        type=pathlib.Path,
        required=True,
        help="Path to batch tracking file"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Number of investors to process in this batch"
    )
    parser.add_argument(
        "--geonames-user",
        type=str,
        required=True,
        help="Geonames API username for resolving location data"
    )
    args = parser.parse_args()
    return args

def load_data(input_file, batch_file, output_file, batch_size):
    # Load PermID data
    permid_data = load_permid_data(input_file)

    # Load batch tracking
    batch_tracking = load_batch_tracking(batch_file)

    # Load existing results (company_info results are a list, not a dict)
    existing_results = load_existing_results(output_file, default_type="list")

    # Get unprocessed investors
    unprocessed_investors = get_unprocessed_investors(permid_data, batch_tracking)

    if not unprocessed_investors:
        logging.info("All investors have been processed!")
        return

    if batch_size > len(unprocessed_investors):
        logging.warning(
            f"Batch size ({batch_size}) is larger than remaining investors "
            f"({len(unprocessed_investors)}). Processing all remaining investors."
        )

    return permid_data, existing_results, unprocessed_investors

def query_geonames_location(
    session: requests.Session,
    url: str,
    geonames_user: str
) -> dict[str, Any]:
    """
    Query Geonames API to resolve location information.

    Args:
        session: requests Session object
        url: Geonames URL (e.g., http://sws.geonames.org/6252001/)
        geonames_user: Geonames API username

    Returns:
        Dictionary with location information
    """
    # Extract geoname ID from URL (e.g., http://sws.geonames.org/6252001/)
    geoname_id = url.rstrip('/').split('/')[-1]

    # Query Geonames API with credentials (per https://www.geonames.org/export/web-services.html)
    params = {
        "geonameId": geoname_id,
        "username": geonames_user
    }
    response = session.get(GEONAMES_API_URL, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()

    return data.get("name") or data.get("asciiName") or data.get("countryName")

def extract_permid_fields(
    data: dict[str, Any],
    session: requests.Session,
    geonames_user: str,
    resolve_urls: bool
) -> dict[str, Any]:
    """
    Extract and map fields from PermID API response.

    Args:
        data: Raw JSON response from PermID API
        session: requests Session object
        api_key: API access token
        geonames_user: Geonames API username
        resolve_urls: Whether to resolve URL fields

    Returns:
        Dictionary with mapped company information
    """
    result = {}

    for api_field, output_field in FIELD_MAPPING.items():
        if api_field in data:
            value = data[api_field]

            # Handle URL fields
            if api_field in URL_FIELDS and resolve_urls and value:
                logging.info(f"    Resolving URL field: {api_field} -> {value}")
                result[output_field] = query_geonames_location(session, value, geonames_user)
            else:
                result[output_field] = value

    # Extract PermID from @id if not present
    if "permid" not in result and "@id" in data:
        permid_from_id = data["@id"].replace(f"{PERMID_BASE_URL}/", "")
        result["permid"] = permid_from_id

    return result

def query_permid_entity(
    session: requests.Session,
    permid_url: str,
    api_key: str,
    geonames_user: str,
    resolve_urls: bool = True
) -> dict[str, Any] | None:
    """
    Query PermID API and return company information.

    Args:
        session: requests Session object
        permid_url: PermID URL (e.g., "https://permid.org/1-5000051854")
        api_key: API access token
        geonames_user: Geonames API username
        resolve_urls: Whether to resolve URL fields

    Returns:
        Dictionary with company information or None if not found
    """
    headers = {
        "X-AG-Access-Token": api_key,
        "Accept": "application/ld+json",
    }
    params = {"format": "json-ld"}

    try:
        response = session.get(
            permid_url,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()

        data = response.json()

        # Extract and map fields
        return extract_permid_fields(data, session, geonames_user, resolve_urls)

    except requests.exceptions.RequestException as e:
        logging.error(f"Error querying PermID {permid_url}: {e}")
        return None

def load_permid_data(input_file: pathlib.Path) -> dict[str, list[dict[str, list[str] | str]]]:
    """Load PermID data from JSON file."""
    logging.info(f"Loading PermID data from: {input_file}")
    with open(input_file) as f:
        data = json.load(f)
    logging.info(f"Loaded {len(data)} investors with PermID data")
    return data

def process_investor(
    session: requests.Session,
    investor_name: str,
    cik_permid_pairs: list[dict[str, list[str] | str]],
    api_key: str,
    geonames_user: str,
    stats: dict
) -> list[dict[str, Any]]:
    """
    Process a single investor by querying all their PermIDs.

    Args:
        session: requests Session object
        investor_name: Name of the investor
        cik_permid_pairs: List of dicts with CIKs (list) and PermID pairs
        api_key: API access token
        geonames_user: Geonames API username
        stats: Statistics dictionary to update

    Returns:
        List of company information dictionaries
    """
    stats["total_investors"] += 1

    if len(cik_permid_pairs) > 1:
        stats["investors_with_multiple_permids"] += 1
        permid_list = [pair["permid"] for pair in cik_permid_pairs]
        logging.warning(f"  Multiple PermIDs for {investor_name}: {permid_list}")

    # Query each PermID for this investor
    investor_results = []
    for pair in cik_permid_pairs:
        ciks = pair["ciks"]  # a list of CIKs
        permid_url = pair["permid"]
        stats["total_permids_queried"] += 1

        # Log all CIKs that map to this PermID
        ciks_str = ", ".join(ciks)
        logging.info(f"  Querying PermID: {permid_url} (CIKs: {ciks_str})")
        company_info = query_permid_entity(session, permid_url, api_key, geonames_user)

        if company_info:
            stats["successful_queries"] += 1
            # Add the original investor name and all CIKs for reference
            company_info["original_investor_name"] = investor_name
            company_info["ciks"] = ciks  # Store as list
            investor_results.append(company_info)
            logging.info(f"  Successfully retrieved info for PermID {permid_url}")
        else:
            stats["failed_queries"] += 1
            investor_results.append(None)
            logging.warning(f"  Failed to retrieve info for PermID {permid_url}")

        # Rate limiting: wait 1 second between requests
        time.sleep(RATE_LIMIT_DELAY)

    return investor_results

def process_batch(
    session: requests.Session,
    permid_data: dict[str, list[dict[str, list[str] | str]]],
    investors_to_process: list[str],
    batch_size: int,
    api_key: str,
    geonames_user: str
) -> tuple[list[dict[str, Any]], list[str], dict]:
    """
    Process a batch of investors.

    Returns:
        Tuple of (results_list, processed_investors_list, stats_dict)
    """
    results = []
    processed_investors = []

    stats = {
        "total_investors": 0,
        "total_permids_queried": 0,
        "successful_queries": 0,
        "failed_queries": 0,
        "investors_with_multiple_permids": 0,
        "url_fields_resolved": 0,
        "url_fields_failed": 0
    }

    batch = investors_to_process[:batch_size]
    logging.info(f"Processing batch of {len(batch)} investors")

    for idx, investor_name in enumerate(batch, 1):
        cik_permid_pairs = permid_data[investor_name]
        # Filter out pairs where PermID is None
        cik_permid_pairs = [pair for pair in cik_permid_pairs if pair.get("permid") is not None]

        if not cik_permid_pairs:
            logging.warning(f"[{idx}/{len(batch)}] Skipping {investor_name}: No valid PermIDs")
            investor_results = [ None ]

        else:
            logging.info(
                f"[{idx}/{len(batch)}] Processing: {investor_name} ({len(cik_permid_pairs)} PermID(s))"
            )
            # Process this investor
            investor_results = process_investor(
                session, investor_name, cik_permid_pairs, api_key, geonames_user, stats
            )

        # Store all results for this investor
        if investor_results:
            results.extend(investor_results)

        processed_investors.append(investor_name)

    return results, processed_investors, stats

def finalize_batch(
    output_file: pathlib.Path,
    batch_file: pathlib.Path,
    existing_results: list[dict[str, Any]],
    batch_results: list[dict[str, Any]],
    processed_investors: list[str],
    batch_stats: dict
) -> list[dict[str, Any]]:
    """
    Finalize batch by saving results and updating tracking.

    Args:
        output_file: Path to save results
        batch_file: Path to batch tracking file
        existing_results: Previously saved results
        batch_results: Results from this batch
        processed_investors: List of investor names processed
        batch_stats: Statistics from this batch

    Returns:
        Combined results list
    """
    # Merge with existing results
    all_results = existing_results + batch_results

    # Save results
    save_results(output_file, all_results)

    # Update batch tracking
    batch_timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    batch_tracking_entry = {
        "processed_investors": processed_investors,
        "batch_size": len(processed_investors),
        "stats": batch_stats
    }

    # Load current tracking and add new entry
    batch_tracking = load_batch_tracking(batch_file)
    batch_tracking[batch_timestamp] = batch_tracking_entry
    save_batch_tracking(batch_file, batch_tracking)

    return all_results

def print_stats(all_results: list[dict[str, Any]], batch_stats: dict):
    """Print statistics about the processing."""
    logging.info("=" * 60)
    logging.info("BATCH STATISTICS")
    logging.info("=" * 60)
    logging.info(f"Investors processed: {batch_stats['total_investors']}")
    logging.info(f"PermIDs queried: {batch_stats['total_permids_queried']}")
    logging.info(f"Successful queries: {batch_stats['successful_queries']}")
    logging.info(f"Failed queries: {batch_stats['failed_queries']}")
    logging.info(
        f"Investors with multiple PermIDs: {batch_stats['investors_with_multiple_permids']}"
    )

    logging.info("=" * 60)
    logging.info("CUMULATIVE STATISTICS")
    logging.info("=" * 60)
    logging.info(f"Total company records: {len(all_results)}")

    # Count unique investors
    unique_investors = set(r.get("original_investor_name") for r in all_results if r)
    logging.info(f"Unique investors: {len(unique_investors)}")

    logging.info("=" * 60)

def main():
    """Main function to query PermID API and process company information."""
    args = get_args()

    # Log arguments (except API key)
    for key, value in args.__dict__.items():
        if key != "api_key":
            logging.info(f"{key}: {value}")

    # Create output file's parent directory if it doesn't exist
    args.output_file.parent.mkdir(parents=True, exist_ok=True)

    # Load PermID data
    permid_data, existing_results, unprocessed_investors = load_data(
        args.input_file,
        args.batch_file,
        args.output_file,
        args.batch_size
    )

    # Create session
    session = create_session()

    # Process batch
    batch_results, processed_investors, batch_stats = process_batch(
        session,
        permid_data,
        unprocessed_investors,
        args.batch_size,
        args.api_key,
        args.geonames_user
    )

    # Finalize: save results and update tracking
    all_results = finalize_batch(
        args.output_file,
        args.batch_file,
        existing_results,
        batch_results,
        processed_investors,
        batch_stats
    )

    # Print statistics
    print_stats(all_results, batch_stats)


if __name__ == "__main__":
    main()
