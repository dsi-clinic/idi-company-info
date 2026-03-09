#!/usr/bin/env python3
"""
Extract identifiers from parquet data for PermID matching.

Supports two modes:
1. CIK mode: Extract investor names and CIKs
2. Record mode: Extract issuer names, tickers, and MIC codes for record matching
"""

import argparse
import datetime
import json
import logging
import pathlib
import re

import pandas as pd

logging.getLogger().setLevel(logging.INFO)
logging.basicConfig(format='%(asctime)s,%(msecs)d %(module)s:%(lineno)d %(levelname)s %(message)s',
                    datefmt='%Y-%m-%dT%H:%M:%S',
                    level=logging.INFO)

# Constants for exchange mapping
EXCHANGE_TO_MIC = {
    "SS": "XSTO",  # Stockholm Stock Exchange
}


def get_args():
    parser = argparse.ArgumentParser(
        description="Extract identifiers from parquet data for PermID matching"
    )
    parser.add_argument(
        "--type",
        type=str,
        required=True,
        choices=["cik", "record"],
        help="Type of identifiers to extract: 'cik' for investor CIKs, 'record' for issuer record matching"
    )
    parser.add_argument(
        "--input-file",
        type=pathlib.Path,
        required=True,
        help="Path to input parquet file"
    )
    parser.add_argument(
        "--output-file",
        type=pathlib.Path,
        required=True,
        help="Path to output JSON file"
    )
    args = parser.parse_args()
    return args


def read_parquet(input_file, required_columns):
    """Read parquet file and validate required columns exist."""
    logging.info(f"Reading parquet file: {input_file}")
    df = pd.read_parquet(input_file)

    # Validate required columns
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        raise ValueError(
            f"Required columns {missing_columns} not found in dataframe"
        )

    logging.info(f"Loaded {len(df)} rows")
    return df


# ============================================================================
# CIK MODE - Functionality for Debt Tracker
# ============================================================================

def extract_filter_parquet_cik(df):
    """Extract investor_name and investor_cik pairs (CIK mode)."""
    # Extract investor_name and investor_cik columns
    subset = df[["investor_name", "investor_cik"]].copy()

    # Filter out rows where investor_cik is null or empty
    subset = subset[subset["investor_cik"].notna() & (subset["investor_cik"] != "")]
    logging.info(f"Found {len(subset)} rows with valid CIKs")

    # Convert investor_cik to string to ensure JSON serialization
    subset["investor_cik"] = subset["investor_cik"].astype(str)

    # Remove "CIK" prefix from CIK values (e.g., "CIK0001546531" -> "0001546531")
    subset["investor_cik"] = subset["investor_cik"].str.replace("^CIK", "", regex=True)

    # Remove duplicates AFTER normalization to catch formatting differences
    subset = subset.drop_duplicates(subset=["investor_name", "investor_cik"])
    logging.info(f"After normalization and deduplication: {len(subset)} unique investor_name/CIK pairs")

    # Group by investor_name and aggregate CIKs into a list
    result = subset.groupby("investor_name")["investor_cik"].apply(list).to_dict()

    return result


def save_result_cik(result, output_file):
    """Save CIK results to JSON."""
    # Save to JSON
    logging.info(f"Writing {len(result)} unique investor names to: {output_file}")
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)
    logging.info(f"Successfully wrote {len(result)} investor records to {output_file}")

    # Print sample statistics
    total_ciks = sum(len(ciks) for ciks in result.values())
    logging.info(f"Total investors: {len(result.keys())}")
    logging.info(f"Total CIKs: {total_ciks}")
    logging.info(f"Average CIKs per investor: {total_ciks / len(result):.2f}")


# ============================================================================
# RECORD MODE - Functionality for Shareholder Tracker
# ============================================================================

