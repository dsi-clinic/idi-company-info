"""Builds configured IdentifierPipeline instances from a top-level OrchestratorConfig."""

import pathlib
from dataclasses import dataclass

from idi_company_info.processors.identifier import IdentifierPipeline
from idi_company_info.processors.registry import IDENTIFIER_REGISTRY, IdentifierType
from idi_company_info.processors.types import ApiCredentials, BatchConfig, FilePaths


@dataclass
class OrchestratorConfig:
    """Configuration for a single orchestrator run."""

    input_file: str | pathlib.Path
    output_dir: str | pathlib.Path
    identifier_type: IdentifierType
    api_key: str
    geonames_user: str
    batch_size: int = 2450
    buffer_size: int = 500
    threshold_days: int | None = None
    match_score_threshold: int = 1


class IdentifierFactory:
    """Builds a configured IdentifierPipeline instance from an OrchestratorConfig.

    Single responsibility: translate orchestrator-level config into the
    dataclasses expected by the IdentifierPipeline base class, then instantiate the
    correct subclass.
    """

    @staticmethod
    def build(config: OrchestratorConfig) -> IdentifierPipeline:
        """Build and return the appropriate IdentifierPipeline for the given config.

        Args:
            config: Orchestrator configuration.

        Returns:
            A fully configured IdentifierPipeline subclass instance.

        Raises:
            KeyError: If config.identifier_type is not in IDENTIFIER_REGISTRY.
        """
        spec = IDENTIFIER_REGISTRY[config.identifier_type]

        output_base = str(config.output_dir)
        if output_base.startswith("s3://"):
            base = output_base.rstrip("/")
            file_paths = FilePaths(
                input_file=str(config.input_file),
                result_file=f"{base}/company_info/{spec.result_filename}",
                permid_file=f"{base}/permid_data/{spec.permid_filename}",
                failure_file=f"{base}/failures/{spec.failure_filename}",
            )
        else:
            output_path = pathlib.Path(config.output_dir)
            file_paths = FilePaths(
                input_file=str(config.input_file),
                result_file=str(output_path / "company_info" / spec.result_filename),
                permid_file=str(output_path / "permid_data" / spec.permid_filename),
                failure_file=str(output_path / "failures" / spec.failure_filename),
            )

        batch_config = BatchConfig(
            batch_size=config.batch_size,
            buffer_size=config.buffer_size,
            threshold_days=config.threshold_days,
        )

        api_credentials = ApiCredentials(
            api_key=config.api_key,
            geonames_user=config.geonames_user,
        )

        return spec.cls(
            file_paths=file_paths,
            batch_config=batch_config,
            api_credentials=api_credentials,
            query_type=spec.query_type,
            match_score_threshold=config.match_score_threshold,
        )
