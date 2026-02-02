#!/usr/bin/env python3
"""
Job #2: Query PermID API by CIK to retrieve PermID and company information.

Reads CIK data from JSON, queries the PermID API for each CIK,
and saves investor_name to PermID mappings as JSON.
Supports batch processing with rate limiting and retry logic.
"""

import argparse
import json
import logging
import pathlib
import time
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.getLogger().setLevel(logging.INFO)
logging.basicConfig(
    format='%(asctime)s,%(msecs)d %(module)s:%(lineno)d %(levelname)s %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S',
    level=logging.INFO
)

# API configuration
API_URL = "https://api-eit.refinitiv.com/permid/search"
REQUEST_TIMEOUT = (10, 30)  # (connect timeout, read timeout)
RATE_LIMIT_DELAY = 1.0  # 1 request per second


def get_args():
    parser = argparse.ArgumentParser(
        description="Query PermID API by CIK to retrieve PermID and company information"
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
        help="Path to input JSON file with CIK data"
    )
    parser.add_argument(
        "--output-file",
        type=pathlib.Path,
        required=True,
        help="Path to output JSON file for PermID results"
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
    args = parser.parse_args()
    return args

def create_session() -> requests.Session:
    """Create a requests Session with retry strategy."""
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

def query_permid_by_cik(session: requests.Session, cik: str, api_key: str) -> str | None:
    """
    Query PermID API by CIK and return the PermID.

    Args:
        session: requests Session object
        cik: CIK identifier
        api_key: PermID API access token

    Returns:
        PermID string (extracted from @id field) or None if not found
    """
    params = {
        "q": f"cik:{cik}",
        "format": "json",
    }

    headers = {
        "X-AG-Access-Token": api_key,
        "Accept": "application/json",
        "User-Agent": "ftm2j/1.0 (contact: research@example.com)",
    }

    try:
        response = session.get(
            API_URL,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()

        data = response.json()

        # Extract PermID from organizations entities
        organizations = data.get("result", {}).get("organizations", {})
        entities = organizations.get("entities", [])

        if entities:
            # Get the @id field and extract just the PermID part
            id_url = entities[0].get("@id", "")
            return id_url

        return None

    except requests.exceptions.RequestException as e:
        logging.error(f"Error querying CIK {cik}: {e}")
        return None

def load_cik_data(input_file: pathlib.Path) -> dict[str, list[str]]:
    """Load CIK data from JSON file."""
    logging.info(f"Loading CIK data from: {input_file}")
    with open(input_file) as f:
        data = json.load(f)
    logging.info(f"Loaded {len(data)} investors with CIK data")
    return data

def load_batch_tracking(batch_file: pathlib.Path) -> dict:
    """Load batch tracking data or create new tracking dict."""
    if batch_file.exists():
        logging.info(f"Loading existing batch tracking from: {batch_file}")
        with open(batch_file) as f:
            return json.load(f)
    else:
        logging.info("Creating new batch tracking file")
        return {}

def save_batch_tracking(batch_file: pathlib.Path, tracking_data: dict):
    """Save batch tracking data to file."""
    with open(batch_file, 'w') as f:
        json.dump(tracking_data, f, indent=2)
    logging.info(f"Saved batch tracking to: {batch_file}")

def load_existing_results(output_file: pathlib.Path) -> dict[str, list[str]]:
    """Load existing results or return empty dict."""
    if output_file.exists():
        logging.info(f"Loading existing results from: {output_file}")
        with open(output_file) as f:
            return json.load(f)
    else:
        logging.info("No existing results found, starting fresh")
        return {}

def save_results(output_file: pathlib.Path, results: dict[str, list[str]]):
    """Save results to JSON file."""
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    logging.info(f"Saved results to: {output_file}")

def get_unprocessed_investors(
    cik_data: dict[str, list[str]],
    batch_tracking: dict
) -> list[str]:
    """Get list of investors that haven't been processed yet."""
    processed_investors = set()

    # Collect all processed investors from all batches
    for batch_info in batch_tracking.values():
        processed_investors.update(batch_info.get("processed_investors", []))

    # Find unprocessed investors
    all_investors = set(cik_data.keys())
    unprocessed = list(all_investors - processed_investors)

    logging.info(f"Total investors: {len(all_investors)}")
    logging.info(f"Already processed: {len(processed_investors)}")
    logging.info(f"Remaining to process: {len(unprocessed)}")

    return unprocessed

def _initialize_batch_stats() -> dict:
    """Initialize statistics dictionary for batch processing."""
    return {
        "total_investors": 0,
        "total_ciks_queried": 0,
        "successful_queries": 0,
        "failed_queries": 0,
        "investors_with_permid": 0,
        "investors_without_permid": 0,
        "total_permids": 0,
        "duplicates_removed": 0
    }

def _process_investor_ciks(
    session: requests.Session,
    ciks: list[str],
    api_key: str,
    stats: dict
) -> list[str | None]:
    """
    Process all CIKs for a single investor with rate limiting.

    Args:
        session: requests Session object
        ciks: List of CIK identifiers for this investor
        api_key: PermID API access token
        stats: Statistics dictionary to update

    Returns:
        List of PermIDs (or None for failed queries)
    """
    permids = []
    for cik in ciks:
        stats["total_ciks_queried"] += 1

        # Query API with rate limiting
        permid = query_permid_by_cik(session, cik, api_key)

        if permid:
            stats["successful_queries"] += 1
            permids.append(permid)
            logging.info(f"  CIK {cik} -> PermID {permid}")
        else:
            stats["failed_queries"] += 1
            permids.append(None)
            logging.warning(f"  CIK {cik} -> No PermID found")

        # Rate limiting: wait 1 second between requests
        time.sleep(RATE_LIMIT_DELAY)

    return permids

def _store_investor_results(
    investor_name: str,
    permids: list[str | None],
    results: dict[str, list[str]],
    stats: dict
):
    """
    Store investor results with deduplication and update statistics.

    Args:
        investor_name: Name of the investor
        permids: List of PermIDs (may contain None and duplicates)
        results: Results dictionary to update
        stats: Statistics dictionary to update
    """
    if permids:
        # Remove duplicates
        unique_permids = list(set(permids))
        stats["duplicates_removed"] += len(permids) - len(unique_permids)

        # Log when investor has multiple PermIDs
        if len(unique_permids) > 1:
            logging.warning(
                f"  MULTIPLE PermIDs for {investor_name}: {unique_permids}"
            )

        results[investor_name] = unique_permids
        stats["investors_with_permid"] += 1
        stats["total_permids"] += len(unique_permids)
    else:
        stats["investors_without_permid"] += 1

def process_batch(
    session: requests.Session,
    cik_data: dict[str, list[str]],
    investors_to_process: list[str],
    batch_size: int,
    api_key: str
) -> tuple[dict[str, list[str]], list[str], dict]:
    """
    Process a batch of investors.

    Returns:
        Tuple of (results_dict, processed_investors_list, stats_dict)
    """
    results = {}
    processed_investors = []
    stats = _initialize_batch_stats()

    batch = investors_to_process[:batch_size]
    logging.info(f"Processing batch of {len(batch)} investors")

    for idx, investor_name in enumerate(batch, 1):
        ciks = cik_data[investor_name]
        stats["total_investors"] += 1

        logging.info(f"[{idx}/{len(batch)}] Processing: {investor_name} ({len(ciks)} CIK(s))")

        # Process all CIKs for this investor
        permids = _process_investor_ciks(session, ciks, api_key, stats)

        # Store results and update stats
        _store_investor_results(investor_name, permids, results, stats)

        processed_investors.append(investor_name)

    return results, processed_investors, stats

def print_stats(stats: dict, batch_stats: dict):
    """Print statistics about the processing."""
    logging.info("=" * 60)
    logging.info("BATCH STATISTICS")
    logging.info("=" * 60)
    logging.info(f"Investors processed: {batch_stats['total_investors']}")
    logging.info(f"CIKs queried: {batch_stats['total_ciks_queried']}")
    logging.info(f"Successful queries: {batch_stats['successful_queries']}")
    logging.info(f"Failed queries: {batch_stats['failed_queries']}")
    logging.info(f"Investors with PermID: {batch_stats['investors_with_permid']}")
    logging.info(f"Investors without PermID: {batch_stats['investors_without_permid']}")
    logging.info(f"Total PermIDs found: {batch_stats['total_permids']}")
    logging.info(f"Duplicates removed: {batch_stats['duplicates_removed']}")

    logging.info("=" * 60)
    logging.info("CUMULATIVE STATISTICS")
    logging.info("=" * 60)
    logging.info(f"Total investors with PermID: {len(stats)}")
    total_permids = sum(len(permids) for permids in stats.values())
    logging.info(f"Total PermIDs: {total_permids}")
    if stats:
        avg_permids = total_permids / len(stats)
        logging.info(f"Average PermIDs per investor: {avg_permids:.2f}")
    logging.info("=" * 60)

def _load_data(
    input_file: pathlib.Path,
    batch_file: pathlib.Path,
    output_file: pathlib.Path
) -> tuple[dict[str, list[str]], dict, dict[str, list[str]], list[str]]:
    """
    Load all input data and determine unprocessed investors.

    Args:
        input_file: Path to CIK data JSON file
        batch_file: Path to batch tracking file
        output_file: Path to existing results file

    Returns:
        Tuple of (cik_data, batch_tracking, existing_results, unprocessed_investors)
    """
    cik_data = load_cik_data(input_file)
    batch_tracking = load_batch_tracking(batch_file)
    existing_results = load_existing_results(output_file)
    unprocessed_investors = get_unprocessed_investors(cik_data, batch_tracking)

    return cik_data, batch_tracking, existing_results, unprocessed_investors

def _finalize_batch(
    output_file: pathlib.Path,
    batch_file: pathlib.Path,
    existing_results: dict[str, list[str]],
    batch_results: dict[str, list[str]],
    batch_tracking: dict,
    processed_investors: list[str],
    batch_stats: dict
):
    """
    Save results and update batch tracking.

    Args:
        output_file: Path to save results
        batch_file: Path to save batch tracking
        existing_results: Previously saved results
        batch_results: Results from current batch
        batch_tracking: Batch tracking data
        processed_investors: List of investors processed in this batch
        batch_stats: Statistics from batch processing
    """
    # Merge with existing results
    all_results = {**existing_results, **batch_results}

    # Save results
    save_results(output_file, all_results)

    # Update batch tracking
    batch_timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    batch_tracking[batch_timestamp] = {
        "processed_investors": processed_investors,
        "batch_size": len(processed_investors),
        "stats": batch_stats
    }
    save_batch_tracking(batch_file, batch_tracking)

    # Print statistics
    print_stats(all_results, batch_stats)

def main():
    """Main function to query PermID API and process results."""
    args = get_args()

    # Log arguments
    for key, value in args.__dict__.items():
        if key == "api_key":
            continue
        logging.info(f"{key}: {value}")

    # Load data and get unprocessed investors
    cik_data, batch_tracking, existing_results, unprocessed_investors = _load_data(
        args.input_file,
        args.batch_file,
        args.output_file
    )

    if not unprocessed_investors:
        logging.info("All investors have been processed!")
        return

    if args.batch_size > len(unprocessed_investors):
        logging.warning(
            f"Batch size ({args.batch_size}) is larger than remaining investors "
            f"({len(unprocessed_investors)}). Processing all remaining investors."
        )

    # Create session and process batch
    session = create_session()
    batch_results, processed_investors, batch_stats = process_batch(
        session,
        cik_data,
        unprocessed_investors,
        args.batch_size,
        args.api_key
    )

    # Save results and update tracking
    _finalize_batch(
        args.output_file,
        args.batch_file,
        existing_results,
        batch_results,
        batch_tracking,
        processed_investors,
        batch_stats
    )


if __name__ == "__main__":
    main()
