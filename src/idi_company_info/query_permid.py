#!/usr/bin/env python3
"""
Query PermID API to retrieve PermID and company information.

Supports two modes:
1. CIK mode: Query by CIK identifiers (Entity Search API)
2. Record mode: Query by issuer name and ticker (Record Match API)

Both modes support batch processing with rate limiting and retry logic.
"""

import argparse
import json
import pathlib
import time
from datetime import datetime

import pandas as pd
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
ENTITY_SEARCH_URL = "https://api-eit.refinitiv.com/permid/search"
RECORD_MATCH_URL = "https://api-eit.refinitiv.com/permid/match"

# Record matching constants
RECORD_BATCH_SIZE = 1000  # Records per batch for Record Match API
RECORD_BATCH_DELAY = 2.0  # Seconds between batches
RECORD_MAX_RETRIES = 3  # Max retry attempts for failed batches


def get_args():
    parser = argparse.ArgumentParser(
        description="Query PermID API to retrieve PermID and company information"
    )
    parser.add_argument(
        "--type",
        type=str,
        required=True,
        choices=["cik", "record"],
        help="Type of query: 'cik' for CIK-based search, 'record' for record matching"
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
        help="Path to input JSON file (CIK data or issuer records)"
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
        help="Number of investors to process in this batch (CIK mode only)"
    )
    args = parser.parse_args()
    return args


# ============================================================================
# CIK MODE - Entity Search API
# ============================================================================

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
            ENTITY_SEARCH_URL,
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
        logger.error(f"Error querying CIK {cik}: {e}")
        return None


def load_cik_data(input_file: pathlib.Path) -> dict[str, list[str]]:
    """Load CIK data from JSON file."""
    logger.info(f"Loading CIK data from: {input_file}")
    with open(input_file) as f:
        data = json.load(f)
    logger.info(f"Loaded {len(data)} investors with CIK data")
    return data


def _initialize_cik_batch_stats() -> dict:
    """Initialize statistics dictionary for CIK batch processing."""
    return {
        "total_investors": 0,
        "total_ciks_queried": 0,
        "successful_queries": 0,
        "failed_queries": 0,
        "investors_with_permid": 0,
        "investors_without_permid": 0,
        "total_permids": 0,
        "duplicates_removed": 0,
        "duplicates_removed_cik": 0
    }


def _process_investor_ciks(
    session: requests.Session,
    ciks: list[str],
    api_key: str,
    stats: dict
) -> list[dict[str, str | None]]:
    """
    Process all CIKs for a single investor with rate limiting.

    Args:
        session: requests Session object
        ciks: List of CIK identifiers for this investor
        api_key: PermID API access token
        stats: Statistics dictionary to update

    Returns:
        List of dicts with CIK and PermID pairs (PermID may be None for failed queries)
    """
    results = []
    for cik in ciks:
        stats["total_ciks_queried"] += 1

        # Query API with rate limiting
        permid = query_permid_by_cik(session, cik, api_key)

        if permid:
            stats["successful_queries"] += 1
            results.append({"cik": cik, "permid": permid})
            logger.info(f"  CIK {cik} -> PermID {permid}")
        else:
            stats["failed_queries"] += 1
            results.append({"cik": cik, "permid": None})
            logger.warning(f"  CIK {cik} -> No PermID found")

        # Rate limiting: wait 1 second between requests
        time.sleep(RATE_LIMIT_DELAY)

    return results


