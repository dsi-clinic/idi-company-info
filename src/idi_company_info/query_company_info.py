#!/usr/bin/env python3
"""
Job #3: Query PermID API to retrieve detailed company information.

Reads PermID data from JSON, queries the PermID API for each PermID,
and saves detailed company information as JSON.
Supports batch processing with rate limiting and retry logic.
"""

import argparse
import json
import pathlib
import time
from datetime import datetime, timedelta
from typing import Any

import requests

from .utils import (
    get_logger,
    create_session,
    REQUEST_TIMEOUT,
    RATE_LIMIT_DELAY,
    load_batch_tracking,
    save_batch_tracking,
    get_unprocessed_investors,
    load_existing_results,
    save_results,
)

logger = get_logger(__name__)

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
    parser.add_argument(
        "--threshold-days",
        type=int,
        default=None,
        help="Re-query investors not updated in the last N days (default: None, no re-querying)"
    )
    args = parser.parse_args()
    return args

def get_stale_investors(
    existing_results: list[dict[str, Any]],
    threshold_days: int
) -> set[str]:
    """
    Identify investors whose data hasn't been updated within threshold days.

    Args:
        existing_results: List of company info records
        threshold_days: Number of days after which data is considered stale

    Returns:
        Set of investor names that need re-processing
    """
    if threshold_days is None:
        return set()

    threshold_date = datetime.now() - timedelta(days=threshold_days)
    stale_investors = set()

    # Group results by original_investor_name
    investor_records = {}
    for record in existing_results:
        if not record:  # Skip None records
            continue

        investor_name = record.get("original_investor_name")
        if not investor_name:
            continue

        if investor_name not in investor_records:
            investor_records[investor_name] = []
        investor_records[investor_name].append(record)

    # Check each investor's most recent update
    for investor_name, records in investor_records.items():
        # Find the most recent last_processed timestamp for this investor
        most_recent = None

        for record in records:
            last_processed_str = record.get("last_processed")
            if not last_processed_str:
                # No timestamp means old data, needs re-processing
                most_recent = None
                break

            try:
                last_processed = datetime.fromisoformat(last_processed_str)
                if most_recent is None or last_processed > most_recent:
                    most_recent = last_processed
            except (ValueError, TypeError):
                # Invalid timestamp, treat as stale
                logger.warning(f"Invalid timestamp for {investor_name}: {last_processed_str}")
                most_recent = None
                break

        # If no valid timestamp or older than threshold, mark as stale
        if most_recent is None or most_recent < threshold_date:
            stale_investors.add(investor_name)
            if most_recent:
                days_old = (datetime.now() - most_recent).days
                logger.info(f"  Marking {investor_name} as stale ({days_old} days old)")
            else:
                logger.info(f"  Marking {investor_name} as stale (no timestamp)")

    return stale_investors


def remove_stale_records(
    existing_results: list[dict[str, Any]],
    stale_investors: set[str]
) -> list[dict[str, Any]]:
    """
    Remove records for stale investors so they can be re-processed.

    Args:
        existing_results: List of company info records
        stale_investors: Set of investor names to remove

    Returns:
        Filtered list without stale investor records
    """
    if not stale_investors:
        return existing_results

    filtered_results = [
        record for record in existing_results
        if record and record.get("original_investor_name") not in stale_investors
    ]

    removed_count = len(existing_results) - len(filtered_results)
    logger.info(f"Removed {removed_count} stale record(s) for re-processing")

    return filtered_results


def _remove_stale_from_batch_tracking(
    batch_tracking: dict,
    stale_investors: set[str]
) -> None:
    """
    Remove stale investors from batch tracking so they're treated as unprocessed.

    Args:
        batch_tracking: Batch tracking dictionary to modify in-place
        stale_investors: Set of investor names to remove
    """
    for batch_data in batch_tracking.values():
        processed_list = batch_data.get("processed_investors", [])
        batch_data["processed_investors"] = [
            inv for inv in processed_list if inv not in stale_investors
        ]