def extract_filter_parquet_record(df):
    """
    Extract issuer_name, ticker, MIC, and local_id for record matching.

    Deduplicates by (issuer_name, stock_ticker) combo; retains all unique combos
    (e.g. APPLE INC + AAPL and APPLE INC + AAPL2 are both kept).

    Returns:
        Dict with issuer_name as key, list of {ticker, mic, local_id} as value
        Example: {"ACTIVE BIOTECH AB": [{"ticker": "ACTI", "mic": "XSTO", "local_id": 42}]}
    """
    # Extract relevant columns
    subset = df[["issuer_name", "stock_ticker"]].copy()

    # Filter out rows where issuer_name or stock_ticker is null or empty
    subset = subset[
        subset["issuer_name"].notna() &
        (subset["issuer_name"] != "") &
        subset["stock_ticker"].notna() &
        (subset["stock_ticker"] != "")
    ]
    logging.info(f"Found {len(subset)} rows with valid issuer_name and stock_ticker")

    # Remove duplicate (issuer_name, stock_ticker) combos; retain all unique combos
    subset = subset.drop_duplicates(subset=["issuer_name", "stock_ticker"], keep="first")
    logging.info(f"After deduplication by issuer_name+stock_ticker: {len(subset)} unique combos")

    # Parse ticker and MIC
    parsed_records = []
    bonds_filtered = 0
    invalid_format = 0

    for idx, row in subset.iterrows():
        ticker, mic = parse_ticker_and_mic(row["stock_ticker"])

        if ticker is None:
            # Check if it was filtered as bond
            if is_bond_security(row["stock_ticker"]):
                bonds_filtered += 1
            else:
                invalid_format += 1
            continue

        parsed_records.append({
            "issuer_name": row["issuer_name"],
            "ticker": ticker,
            "mic": mic,
            "local_id": int(idx)  # Store original parquet row index
        })

    logging.info(f"Parsed {len(parsed_records)} equity securities")
    logging.info(f"Filtered out {bonds_filtered} bond securities")
    logging.info(f"Skipped {invalid_format} records with invalid format")

    # Group by issuer_name; each issuer can have multiple tickers
    # {"issuer_name": [{"ticker": "ACTI", "mic": "XSTO", "local_id": 42}, ...]}
    result = {}
    for record in parsed_records:
        entry = {"ticker": record["ticker"], "mic": record["mic"], "local_id": record["local_id"]}
        result.setdefault(record["issuer_name"], []).append(entry)

    print("RESULT: ", result)
    exit()

    return result


def parse_ticker_and_mic(ticker_str):
    """
    Parse stock_ticker into ticker symbol and MIC code.

    Args:
        ticker_str: Stock ticker string (e.g., "ACTI SS" or "AAPL")

    Returns:
        Tuple of (ticker, mic) or (None, None) if invalid

    Examples:
        "ACTI SS" -> ("ACTI", "XSTO")
        "AAPL" -> ("AAPL", None)  # No MIC = let PermID API determine exchange
        "WEC 4.375 06/01/29" -> (None, None)  # Bond, filtered out
    """
    if not ticker_str or not isinstance(ticker_str, str):
        return None, None

    # Filter out bond securities
    if is_bond_security(ticker_str):
        return None, None

    parts = ticker_str.strip().split()

    if len(parts) == 1:
        # US ticker with no suffix - no MIC specified (let API determine)
        return parts[0], None

    if len(parts) == 2:
        # Ticker with exchange suffix
        ticker = parts[0]
        exchange = parts[1]
        # Look up MIC code, default to None if unknown (let API determine)
        mic = EXCHANGE_TO_MIC.get(exchange, None)
        return ticker, mic

    # More than 2 parts - unexpected format, skip
    return None, None


def is_bond_security(ticker_str):
    """
    Determine if a stock_ticker value represents a bond security.

    Bond securities have numeric values (coupon rates) or date patterns.
    Examples: "WEC 4.375 06/01/29", "MET F PERP A"

    Returns True if bond, False if equity
    """
    if not ticker_str or not isinstance(ticker_str, str):
        return False

    parts = ticker_str.strip().split()

    if len(parts) <= 1:
        return False

    # Check for numeric values (coupon rates) or date patterns
    for part in parts[1:]:  # Skip first part (ticker symbol)
        # Check for decimal numbers (coupon rates like "4.375", "7.5")
        if re.match(r'^\d+(\.\d+)?$', part):
            return True
        # Check for date patterns (MM/DD/YY)
        if re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}$', part):
            return True
        # Check for "PERP" (perpetual bonds)
        if part.upper() == "PERP":
            return True

    return False


def save_result_record(result, output_file):
    """Save record matching results to JSON."""
    total_combos = sum(len(entries) for entries in result.values())
    logging.info(f"Writing {len(result)} issuers ({total_combos} issuer+ticker combos) to: {output_file}")
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)
    logging.info(f"Successfully wrote {len(result)} issuer records to {output_file}")

    # Print sample statistics
    mic_counts = {}
    for entries in result.values():
        for entry in entries:
            mic = entry.get("mic", "UNKNOWN")
            mic_counts[mic] = mic_counts.get(mic, 0) + 1

    logging.info(f"Total unique issuers: {len(result)}")
    logging.info("MIC distribution:")
    for mic, count in sorted(mic_counts.items(), key=lambda x: x[1], reverse=True):
        logging.info(f"  {mic}: {count}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Main function to process parquet data and extract identifiers."""
    start = datetime.datetime.now()
    args = get_args()

    for key, value in args.__dict__.items():
        logging.info(f"{key}: {value}")

    if args.type == "cik":
        # CIK mode - original functionality
        df = read_parquet(args.input_file, required_columns=["investor_name", "investor_cik"])
        result = extract_filter_parquet_cik(df)
        save_result_cik(result, args.output_file)

    elif args.type == "record":
        # Record mode - new functionality
        df = read_parquet(args.input_file, required_columns=["issuer_name", "stock_ticker"])
        result = extract_filter_parquet_record(df)
        save_result_record(result, args.output_file)

    end = datetime.datetime.now()
    logging.info(f"Elapsed time: {end - start}")


if __name__ == "__main__":
    main()
