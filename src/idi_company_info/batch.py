"""Batch processing utilities for tracking and managing batch operations."""

# Standard library imports
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Third party imports
from idi_ftm2j_shared.failures import FailureRegistry
from idi_ftm2j_shared.logs import get_logger
from idi_ftm2j_shared.storage import save_json

# Application imports
from idi_company_info.cache_keys import permid_cache_key


class BatchProcessing:
    """Tracks and manages batch processing state for entity pipelines."""

    def __init__(
        self,
        result_data: dict[str, dict],
        permid_data: dict[str, dict],
        identifier_type: str,
        threshold_days: int | None = 30,
        failure_registry: "FailureRegistry | None" = None,
    ) -> None:
        """Initialize the BatchProcessing.

        Args:
            result_data: The result_file, keyed by permid_url.
            permid_data: The permid_file, keyed by permid_cache_key. Owns the input
                (name, identifier) -> permid_url linkage.
            identifier_type: The identifier type (e.g. 'cik', 'cusip'), used to rebuild
                the prefixed LocalID for cache-key and failure-registry lookups.
            threshold_days: The staleness threshold in days (None disables staleness).
            failure_registry: Optional registry of permanent failures to exclude from retries.
        """
        self.result_data = result_data
        self.permid_data = permid_data
        self.identifier_type = identifier_type
        self.threshold_days = threshold_days
        self.failure_registry = failure_registry
        self.logger = get_logger(type(self).__name__)

    def get_unprocessed_entities(self, entity_data: dict[str, list[Any]]) -> dict[str, Any]:
        """Get input rows that still need processing.

        A (name, identifier) row is already processed when its permid is resolved AND every
        permid_url it resolved to is present in result_data. The linkage is read from
        permid_data (result_file no longer stores identifiers).

        Args:
            entity_data: Dict of entity_name -> list of identifiers (raw, unprefixed)

        Returns:
            Dict of entity_name -> list of identifiers still to process
        """
        unprocessed_entities: dict[str, list[str]] = {}
        new_entity_count = 0
        processed_count = 0
        for entity_name, identifier_list in entity_data.items():
            for identifier in identifier_list:
                new_entity_count += 1
                if self._is_processed(entity_name, identifier):
                    processed_count += 1
                else:
                    unprocessed_entities.setdefault(entity_name, []).append(identifier)

        # Exclude entries in do-not-retry registry
        unprocessed_entities, excluded = self._remove_failed_entities(unprocessed_entities)

        unprocessed_count = sum(len(identifiers) for identifiers in unprocessed_entities.values())
        self.logger.info("Total new entities: %s", new_entity_count)
        self.logger.info("Already processed entities: %s", processed_count)
        self.logger.info("Excluded %d entities from do-not-retry registry", excluded)
        self.logger.info("Remaining to process: %s", unprocessed_count)

        return unprocessed_entities

    def _is_processed(self, entity_name: str, identifier: str) -> bool:
        """True if this row's permid is resolved and all its permid_urls have results."""
        key = permid_cache_key(entity_name, f"{self.identifier_type}_{identifier}")
        entry = self.permid_data.get(key)
        urls = entry["result"] if entry else []
        return bool(urls) and all(url in self.result_data for url in urls)

    def _remove_failed_entities(
        self, entities: dict[str, list[str]]
    ) -> tuple[dict[str, list[str]], int]:
        """Remove entities that are in the do-not-retry registry.

        Args:
            entities: Dict of entity_name -> [identifier, ...].

        Returns:
            Tuple of (entities with failures removed, number of identifiers excluded).
        """
        if not self.failure_registry:
            return entities, 0

        before_count = sum(len(identifiers) for identifiers in entities.values())

        # Failures are keyed by the prefixed LocalID (e.g. "cik_0001234567") to match
        # what both retrieval stages register.
        result = {}
        for entity_name, identifiers in entities.items():
            for identifier in identifiers:
                key = (entity_name, f"{self.identifier_type}_{identifier}")
                if key not in self.failure_registry:
                    result.setdefault(entity_name, []).append(identifier)

        result_count = sum(len(identifiers) for identifiers in result.values())
        excluded = before_count - result_count

        return result, excluded

    def filter_stale_entities(self, result_file: Path, permid_file: Path) -> int:
        """Prune stale results and their permid_file keys in sync, then persist both.

        A result entry is stale when its ``last_processed`` is older than threshold_days.
        Stale ``permid_url``s are reverse-indexed to their permid_cache_key so the matching
        permid_file entry is removed too — that way the affected input rows fall back into
        the "needs permid" bucket and re-resolve. Mutates ``self.result_data`` and
        ``self.permid_data`` in place.

        Args:
            result_file: Path to the result_file (written if anything is pruned).
            permid_file: Path to the permid_file (written if anything is pruned).

        Returns:
            The number of result entries remaining after pruning.
        """
        if self.threshold_days is None:
            return len(self.result_data)

        self.logger.info("Checking for entities not updated in last %d days", self.threshold_days)
        stale_urls = self._get_stale_urls()
        self.logger.info("Located %s stale entities", len(stale_urls))
        if not stale_urls:
            return len(self.result_data)

        # Multiple keys can share one permid_url, so map to a list and prune
        url_to_keys: dict[str, list[str]] = defaultdict(list)
        for key, entry in self.permid_data.items():
            for url in entry["result"]:
                url_to_keys[url].append(key)  # Reverse index: permid_url -> [permid_cache_key, ...]

        stale_keys = {key for url in stale_urls for key in url_to_keys.get(url, [])}

        # Prune both caches in place.
        for url in stale_urls:
            self.result_data.pop(url, None)
        for key in stale_keys:
            self.permid_data.pop(key, None)

        save_json(str(result_file), self.result_data)
        save_json(str(permid_file), self.permid_data)
        self.logger.info(
            "Pruned %d stale result(s) and %d permid key(s) for re-processing",
            len(stale_urls),
            len(stale_keys),
        )

        return len(self.result_data)

    def _get_stale_urls(self) -> set[str]:
        """Return the set of permid_urls whose result is older than threshold_days."""
        if self.threshold_days is None:
            return set()

        threshold_date = datetime.now() - timedelta(days=self.threshold_days)
        stale: set[str] = set()
        for permid_url, entry in self.result_data.items():
            time_str = entry.get("result", {}).get("last_processed")
            if not time_str:
                continue
            try:
                time_dt = datetime.strptime(time_str, "%Y%m%dT%H%M%S")
            except ValueError:
                continue
            if time_dt < threshold_date:
                stale.add(permid_url)
        return stale


