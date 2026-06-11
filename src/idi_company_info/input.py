"""Input classes for loading and normalising parquet data into the Company Info pipeline.

Each subclass handles a specific parquet schema and returns a ``{name: [identifier, ...]}``
dict consumed by the pipeline's batch processing layer.
"""

# Standard library imports
import re
from abc import ABC, abstractmethod

# Third party imports
import pandas as pd
import pyarrow.dataset as ds
from idi_ftm2j_shared.logs import get_logger


class Input(ABC):
    """Abstract base class for all pipeline input sources."""

    def __init__(self, input_file) -> None:
        """"""
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

    def _extract_filter_parquet_cik(self, df: pd.DataFrame) -> dict[str, list[str]]:
        """Filter and normalise CIK rows, returning ``{investor_name: [cik, ...]}``.

        Drops rows with null or empty CIKs, strips the ``"CIK"`` prefix, deduplicates,
        then groups by investor name.

        Args:
            df: DataFrame containing ``investor_name`` and ``investor_cik`` columns.

        Returns:
            Mapping of investor name to list of normalised CIK strings.
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

    def load_data(self) -> dict[str, list[str]]:
        """Load the shareholder parquet and return ``{investor_name: [cik, ...]}``."""
        input_df = self.read_parquet(
            self.input_file, required_columns=["investor_name", "investor_cik"]
        )
        self.logger.info("Loaded %s rows", len(input_df))

        result = self._extract_filter_parquet_cik(input_df)
        return result


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
    """Input for CDT debt-instrument shards partitioned by ``cik_shard=*``.

    ``input_file`` must point to the shard root directory (local or ``s3://``),
    not a single file. Expects columns ``company_name`` and ``cik``.
    Returns ``{company_name: [cik, ...]}``.
    """

    @property
    def identifier_type(self) -> str:
        """Get the identifier type.

        Returns:
            The identifier type.
        """
        return "cik"

    @staticmethod
    def read_parquet(input_file: str, required_columns: list[str]) -> pd.DataFrame:
        """Read all ``cik_shard=*`` partitions under ``input_file`` as a single DataFrame.

        Overrides the base implementation because CDT output is a partitioned dataset
        rather than a single file. ``required_columns`` is accepted for interface
        compatibility but ignored — only ``company_name`` and ``cik`` are projected.

        Args:
            input_file: Path to the shard root directory (local path or ``s3://`` URI).
            required_columns: Unused; present for interface compatibility.

        Returns:
            DataFrame with columns ``company_name`` and ``cik``.
        """
        # A pyarrow dataset over the root reads every cik_shard partition in one go,
        # whether shard_root is local or on S3 (s3fs resolves the s3:// scheme).
        dataset = ds.dataset(input_file, format="parquet")
        table = dataset.to_table(columns=["company_name", "cik"])
        df = table.to_pandas()
        return df

    def _extract_filter_parquet_cik(self, df: pd.DataFrame) -> dict[str, list[str]]:
        """Filter and deduplicate CDT rows, returning ``{company_name: [cik, ...]}``.

        Drops rows where ``company_name`` is null or the literal string ``"nan"``
        (written by upstream pandas when the field was null at serialisation time),
        and rows with empty CIKs.

        Args:
            df: DataFrame with ``company_name`` and ``cik`` columns.

        Returns:
            Mapping of company name to list of CIK strings.
        """
        # Extract company and cik columns
        subset = df[["company_name", "cik"]].copy()
        self.logger.info("Found %s total rows", len(subset))

        # Filter out rows with null or empty values
        subset = subset[
            (subset["company_name"].notna())
            & (subset["company_name"] != "nan")
            & (subset["cik"].astype(str) != "")
        ]
        self.logger.info("Found %s rows with valid CIKs", len(subset))

        # Remove duplicates
        subset["cik"] = subset["cik"].astype(str)    # convert identifier row to str
        subset["cik"] = subset["cik"].astype(str).str.zfill(10)    # Pad with zero
        subset = subset.drop_duplicates(subset=["company_name", "cik"])
        self.logger.info(
            "After normalization and deduplication: %s unique name/CIK pairs", len(subset)
        )

        return subset.groupby("company_name")["cik"].apply(list).to_dict()

    def load_data(self) -> dict[str, list[str]]:
        """Load all CDT debt-instrument shards and return ``{company_name: [cik, ...]}``."""
        input_df = self.read_parquet(
            self.input_file, required_columns=["cik", "company_name"]
        )
        return self._extract_filter_parquet_cik(input_df)


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

    def _extract_filter_parquet_cik(self, input_df: pd.DataFrame) -> dict[str, list[str]]:
        """Filter and deduplicate subsidiary rows, returning ``{parent_name: [cik, ...]}``.

        Args:
            input_df: DataFrame containing ``parent_name`` and ``parent_cik`` columns.

        Returns:
            Mapping of parent company name to list of CIK strings.
        """
        subset = input_df[["parent_name", "parent_cik"]].copy()
        self.logger.info("Found %s total rows", len(subset))

        # Filter out rows with null or empty values
        subset = subset[
            (subset["parent_name"].notna())
            & (subset["parent_cik"].astype(str) != "")
        ]
        self.logger.info("Found %s rows with valid CIKs", len(subset))

        # Remove duplicates
        subset["parent_cik"] = subset["parent_cik"].astype(str)    # convert identifier row to str
        subset = subset.drop_duplicates(subset=["parent_name", "parent_cik"])
        self.logger.info(
            "After normalization and deduplication: %s unique name/CIK pairs", len(subset)
        )

        return subset.groupby("parent_name")["parent_cik"].apply(list).to_dict()

    def load_data(self) -> dict[str, list[str]]:
        """Load the subsidiary parquet and return ``{parent_name: [cik, ...]}``."""
        input_df = self.read_parquet(
            self.input_file, required_columns=["parent_cik", "parent_name"]
        )
        result = self._extract_filter_parquet_cik(input_df)
        return result
