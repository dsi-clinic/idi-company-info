#!/usr/bin/env python3
"""Unit tests for idi_company_info.company_pipeline.CompanyPipeline.

Covers pipeline-level behaviour that is independent of the composed Input —
currently the CUSIP-collision reporting, which reads the on-disk permid cache.
"""

import json
from unittest.mock import MagicMock

from idi_company_info.company_pipeline import CompanyPipeline
from idi_company_info.types import FilePaths

_PERMID_URL = "https://permid.org/1-test"


def _collision_permid_data() -> dict:
    """A permid cache with two different issuers (ABBOTT, ABACUS) on one PermID."""
    return {
        "ABBOTT_cusip_002824100": {
            "search": {
                "Name": "ABBOTT",
                "LocalID": "cusip_002824100",
                "Standard Identifier": "ticker:ABT",
            },
            "result": [_PERMID_URL],
        },
        "ABACUS_cusip_00258Y104": {
            "search": {
                "Name": "ABACUS",
                "LocalID": "cusip_00258Y104",
                "Standard Identifier": "ticker:ABL",
            },
            "result": [_PERMID_URL],
        },
    }


def make_pipeline_with_files(tmp_path, permid_data) -> CompanyPipeline:
    """A CompanyPipeline whose file_paths point at written temp caches.

    Built via ``__new__`` to bypass API-client construction; only the
    attributes ``_report_cusip_collisions`` touches are set.
    """
    permid_file = tmp_path / "permid_url.json"
    result_file = tmp_path / "permid_data.json"
    permid_file.write_text(json.dumps(permid_data))
    result_file.write_text(json.dumps({}))

    instance = CompanyPipeline.__new__(CompanyPipeline)
    instance.logger = MagicMock()
    instance.file_paths = FilePaths(
        result_file=str(result_file),
        permid_file=str(permid_file),
    )
    return instance


class TestReportCusipCollisions:
    """CompanyPipeline._report_cusip_collisions scopes warnings to the current run."""

    def test_no_warning_when_collision_not_resolved_this_run(self, tmp_path):
        """A historical collision is not re-warned when this run resolved none of it."""
        instance = make_pipeline_with_files(tmp_path, _collision_permid_data())
        instance._report_cusip_collisions(resolved_keys=set())
        instance.logger.warning.assert_not_called()

    def test_warns_when_a_member_resolved_this_run(self, tmp_path):
        """The collision is reported when this run resolved one of its CUSIPs."""
        instance = make_pipeline_with_files(tmp_path, _collision_permid_data())
        instance._report_cusip_collisions(resolved_keys={"ABBOTT_cusip_002824100"})
        # One per-collision warning + one summary warning.
        assert instance.logger.warning.call_count == 2

    def test_unrelated_resolved_key_does_not_warn(self, tmp_path):
        """A key resolved this run that reaches no colliding PermID warns nothing."""
        instance = make_pipeline_with_files(tmp_path, _collision_permid_data())
        instance._report_cusip_collisions(resolved_keys={"OTHER_cusip_999999999"})
        instance.logger.warning.assert_not_called()
