"""Builds configured CompanyPipeline instances from a top-level OrchestratorConfig."""

# Application imports
from idi_company_info.company_pipeline import CompanyPipeline
from idi_company_info.paths import join_path
from idi_company_info.registry import INPUT_REGISTRY
from idi_company_info.types import (
    ApiCredentials,
    BatchConfig,
    FilePaths,
    OrchestratorConfig,
)


class PipelineFactory:
    """Builds a configured CompanyPipeline instance from an OrchestratorConfig.

    Single responsibility: translate orchestrator-level config into the
    dataclasses the CompanyPipeline expects, compose the input source's Input
    loader, and instantiate the pipeline.
    """

    @staticmethod
    def build(config: OrchestratorConfig) -> CompanyPipeline:
        """Build and return a configured CompanyPipeline for the given config.

        Args:
            config: Orchestrator configuration.

        Returns:
            A fully configured CompanyPipeline instance.

        Raises:
            KeyError: If config.input_type is not in INPUT_REGISTRY.
        """
        input_spec = INPUT_REGISTRY[config.input_type]
        input_source = input_spec.cls(config.input_file)

        output_subdir = join_path(config.output_dir, str(config.input_type).lower())
        file_paths = FilePaths(
            result_file=join_path(output_subdir, "permid_data.json"),
            permid_file=join_path(output_subdir, "permid_url.json"),
            failure_file=join_path(output_subdir, "failure.json"),
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
            match_score_threshold=config.match_score_threshold,
        )
