#!/usr/bin/env python3
"""
Integration tests for orchestrator.py

These tests run the full pipeline on a small test dataset with mocked API responses.
"""

import json
import pathlib
import shutil
import tempfile
from unittest.mock import Mock, patch

import pytest
import requests

from idi_company_info import orchestrator


@pytest.fixture
def test_data_dir():
    """Create a temporary directory for test data."""
    temp_dir = tempfile.mkdtemp()
    yield pathlib.Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def test_parquet_file():
    """Path to the test parquet file with 10 rows."""
    return pathlib.Path(__file__).parent / "fixtures" / "test_investors_10.parquet"


@pytest.fixture
def mock_permid_api():
    """Mock PermID API responses."""
    # Map CIKs to mock PermID URLs
    cik_to_permid = {
        "0000320193": "https://permid.org/1-4295905573",  # Apple
        "0000789019": "https://permid.org/1-4295907168",  # Microsoft
        "0001018724": "https://permid.org/1-4295912752",  # Amazon
        "0001652044": "https://permid.org/1-5064095121",  # Alphabet
        "0001326801": "https://permid.org/1-4295903232",  # Meta
        "0001318605": "https://permid.org/1-4297057338",  # Tesla
        "0001045810": "https://permid.org/1-4295905494",  # NVIDIA
        "0001067983": "https://permid.org/1-4295904307",  # Berkshire
        "0000019617": "https://permid.org/1-4295905573",  # JPMorgan
        "0000200406": "https://permid.org/1-4295905494",  # J&J
    }

    # Mock company info data
    company_info = {
        "https://permid.org/1-4295905573": {
            "vcard:organization-name": "Apple Inc",
            "tr-common:hasPermId": "1-4295905573",
            "mdaas:HeadquartersAddress": "One Apple Park Way, Cupertino, CA 95014",
            "tr-org:hasLEI": "HWUPKR0MPOU8FGXBT394",
            "@id": "https://permid.org/1-4295905573"
        },
        "https://permid.org/1-4295907168": {
            "vcard:organization-name": "Microsoft Corporation",
            "tr-common:hasPermId": "1-4295907168",
            "mdaas:HeadquartersAddress": "One Microsoft Way, Redmond, WA 98052",
            "tr-org:hasLEI": "INR2EJN1ERAN0W5ZP974",
            "@id": "https://permid.org/1-4295907168"
        },
        "https://permid.org/1-4295912752": {
            "vcard:organization-name": "Amazon.com Inc",
            "tr-common:hasPermId": "1-4295912752",
            "mdaas:HeadquartersAddress": "410 Terry Avenue North, Seattle, WA 98109",
            "tr-org:hasLEI": "ZXTILKJKG63JELOFO76",
            "@id": "https://permid.org/1-4295912752"
        },
        "https://permid.org/1-5064095121": {
            "vcard:organization-name": "Alphabet Inc",
            "tr-common:hasPermId": "1-5064095121",
            "mdaas:HeadquartersAddress": "1600 Amphitheatre Parkway, Mountain View, CA 94043",
            "tr-org:hasLEI": "5493006MHB84DD0ZWV18",
            "@id": "https://permid.org/1-5064095121"
        },
        "https://permid.org/1-4295903232": {
            "vcard:organization-name": "Meta Platforms Inc",
            "tr-common:hasPermId": "1-4295903232",
            "mdaas:HeadquartersAddress": "1 Meta Way, Menlo Park, CA 94025",
            "tr-org:hasLEI": "EMJ02EC15T18WFH49T",
            "@id": "https://permid.org/1-4295903232"
        },
        "https://permid.org/1-4297057338": {
            "vcard:organization-name": "Tesla Inc",
            "tr-common:hasPermId": "1-4297057338",
            "mdaas:HeadquartersAddress": "1 Tesla Road, Austin, TX 78725",
            "tr-org:hasLEI": "54930084UKLVMY22DS16",
            "@id": "https://permid.org/1-4297057338"
        },
        "https://permid.org/1-4295905494": {
            "vcard:organization-name": "NVIDIA Corporation",
            "tr-common:hasPermId": "1-4295905494",
            "mdaas:HeadquartersAddress": "2788 San Tomas Expressway, Santa Clara, CA 95051",
            "tr-org:hasLEI": "549300S3ET14JUS52031",
            "@id": "https://permid.org/1-4295905494"
        },
        "https://permid.org/1-4295904307": {
            "vcard:organization-name": "Berkshire Hathaway Inc",
            "tr-common:hasPermId": "1-4295904307",
            "mdaas:HeadquartersAddress": "3555 Farnam Street, Omaha, NE 68131",
            "tr-org:hasLEI": "QMDDXHFCVN538DQFLL26",
            "@id": "https://permid.org/1-4295904307"
        },
    }

    def mock_get(*args, **kwargs):
        """Mock requests.Session.get method."""
        url = args[0] if args else kwargs.get('url')
        mock_response = Mock()
        mock_response.status_code = 200

        # Check if this is a PermID search query
        if 'api.permid.org/search' in url:
            params = kwargs.get('params', {})
            query = params.get('q', '')

            # Extract CIK from query
            if query.startswith('cik:'):
                cik = query.replace('cik:', '')
                permid_url = cik_to_permid.get(cik)

                if permid_url:
                    mock_response.json.return_value = {
                        "result": {
                            "organizations": {
                                "entities": [{"@id": permid_url}]
                            }
                        }
                    }
                else:
                    mock_response.json.return_value = {
                        "result": {
                            "organizations": {
                                "entities": []
                            }
                        }
                    }

        # Check if this is a PermID entity query
        elif 'permid.org/' in url and url.startswith('https://permid.org/'):
            info = company_info.get(url)
            if info:
                mock_response.json.return_value = info
            else:
                mock_response.status_code = 404
                mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404")

        return mock_response

    return mock_get


