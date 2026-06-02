"""Retrieve PermIDs for entities using the LSEG Record Match API."""

# Standard library imports
from typing import TYPE_CHECKING, Any

# Third party imports
import pandas as pd
from idi_ftm2j_shared.failures import FailureRegistry

# Application imports
from idi_company_info.buffer import Buffer
from idi_company_info.cache_keys import permid_cache_key
from idi_company_info.failures import CompanyInfoFailureClassifier, FailureType
from idi_company_info.retrieval import Retrieval
from idi_company_info.types import BatchConfig, BatchStats, FilePaths, MergeStrategy, PermidEntry

if TYPE_CHECKING:
    from idi_company_info.company_pipeline import ApiClients


class PermidRetrieval(Retrieval):
    """Retrieve PermIDs for entities using the Record Match API."""

    RECORD_BATCH_SIZE = 1000

    def __init__(
        self,
        file_paths: FilePaths,
        batch_config: BatchConfig,
        api_clients: "ApiClients",
        identifier_type: str,
        failure_registry: FailureRegistry | None = None,
        match_score_threshold: float = 1.0,
        std_ticker_map: dict[str, str] | None = None,
    ) -> None:
        """Initialize the PermidRetrieval.

        Args:
            file_paths: The file paths.
            batch_config: The batch configuration.
            api_clients: The constructed API client instances.
            identifier_type: The identifier type (e.g. 'cik', 'cusip').
            failure_registry: Optional registry for permanent failures.
            match_score_threshold: Minimum match score (0–1) to accept a result.
            std_ticker_map: Optional CUSIP → formatted Standard Identifier map used
                when identifier_type is 'cusip' to supply the ticker search term
                while keeping CUSIP as LocalID.
        """
        super().__init__(file_paths, batch_config, api_clients, failure_registry)
        self.identifier_type = identifier_type
        self._match_score_threshold = match_score_threshold
        self._std_ticker_map: dict[str, str] = std_ticker_map or {}

    def retrieve(
        self,
        entities_to_process: dict[str, Any],
        batch_size: int,
        batch_stats: BatchStats,
    ) -> dict[str, Any]:
        """Retrieve PermIDs for entities using the Record Match API.

        Args:
            entities_to_process: Mapping of entity name to identifier list.
            batch_size: Maximum number of entities to process this run.
            batch_stats: Accumulator for run-level statistics.

        Returns:
            The permid data.
        """
        all_entities = [
            (entity_name, identifier)
            for entity_name, identifiers in entities_to_process.items()
            for identifier in identifiers
        ]
        batch_entities = all_entities[:batch_size]

        all_records, total_records, total_batches = self._retrieve_records(batch_entities)

        buffer = Buffer(
            file_path=self.file_paths.permid_file,
            merge=MergeStrategy.PERMID,
            buffer_size=self.batch_config.buffer_size,
        )

        for batch_start in range(0, total_records, self.RECORD_BATCH_SIZE):
            batch_records = all_records[batch_start : batch_start + self.RECORD_BATCH_SIZE]
            batch_num = batch_start // self.RECORD_BATCH_SIZE + 1

            batch_permid_data = self._record_match_batch(
                batch_records, batch_num, total_batches, batch_stats
            )

            if batch_permid_data:
                buffer.add(data=batch_permid_data)
                batch_stats.total_permids += sum(
                    len(v["result"]) for v in batch_permid_data.values()
                )

        permid_data = buffer.load_all()
        return permid_data

    def _retrieve_records(
        self, batch_entities: list[tuple[str, str]]
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Retrieve records from the Record Match API.

        Args:
            batch_entities: The entities to process.

        Returns:
            A tuple of (all_records, total_records, total_batches).
        """
        self.logger.info("Retrieving PermIDs for %d entities", len(batch_entities))
        all_records = self._build_records(batch_entities)

        total_records = len(all_records)
        total_batches = (total_records + self.RECORD_BATCH_SIZE - 1) // self.RECORD_BATCH_SIZE
        self.logger.info(
            "Processing %d records in %d API batches of up to %d rows each",
            total_records,
            total_batches,
            self.RECORD_BATCH_SIZE,
        )
        return all_records, total_records, total_batches

    def _build_records(self, batch_entities: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """Build the flat record list for the Record Match API payload.

        Args:
            batch_entities: List of (entity_name, identifier) tuples.

        Returns:
            Flat list of record dicts ({LocalID, Standard Identifier, Name}) for the CSV.
        """
        records = []
        for entity_name, identifier in batch_entities:
            if self.identifier_type == "cusip":
                local_id = f"cusip_{identifier}"  # CUSIP is the stable LocalID
                standard_identifier = self._std_ticker_map[identifier]
            elif self.identifier_type == "cik":
                local_id = f"cik_{identifier}"
                standard_identifier = f"Cik:{identifier}"
            else:
                raise ValueError(f"Invalid identifier type: {self.identifier_type}")

            records.append(
                {
                    "LocalID": local_id,
                    "Standard Identifier": standard_identifier,
                    "Name": entity_name,
                }
            )
        return records

    def _record_match_batch(
        self,
        batch_records: list[dict[str, Any]],
        batch_num: int,
        total_batches: int,
        batch_stats: BatchStats,
    ) -> dict[str, Any]:
        """Send records to the Record Match API and parse the response.

        Args:
            batch_records: The batch records to process.
            batch_num: The batch number.
            total_batches: The total batches.
            batch_stats: Accumulator for run-level statistics.
            buffer: The buffer.

        Returns:
            The parsed response.
        """
        self.logger.info(
            "[%d/%d] Sending %d records to Record Match API",
            batch_num,
            total_batches,
            len(batch_records),
        )
        batch_permid_data = self._retrieve_record_match(batch_records, batch_stats)
        return batch_permid_data

    def _retrieve_record_match(
        self, records: list[dict[str, Any]], batch_stats: BatchStats
    ) -> dict[str, Any]:
        """Convert records to CSV and send to the Record Match API.

        Args:
            records: Pre-built flat record list from _build_records.
            batch_stats: Accumulator for run-level statistics.

        Returns:
            Mapping of permid_cache_key → {"search": {...}, "result": [permid_url, ...]}.
        """
        records_df = pd.DataFrame(records)
        csv_data = records_df.to_csv(index=False)

        parsed_response: dict[str, Any] = {}
        try:
            self.logger.info("Submitted %d record(s) to Record Match API", len(records))
            response = self.api_clients.record_match.query_endpoint(csv_data)
            if response["status_code"] == self._HTTP_OK:
                parsed_response = self._parse_response(response, records, batch_stats)

            else:
                record_list = [
                    (record["Name"], record["Standard Identifier"]) for record in records
                ]
                self.logger.error(
                    "Error retrieving PermIDs for %d records: %s", len(records), record_list
                )
                batch_stats.total_permid_failed += len(records)

        except Exception as e:
            self.logger.error("Error retrieving PermIDs for %d records: %s", len(records), e)
            batch_stats.total_permid_failed += len(records)

        return parsed_response

    def _parse_response(
        self, response: dict[str, Any], records: list[dict[str, Any]], batch_stats: BatchStats
    ) -> dict[str, Any]:
        """Parse the response from the Record Match API.

        Args:
            response: The response from the Record Match API.
            records: The records to parse.
            batch_stats: Accumulator for run-level statistics.

        Returns:
            The parsed response.
        """
        full_response = response.get("data", {}).get("outputContentResponse", [])

        # Retrieve record scores and separate into passed and low score lists
        passed, low_score = [], []
        for r in full_response:
            (passed if self._parse_score(r) >= self._match_score_threshold else low_score).append(r)

        # Separate records that were not matched by the API from the original records
        api_ids = {(r.get("Input_Name"), r.get("Input_LocalID")) for r in full_response}
        no_match_records = [r for r in records if (r["Name"], r["LocalID"]) not in api_ids]

        # Log the number of records removed due to low score or no match
        removed_records = len(low_score) + len(no_match_records)
        self.logger.info(
            "Removed %d records with score less than %s",
            len(low_score),
            self._match_score_threshold,
        )
        self.logger.info("Removed %d records with no match", len(no_match_records))

        # If there are any removed records, add them to the batch stats and handle failures
        if removed_records > 0:
            batch_stats.total_permid_failed += removed_records

            if self.failure_registry:
                self._handle_failures(low_score, no_match_records)

        # Parse the passed records into the permid_data structure
        parsed_response = self._parse_record_match_response(passed)

        return parsed_response

    @staticmethod
    def _parse_score(match: dict) -> float:
        """Extract the normalised match score (0–1) from a record.

        Args:
            match: A single record dict from the Record Match API response.

        Returns:
            Score as a float between 0 and 1, or 0 if absent/unparseable.
        """
        s = match.get("Match Score")
        return float(str(s).rstrip("%")) / 100 if s else 0

    def _parse_record_match_response(
        self, response: list[dict[str, Any]]
    ) -> dict[str, PermidEntry]:
        """Convert filtered Record Match records into the permid_data structure.

        Args:
            response: Filtered list of match records.

        Returns:
            Mapping of permid_cache_key → {search, result: [permid_url, ...]}.
        """
        permid_data: dict[str, PermidEntry] = {}
        for record in response:
            permid_url = record.get("Match OpenPermID")
            name = record.get("Input_Name")
            local_id = record.get("Input_LocalID")
            standard_id = record.get("Input_Standard Identifier")

            if not (name and local_id and permid_url):
                continue

            value: PermidEntry = {
                "search": {"Name": name, "LocalID": local_id, "Standard Identifier": standard_id},
                "result": [],
            }

            key = permid_cache_key(name, local_id)
            entry = permid_data.setdefault(key, value)

            if permid_url not in entry["result"]:
                entry["result"].append(permid_url)

        return permid_data

    def _handle_failures(
        self,
        low_score_records: list[dict[str, Any]],
        no_match_records: list[dict[str, Any]],
    ) -> None:
        """Classify and register permanent failures for records that were not resolved.

        Args:
            low_score_records: API response records that scored below the threshold.
            no_match_records: Original submitted records that the API returned no result for.
        """
        for record in low_score_records:
            failure_type = FailureType.LOW_MATCH_SCORE
            if not CompanyInfoFailureClassifier.is_retryable(failure_type):
                self.failure_registry.add(
                    key=(record["Input_Name"], record["Input_LocalID"]),
                    failure_type=failure_type,
                )

        for record in no_match_records:
            failure_type = FailureType.NO_PERMID
            if not CompanyInfoFailureClassifier.is_retryable(failure_type):
                self.failure_registry.add(
                    key=(record["Name"], record["LocalID"]),
                    failure_type=failure_type,
                )
