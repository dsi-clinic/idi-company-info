"""Retrieve PermID for company information."""

# Standard library imports
import logging
from abc import ABC, abstractmethod
from functools import cached_property
from typing import Any, Callable, Protocol, TYPE_CHECKING

# Application imports
from ftm2j.common.logs import get_logger
from ftm2j.common.buffer import Buffer
if TYPE_CHECKING:
    from ftm2j.processors.idi_company_info.identifier import BatchStats, ApiClients

# Third party imports
import pandas as pd


class EntitySearchContext(Protocol):
    """Context for the Entity Search API."""

    @property
    def api_clients(self) -> "ApiClients":
        """Get the API clients."""
        ...

    def _build_query_params(self, identifier: str) -> dict[str, Any]:
        """Build the query parameters."""
        ...

    def _parse_permid_entities(self, response: dict) -> list[str]:
        """Parse the response."""
        ...

    def _handle_api_response(self, response: dict, entity_name: str, identifier: str, parse_fn: Callable[[dict], Any]) -> tuple[bool, Any]:
        """Handle the API response."""
        ...


class PermidRetriever(ABC):
    """Strategy for retrieving PermIDs. Produces unified {identifier: [permid, ...]} format."""

    def __init__(self, context: EntitySearchContext, match_score_threshold: int = 1):
        """Initialize the PermidRetriever."""
        self._context = context
        self._match_score_threshold = match_score_threshold

    @cached_property
    def logger(self) -> logging.Logger:
        """Get the logger."""
        return get_logger(__name__)

    @abstractmethod
    def retrieve(self, entities_to_process: dict[str, Any], batch_size: int, batch_stats: "BatchStats") -> dict[str, Any]:
        """Retrieve PermIDs for the entities.

        Args:
            entities_to_process: The entities to process.
            batch_size: The batch size.
            batch_stats: The batch stats.

        Returns:
            A dictionary of {identifier: [permid, ...]}.
        """
        ...


class EntitySearchRetriever(PermidRetriever):
    """Retrieve PermIDs for entities using the Entity Search API."""

    def retrieve(self, entities_to_process: dict[str, Any], batch_size: int, batch_stats: "BatchStats") -> dict[str, Any]:
        """Retrieve PermIDs for entities using the Entity Search API.

        Args:
            entities_to_process: The entities to process.
            batch_size: The batch size.
            batch_stats: The batch stats.
        """
        batch = list(entities_to_process.keys())[:batch_size]
        self.logger.info(f"Retrieving PermIDs for {len(batch)} entities")

        buffer = Buffer(
            file_path=self._context.file_paths.permid_file,
            buffer_size=self._context.batch_config.buffer_size,
            mode="dict"
        )

        permid_data = {}
        for idx, entity_name in enumerate(batch, 1):
            identifier_list = entities_to_process[entity_name]
            self.logger.info(f"[{idx}/{len(batch)}] Processing: {entity_name} ({len(identifier_list)})")
            permid_data[entity_name] = self._retrieve_permid_search(entity_name, identifier_list, batch_stats)
            buffer.add(data={entity_name: permid_data[entity_name]})

        buffer.flush()

    def _retrieve_permid_search(self, entity_name: str, identifier_list: list[str], batch_stats: "BatchStats") -> dict[str, Any]:
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
        return permid_data


class RecordMatchRetriever(PermidRetriever):
    """Retrieve PermIDs for entities using the Record Match API."""

    RECORD_BATCH_SIZE = 1000

    def retrieve(self, entities_to_process: dict[str, Any], batch_size: int, batch_stats: "BatchStats") -> dict[str, Any]:
        """Retrieve PermIDs for entities using the Record Match API.

        Args:
            entities_to_process: The entities to process.
            batch_size: The batch size.
            batch_stats: The batch stats.
        """
        items = list(entities_to_process.keys())[:batch_size]
        self.logger.info(f"Retrieving PermIDs for {len(items)} entities")

        total_batches = (len(items) + self.RECORD_BATCH_SIZE - 1) // self.RECORD_BATCH_SIZE
        self.logger.info(f"Processing {len(items)} entities in {total_batches} batches")

        buffer = Buffer(
            file_path=self._context.file_paths.permid_file,
            buffer_size=self._context.batch_config.buffer_size,
            mode="dict"
        )

        permid_data = {}
        for batch_start in range(0, len(items), self.RECORD_BATCH_SIZE):
            batch_items = items[batch_start : batch_start + self.RECORD_BATCH_SIZE]
            batch_entities = [(item, entities_to_process[item]) for item in batch_items]

            self.logger.info(f"[{batch_start +1}/{total_batches}] Processing: {len(batch_items)} entities")
            batch_permid_data = self._retrieve_record_match(batch_entities, batch_stats)
            if batch_permid_data:
                permid_data.update(batch_permid_data)
                buffer.add(data=batch_permid_data)
            else:
                batch_stats.total_permid_failed += 1

        buffer.flush()
        batch_stats.total_permids += sum(len(permid_list) for permid_list in permid_data.values())

    def _retrieve_record_match(self, batch_entities: list[tuple[str, list[str]]], batch_stats: "BatchStats") -> dict[str, Any]:
        """Retrieve the PermID for the records.

        Args:
            batch_entities: The batch entities to process.
        """
        records = []
        for entity_name, identifier_list in batch_entities:
            for identifier in identifier_list:
                records.append({
                    "LocalID": identifier,
                    "Standard Identifier": identifier if self._context.identifier_type == "cusip" else f"Cik:{identifier}",
                    "Name": entity_name
                })

        # Create CSV string
        df = pd.DataFrame(records)
        csv_data = df.to_csv(index=False)

        parsed_response = None
        try:
            response = self._context.api_clients.record_match.query_endpoint(csv_data)
            if response["status_code"] == 200:
                filtered_response = self._filter_record_match_response(response)

                removed_records = len(records) - len(filtered_response)
                self.logger.info(f"Removed %d records with score less than %d", removed_records, self._match_score_threshold)
                if removed_records > 0:
                    batch_stats.total_permid_failed += removed_records

                parsed_response = self._parse_record_match_response(filtered_response)
                self.logger.info(f"Parsed %d records", len(parsed_response))

            else:
                record_list = [(record["Name"], record["Standard Identifier"]) for record in records]
                self.logger.error(f"Error retrieving PermIDs for %d records: %s", len(records), record_list)

        except Exception as e:
            self.logger.error(f"Error retrieving PermIDs for %d records: %s", len(records), e)

        return parsed_response

    def _filter_record_match_response(self, response: dict) -> dict[str, Any]:
        """Filter the response.

        Args:
            response: The response to filter.
        """
        matched_data = [
            match for match in response.get("data", {}).get("outputContentResponse", [])
            if self._parse_score(match) >= self._match_score_threshold
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

    def _parse_record_match_response(self, response: dict) -> dict[str, Any]:
        """Parse the response.

        Args:
            response: The response to parse.
        """
        permid_data = {}
        for record in response:
            permid_data[record["Input_Name"]] = [{record["Input_LocalID"]: [record["Match OpenPermID"]]}]
        return permid_data
