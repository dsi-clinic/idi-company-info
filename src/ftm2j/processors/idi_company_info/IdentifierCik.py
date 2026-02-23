"""Processes CIK identifiers for company information."""

# Standard library imports
from typing import Any
from dataclasses import asdict

# Third party imports
import pandas as pd

# Application imports
from ftm2j.processors.idi_company_info.identifier import Identifier

class IdentifierCik(Identifier):

    @property
    def identifier_type(self) -> str:
        """Get the identifier type."""
        return "cik"

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

    def _build_query_params(self, identifier: str) -> dict[str, Any]:
        """Build the query parameters.
            Args:
                identifier: The identifier.

            Returns:
                The query parameters.
        """
        return {"q": f"cik:{identifier}", "format": "json"}


if __name__ == "__main__":
    from ftm2j.processors.idi_company_info.identifier import Identifier, FilePaths, BatchConfig, ApiCredentials

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


    # {"q": f"cik:{cik}", "format": "json"}