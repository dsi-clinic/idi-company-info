"""Processes CIK identifiers for company information."""

# Standard library imports
from typing import Any

# Application imports
from ftm2j.processors.idi_company_info.identifier import Identifier

class IdentifierCusip(Identifier):

    @property
    def identifier_type(self) -> str:
        """Get the identifier type.

        Returns:
            The identifier type.
        """
        return "cusip"

    def _extract_filter_parquet_cusip(self,df):
        """Extract issuer_name and security_cusip pairs (CUSIP mode).

        Args:
            df: The dataframe to extract the data from.

        Returns:
            A dictionary with issuer_name as key and a list of security_cusip as value.
        """
        # Extract issuer_name and security_cusip columns
        subset = df[["issuer_name", "security_cusip"]].copy()

        # Filter out rows where security_cusip is null or empty
        subset = subset[subset["security_cusip"].notna() & (subset["security_cusip"] != "")]
        self.logger.info("Found %s rows with valid CUSIPs", len(subset))

        # Convert security_cusip to string to ensure JSON serialization
        subset["security_cusip"] = subset["security_cusip"].astype(str)

        # Remove duplicates AFTER normalization to catch formatting differences
        subset = subset.drop_duplicates(subset=["issuer_name", "security_cusip"])
        self.logger.info("After normalization and deduplication: %s unique issuer_name/CUSIP pairs", len(subset))

        # Group by issuer_name and aggregate CUSIPs into a list
        result = subset.groupby("issuer_name")["security_cusip"].apply(list).to_dict()

        return result

    def load_data(self) -> dict[str, Any]:
        """Load the data from the input file.

        Returns:
            A dictionary with issuer_name as key and a list of security_cusip as value.
        """

        df = self.read_parquet(self.file_paths.input_file, required_columns=["issuer_name", "security_cusip"])
        self.logger.info("Loaded %s rows", len(df))

        result = self._extract_filter_parquet_cusip(df)
        return result

    def _build_query_params(self, identifier: str) -> dict[str, Any]:
        """Build the query parameters.

        Args:
            identifier: The identifier.

        Returns:
            The query parameters.
        """
        return {"q": identifier, "format": "json"}
