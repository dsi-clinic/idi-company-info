"""Processes CIK identifiers for company information."""

# Standard library imports
from typing import Any
from dataclasses import asdict

# Third party imports
import pandas as pd

# Application imports
from ftm2j.processors.idi_company_info.identifier import Identifier, BatchStatsPermid, Identifier, FilePaths, BatchConfig, ApiCredentials
from ftm2j.common.storage import save_json

class IdentifierCik(Identifier):

    @staticmethod
    def _read_parquet(input_file, required_columns):
        """Read parquet file and validate required columns exist."""
        df = pd.read_parquet(input_file)

        # Validate required columns
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise ValueError(
                f"Required columns {missing_columns} not found in dataframe"
            )

        return df

    def _extract_filter_parquet_cik(self,df):
        """Extract investor_name and investor_cik pairs (CIK mode)."""
        # Extract investor_name and investor_cik columns
        subset = df[["investor_name", "investor_cik"]].copy()

        # Filter out rows where investor_cik is null or empty
        subset = subset[subset["investor_cik"].notna() & (subset["investor_cik"] != "")]
        self.logger.info("Found %s rows with valid CIKs", len(subset))

        # Convert investor_cik to string to ensure JSON serialization
        subset["investor_cik"] = subset["investor_cik"].astype(str)

        # Remove "CIK" prefix from CIK values (e.g., "CIK0001546531" -> "0001546531")
        subset["investor_cik"] = subset["investor_cik"].str.replace("^CIK", "", regex=True)

        # Remove duplicates AFTER normalization to catch formatting differences
        subset = subset.drop_duplicates(subset=["investor_name", "investor_cik"])
        self.logger.info("After normalization and deduplication: %s unique investor_name/CIK pairs", len(subset))

        # Group by investor_name and aggregate CIKs into a list
        result = subset.groupby("investor_name")["investor_cik"].apply(list).to_dict()

        return result

    def load_data(self) -> dict[str, Any]:
        """Load the data from the input file."""

        df = self._read_parquet(self.file_paths.input_file, required_columns=["investor_name", "investor_cik"])
        self.logger.info("Loaded %s rows", len(df))

        result = self._extract_filter_parquet_cik(df)
        return result

    def retrieve_permid(self, entity_name: str, entity_data: list[str], batch_stats: BatchStatsPermid) -> dict[str, Any]:
        """Retrieve the PermID for the company."""
        batch_stats.total_ids += len(entity_data)

        # Remove duplicate CIKs before processing
        original_count = len(entity_data)
        entity_data = list(dict.fromkeys(entity_data))  # Preserves order while removing duplicates
        if len(entity_data) < original_count:
            self.logger.info("  Removed %s duplicate CIK(s) for %s", original_count - len(entity_data), entity_name)
            batch_stats.duplicates_ids_removed += 1

        # Query by CIK for entity PermID
        permid_data = {}
        for cik in entity_data:
            response = self.api_clients.entity_search.query_endpoint(params={"q": f"cik:{cik}", "format": "json"})
            success, permids = self._handle_api_response(
                response,
                entity_name,
                cik,
                parse_fn=self._parse_permid_entities,
                error_msg="PermID query error for entity %s with CIK %s: %s",
                no_match_msg="No PermID found for entity %s with CIK %s",
            )
            permid_data[cik] = permids or []
            if success:
                batch_stats.total_permids += 1
            else:
                batch_stats.total_permid_failed += 1

        return permid_data

    def retrieve_company_info(self, entity_name: str, permid_data: dict[str, Any], batch_stats: BatchStatsPermid) -> list[dict[str, Any]]:
        """Retrieve the company information."""
        company_info = []
        for cik, permid_list in permid_data.items():
            for permid in permid_list:
                response = self.api_clients.entity_lookup.query_endpoint(permid_url=permid)
                success, company_data = self._handle_api_response(
                    response,
                    entity_name,
                    permid,
                    parse_fn=self._parse_company_data(entity_name, cik, permid),
                    error_msg="Company info query error for entity %s with PermID %s: %s",
                    no_match_msg="No company data found for entity %s with PermID %s",
                )
                if success:
                    company_info.append(company_data)
                    batch_stats.total_company_info += 1
                else:
                    batch_stats.total_company_info_failed += 1

        return company_info

    def save_company_info(self, company_info: list[dict[str, Any]]) -> list[str]:
        """Save the company information."""
        save_json(self.file_paths.result_file, company_info)


if __name__ == "__main__":
    identifier = IdentifierCik(
        file_paths=FilePaths(input_file="/Users/REMOVED/Documents/workspace/11hour/ftm2j/data/company-info/shareholder_tracker/shareholder_tracker_release_20251218.parquet",
        result_file="/Users/REMOVED/Documents/workspace/11hour/ftm2j/data/company-info/processing_data/company_info_cik.json",
        batch_file="/Users/REMOVED/Documents/workspace/11hour/ftm2j/data/company-info/batch_data/batch_tracking_cik.json"),
        batch_config=BatchConfig(
            batch_size=10,
            buffer_size=5,
            threshold_days=30
        ),
        api_credentials=ApiCredentials(api_key="REMOVED",
        geonames_user="REMOVED")
    )
    identifier.run()