def _store_investor_results(
    investor_name: str,
    cik_permid_pairs: list[dict[str, str | None]],
    results: dict[str, list[dict[str, list[str] | str]]],
    stats: dict
):
    """
    Store investor results with deduplication and update statistics.
    Groups multiple CIKs that map to the same PermID together.

    Args:
        investor_name: Name of the investor
        cik_permid_pairs: List of dicts with CIK and PermID (PermID may be None)
        results: Results dictionary to update
        stats: Statistics dictionary to update
    """
    # Filter out entries where PermID is None
    valid_pairs = [pair for pair in cik_permid_pairs if pair["permid"] is not None]

    if valid_pairs:
        # Group CIKs by PermID
        permid_to_ciks: dict[str, list[str]] = {}
        for pair in valid_pairs:
            permid = pair["permid"]
            cik = pair["cik"]
            if permid not in permid_to_ciks:
                permid_to_ciks[permid] = []
            permid_to_ciks[permid].append(cik)

        # Create unique pairs with all CIKs grouped by PermID
        unique_pairs = [
            {"ciks": ciks, "permid": permid}
            for permid, ciks in permid_to_ciks.items()
        ]

        # Track duplicates (CIKs that mapped to the same PermID)
        total_ciks = len(valid_pairs)
        unique_mappings = len(unique_pairs)
        stats["duplicates_removed"] += total_ciks - unique_mappings

        # Log when investor has multiple PermIDs
        if len(unique_pairs) > 1:
            permid_list = [pair["permid"] for pair in unique_pairs]
            logger.warning(
                f"  MULTIPLE PermIDs for {investor_name}: {permid_list}"
            )

        # Log when multiple CIKs map to same PermID
        for pair in unique_pairs:
            if len(pair["ciks"]) > 1:
                logger.info(
                    f"  Multiple CIKs for same PermID {pair['permid']}: {pair['ciks']}"
                )

        results[investor_name] = unique_pairs
        stats["investors_with_permid"] += 1
        stats["total_permids"] += len(unique_pairs)
    else:
        stats["investors_without_permid"] += 1


def process_cik_batch(
    session: requests.Session,
    cik_data: dict[str, list[str]],
    investors_to_process: list[str],
    batch_size: int,
    api_key: str
) -> tuple[dict[str, list[dict[str, list[str] | str]]], list[str], dict]:
    """
    Process a batch of investors (CIK mode).

    Returns:
        Tuple of (results_dict, processed_investors_list, stats_dict)
    """
    results = {}
    processed_investors = []
    stats = _initialize_cik_batch_stats()

    batch = investors_to_process[:batch_size]
    logger.info(f"Processing batch of {len(batch)} investors")

    for idx, investor_name in enumerate(batch, 1):
        ciks = cik_data[investor_name]

        # Remove duplicate CIKs before processing
        original_count = len(ciks)
        ciks = list(dict.fromkeys(ciks))  # Preserves order while removing duplicates
        if len(ciks) < original_count:
            logger.info(f"  Removed {original_count - len(ciks)} duplicate CIK(s) for {investor_name}")
            stats["duplicates_removed_cik"] += 1

        stats["total_investors"] += 1

        logger.info(f"[{idx}/{len(batch)}] Processing: {investor_name} ({len(ciks)} CIK(s))")

        # Process all CIKs for this investor
        cik_permid_pairs = _process_investor_ciks(session, ciks, api_key, stats)

        # Store results and update stats
        _store_investor_results(investor_name, cik_permid_pairs, results, stats)

        processed_investors.append(investor_name)

    return results, processed_investors, stats

<<<<<<< HEAD
def print_stats(stats: dict, batch_stats: dict):
    """Print statistics about the processing."""
    logger.info("=" * 60)
    logger.info("BATCH STATISTICS")
    logger.info("=" * 60)
    logger.info(f"Investors processed: {batch_stats['total_investors']}")
    logger.info(f"CIKs queried: {batch_stats['total_ciks_queried']}")
    logger.info(f"Successful queries: {batch_stats['successful_queries']}")
    logger.info(f"Failed queries: {batch_stats['failed_queries']}")
    logger.info(f"Investors with PermID: {batch_stats['investors_with_permid']}")
    logger.info(f"Investors without PermID: {batch_stats['investors_without_permid']}")
    logger.info(f"Total PermIDs found: {batch_stats['total_permids']}")
    logger.info(f"Duplicates removed: {batch_stats['duplicates_removed']}")
    logger.info(f"Duplicate CIKs removed: {batch_stats['duplicates_removed_cik']}")
=======

def print_cik_stats(stats: dict, batch_stats: dict):
    """Print statistics about CIK processing."""
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
    logging.info(f"Duplicate CIKs removed: {batch_stats['duplicates_removed_cik']}")
