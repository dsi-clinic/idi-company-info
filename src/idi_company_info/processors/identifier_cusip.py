"""Processes CUSIP identifiers for company information."""

# Standard library imports
import re

# Third party imports
import pandas as pd

# Application imports
from idi_company_info.processors.identifier import IdentifierPipeline

_TICKER_WITH_EXCHANGE_PARTS = 2


class IdentifierCusip(IdentifierPipeline):
    """Processes CUSIP/ticker identifiers via the PermID Record Match pipeline.

    load_data returns {issuer_name: [cusip, ...]} so that CUSIP is the
    stable identifier that flows through BatchProcessing, PermID retrieval, and
    company-info output.  Two auxiliary maps are built as side-effects:

    - _raw_ticker_map: CUSIP → raw ticker symbol (stored in output).
    - _std_ticker_map: CUSIP → formatted Standard Identifier for the API.
    """

    EXCHANGE_TO_MIC = {
        "SS": "XSTO",  # Stockholm Stock Exchange
    }

    @property
    def identifier_type(self) -> str:
        return "cusip"

    @property
    def std_ticker_map(self) -> dict[str, str]:
        """Formatted Standard Identifier strings keyed by CUSIP, for Record Match API calls."""
        return getattr(self, "_std_ticker_map", {})

    @property
    def raw_ticker_map(self) -> dict[str, str]:
        """Raw ticker symbols keyed by CUSIP, for storing in company info output."""
        return getattr(self, "_raw_ticker_map", {})

    def _extract_filter_parquet_ticker(self, df: pd.DataFrame) -> dict[str, list[str]]:
        """Extract issuer_name/CUSIP pairs and build ticker lookup maps.

        Sets two instance attributes as side-effects:
          - ``_raw_ticker_map``: CUSIP → raw ticker (e.g. ``"AAPL"``).
          - ``_std_ticker_map``: CUSIP → formatted Standard Identifier
            (e.g. ``"ticker:AAPL&&mic:XSTO"``).

        Args:
            df: Input DataFrame.

        Returns:
            ``{issuer_name: [cusip, ...]}`` — plain CUSIP strings for BatchProcessing.
        """
        subset = df[["issuer_name", "security_cusip", "stock_ticker"]].copy()

        # Keep only rows where issuer_name, CUSIP, and ticker are all present
        subset = subset[
            subset["issuer_name"].notna()
            & (subset["issuer_name"] != "")
            & subset["security_cusip"].notna()
            & (subset["security_cusip"] != "")
            & subset["stock_ticker"].notna()
            & (subset["stock_ticker"] != "")
        ]
        self.logger.info("Found %s rows with issuer_name, cusip and ticker", len(subset))

        subset["stock_ticker"] = subset["stock_ticker"].astype(str)
        subset = subset.drop_duplicates(subset=["issuer_name", "security_cusip", "stock_ticker"])
        self.logger.info("After deduplication: %s unique issuer_name/cusip/ticker pairs", len(subset))

        # Capture raw tickers BEFORE bond-filtering and formatting (stored in output)
        self._raw_ticker_map: dict[str, str] = dict(
            zip(subset["security_cusip"], subset["stock_ticker"])
        )

        # Format tickers into Standard Identifier strings and filter out bonds
        subset["stock_ticker"] = subset["stock_ticker"].apply(IdentifierCusip._parse_ticker_and_mic)
        subset = subset[subset["stock_ticker"] != ""]
        self.logger.info(
            "After parsing and filtering: %s unique issuer_name/cusip pairs", len(subset)
        )

        # Capture formatted Standard Identifiers AFTER filtering (used in API call)
        self._std_ticker_map: dict[str, str] = dict(
            zip(subset["security_cusip"], subset["stock_ticker"])
        )

        # Return plain CUSIP strings — BatchProcessing expects list[str]
        result: dict[str, list[str]] = (
            subset.groupby("issuer_name")["security_cusip"]
            .apply(list)
            .to_dict()
        )
        return result

    @staticmethod
    def _parse_ticker_and_mic(ticker_str: str) -> str:
        """Parse stock_ticker into a Standard Identifier string.

        Args:
            ticker_str: Raw stock ticker (e.g. ``"ACTI SS"`` or ``"AAPL"``).

        Returns:
            Formatted Standard Identifier (e.g. ``"ticker:ACTI&&mic:XSTO"``) or
            an empty string if the ticker represents a bond or is otherwise invalid.
        """
        if not ticker_str or not isinstance(ticker_str, str):
            return ""

        if IdentifierCusip._is_bond_security(ticker_str):
            return ""

        parts = ticker_str.strip().split()

        if len(parts) == 1:
            return f"ticker:{parts[0]}"

        if len(parts) == _TICKER_WITH_EXCHANGE_PARTS:
            ticker, exchange = parts[0], parts[1]
            mic = IdentifierCusip.EXCHANGE_TO_MIC.get(exchange, "")
            return f"ticker:{ticker}&&mic:{mic}" if mic else f"ticker:{ticker}"

        return ""

    @staticmethod
    def _is_bond_security(ticker_str: str) -> bool:
        """Return True if the ticker string represents a bond rather than an equity.

        Detects coupon rates (e.g. ``"4.375"``), maturity dates (``"06/01/29"``),
        and perpetual-bond markers (``"PERP"``).
        """
        if not ticker_str or not isinstance(ticker_str, str):
            return False

        parts = ticker_str.strip().split()
        if len(parts) <= 1:
            return False

        for part in parts[1:]:
            # Check if the part is a coupon rate (e.g. "4.375")
            if re.match(r"^\d+(\.\d+)?$", part):
                return True
            # Check if the part is a maturity date (e.g. "06/01/29")
            if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", part):
                return True
            # Check if the part is a perpetual-bond marker (e.g. "PERP")
            if part.upper() == "PERP":
                return True

        return False

    def load_data(self) -> dict[str, list[str]]:
        """Load the input parquet and return ``{issuer_name: [cusip, ...]}``."""
        df = self.read_parquet(
            self.file_paths.input_file,
            required_columns=["issuer_name", "security_cusip", "stock_ticker"],
        )
        self.logger.info("Loaded %s rows (ticker mode)", len(df))
        return self._extract_filter_parquet_ticker(df)
