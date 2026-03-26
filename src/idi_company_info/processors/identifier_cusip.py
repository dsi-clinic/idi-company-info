"""Processes CUSIP identifiers for company information."""

# Standard library imports
import re
from typing import Any

# Third party imports
import pandas as pd

# Application imports
from idi_company_info.processors.identifier import IdentifierPipeline, QueryType

_TICKER_WITH_EXCHANGE_PARTS = 2


class IdentifierCusip(IdentifierPipeline):
    """Processes CUSIP identifiers for company information."""

    EXCHANGE_TO_MIC = {
        "SS": "XSTO",  # Stockholm Stock Exchange
    }

    @property
    def identifier_type(self) -> str:
        """Get the identifier type.

        Returns:
            The identifier type. "cusip".
        """
        return "cusip"

    def _extract_filter_parquet_ticker(self, df: pd.DataFrame) -> dict[str, list[str]]:
        """Extract issuer_name and stock_ticker pairs (ticker mode for Record Match).

        Keeps only rows with both issuer_name and stock_ticker. Discards rows without both.

        Args:
            df: The dataframe to extract the data from.

        Returns:
            A dictionary with issuer_name as key and a list of stock_ticker as value.
        """
        subset = df[["issuer_name", "stock_ticker"]].copy()

        # Filter: keep only rows where both issuer_name and stock_ticker are non-null and non-empty
        subset = subset[
            subset["issuer_name"].notna()
            & (subset["issuer_name"] != "")
            & subset["stock_ticker"].notna()
            & (subset["stock_ticker"] != "")
        ]
        self.logger.info("Found %s rows with issuer_name and ticker", len(subset))

        # Set stock_ticker to string and drop duplicates after formatting
        subset["stock_ticker"] = subset["stock_ticker"].astype(str)
        subset = subset.drop_duplicates(subset=["issuer_name", "stock_ticker"])
        self.logger.info("After deduplication: %s unique issuer_name/ticker pairs", len(subset))

        # Parse ticker to remove bond securities and extract ticker and mic
        subset["stock_ticker"] = subset["stock_ticker"].apply(
            lambda x: IdentifierCusip._parse_ticker_and_mic(x)
        )
        subset = subset[subset["stock_ticker"] != ""]
        self.logger.info(
            "After parsing and filtering: %s unique issuer_name/ticker pairs", len(subset)
        )

        # Group by issuer_name and aggregate tickers into a list
        result = subset.groupby("issuer_name")["stock_ticker"].apply(list).to_dict()
        return result

    @staticmethod
    def _parse_ticker_and_mic(ticker_str: str) -> str:
        """Parse stock_ticker into ticker symbol and MIC code.

        Args:
            ticker_str: Stock ticker string (e.g., "ACTI SS" or "AAPL")

        Returns:
            String (Ticker:ticker&&MIC:mic) or empty string if invalid

        Examples:
            "ACTI SS" -> "Ticker:ACTI&&MIC:XSTO"
            "AAPL" -> "Ticker:AAPL"
            "WEC 4.375 06/01/29" -> ""  # Bond, filtered out
        """
        if not ticker_str or not isinstance(ticker_str, str):
            return ""

        # Filter out bond securities
        if IdentifierCusip._is_bond_security(ticker_str):
            return ""

        parts = ticker_str.strip().split()

        if len(parts) == 1:
            # US ticker with no suffix - no MIC specified (let API determine)
            return f"ticker:{parts[0]}"

        elif len(parts) == _TICKER_WITH_EXCHANGE_PARTS:
            # Ticker with exchange suffix
            ticker = parts[0]
            exchange = parts[1]
            # Look up MIC code, default to None if unknown (let API determine)
            mic = IdentifierCusip.EXCHANGE_TO_MIC.get(exchange, "")
            if mic:
                return f"ticker:{ticker}&&mic:{mic}"
            else:
                return f"ticker:{ticker}"

        else:
            # More than 2 parts - unexpected format, skip
            return ""

    @staticmethod
    def _is_bond_security(ticker_str: str) -> bool:
        """Determine if a stock_ticker value represents a bond security.

        Bond securities have numeric values (coupon rates) or date patterns.
        Examples: "WEC 4.375 06/01/29", "MET F PERP A"

        Returns True if bond, False if equity
        """
        if not ticker_str or not isinstance(ticker_str, str):
            return False

        parts = ticker_str.strip().split()

        if len(parts) <= 1:
            return False

        # Check for numeric values (coupon rates) or date patterns
        for part in parts[1:]:  # Skip first part (ticker symbol)
            # Check for decimal numbers (coupon rates like "4.375", "7.5")
            if re.match(r"^\d+(\.\d+)?$", part):
                return True
            # Check for date patterns (MM/DD/YY)
            if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", part):
                return True
            # Check for "PERP" (perpetual bonds)
            if part.upper() == "PERP":
                return True

        return False

    def load_data(self) -> dict[str, Any]:
        """Load the data from the input file.

        Returns:
            A dictionary with issuer_name as key and a list of identifiers (tickers and cusips) as value.
        """
        df = self.read_parquet(
            self.file_paths.input_file,
            required_columns=["issuer_name", "security_cusip", "stock_ticker"],
        )
        self.logger.info("Loaded %s rows (ticker mode)", len(df))
        return self._extract_filter_parquet_ticker(df)