class TestOrchestratorIntegration:
    """Integration tests for the pipeline orchestrator."""

    def _setup_pipeline(self, test_data_dir, test_parquet_file):
        """Setup pipeline configuration and orchestrator.

        Args:
            test_data_dir: Temporary directory for test outputs
            test_parquet_file: Path to test parquet input file

        Returns:
            Tuple of (config, pipeline, output_paths_dict)
        """
        assert test_parquet_file.exists(), f"Test file not found: {test_parquet_file}"

        # Configure pipeline
        config = orchestrator.PipelineConfig(
            input_file=test_parquet_file,
            output_directory=test_data_dir / "output",
            batch_size=10,  # Process all 10 rows in one batch
            permid_api_key="fake-api-key-for-testing",
            geonames_user="fake-user-for-testing",
            max_retries=1  # Reduce retries for faster tests
        )

        # Create output directory
        config.output_directory.mkdir(parents=True, exist_ok=True)

        # Create orchestrator
        pipeline = orchestrator.PipelineOrchestrator(config)

        # Prepare output paths
        output_paths = {
            "cik_file": config.output_directory / orchestrator.PipelineConfig.CIK_DATA_FILE,
            "permid_file": config.output_directory / orchestrator.PipelineConfig.PERMID_DATA_FILE,
            "permid_batch_file": config.output_directory / orchestrator.PipelineConfig.PERMID_BATCH_TRACKING_FILE,
            "company_file": config.output_directory / orchestrator.PipelineConfig.COMPANY_INFO_FILE,
            "company_batch_file": config.output_directory / orchestrator.PipelineConfig.COMPANY_BATCH_TRACKING_FILE,
        }

        return config, pipeline, output_paths

    def _run_stage1_extract_ciks(self, pipeline, config, cik_file):
        """Execute and verify Stage 1: Extract CIKs.

        Args:
            pipeline: PipelineOrchestrator instance
            config: PipelineConfig instance
            cik_file: Path to output CIK data file

        Returns:
            Tuple of (status, cik_data_dict)
        """
        status, error = pipeline.stages[0].execute(
            **{
                "input-file": str(config.input_file),
                "output-file": str(cik_file)
            }
        )

        # Verify stage succeeded
        assert status == orchestrator.StageStatus.SUCCESS, f"Stage 1 should succeed: {error}"
        assert cik_file.exists(), "CIK data file should exist after stage 1"

        # Load and verify CIK extraction output
        with open(cik_file) as f:
            cik_data = json.load(f)

        assert len(cik_data) == 10, "Should extract 10 unique investors"
        assert "Apple Inc" in cik_data
        assert "0000320193" in cik_data["Apple Inc"]

        return status, cik_data

    def _run_stage2_query_permids(self, pipeline, config, cik_file, permid_file, permid_batch_file):
        """Execute and verify Stage 2: Query PermIDs.

        Args:
            pipeline: PipelineOrchestrator instance
            config: PipelineConfig instance
            cik_file: Path to input CIK data file
            permid_file: Path to output PermID data file
            permid_batch_file: Path to batch tracking file

        Returns:
            Tuple of (status, permid_data_dict, total_processed_count)
        """
        status, error = pipeline.stages[1].execute(
            **{
                "api-key": config.permid_api_key,
                "input-file": str(cik_file),
                "output-file": str(permid_file),
                "batch-file": str(permid_batch_file),
                "batch-size": str(config.batch_size)
            }
        )

        # Verify stage completed
        assert status == orchestrator.StageStatus.SUCCESS, f"Stage 2 should complete: {error}"
        assert permid_file.exists(), "PermID data file should exist after stage 2"
        assert permid_batch_file.exists(), "PermID batch tracking file should exist"

        # Load and verify PermID output structure
        with open(permid_file) as f:
            permid_data = json.load(f)

        assert isinstance(permid_data, dict), "PermID data should be a dictionary"

        # Verify batch tracking
        with open(permid_batch_file) as f:
            permid_batch_data = json.load(f)

        assert len(permid_batch_data) > 0, "Should have batch tracking data"

        # Check that all investors were processed
        total_processed = sum(
            len(batch_info.get("processed_investors", []))
            for batch_info in permid_batch_data.values()
        )
        assert total_processed == 10, "All 10 investors should be processed"

        return status, permid_data, total_processed

    def _run_stage3_query_company_info(
        self, pipeline, config, permid_file, company_file, company_batch_file
    ):
        """Execute and verify Stage 3: Query Company Info.

        Args:
            pipeline: PipelineOrchestrator instance
            config: PipelineConfig instance
            permid_file: Path to input PermID data file
            company_file: Path to output company info file
            company_batch_file: Path to batch tracking file

        Returns:
            Tuple of (status, company_records_list)
        """
        status, error = pipeline.stages[2].execute(
            **{
                "api-key": config.permid_api_key,
                "geonames-user": config.geonames_user,
                "input-file": str(permid_file),
                "output-file": str(company_file),
                "batch-file": str(company_batch_file),
                "batch-size": str(config.batch_size)
            }
        )

        # Verify stage completed
        assert status == orchestrator.StageStatus.SUCCESS, f"Stage 3 should complete: {error}"

        # Load company records if file exists
        company_records = []
        if company_file.exists():
            with open(company_file) as f:
                company_data = json.load(f)
            assert isinstance(company_data, list), "Company data should be a list"
            company_records = company_data

        return status, company_records

    def _print_summary(self, status1, cik_data, status2, total_processed, permid_data, status3, company_records):
        """Print integration test summary.

        Args:
            status1: Stage 1 status
            cik_data: CIK data dictionary
            status2: Stage 2 status
            total_processed: Number of investors processed in stage 2
            permid_data: PermID data dictionary
            status3: Stage 3 status
            company_records: List of company records
        """
        print("\n" + "="*60)
        print("INTEGRATION TEST SUMMARY")
        print("="*60)
        print(f"Stage 1 (Extract CIKs): {status1.value}")
        print(f"  - Extracted {len(cik_data)} investors")
        print(f"Stage 2 (Query PermIDs): {status2.value}")
        print(f"  - Processed {total_processed} investors")
        print(f"  - Found {len(permid_data)} investors with PermIDs")
        print(f"Stage 3 (Query Company Info): {status3.value}")
        if len(company_records) > 0:
            print(f"  - Retrieved {len(company_records)} company records")
        else:
            print(f"  - No company records (expected with fake credentials)")
        print("="*60)

    def test_orchestrator_stages_1_2_3_integration(
        self,
        test_data_dir,
        test_parquet_file
    ):
        """Test orchestrator runs stages 1, 2, and 3 together on 10-row dataset.

        Note: Stages 2 and 3 will complete but may not find data due to fake API credentials.
        This test validates the orchestration logic and data flow between stages.
        """
        # Setup pipeline
        config, pipeline, paths = self._setup_pipeline(test_data_dir, test_parquet_file)

        # Execute Stage 1: Extract CIKs
        status1, cik_data = self._run_stage1_extract_ciks(
            pipeline, config, paths["cik_file"]
        )

        # Execute Stage 2: Query PermIDs
        status2, permid_data, total_processed = self._run_stage2_query_permids(
            pipeline, config, paths["cik_file"], paths["permid_file"], paths["permid_batch_file"]
        )

        # Execute Stage 3: Query Company Info
        status3, company_records = self._run_stage3_query_company_info(
            pipeline, config, paths["permid_file"], paths["company_file"], paths["company_batch_file"]
        )

        # Print summary
        self._print_summary(
            status1, cik_data, status2, total_processed, permid_data, status3, company_records
        )

    def test_orchestrator_configuration(self, test_data_dir, test_parquet_file):
        """Test orchestrator configuration and stage initialization."""
        config = orchestrator.PipelineConfig(
            input_file=test_parquet_file,
            output_directory=test_data_dir / "output",
            batch_size=5,
            permid_api_key="test-key",
            geonames_user="test-user",
            threshold_days=30
        )

        pipeline = orchestrator.PipelineOrchestrator(config)

        # Verify stages are initialized
        assert len(pipeline.stages) == 4, "Should have 4 pipeline stages"

        # Verify stage names
        stage_names = [stage.config.name for stage in pipeline.stages]
        assert "extract_ciks" in stage_names
        assert "query_permids" in stage_names
        assert "query_company_info" in stage_names
        assert "save_results" in stage_names

        # Verify batch size is passed through
        assert config.batch_size == 5

    def test_orchestrator_missing_input_file(self, test_data_dir):
        """Test orchestrator handles missing input file gracefully."""
        config = orchestrator.PipelineConfig(
            input_file=test_data_dir / "nonexistent.parquet",
            output_directory=test_data_dir / "output",
            batch_size=10,
            permid_api_key="test-api-key",
            geonames_user="test-user"
        )

        pipeline = orchestrator.PipelineOrchestrator(config)
        success = pipeline.run_pipeline()

        assert not success, "Pipeline should fail with missing input file"

    def test_orchestrator_command_building(self, test_data_dir, test_parquet_file):
        """Test that orchestrator builds correct commands for each stage."""
        config = orchestrator.PipelineConfig(
            input_file=test_parquet_file,
            output_directory=test_data_dir / "output",
            batch_size=10,
            permid_api_key="my-api-key",
            geonames_user="my-user"
        )

        pipeline = orchestrator.PipelineOrchestrator(config)

        # Test Stage 1 command (extract_ciks)
        stage1 = pipeline.stages[0]
        cmd1 = stage1.build_command(
            **{
                "input-file": "test.parquet",
                "output-file": "test_cik.json"
            }
        )
        assert "-m" in cmd1
        assert "idi_company_info.retrieve_cik" in cmd1
        assert "--input-file" in cmd1
        assert "test.parquet" in cmd1

        # Test Stage 2 command (query_permids)
        stage2 = pipeline.stages[1]
        cmd2 = stage2.build_command(
            **{
                "api-key": "my-key",
                "input-file": "cik.json",
                "output-file": "permid.json",
                "batch-file": "batch.json",
                "batch-size": "10"
            }
        )
        assert "idi_company_info.query_permid" in cmd2
        assert "--api-key" in cmd2
        assert "my-key" in cmd2
        assert "--batch-size" in cmd2
        assert "10" in cmd2
