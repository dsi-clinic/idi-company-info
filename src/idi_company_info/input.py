"""Input classes for loading and normalising parquet data into the Company Info pipeline.

Each subclass handles a specific parquet schema and returns a ``{name: [identifier, ...]}``
dict consumed by the pipeline's batch processing layer.
"""

# Standard library imports
import re
from abc import ABC, abstractmethod

# Third party imports
import pandas as pd
from idi_ftm2j_shared.logs import get_logger


class Input(ABC):
    """Abstract base class for all pipeline input sources."""

    def __init__(self, input_file: str) -> None:
        """Store the input parquet path (local or s3://)."""
        self.input_file = input_file
        self.logger = get_logger(type(self).__name__)

    @property
    @abstractmethod
    def identifier_type(self) -> str:
        """Get the identifier type."""
        ...

    @property
    def std_ticker_map(self) -> dict[str, str]:
        """Formatted Standard Identifier strings keyed by local ID.

        Overridden by subclasses that need to supply auxiliary search identifiers
        to PermidRetrieval (e.g. CompanyByCusipPipeline supplies CUSIP → ticker string).
        """
        return {}

    @abstractmethod
    def load_data(self) -> dict[str, list[str]]:
        """Load the input source and return ``{entity_name: [identifier, ...]}``."""
        ...

    @staticmethod
    def read_parquet(input_file: str, required_columns: list[str]) -> pd.DataFrame:
        """Read parquet file and validate required columns exist.

        Args:
            input_file: The input file to read.
            required_columns: The required columns to validate.

        Returns:
            The dataframe with the required columns.
        """
        input_df = pd.read_parquet(input_file)

        # Validate required columns
        missing_columns = [col for col in required_columns if col not in input_df.columns]
        if missing_columns:
            raise ValueError(f"Required columns {missing_columns} not found in dataframe")

        return input_df

    def _filter_group_cik(
        self,
        df: pd.DataFrame,
        name_col: str,
        cik_col: str,
        strip_cik_prefix: bool = False,
    ) -> dict[str, list[str]]:
        """Filter, normalise, and group CIK rows into ``{name: [cik, ...]}``.

        Shared by all CIK-based inputs. Drops rows with null/empty names or CIKs,
        optionally strips a leading ``"CIK"`` prefix, zero-pads CIKs to 10 digits,
        deduplicates after normalisation, then groups by name.

        Args:
            df: DataFrame containing ``name_col`` and ``cik_col``.
            name_col: Name of the entity-name column (e.g. ``"company_name"``).
            cik_col: Name of the CIK column (e.g. ``"cik"``).
            strip_cik_prefix: If True, strip a leading ``"CIK"`` (e.g. ``"CIK0001..." -> "0001..."``).

        Returns:
            Mapping of entity name to list of normalised CIK strings.
        """
        subset = df[[name_col, cik_col]].copy()
        self.logger.info("Found %s total rows", len(subset))

        # Cast to string up front so the null/junk filter works on one form
        subset[name_col] = subset[name_col].astype(str)
        subset[cik_col] = subset[cik_col].astype(str)

        # Filter out rows with null or empty values
        subset = subset[
            ~subset[name_col].isin(["", "nan", "None"])
            & ~subset[cik_col].isin(["", "nan", "None"])
        ]
        self.logger.info("Found %s rows with valid CIKs", len(subset))

        # Normalise CIKs
        if strip_cik_prefix:
            subset[cik_col] = subset[cik_col].str.replace("^CIK", "", regex=True)
        subset[cik_col] = subset[cik_col].str.zfill(10)  # Pad with zero

        # Deduplicate AFTER normalisation to catch formatting differences
        subset = subset.drop_duplicates(subset=[name_col, cik_col])
        self.logger.info(
            "After normalization and deduplication: %s unique name/CIK pairs", len(subset)
        )

        return subset.groupby(name_col)[cik_col].apply(list).to_dict()


