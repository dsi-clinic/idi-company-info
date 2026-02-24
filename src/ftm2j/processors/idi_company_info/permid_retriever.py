"""Retrieve PermID for company information."""

# Standard library imports
import logging
from abc import ABC, abstractmethod
from functools import cached_property
from typing import Any, Callable, Protocol, TYPE_CHECKING

# Application imports
from ftm2j.common.logs import get_logger
if TYPE_CHECKING:
    from ftm2j.processors.idi_company_info.identifier import BatchStats, ApiClients


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

    def __init__(self, context: EntitySearchContext):
        """Initialize the PermidRetriever."""
        self._context = context

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

        permid_data = {}
        for idx, entity_name in enumerate(batch, 1):
            identifier_list = entities_to_process[entity_name]
            self.logger.info(f"[{idx}/{len(batch)}] Processing: {entity_name} ({len(identifier_list)})")
            permid_data[entity_name] = self._retrieve_permid_search(entity_name, identifier_list, batch_stats)
        return permid_data

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

            permid_data.append((identifier, permids or []))
            if success:
                batch_stats.total_permids += 1
            else:
                batch_stats.total_permid_failed += 1
        return permid_data


class RecordMatchRetriever(PermidRetriever):
    """Retrieve PermIDs for entities using the Record Match API."""

    def retrieve(self, entities_to_process: dict[str, Any], batch_size: int, batch_stats: "BatchStats") -> dict[str, Any]:
        """Retrieve PermIDs for entities using the Record Match API.

        Args:
            entities_to_process: The entities to process.
            batch_size: The batch size.
            batch_stats: The batch stats.
        """
        ...