>>>>>>> d7c7661 (Update query PermID to record match for issuer stock ticker data)

    logger.info("=" * 60)
    logger.info("CUMULATIVE STATISTICS")
    logger.info("=" * 60)
    logger.info(f"Total investors with PermID: {len(stats)}")
    total_permids = sum(len(pairs) for pairs in stats.values())
    logger.info(f"Total PermIDs: {total_permids}")
    if stats:
        avg_permids = total_permids / len(stats)
        logger.info(f"Average PermIDs per investor: {avg_permids:.2f}")
    logger.info("=" * 60)


# ============================================================================
# RECORD MODE - Record Match API
# ============================================================================

def load_record_data(input_file: pathlib.Path) -> dict[str, dict]:
    """
    Load issuer record data from JSON file.

    Expected format:
    {
        "ISSUER NAME": {"ticker": "AAPL", "mic": "XNAS", "local_id": 42},
        ...
    }
    """
    logging.info(f"Loading issuer record data from: {input_file}")
    with open(input_file) as f:
        data = json.load(f)
    logging.info(f"Loaded {len(data)} issuers")
    return data


def _initialize_record_batch_stats() -> dict:
    """Initialize statistics dictionary for record batch processing."""
    return {
        "total_issuers": 0,
        "total_batches": 0,
        "successful_matches": 0,
        "no_matches": 0,
        "failed_batches": 0,
        "retries": 0
    }


def _build_record_match_csv(records: list[dict]) -> str:
    """
    Build CSV string for Record Match API.

    Args:
        records: List of dicts with LocalID, Name, ticker, mic

    Returns:
        CSV string formatted for API
    """
    # Build DataFrame with required columns
    rows = []
    for record in records:
        row = {
            "LocalID": record["local_id"],
            "Name": record["name"]
        }

        # Build Standard Identifier with ticker and optionally MIC
        # Valid keys per API: [RIC, Ilx, Datastream_Mnemonic, Ticker, EXCHANGE, MIC]
        ticker = record.get("ticker")
        mic = record.get("mic")

        identifier_parts = []

        if ticker:
            identifier_parts.append(f"Ticker:{ticker}")

        if mic:
            identifier_parts.append(f"MIC:{mic}")

        # Only add Standard Identifier if we have at least one identifier
        if identifier_parts:
            row["Standard Identifier"] = "&&".join(identifier_parts)

        rows.append(row)

    df = pd.DataFrame(rows)
    return df.to_csv(index=False)


def _parse_record_match_response(response_data: dict) -> list[dict]:
    """
    Parse JSON response from Record Match API.

    Args:
        response_data: JSON response dictionary

    Returns:
        List of dicts with local_id and permid
    """
    results = []

    # The API returns data in "outputContentResponse" key
    output_records = response_data.get("outputContentResponse", [])

    for row in output_records:
        local_id = row.get("Input_LocalID", "")
        # Try different PermID field names (API returns different names based on entity type)
        permid = (row.get("Match OpenPermID", "") or
                  row.get("Match OrgPermID", "") or
                  row.get("Match InstrumentPermID", ""))
        match_level = row.get("Match Level", "")

        results.append({
            "local_id": local_id,
            "permid": permid if permid and match_level != "No Match" else None,
            "match_level": match_level
        })

    return results


