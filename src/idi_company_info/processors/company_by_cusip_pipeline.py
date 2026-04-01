"""Processes CUSIP identifiers for company information."""

# Standard library imports
import re

# Third party imports
import pandas as pd

# Application imports
from idi_company_info.processors.company_pipeline import CompanyPipeline

_TICKER_WITH_EXCHANGE_PARTS = 2


class CompanyByCusipPipeline(CompanyPipeline):
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
        """The identifier type.

        Returns:
            The identifier type. "cusip".
        """
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
        """Filter and deduplicate rows, build ticker maps, return ``{issuer_name: [cusip, ...]}``.

        Args:
            df: Input DataFrame.

        Returns:
            ``{issuer_name: [cusip, ...]}`` — plain CUSIP strings for BatchProcessing.
        """
        subset = self._filter_and_deduplicate(df)
        self._build_ticker_maps(subset)
        return self._group_by_issuer(subset)

    def _filter_and_deduplicate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Keep rows with all three fields present, cast types, and deduplicate.

        Args:
            df: Raw input DataFrame.

        Returns:
            Filtered and deduplicated DataFrame with columns
            ``["issuer_name", "security_cusip", "stock_ticker"]``.
        """
        subset = df[["issuer_name", "security_cusip", "stock_ticker"]].copy()
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
        self.logger.info(
            "After deduplication: %s unique issuer_name/cusip/ticker pairs", len(subset)
        )
        return subset

    def _build_ticker_maps(self, subset: pd.DataFrame) -> None:
        """Populate ``_raw_ticker_map`` and ``_std_ticker_map`` from the filtered subset.

        Warns if any CUSIP maps to more than one ticker (data quality issue).
        Both maps keep only the first occurrence per CUSIP.

        Args:
            subset: Deduplicated DataFrame from :meth:`_filter_and_deduplicate`.
        """
        self._warn_ambiguous_cusips(subset)

        cusip_first = subset.drop_duplicates(subset=["security_cusip"])
        self._raw_ticker_map: dict[str, str] = dict(
            zip(cusip_first["security_cusip"], cusip_first["stock_ticker"])
        )

        formatted = subset.copy()
        formatted["stock_ticker"] = formatted["stock_ticker"].apply(
            CompanyByCusipPipeline._parse_ticker_and_mic
        )
        formatted = formatted[formatted["stock_ticker"] != ""]

        cusip_first_std = formatted.drop_duplicates(subset=["security_cusip"])
        self._std_ticker_map: dict[str, str] = dict(
            zip(cusip_first_std["security_cusip"], cusip_first_std["stock_ticker"])
        )

    def _warn_ambiguous_cusips(self, subset: pd.DataFrame) -> None:
        """Log a warning if any CUSIP appears with more than one ticker.

        Args:
            subset: Deduplicated DataFrame from :meth:`_filter_and_deduplicate`.
        """
        counts = subset.groupby("security_cusip")["stock_ticker"].nunique()
        ambiguous = counts[counts > 1].index.tolist()
        if ambiguous:
            self.logger.warning(
                "%d CUSIP(s) mapped to multiple tickers — keeping first occurrence: %s",
                len(ambiguous),
                ambiguous,
            )

    def _group_by_issuer(self, subset: pd.DataFrame) -> dict[str, list[str]]:
        """Return ``{issuer_name: [cusip, ...]}`` restricted to CUSIPs with valid tickers.

        Applies ticker formatting and bond filtering so only CUSIPs that will
        produce a valid Record Match API row are included.

        Args:
            subset: Deduplicated DataFrame from :meth:`_filter_and_deduplicate`.

        Returns:
            Mapping of issuer name to list of CUSIP strings.
        """
        formatted = subset.copy()
        formatted["stock_ticker"] = formatted["stock_ticker"].apply(
            CompanyByCusipPipeline._parse_ticker_and_mic
        )
        formatted = formatted[formatted["stock_ticker"] != ""]
        self.logger.info(
            "After parsing and filtering: %s unique issuer_name/cusip pairs", len(formatted)
        )
        return formatted.groupby("issuer_name")["security_cusip"].apply(list).to_dict()

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

        if CompanyByCusipPipeline._is_bond_security(ticker_str):
            return ""

        parts = ticker_str.strip().split()

        if len(parts) == 1:
            return f"ticker:{parts[0]}"

        if len(parts) == _TICKER_WITH_EXCHANGE_PARTS:
            ticker, exchange = parts[0], parts[1]
            mic = CompanyByCusipPipeline.EXCHANGE_TO_MIC.get(exchange, "")
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
