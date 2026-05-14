"""Builds configured CompanyPipeline instances from a top-level OrchestratorConfig."""

# Standard library imports
import pathlib

# Application imports
from idi_company_info.company_pipeline import CompanyPipeline
from idi_company_info.registry import IDENTIFIER_REGISTRY
from idi_company_info.types import (
    ApiCredentials,
    BatchConfig,
    FilePaths,
    OrchestratorConfig,
)


def _join(base: str | pathlib.Path, name: str) -> str:
    """Join a filename onto a base directory, supporting both local paths and s3:// URLs."""
    base_str = str(base)
    if base_str.startswith("s3://"):
        return f"{base_str.rstrip('/')}/{name}"
    return str(pathlib.Path(base_str) / name)


class IdentifierFactory:
    """Builds a configured CompanyPipeline instance from an OrchestratorConfig.

    Single responsibility: translate orchestrator-level config into the
    dataclasses expected by the CompanyPipeline base class, then instantiate the
    correct subclass.
    """

    @staticmethod
    def build(config: OrchestratorConfig) -> CompanyPipeline:
        """Build and return the appropriate CompanyPipeline for the given config.

        Args:
            config: Orchestrator configuration.

        Returns:
            A fully configured CompanyPipeline subclass instance.

        Raises:
            KeyError: If config.identifier_type is not in IDENTIFIER_REGISTRY.
        """
        spec = IDENTIFIER_REGISTRY[config.identifier_type]

        type_subdir = str(config.identifier_type)
        output_subdir = _join(config.output_dir, type_subdir)
        file_paths = FilePaths(
            input_file=str(config.input_file),
            result_file=_join(output_subdir, spec.result_filename),
            permid_file=_join(output_subdir, spec.permid_filename),
            failure_file=_join(config.failure_dir, spec.failure_filename),
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
            match_score_threshold=config.match_score_threshold,
        )
