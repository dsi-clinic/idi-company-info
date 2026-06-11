"""Builds configured CompanyPipeline instances from a top-level OrchestratorConfig."""

# Standard library imports
import pathlib

# Application imports
from idi_company_info.company_pipeline import CompanyPipeline
from idi_company_info.input import Input
from idi_company_info.registry import INPUT_REGISTRY
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


class PipelineFactory:
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
        input_spec = INPUT_REGISTRY[config.input_type]
        input_source = input_spec.cls(config.input_file)

        output_subdir = _join(config.output_dir, str(config.input_type).lower())
        file_paths = FilePaths(
            result_file=_join(output_subdir, "permid_data.json"),
            permid_file=_join(output_subdir, "permid_url.json"),
            failure_file=_join(output_subdir, "failure.json"),
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

        return CompanyPipeline(
            input_source=input_source,
            file_paths=file_paths,
            batch_config=batch_config,
            api_credentials=api_credentials,
            match_score_threshold=config.match_score_threshold
        )
