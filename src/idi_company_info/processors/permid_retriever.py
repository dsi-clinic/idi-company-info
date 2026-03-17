"""Retrieve PermID for company information."""

# Standard library imports
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from functools import cached_property
from typing import TYPE_CHECKING, Any, Protocol

# Third party imports
import pandas as pd

# Application imports
from idi_company_info.common.buffer import Buffer
from idi_company_info.common.failures import FailureClassifier, FailureRegistry, FailureType
from idi_company_info.common.logs import get_logger

_HTTP_OK = 200

if TYPE_CHECKING:
    from idi_company_info.processors.identifier import (
        ApiClients,
        BatchConfig,
        BatchStats,
        FilePaths,
    )


class EntitySearchContext(Protocol):
    """Context for the Entity Search API."""

    @property
    def api_clients(self) -> "ApiClients":
        """Get the API clients."""
        ...

    @property
    def failure_registry(self) -> "FailureRegistry | None":
        """Optional failure registry for do-not-retry list."""
        ...

    @property
    def file_paths(self) -> "FilePaths":
        """Get the file paths."""
        ...

    @property
    def batch_config(self) -> "BatchConfig":
        """Get the batch config."""
        ...

    def _build_query_params(self, identifier: str) -> dict[str, Any]:
        """Build the query parameters."""
        ...

    def _parse_permid_entities(self, response: dict) -> list[str]:
        """Parse the response."""
        ...

    def _handle_api_response(
        self, response: dict, entity_name: str, identifier: str, parse_fn: Callable[[dict], Any]
    ) -> tuple[bool, Any]:
        """Handle the API response."""
        ...

    def _handle_failures(
        self, response: dict, entity_name: str, identifier: str, permids: list[str]
    ) -> None:
        """Handle the failures."""
        ...


class PermidRetriever(ABC):
    """Strategy for retrieving PermIDs. Produces unified {identifier: [permid, ...]} format."""

    def __init__(self, context: EntitySearchContext, match_score_threshold: int = 1) -> None:
        """Initialize the PermidRetriever."""
        self._context = context
        self._match_score_threshold = match_score_threshold

    @cached_property
    def logger(self) -> logging.Logger:
        """Get the logger."""
        return get_logger("PermidRetriever")

    @abstractmethod
    def retrieve(
        self, entities_to_process: dict[str, Any], batch_size: int, batch_stats: "BatchStats"
    ) -> list[str]:
        """Retrieve PermIDs for the entities.

        Args:
            entities_to_process: The entities to process.
            batch_size: The batch size.
            batch_stats: The batch stats.

        Returns:
            A list of entities that were processed.
        """
        ...