class ShareholderInputCik(Input):
    """Input for shareholder parquet files keyed by CIK.

    Expects columns: ``investor_name``, ``investor_cik``.
    Returns ``{investor_name: [cik, ...]}``.
    """

    @property
    def identifier_type(self) -> str:
        """Get the identifier type.

        Returns:
            The identifier type.
        """
        return "cik"

    def load_data(self) -> dict[str, list[str]]:
        """Load the shareholder parquet and return ``{investor_name: [cik, ...]}``."""
        input_df = self.read_parquet(
            self.input_file, required_columns=["investor_name", "investor_cik"]
        )
        self.logger.info("Loaded %s rows", len(input_df))

        return self._filter_group_cik(
            input_df, "investor_name", "investor_cik", strip_cik_prefix=True
        )


class ShareholderInputCusip(Input):
    """Input for shareholder parquet files keyed by CUSIP and stock ticker.

    Expects columns: ``issuer_name``, ``security_cusip``, ``stock_ticker``.
    Returns ``{issuer_name: [cusip, ...]}``.
    """

    EXCHANGE_TO_MIC = {
        "SS": "XSTO",  # Stockholm Stock Exchange
    }
    _TICKER_WITH_EXCHANGE_PARTS = 2

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
        """Populate ``_std_ticker_map`` from the filtered subset.

        Warns if any CUSIP maps to more than one ticker (data quality issue).
        Keeps only the first occurrence per CUSIP.

        Args:
            subset: Deduplicated DataFrame from :meth:`_filter_and_deduplicate`.
        """
        self._warn_ambiguous_cusips(subset)

        formatted = subset.copy()
        formatted["stock_ticker"] = formatted["stock_ticker"].apply(
            ShareholderInputCusip._parse_ticker_and_mic
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
            ShareholderInputCusip._parse_ticker_and_mic
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

        if ShareholderInputCusip._is_bond_security(ticker_str):
            return ""

        parts = ticker_str.strip().split()

        if len(parts) == 1:
            return f"ticker:{parts[0]}"

        if len(parts) == ShareholderInputCusip._TICKER_WITH_EXCHANGE_PARTS:
            ticker, exchange = parts[0], parts[1]
            mic = ShareholderInputCusip.EXCHANGE_TO_MIC.get(exchange, "")
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
        """Load the shareholder parquet and return ``{issuer_name: [cusip, ...]}``."""
        input_df = self.read_parquet(
            self.input_file,
            required_columns=["issuer_name", "security_cusip", "stock_ticker"],
        )
        self.logger.info("Loaded %s rows (ticker mode)", len(input_df))
        return self._extract_filter_parquet_ticker(input_df)


class CdtInput(Input):
    """Input for the CDT debt-instruments parquet file.

    ``input_file`` is a single parquet file (local or ``s3://``) — e.g.
    ``.../processors/cdt/debt-instruments/latest.parquet``. Expects columns
    ``company_name`` and ``cik``. Returns ``{company_name: [cik, ...]}``.
    """

    @property
    def identifier_type(self) -> str:
        """Get the identifier type.

        Returns:
            The identifier type.
        """
        return "cik"

    def load_data(self) -> dict[str, list[str]]:
        """Load the CDT debt-instruments parquet and return ``{company_name: [cik, ...]}``."""
        input_df = self.read_parquet(self.input_file, required_columns=["cik", "company_name"])
        return self._filter_group_cik(input_df, "company_name", "cik")


class SubsidiaryInput(Input):
    """Input for subsidiary relationship parquet files keyed by parent CIK.

    Expects columns: ``parent_name``, ``parent_cik``.
    Returns ``{parent_name: [cik, ...]}``.
    """

    @property
    def identifier_type(self) -> str:
        """Get the identifier type.

        Returns:
            The identifier type.
        """
        return "cik"

    def load_data(self) -> dict[str, list[str]]:
        """Load the subsidiary parquet and return ``{parent_name: [cik, ...]}``."""
        input_df = self.read_parquet(
            self.input_file, required_columns=["parent_cik", "parent_name"]
        )
        return self._filter_group_cik(input_df, "parent_name", "parent_cik")
