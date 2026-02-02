#!/usr/bin/env python3
"""
Job #1: Extract investor names and CIKs from parquet data.

Reads parquet file, extracts unique investor_name and investor_cik pairs,
and saves as JSON with investor_name as key and list of associated CIKs as value.
"""

import argparse
import json
import logging
import pathlib

import pandas as pd

logging.getLogger().setLevel(logging.INFO)
logging.basicConfig(format='%(asctime)s,%(msecs)d %(module)s:%(lineno)d %(levelname)s %(message)s',
                    datefmt='%Y-%m-%dT%H:%M:%S',
                    level=logging.INFO)

def get_args():
    parser = argparse.ArgumentParser(
        description="Extract investor names and CIKs from parquet data"
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

def read_parquet(input_file):
    logging.info(f"Reading parquet file: {input_file}")
    df = pd.read_parquet(input_file)

    # Select relevant columns
    if "investor_name" not in df.columns or "investor_cik" not in df.columns:
        raise ValueError(
            "Required columns 'investor_name' and 'investor_cik' not found in dataframe"
        )

    logging.info(f"Loaded {len(df)} rows")
    return df

def extract_filter_parquet(df):
    # Extract investor_name and investor_cik columns
    subset = df[["investor_name", "investor_cik"]].copy()

    # Filter out rows where investor_cik is null or empty
    subset = subset[subset["investor_cik"].notna() & (subset["investor_cik"] != "")]
    logging.info(f"Found {len(subset)} rows with valid CIKs")

    # Remove duplicates where both investor_name and investor_cik are the same
    subset = subset.drop_duplicates(subset=["investor_name", "investor_cik"])
    logging.info(f"After removing duplicates: {len(subset)} unique investor_name/CIK pairs")

    # Convert investor_cik to string to ensure JSON serialization
    subset["investor_cik"] = subset["investor_cik"].astype(str)

    # Remove "CIK" prefix from CIK values (e.g., "CIK0001546531" -> "0001546531")
    subset["investor_cik"] = subset["investor_cik"].str.replace("^CIK", "", regex=True)

    # Group by investor_name and aggregate CIKs into a list
    result = subset.groupby("investor_name")["investor_cik"].apply(list).to_dict()

    return result

def save_result(result, output_file):
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

def main():
    """Main function to process parquet data and extract CIK information."""
    args = get_args()
    for key, value in args.__dict__.items():
        logging.info(f"{key}: {value}")

    # Read parquet file
    df = read_parquet(args.input_file)

    # Extract and filter investor_name and investor_cik columns
    result = extract_filter_parquet(df)

    # Save to JSON
    save_result(result, args.output_file)


if __name__ == "__main__":
    main()
