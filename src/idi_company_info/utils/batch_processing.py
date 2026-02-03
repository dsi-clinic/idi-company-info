"""Batch processing utilities for tracking and managing batch operations."""

import json
import logging
import pathlib
from typing import Any

def load_batch_tracking(batch_file: pathlib.Path) -> dict:
    """
    Load batch tracking data or create new tracking dict.

    Args:
        batch_file: Path to batch tracking JSON file

    Returns:
        Dictionary with batch tracking data
    """
    if batch_file.exists():
        logging.info(f"Loading existing batch tracking from: {batch_file}")
        with open(batch_file) as f:
            return json.load(f)
    else:
        logging.info("Creating new batch tracking file")
        return {}

def save_batch_tracking(batch_file: pathlib.Path, tracking_data: dict):
    """
    Save batch tracking data to file.

    Args:
        batch_file: Path to batch tracking JSON file
        tracking_data: Dictionary with batch tracking data
    """
    with open(batch_file, 'w') as f:
        json.dump(tracking_data, f, indent=2)
    logging.info(f"Saved batch tracking to: {batch_file}")

def get_unprocessed_investors(
    investor_data: dict[str, Any],
    batch_tracking: dict
) -> list[str]:
    """
    Get list of investors that haven't been processed yet.

    Args:
        investor_data: Dictionary mapping investor names to their data
        batch_tracking: Batch tracking dictionary

    Returns:
        List of unprocessed investor names
    """
    processed_investors = set()

    # Collect all processed investors from all batches
    for batch_info in batch_tracking.values():
        processed_investors.update(batch_info.get("processed_investors", []))

    # Find unprocessed investors
    all_investors = set(investor_data.keys())
    unprocessed = list(all_investors - processed_investors)

    logging.info(f"Total investors: {len(all_investors)}")
    logging.info(f"Already processed: {len(processed_investors)}")
    logging.info(f"Remaining to process: {len(unprocessed)}")

    return unprocessed

def load_existing_results(output_file: pathlib.Path) -> dict | list:
    """
    Load existing results or return appropriate empty structure.

    Args:
        output_file: Path to results JSON file

    Returns:
        Dictionary or list with existing results, or empty structure if file doesn't exist
    """
    if output_file.exists():
        logging.info(f"Loading existing results from: {output_file}")
        with open(output_file) as f:
            return json.load(f)
    else:
        logging.info("No existing results found, starting fresh")
        # Try to infer the structure from the file extension or default to dict
        return {}

def save_results(output_file: pathlib.Path, results: dict | list):
    """
    Save results to JSON file.

    Args:
        output_file: Path to output JSON file
        results: Results dictionary or list to save
    """
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    if isinstance(results, list):
        logging.info(f"Saved {len(results)} records to: {output_file}")
    else:
        logging.info(f"Saved results to: {output_file}")
