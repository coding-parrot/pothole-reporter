#!/usr/bin/env python3
"""Focused checks for official eMARG public road-maintenance normalization."""

import importlib.util
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "pull_emarg_road_contracts", ROOT / "tools" / "pull-emarg-road-contracts.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class EmargRoadContractPullerTest(unittest.TestCase):
    def setUp(self):
        fixture = ROOT / "tests" / "fixtures" / "emarg-road-contracts.json"
        self.payload = json.loads(fixture.read_text(encoding="utf-8"))

    def test_active_record_preserves_ids_contractor_and_dates(self):
        pack = MODULE.build_pack(
            MODULE.FixtureClient(self.payload),
            "2026-08-26T08:00:00Z",
            state_selectors=["BR"],
        )
        self.assertEqual(
            set(pack), {"schema_version", "generated_at", "sources", "coverage", "contracts"}
        )
        self.assertEqual(len(pack["contracts"]), 1)
        record = pack["contracts"][0]
        self.assertEqual(record["reference_label"], "PMGSY package")
        self.assertEqual(record["reference_value"], "BR01P3R04")
        self.assertEqual(record["road_id"], 92692)
        self.assertEqual(record["encoded_road_id"], "nIehGclYQ-g")
        self.assertEqual(record["package_id"], 70677)
        self.assertEqual(record["contractor"], "V.B. BUILDCON")
        self.assertEqual(record["maintenance_start_date"], "2023-08-29")
        self.assertEqual(record["maintenance_end_date"], "2028-08-28")
        self.assertEqual(record["lifecycle_status"], "maintenance period active")
        self.assertTrue(record["scope_verified"])
        self.assertTrue(record["contractor_assignment_verified"])
        self.assertTrue(record["maintenance_period_verified"])

    def test_does_not_invent_tender_award_segment_or_dlp_verification(self):
        pack = MODULE.build_pack(
            MODULE.FixtureClient(self.payload), "2026-08-26T08:00:00Z"
        )
        record = pack["contracts"][0]
        self.assertNotEqual(record["reference_label"], "Tender ID")
        self.assertFalse(record["segment_verified"])
        self.assertFalse(record["award_verified"])
        self.assertFalse(record["dlp_verified"])
        self.assertIsNone(record["agreement_number"])
        self.assertIsNone(record["work_order_number"])

    def test_expired_maintenance_is_excluded_unless_requested(self):
        default_pack = MODULE.build_pack(
            MODULE.FixtureClient(self.payload), "2026-08-26T08:00:00Z"
        )
        all_pack = MODULE.build_pack(
            MODULE.FixtureClient(self.payload),
            "2026-08-26T08:00:00Z",
            include_inactive=True,
        )
        self.assertEqual(len(default_pack["contracts"]), 1)
        self.assertEqual(len(all_pack["contracts"]), 2)
        ended = next(row for row in all_pack["contracts"] if row["road_id"] == 12718)
        self.assertEqual(ended["lifecycle_status"], "maintenance period ended")

    def test_cap_is_explicitly_reported_as_truncation(self):
        pack = MODULE.build_pack(
            MODULE.FixtureClient(self.payload),
            "2026-08-26T08:00:00Z",
            max_details=1,
        )
        self.assertEqual(pack["coverage"]["details_fetched"], 1)
        self.assertTrue(pack["coverage"]["truncated_by_max_details"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
