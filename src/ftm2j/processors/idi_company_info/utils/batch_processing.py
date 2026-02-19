"""Batch processing utilities for tracking and managing batch operations."""

import json
import pathlib
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)


def load_batch_tracking(batch_file: pathlib.Path) -> dict:
    """
    Load batch tracking data or create new tracking dict.

    Args:
        batch_file: Path to batch tracking JSON file

    Returns:
        Dictionary with batch tracking data
    """
    if batch_file.exists():
        logger.info(f"Loading existing batch tracking from: {batch_file}")
        with open(batch_file) as f:
            return json.load(f)
    else:
        logger.info("Creating new batch tracking file")
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
    logger.info(f"Saved batch tracking to: {batch_file}")

def get_unprocessed_investors(
    investor_data: dict[str, Any],
    batch_tracking: dict
) -> list[str]:
    """
    Get list of entities (investors/issuers) that haven't been processed yet.

    Args:
        investor_data: Dictionary mapping entity names to their data (investors or issuers)
        batch_tracking: Batch tracking dictionary

    Returns:
        List of unprocessed entity names
    """
    processed_entities = set()

    # Collect all processed entities from all batches
    # Support both "processed_investors" (CIK mode) and "processed_items" (record mode)
    for batch_info in batch_tracking.values():
        entities = batch_info.get("processed_investors") or batch_info.get("processed_items") or []
        processed_entities.update(entities)

    # Find unprocessed entities
    all_entities = set(investor_data.keys())
    unprocessed = list(all_entities - processed_entities)

    logger.info(f"Total entities: {len(all_entities)}")
    logger.info(f"Already processed: {len(processed_entities)}")
    logger.info(f"Remaining to process: {len(unprocessed)}")

    return unprocessed

def load_existing_results(output_file: pathlib.Path, default_type: str = "dict") -> dict | list:
    """
    Load existing results or return appropriate empty structure.

    Args:
        output_file: Path to results JSON file
        default_type: Type of empty structure to return if file doesn't exist ("dict" or "list")

    Returns:
        Dictionary or list with existing results, or empty structure if file doesn't exist
    """
    if output_file.exists():
        logger.info(f"Loading existing results from: {output_file}")
        with open(output_file) as f:
            return json.load(f)
    else:
        logger.info("No existing results found, starting fresh")
        if default_type == "list":
            return []
        else:
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
        logger.info(f"Saved {len(results)} records to: {output_file}")
    else:
        logger.info(f"Saved results to: {output_file}")