def _handle_stale_investors(
    existing_results: list[dict[str, Any]],
    batch_tracking: dict,
    threshold_days: int | None
) -> tuple[list[dict[str, Any]], set[str]]:
    """
    Identify and process stale investors based on threshold.

    Args:
        existing_results: List of company info records
        batch_tracking: Batch tracking dictionary
        threshold_days: Number of days after which data is considered stale

    Returns:
        Tuple of (filtered_results, stale_investors_set)
    """
    if threshold_days is None:
        return existing_results, set()

    logger.info(f"Checking for investors not updated in last {threshold_days} days")
    stale_investors = get_stale_investors(existing_results, threshold_days)

    if not stale_investors:
        return existing_results, set()

    logger.info(f"Found {len(stale_investors)} stale investor(s) to re-process")

    # Remove stale investor records so they can be re-processed
    filtered_results = remove_stale_records(existing_results, stale_investors)

    # Remove stale investors from batch tracking
    _remove_stale_from_batch_tracking(batch_tracking, stale_investors)

    return filtered_results, stale_investors


def _get_investors_to_process(
    permid_data: dict,
    batch_tracking: dict,
    stale_investors: set[str]
) -> list[str] | None:
    """
    Get combined list of unprocessed and stale investors.

    Args:
        permid_data: PermID data dictionary
        batch_tracking: Batch tracking dictionary
        stale_investors: Set of stale investor names

    Returns:
        List of investor names to process, or None if all are up to date
    """
    # Get unprocessed investors (never processed)
    unprocessed_investors = get_unprocessed_investors(permid_data, batch_tracking)

    # Combine stale + unprocessed investors for processing
    investors_to_process = list(set(unprocessed_investors) | stale_investors)

    if not investors_to_process:
        logger.info("All investors are up to date!")
        return None

    logger.info(
        f"Investors to process: {len(investors_to_process)} "
        f"(unprocessed: {len(unprocessed_investors)}, stale: {len(stale_investors)})"
    )

    return investors_to_process


def load_data(input_file, batch_file, output_file, batch_size, threshold_days=None):
    """
    Load and prepare data for batch processing.

    Args:
        input_file: Path to PermID data file
        batch_file: Path to batch tracking file
        output_file: Path to existing results file
        batch_size: Number of investors to process
        threshold_days: Optional threshold for re-querying stale data

    Returns:
        Tuple of (permid_data, existing_results, investors_to_process) or None
    """
    # Load all input data
    permid_data = load_permid_data(input_file)
    batch_tracking = load_batch_tracking(batch_file)
    existing_results = load_existing_results(output_file, default_type="list")

    # Handle stale investors if threshold provided
    existing_results, stale_investors = _handle_stale_investors(
        existing_results, batch_tracking, threshold_days
    )

    # Get investors to process (unprocessed + stale)
    investors_to_process = _get_investors_to_process(
        permid_data, batch_tracking, stale_investors
    )

    if investors_to_process is None:
        return None

    # Validate batch size
    if batch_size > len(investors_to_process):
        logger.warning(
            f"Batch size ({batch_size}) is larger than investors to process "
            f"({len(investors_to_process)}). Processing all."
        )

    return permid_data, existing_results, investors_to_process

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
                logger.info(f"    Resolving URL field: {api_field} -> {value}")
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
        logger.error(f"Error querying PermID {permid_url}: {e}")
        return None

def detect_input_format(data: dict) -> str:
    """
    Detect whether input data is in CIK or Record format.

    Args:
        data: Loaded JSON data

    Returns:
        "cik" or "record"
    """
    if not data:
        raise ValueError("Empty input data")

    # Get first value to inspect structure
    first_value = next(iter(data.values()))

    # CIK format: value is a list of dicts with "ciks" and "permid"
    if isinstance(first_value, list):
        return "cik"

    # Record format: value is a dict with "ticker", "permid", etc.
    if isinstance(first_value, dict) and "ticker" in first_value:
        return "record"

    raise ValueError(f"Unknown input format. Expected CIK or Record format.")