def _query_record_batch(
    session: requests.Session,
    batch_records: list[dict],
    api_key: str,
    attempt: int = 1
) -> list[dict] | None:
    """
    Query Record Match API with a batch of records.

    Args:
        session: requests Session object
        batch_records: List of records to match
        api_key: PermID API access token
        attempt: Current attempt number (for logging)

    Returns:
        List of results or None if failed
    """
    headers = {
        "accept": "application/json",
        "Content-Type": "text/plain",
        "x-ag-access-token": api_key,
        "x-openmatch-numberOfMatchesPerRecord": "1",
        "x-openmatch-dataType": "Instrument"
    }

    csv_data = _build_record_match_csv(batch_records)

    logging.debug(f"Batch CSV (attempt {attempt}):\n{csv_data[:500]}...")

    try:
        response = session.post(
            RECORD_MATCH_URL,
            headers=headers,
            data=csv_data,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()

        # Parse JSON response
        results = _parse_record_match_response(response.json())
        return results

    except requests.exceptions.RequestException as e:
        logging.error(f"Error querying batch (attempt {attempt}): {e}")
        return None


def process_record_batch(
    session: requests.Session,
    record_data: dict[str, dict],
    issuers_to_process: list[str],
    api_key: str
) -> tuple[dict[str, dict], list[str], dict]:
    """
    Process issuers using Record Match API (record mode).

    Args:
        session: requests Session object
        record_data: Dict of issuer_name -> {ticker, mic, local_id}
        issuers_to_process: List of issuer names to process
        api_key: PermID API access token

    Returns:
        Tuple of (results_dict, processed_issuers_list, stats_dict)
    """
    results = {}
    processed_issuers = []
    stats = _initialize_record_batch_stats()

    # Prepare records for batching
    all_records = []
    issuer_lookup = {}  # Map local_id back to issuer_name

    for issuer_name in issuers_to_process:
        issuer_data = record_data[issuer_name]
        local_id = str(issuer_data.get("local_id"))  # Use parquet row index

        all_records.append({
            "local_id": local_id,
            "name": issuer_name,
            "ticker": issuer_data.get("ticker"),
            "mic": issuer_data.get("mic")
        })
        issuer_lookup[local_id] = issuer_name

    stats["total_issuers"] = len(all_records)

    # Process in batches of 100
    total_batches = (len(all_records) + RECORD_BATCH_SIZE - 1) // RECORD_BATCH_SIZE
    logging.info(f"Processing {len(all_records)} issuers in {total_batches} batches")

    for batch_idx in range(0, len(all_records), RECORD_BATCH_SIZE):
        batch_records = all_records[batch_idx:batch_idx + RECORD_BATCH_SIZE]
        batch_num = (batch_idx // RECORD_BATCH_SIZE) + 1

        logging.info(f"[Batch {batch_num}/{total_batches}] Processing {len(batch_records)} issuers")

        # Retry logic
        batch_results = None
        for attempt in range(1, RECORD_MAX_RETRIES + 1):
            batch_results = _query_record_batch(session, batch_records, api_key, attempt)

            if batch_results is not None:
                break

            if attempt < RECORD_MAX_RETRIES:
                logging.warning(f"Retrying batch {batch_num} (attempt {attempt + 1}/{RECORD_MAX_RETRIES})...")
                stats["retries"] += 1
                time.sleep(RECORD_BATCH_DELAY)

        if batch_results is None:
            logging.error(f"Batch {batch_num} failed after {RECORD_MAX_RETRIES} attempts")
            stats["failed_batches"] += 1
            continue

        stats["total_batches"] += 1

        # Process results
        for result in batch_results:
            local_id = result["local_id"]
            issuer_name = issuer_lookup.get(local_id)

            if not issuer_name:
                continue

            permid = result["permid"]
            match_level = result["match_level"]

            if permid:
                # Include ticker and MIC along with PermID (similar to CIK mode)
                issuer_data = record_data[issuer_name]
                results[issuer_name] = {
                    "ticker": issuer_data.get("ticker"),
                    "mic": issuer_data.get("mic"),
                    "permid": permid
                }
                stats["successful_matches"] += 1
                logging.info(f"  {issuer_name} -> {permid} ({match_level})")
            else:
                stats["no_matches"] += 1
                logging.warning(f"  {issuer_name} -> No match ({match_level})")

            processed_issuers.append(issuer_name)

        # Delay between batches
        if batch_num < total_batches:
            time.sleep(RECORD_BATCH_DELAY)

    return results, processed_issuers, stats


def print_record_stats(stats: dict, batch_stats: dict):
    """Print statistics about record processing."""
    logging.info("=" * 60)
    logging.info("BATCH STATISTICS")
    logging.info("=" * 60)
    logging.info(f"Issuers processed: {batch_stats['total_issuers']}")
    logging.info(f"Batches sent: {batch_stats['total_batches']}")
    logging.info(f"Successful matches: {batch_stats['successful_matches']}")
    logging.info(f"No matches: {batch_stats['no_matches']}")
    logging.info(f"Failed batches: {batch_stats['failed_batches']}")
    logging.info(f"Retries: {batch_stats['retries']}")

    logging.info("=" * 60)
    logging.info("CUMULATIVE STATISTICS")
    logging.info("=" * 60)
    logging.info(f"Total issuers with PermID: {len(stats)}")
    logging.info("=" * 60)


# ============================================================================
# SHARED FUNCTIONS
# ============================================================================

def _finalize_batch(
    output_file: pathlib.Path,
    batch_file: pathlib.Path,
    existing_results: dict,
    batch_results: dict,
    batch_tracking: dict,
    processed_items: list[str],
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
        processed_items: List of items processed in this batch
        batch_stats: Statistics from batch processing
    """
    # Merge with existing results
    all_results = {**existing_results, **batch_results}

    # Save results
    save_results(output_file, all_results)

    # Update batch tracking
    batch_timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    batch_tracking[batch_timestamp] = {
        "processed_items": processed_items,
        "batch_size": len(processed_items),
        "stats": batch_stats
    }
    save_batch_tracking(batch_file, batch_tracking)


def main():
    """Main function to query PermID API and process results."""
    start = datetime.now()
    args = get_args()

    # Log arguments
    for key, value in args.__dict__.items():
        if key == "api_key":
            continue
        logger.info(f"{key}: {value}")

    # Create session
    session = create_session()

<<<<<<< HEAD
    if not unprocessed_investors:
        logger.info("All investors have been processed!")
        return

    if args.batch_size > len(unprocessed_investors):
        logger.warning(
            f"Batch size ({args.batch_size}) is larger than remaining investors "
            f"({len(unprocessed_investors)}). Processing all remaining investors."
=======
    if args.type == "cik":
        # CIK MODE
        # Load data
        cik_data = load_cik_data(args.input_file)
        batch_tracking = load_batch_tracking(args.batch_file)
        existing_results = load_existing_results(args.output_file, default_type="dict")
        unprocessed_investors = get_unprocessed_investors(cik_data, batch_tracking)

        if not unprocessed_investors:
            logging.info("All investors have been processed!")
            return

        if args.batch_size > len(unprocessed_investors):
            logging.warning(
                f"Batch size ({args.batch_size}) is larger than remaining investors "
                f"({len(unprocessed_investors)}). Processing all remaining investors."
            )

        # Process batch
        batch_results, processed_investors, batch_stats = process_cik_batch(
            session,
            cik_data,
            unprocessed_investors,
            args.batch_size,
            args.api_key
>>>>>>> d7c7661 (Update query PermID to record match for issuer stock ticker data)
        )

        # Save and print stats
        _finalize_batch(
            args.output_file,
            args.batch_file,
            existing_results,
            batch_results,
            batch_tracking,
            processed_investors,
            batch_stats
        )
        print_cik_stats(existing_results | batch_results, batch_stats)

    elif args.type == "record":
        # RECORD MODE
        # Load data
        record_data = load_record_data(args.input_file)
        batch_tracking = load_batch_tracking(args.batch_file)
        existing_results = load_existing_results(args.output_file, default_type="dict")
        unprocessed_issuers = get_unprocessed_investors(record_data, batch_tracking)

        if not unprocessed_issuers:
            logging.info("All issuers have been processed!")
            return

        if args.batch_size > len(unprocessed_issuers):
            logging.warning(
                f"Batch size ({args.batch_size}) is larger than remaining issuers "
                f"({len(unprocessed_issuers)}). Processing all remaining issuers."
            )

        # Process batch (limited by batch_size, then internally batched by RECORD_BATCH_SIZE)
        issuers_to_process = unprocessed_issuers[:args.batch_size]
        batch_results, processed_issuers, batch_stats = process_record_batch(
            session,
            record_data,
            issuers_to_process,
            args.api_key
        )

        # Save and print stats
        _finalize_batch(
            args.output_file,
            args.batch_file,
            existing_results,
            batch_results,
            batch_tracking,
            processed_issuers,
            batch_stats
        )
        print_record_stats(existing_results | batch_results, batch_stats)

    end = datetime.now()
    logger.info(f"Elapsed time: {end - start}")


if __name__ == "__main__":
    main()
