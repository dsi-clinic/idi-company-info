"""Processes CIK identifiers for company information."""

# Standard library imports
from typing import Any

# Third party imports
import pandas as pd

# Application imports
from idi_company_info.company_pipeline import CompanyPipeline


class CompanyByCikPipeline(CompanyPipeline):
    """Processes CIK identifiers through the PermID entity search pipeline."""

    @property
    def identifier_type(self) -> str:
        """Get the identifier type.

        Returns:
            The identifier type.
        """
        return "cik"

    def _extract_filter_parquet_cik(self, df: pd.DataFrame) -> dict[str, list[str]]:
        """Extract investor_name and investor_cik pairs (CIK mode).

        Args:
            df: The dataframe to extract the data from.

        Returns:
            A dictionary with investor_name as key and a list of investor_cik as value.
        """
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
        self.logger.info(
            "After normalization and deduplication: %s unique investor_name/CIK pairs", len(subset)
        )

        # Group by investor_name and aggregate CIKs into a list
        result = subset.groupby("investor_name")["investor_cik"].apply(list).to_dict()

        return result

    def load_data(self) -> dict[str, Any]:
        """Load the data from the input file.

        Returns:
            A dictionary with investor_name as key and a list of investor_cik as value.
        """
        df = self.read_parquet(
            self.file_paths.input_file, required_columns=["investor_name", "investor_cik"]
        )
        self.logger.info("Loaded %s rows", len(df))

        result = self._extract_filter_parquet_cik(df)
        return result