class EntitySearchRetriever(PermidRetriever):
    """Retrieve PermIDs for entities using the Entity Search API."""

    def retrieve(
        self, entities_to_process: dict[str, Any], batch_size: int, batch_stats: "BatchStats"
    ) -> list[str]:
        """Retrieve PermIDs for entities using the Entity Search API.

        Args:
            entities_to_process: The entities to process.
            batch_size: The batch size.
            batch_stats: The batch stats.

        Returns:
            A list of entities that were processed.
        """
        batch = list(entities_to_process.keys())[:batch_size]
        self.logger.info("Retrieving PermIDs for %d entities", len(batch))

        buffer = Buffer(
            file_path=self._context.file_paths.permid_file,
            buffer_size=self._context.batch_config.buffer_size,
            mode="dict",
        )

        permid_data = {}
        for idx, entity_name in enumerate(batch, 1):
            identifier_list = entities_to_process[entity_name]
            self.logger.info(
                "[%d/%d] Processing: %s (%d)", idx, len(batch), entity_name, len(identifier_list)
            )
            permid_data[entity_name] = self._retrieve_permid_search(
                entity_name, identifier_list, batch_stats
            )
            buffer.add(data={entity_name: permid_data[entity_name]})

        if buffer._buffer:
            buffer.flush()
        return batch

    def _retrieve_permid_search(
        self, entity_name: str, identifier_list: list[str], batch_stats: "BatchStats"
    ) -> dict[str, Any]:
        """Retrieve the PermID for the entity.

        Args:
            entity_name: The entity name.
            identifier_list: The identifier list.
            batch_stats: The batch stats.
        """
        # Remove duplicate identifiers
        orig_length = len(identifier_list)
        identifier_list = list(set(identifier_list))
        if len(identifier_list) < orig_length:
            batch_stats.duplicates_ids_removed += orig_length - len(identifier_list)

        batch_stats.total_ids += len(identifier_list)

        permid_data = []
        for identifier in identifier_list:
            self._parse_api_response(entity_name, identifier, permid_data, batch_stats)

        return permid_data

    def _parse_api_response(
        self,
        entity_name: str,
        identifier: str,
        permid_data: dict[str, Any],
        batch_stats: "BatchStats",
    ) -> None:
        """Parse the API response.

        Modifies permid_data and batch_stats.

        Args:
            entity_name: The entity name.
            identifier: The identifier.
            permid_data: The PermID data.
            batch_stats: The batch stats.
        """
        query_params = self._context._build_query_params(identifier)
        response = self._context.api_clients.entity_search.query_endpoint(params=query_params)

        success, permids = self._context._handle_api_response(
            response,
            entity_name,
            identifier,
            parse_fn=self._context._parse_permid_entities,
        )

        permid_data.append({identifier: permids or []})
        if success:
            batch_stats.total_permids += 1
        else:
            batch_stats.total_permid_failed += 1
            if self._context.failure_registry:
                self._handle_failures(response, entity_name, identifier, permids)

    def _handle_failures(
        self, response: dict, entity_name: str, identifier: str, permids: list[str]
    ) -> None:
        """Handle the failures.

        Args:
            response: The response.
            entity_name: The entity name.
            identifier: The identifier.
            permids: The PermIDs.
        """
        if self._context.failure_registry:
            empty_data = not permids
            failure_type = FailureClassifier.classify_from_response(
                response, empty_data=empty_data, category="permid"
            )
            if not FailureClassifier.is_retryable(failure_type):
                self._context.failure_registry.add(
                    entity_name, identifier, reason=str(failure_type)
                )