def normalize_to_unified_format(
    data: dict,
    format_type: str
) -> dict[str, list[dict[str, Any]]]:
    """
    Normalize input data to unified internal format.

    Unified format structure:
    {
      "entity_name": [
        {
          "permid": "url",
          "ciks": ["123"] or None,
          "ticker": "AAPL" or None,
          "mic": "XNAS" or None,
          "match_org_name": "..." or None,
          "match_score": "100%" or None,
          "match_level": "Excellent" or None,
          "input_standard_identifier": "Ticker:AAPL" or None,
          "input_name": "..." or None
        }
      ]
    }

    Args:
        data: Raw input data
        format_type: "cik" or "record"

    Returns:
        Normalized data in unified format
    """
    normalized = {}

    if format_type == "cik":
        # CIK format is already close to unified, just add null fields
        for entity_name, permid_list in data.items():
            normalized[entity_name] = []
            for item in permid_list:
                normalized[entity_name].append({
                    "permid": item.get("permid"),
                    "ciks": item.get("ciks"),
                    "ticker": None,
                    "mic": None,
                    "match_org_name": None,
                    "match_score": None,
                    "match_level": None,
                    "input_standard_identifier": None,
                    "input_name": None
                })

    elif format_type == "record":
        # Record format needs to be converted to list structure
        for entity_name, record_data in data.items():
            normalized[entity_name] = [{
                "permid": record_data.get("permid"),
                "ciks": None,
                "ticker": record_data.get("ticker"),
                "mic": record_data.get("mic"),
                "match_org_name": record_data.get("match_org_name"),
                "match_score": record_data.get("match_score"),
                "match_level": record_data.get("match_level"),
                "input_standard_identifier": record_data.get("input_standard_identifier"),
                "input_name": record_data.get("input_name")
            }]

    return normalized


def load_permid_data(input_file: pathlib.Path) -> dict[str, list[dict[str, Any]]]:
    """
    Load PermID data from JSON file and normalize to unified format.

    Supports both CIK and Record input formats.
    Returns empty dict when input is empty (e.g. no PermIDs found in prior stage).
    """
    logging.info(f"Loading PermID data from: {input_file}")
    with open(input_file) as f:
        data = json.load(f)

    if not data:
        logging.info("Input data is empty (no entities with PermIDs)")
        return {}

    # Detect format
    format_type = detect_input_format(data)
    logging.info(f"Detected input format: {format_type}")

    # Normalize to unified format
    normalized_data = normalize_to_unified_format(data, format_type)
    logging.info(f"Loaded {len(normalized_data)} entities with PermID data")

    return normalized_data