_CUSIP_ISSUER_LEN = 6  # CUSIP = 6-char issuer + 2-char issue + check digit


def find_cusip_collisions(permid_data: dict[str, dict]) -> dict[str, dict[str, str]]:
    """Find PermIDs reached by CUSIPs from more than one distinct issuer.

    A PermID legitimately collapses multiple CUSIPs only when they are share classes of the
    same issuer (same 6-char issuer prefix). A PermID reached by CUSIPs with *different*
    issuer prefixes is almost certainly a false Record Match (e.g. tickers ABL/ABLD wrongly
    resolving to Abbott). This is a pure post-run aggregation over permid_data — no API calls.

    Args:
        permid_data: The permid_file, keyed by permid_cache_key, each with a search block
            whose LocalID is the prefixed CUSIP (e.g. "cusip_00258Y104") and Name.

    Returns:
        {permid_url: {cusip: submitted_name, ...}} for each PermID reached by >1 distinct
        issuer prefix. The submitted name per CUSIP is included so the warning is legible.
    """
    url_to_members: dict[str, dict[str, str]] = {}
    for entry in permid_data.values():
        cusip = entry["search"]["LocalID"].split("_", 1)[-1]
        name = entry["search"]["Name"]
        for url in entry["result"]:
            url_to_members.setdefault(url, {}).setdefault(cusip, name)

    return {
        url: members
        for url, members in url_to_members.items()
        if len({cusip[:_CUSIP_ISSUER_LEN] for cusip in members}) > 1
    }