class RecordMatchRetriever(PermidRetriever):
    """Retrieve PermIDs for entities using the Record Match API."""

    RECORD_BATCH_SIZE = 1000

    def retrieve(
        self, entities_to_process: dict[str, Any], batch_size: int, batch_stats: "BatchStats"
    ) -> list[str]:
        """Retrieve PermIDs for entities using the Record Match API.

        Args:
            entities_to_process: The entities to process.
            batch_size: The batch size.
            batch_stats: The batch stats.

        Returns:
            A list of entities that were processed.
        """
        items = list(entities_to_process.keys())[:batch_size]
        self.logger.info("Retrieving PermIDs for %d entities", len(items))

        # Build the flat record list first — each entity may have multiple identifiers,
        # so the total row count can exceed the entity count. Batch by rows, not entities.
        all_entities = [(item, entities_to_process[item]) for item in items]
        all_records = self._build_records(all_entities)

        total_records = len(all_records)
        total_batches = (total_records + self.RECORD_BATCH_SIZE - 1) // self.RECORD_BATCH_SIZE
        self.logger.info(
            "Processing %d records in %d API batches of up to %d rows each",
            total_records,
            total_batches,
            self.RECORD_BATCH_SIZE,
        )

        buffer = Buffer(
            file_path=self._context.file_paths.permid_file,
            buffer_size=self._context.batch_config.buffer_size,
            mode="dict",
        )

        permid_data = {}
        for batch_start in range(0, total_records, self.RECORD_BATCH_SIZE):
            batch_records = all_records[batch_start : batch_start + self.RECORD_BATCH_SIZE]
            batch_num = batch_start // self.RECORD_BATCH_SIZE + 1

            self.logger.info(
                "[%d/%d] Sending %d records to Record Match API",
                batch_num,
                total_batches,
                len(batch_records),
            )
            batch_permid_data = self._retrieve_record_match(batch_records, batch_stats)
            if batch_permid_data:
                permid_data.update(batch_permid_data)
                buffer.add(data=batch_permid_data)
            else:
                batch_stats.total_permid_failed += len(batch_records)

        if buffer._buffer:
            buffer.flush()
        batch_stats.total_permids += sum(len(permid_list) for permid_list in permid_data.values())
        return items

    def _retrieve_record_match(
        self, records: list[dict[str, Any]], batch_stats: "BatchStats"
    ) -> dict[str, Any]:
        """Send a pre-built flat list of records to the Record Match API.

        Args:
            records: The records to send (already built via _build_records).
            batch_stats: The batch stats.

        Returns:
            A dictionary of {entity_name: [permid, ...]}.
        """
        df = pd.DataFrame(records)
        csv_data = df.to_csv(index=False)
        return self._parse_response(csv_data, records, batch_stats)

    def _build_records(self, batch_entities: list[tuple[str, list[str]]]) -> list[dict[str, Any]]:
        """Build the records.

        Args:
            batch_entities: The batch entities to process.

        Returns:
            The records.
        """
        records = []
        for entity_name, identifier_list in batch_entities:
            for identifier in identifier_list:
                if self._context.identifier_type == "ticker":
                    standard_identifier = identifier
                elif self._context.identifier_type == "cik":
                    standard_identifier = f"Cik:{identifier}"
                else:
                    raise ValueError(f"Invalid identifier type: {self._context.identifier_type}")
                records.append(
                    {
                        "LocalID": identifier,
                        "Standard Identifier": standard_identifier,
                        "Name": entity_name,
                    }
                )
        return records

    def _parse_response(
        self, csv_data: str, records: list[dict[str, Any]], batch_stats: "BatchStats"
    ) -> dict[str, Any]:
        """Parse the Record Match response.

        Args:
            csv_data: The CSV data.
            batch_stats: The batch stats.
            records: The records.

        Returns:
            The parsed response.
        """
        parsed_response = None
        try:
            self.logger.info("Submitted %d record(s) to Record Match API", len(records))
            response = self._context.api_clients.record_match.query_endpoint(csv_data)
            if response["status_code"] == _HTTP_OK:
                full_response = response.get("data", {}).get("outputContentResponse", [])
                filtered_response = self._filter_record_match_response(full_response)

                removed_records = len(records) - len(filtered_response)
                self.logger.info(
                    "Removed %d records with score less than %d",
                    removed_records,
                    self._match_score_threshold,
                )
                if removed_records > 0:
                    batch_stats.total_permid_failed += removed_records

                # Add permanent failures (no permid) to do-not-retry registry
                if self._context.failure_registry:
                    self._handle_failures(filtered_response, full_response, records)

                parsed_response = self._parse_record_match_response(filtered_response)
                num_records = sum(len(permid_list) for permid_list in parsed_response.values())
                self.logger.info("Parsed %d records", num_records)

            else:
                record_list = [
                    (record["Name"], record["Standard Identifier"]) for record in records
                ]
                self.logger.error(
                    "Error retrieving PermIDs for %d records: %s", len(records), record_list
                )

        except Exception as e:
            self.logger.error("Error retrieving PermIDs for %d records: %s", len(records), e)

        return parsed_response

    def _filter_record_match_response(self, response: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter the response.

        Args:
            response: The response to filter.
        """
        matched_data = [
            match for match in response if self._parse_score(match) >= self._match_score_threshold
        ]
        return matched_data

    @staticmethod
    def _parse_score(match: dict) -> float:
        """Parse the score.

        Args:
            match: The match to parse.

        Returns:
            The score.
        """
        s = match.get("Match Score")
        return float(str(s).rstrip("%")) / 100 if s else 0

    def _handle_failures(
        self,
        filtered_response: list[dict[str, Any]],
        full_response: list[dict[str, Any]],
        records: list[dict[str, Any]],
    ) -> None:
        """Handle the failures.

        Args:
            filtered_response: The filtered response.
            full_response: The full response.
            records: The records.
        """
        matched_set = {(r["Input_Name"], r["Input_LocalID"]) for r in filtered_response}

        score_map = {
            (r["Input_Name"], r["Input_LocalID"]): self._parse_score(r) for r in full_response
        }

        for record in records:
            key = (record["Name"], record["LocalID"])

            reason = ""

            if key in matched_set:
                continue

            elif key in score_map:
                score = score_map[key]
                reason = f"{FailureType.LOW_MATCH_SCORE}:{score:.2f}"

            else:
                reason = str(FailureType.NO_PERMID)

            if reason:
                self._context.failure_registry.add(record["Name"], record["LocalID"], reason=reason)

    def _parse_record_match_response(self, response: dict) -> dict[str, Any]:
        """Parse the response.

        Args:
            response: The response to parse.
        """
        permid_data = {}
        for record in response:
            permid_data.setdefault(record["Input_Name"], []).append(
                {record["Input_LocalID"]: [record["Match OpenPermID"]]}
            )
        return permid_data
