"""Base class for retrieval operations."""

# Standard library imports
from typing import TYPE_CHECKING

# Application imports
from idi_company_info.common.failures import FailureRegistry
from idi_company_info.common.logs import get_logger
from idi_company_info.processors.types import BatchConfig, FilePaths

if TYPE_CHECKING:
    from idi_company_info.processors.identifier import ApiClients


class Retrieval:
    """Base class providing shared state for retrieval operations.

    Subclasses own their own retrieve and error-handling interfaces.
    """

    _HTTP_OK = 200

    def __init__(
        self,
        file_paths: FilePaths,
        batch_config: BatchConfig,
        api_clients: "ApiClients",
        failure_registry: FailureRegistry | None = None,
    ) -> None:
        """Initialize the Retrieval.

        Args:
            file_paths: The file paths.
            batch_config: The batch configuration.
            api_clients: The constructed API client instances.
            failure_registry: Optional registry for permanent failures.
        """
        self.file_paths = file_paths
        self.batch_config = batch_config
        self.api_clients = api_clients
        self.failure_registry = failure_registry
        self.logger = get_logger(self.__class__.__name__)
