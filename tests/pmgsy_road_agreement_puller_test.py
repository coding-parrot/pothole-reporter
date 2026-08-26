#!/usr/bin/env python3
"""Focused checks for official PMGSY road-agreement normalization."""

import importlib.util
import copy
import gzip
import json
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "pull_pmgsy_road_agreements", ROOT / "tools" / "pull-pmgsy-road-agreements.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class PmgsyRoadAgreementPullerTest(unittest.TestCase):
    def setUp(self):
        fixture = ROOT / "tests" / "fixtures" / "pmgsy-road-agreements.json"
        self.rows = json.loads(fixture.read_text(encoding="utf-8"))

    def test_current_agreement_preserves_authoritative_road_and_package_fields(self):
        pack = MODULE.build_pack(
            self.rows, "2026-08-26T09:00:00Z", state_selectors=["BR"]
        )
        self.assertEqual(
            set(pack), {"schema_version", "generated_at", "sources", "coverage", "contracts"}
        )
        self.assertEqual(len(pack["contracts"]), 1)
        record = pack["contracts"][0]
        self.assertEqual(record["reference_label"], "PMGSY package")
        self.assertEqual(record["reference_value"], "BR01P3R100")
        self.assertEqual(record["road_id"], 271312)
        self.assertEqual(record["road_from"], "PALASI")
        self.assertEqual(record["road_to"], "SAHANDAR")
        self.assertEqual(record["agreement_number"], "09/SBD/PMGSY-III-2025-26")
        self.assertEqual(record["agreement_date"], "2025-07-03")
        self.assertEqual(record["sanctioned_date"], "2025-03-30")
        self.assertEqual(record["lifecycle"], "current_project")
        self.assertEqual(record["lifecycle_status"], "In Progress")
        self.assertTrue(record["scope_verified"])
        self.assertTrue(record["agreement_verified"])
        self.assertFalse(record["award_verified"])

    def test_does_not_invent_contractor_segment_completion_maintenance_or_dlp(self):
        pack = MODULE.build_pack(
            self.rows, "2026-08-26T09:00:00Z", state_selectors=["BR"]
        )
        record = pack["contracts"][0]
        self.assertIsNone(record["contractor"])
        self.assertIsNone(record["completion_date"])
        self.assertIsNone(record["maintenance_start_date"])
        self.assertIsNone(record["maintenance_end_date"])
        self.assertFalse(record["contractor_assignment_verified"])
        self.assertFalse(record["segment_verified"])
        self.assertFalse(record["dlp_verified"])

    def test_missing_agreement_fields_verify_neither_agreement_nor_award(self):
        pack = MODULE.build_pack(
            self.rows,
            "2026-08-26T09:00:00Z",
            state_selectors=["TG"],
            include_noncurrent=True,
        )
        self.assertEqual(len(pack["contracts"]), 1)
        record = pack["contracts"][0]
        self.assertEqual(record["state_code"], "TG")
        self.assertFalse(record["agreement_verified"])
        self.assertFalse(record["award_verified"])

    def test_cancelled_and_not_started_rows_are_not_current_by_default(self):
        current = MODULE.build_pack(self.rows, "2026-08-26T09:00:00Z")
        all_records = MODULE.build_pack(
            self.rows, "2026-08-26T09:00:00Z", include_noncurrent=True
        )
        self.assertEqual(len(current["contracts"]), 1)
        self.assertEqual(len(all_records["contracts"]), 4)
        cancelled = next(
            row for row in all_records["contracts"] if row["lifecycle_status"] == "Agreement Cancelled"
        )
        self.assertEqual(cancelled["lifecycle"], "project_record")

    def test_record_cap_is_explicit(self):
        pack = MODULE.build_pack(
            self.rows,
            "2026-08-26T09:00:00Z",
            max_records=1,
            include_noncurrent=True,
        )
        self.assertEqual(pack["coverage"]["eligible_records"], 4)
        self.assertEqual(pack["coverage"]["records_kept"], 1)
        self.assertTrue(pack["coverage"]["truncated_by_max_records"])

    def test_all_state_feeds_are_explicit_and_legacy_dh_feeds_remain_separate(self):
        self.assertEqual(len(MODULE.ALL_STATE_FEEDS), 37)
        self.assertEqual(
            [(item[0], item[1]) for item in MODULE.resolve_states(["DH"])],
            [(8, "DH"), (9, "DH")],
        )

    def test_normalized_source_snapshot_keeps_only_current_rows_and_counts_exclusions(self):
        snapshot = MODULE.source_snapshot(
            [row for row in self.rows if row["MAST_STATE_CODE"] == 5],
            "2026-08-26T09:00:00Z",
            (5, "BR", "Bihar"),
        )
        self.assertEqual(snapshot["format"], "pmgsy-current-road-agreement-source")
        self.assertEqual(snapshot["source_id"], "in-br-pmgsy-05")
        self.assertEqual(snapshot["rows_scanned"], 3)
        self.assertEqual(snapshot["rows_excluded_by_status"], 2)
        self.assertEqual(snapshot["rows_excluded_by_freshness"], 0)
        self.assertEqual(snapshot["rows_excluded_invalid"], 0)
        self.assertEqual(snapshot["records_kept"], 1)
        self.assertEqual(len(snapshot["agreements"]), 1)

    def test_directory_pull_writes_normalized_snapshots_not_raw_responses(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            MODULE, "fetch_state", return_value=[self.rows[0]]
        ):
            outputs = MODULE.pull_to_directory(
                [(5, "BR", "Bihar")],
                pathlib.Path(directory),
                "2026-08-26T09:00:00Z",
                timeout=1,
            )
            self.assertEqual([path.name for path in outputs], ["in-br-pmgsy-05.json.gz"])
            payload = json.loads(gzip.decompress(outputs[0].read_bytes()))
            self.assertIn("agreements", payload)
            self.assertNotIn("rows", payload)
            self.assertNotIn("IMS_PR_ROAD_CODE", payload)

    def test_stale_source_status_does_not_override_five_year_agreement_gate(self):
        stale = copy.deepcopy(self.rows[0])
        stale["TEND_DATE_OF_AGREEMENT"] = "/Date(1262304000000)/"  # 2010-01-01
        snapshot = MODULE.source_snapshot(
            [stale], "2026-08-26T09:00:00Z", (5, "BR", "Bihar")
        )
        self.assertEqual(snapshot["records_kept"], 0)
        self.assertEqual(snapshot["rows_excluded_by_freshness"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