def process_investor(
    session: requests.Session,
    investor_name: str,
    unified_permid_data: list[dict[str, Any]],
    api_key: str,
    geonames_user: str,
    stats: dict
) -> list[dict[str, Any]]:
    """
    Process a single entity by querying all their PermIDs.

    Args:
        session: requests Session object
        investor_name: Name of the entity (investor or issuer)
        unified_permid_data: List of dicts in unified format with permid, ciks, ticker, etc.
        api_key: API access token
        geonames_user: Geonames API username
        stats: Statistics dictionary to update

    Returns:
        List of company information dictionaries with all unified fields
    """
    stats["total_investors"] += 1

    if len(unified_permid_data) > 1:
        stats["investors_with_multiple_permids"] += 1
        permid_list = [item["permid"] for item in unified_permid_data]
        logging.warning(f"  Multiple PermIDs for {investor_name}: {permid_list}")

    # Query each PermID for this entity
    investor_results = []
    for item in unified_permid_data:
        permid_url = item["permid"]
        stats["total_permids_queried"] += 1

        # Log identifier info
        if item["ciks"]:
            ciks_str = ", ".join(item["ciks"])
            logging.info(f"  Querying PermID: {permid_url} (CIKs: {ciks_str})")
        elif item["ticker"]:
            logging.info(f"  Querying PermID: {permid_url} (Ticker: {item['ticker']})")
        else:
            logging.info(f"  Querying PermID: {permid_url}")

        company_info = query_permid_entity(session, permid_url, api_key, geonames_user)

        if company_info:
            stats["successful_queries"] += 1
            # Add the original entity name and processing timestamp
            company_info["original_investor_name"] = investor_name
            company_info["last_processed"] = datetime.now().isoformat()

            # Add all unified fields (null for missing fields)
            company_info["ciks"] = item["ciks"]
            company_info["ticker"] = item["ticker"]
            company_info["mic"] = item["mic"]
            company_info["match_org_name"] = item["match_org_name"]
            company_info["match_score"] = item["match_score"]
            company_info["match_level"] = item["match_level"]
            company_info["input_standard_identifier"] = item["input_standard_identifier"]
            company_info["input_name"] = item["input_name"]

            investor_results.append(company_info)
            logger.info(f"  Successfully retrieved info for PermID {permid_url}")
        else:
            stats["failed_queries"] += 1
            investor_results.append(None)
            logger.warning(f"  Failed to retrieve info for PermID {permid_url}")

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
    logger.info(f"Processing batch of {len(batch)} investors")

    for idx, investor_name in enumerate(batch, 1):
        unified_permid_data = permid_data[investor_name]
        # Filter out items where PermID is None
        unified_permid_data = [item for item in unified_permid_data if item.get("permid") is not None]

        if not unified_permid_data:
            logger.warning(f"[{idx}/{len(batch)}] Skipping {investor_name}: No valid PermIDs")
            investor_results = [ None ]

        else:
            logger.info(
                f"[{idx}/{len(batch)}] Processing: {investor_name} ({len(unified_permid_data)} PermID(s))"
            )
            # Process this entity
            investor_results = process_investor(
                session, investor_name, unified_permid_data, api_key, geonames_user, stats
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
    logger.info("=" * 60)
    logger.info("BATCH STATISTICS")
    logger.info("=" * 60)
    logger.info(f"Investors processed: {batch_stats['total_investors']}")
    logger.info(f"PermIDs queried: {batch_stats['total_permids_queried']}")
    logger.info(f"Successful queries: {batch_stats['successful_queries']}")
    logger.info(f"Failed queries: {batch_stats['failed_queries']}")
    logger.info(
        f"Investors with multiple PermIDs: {batch_stats['investors_with_multiple_permids']}"
    )

    logger.info("=" * 60)
    logger.info("CUMULATIVE STATISTICS")
    logger.info("=" * 60)
    logger.info(f"Total company records: {len(all_results)}")

    # Count unique investors
    unique_investors = set(r.get("original_investor_name") for r in all_results if r)
    logger.info(f"Unique investors: {len(unique_investors)}")

    logger.info("=" * 60)

def main():
    """Main function to query PermID API and process company information."""
    start = datetime.now()
    args = get_args()

    # Log arguments (except API key)
    for key, value in args.__dict__.items():
        if key != "api_key":
            logger.info(f"{key}: {value}")

    # Create output file's parent directory if it doesn't exist
    args.output_file.parent.mkdir(parents=True, exist_ok=True)

    # Load PermID data with threshold-based filtering
    load_result = load_data(
        args.input_file,
        args.batch_file,
        args.output_file,
        args.batch_size,
        args.threshold_days
    )

    # Check if there's anything to process
    if load_result is None:
        logger.info("Nothing to process. Exiting.")
        return

    permid_data, existing_results, investors_to_process = load_result

    # Create session
    session = create_session()

    # Process batch
    batch_results, processed_investors, batch_stats = process_batch(
        session,
        permid_data,
        investors_to_process,
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
    end = datetime.now()
    logger.info(f"Elapsed time: {end - start}")


if __name__ == "__main__":
    main()
