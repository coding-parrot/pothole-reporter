#!/usr/bin/env python3
"""Focused checks for the anonymous official KPPP awarded-works puller."""

import copy
import importlib.util
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "pull_kppp", ROOT / "tools" / "pull-kppp.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class KpppRoadAwardPullerTest(unittest.TestCase):
    def setUp(self):
        fixture = ROOT / "tests" / "fixtures" / "kppp-awarded-works.json"
        self.rows = json.loads(fixture.read_text(encoding="utf-8"))["rows"]

    def test_keeps_only_explicit_carriageway_work_and_accounts_for_every_row(self):
        snapshot = MODULE.build_snapshot(
            self.rows,
            "2026-08-26T00:00:00Z",
            pages_fetched=1,
            total_reported=2,
        )
        self.assertEqual(snapshot["format"], "official-road-surface-procurement-records")
        self.assertEqual(snapshot["source_id"], "in-ka-kppp-awarded-works")
        self.assertEqual(snapshot["state_code"], "KA")
        self.assertEqual(snapshot["rows_received"], 2)
        self.assertEqual(snapshot["rows_excluded_by_scope"], 1)
        self.assertEqual(snapshot["rows_excluded_invalid"], 0)
        self.assertEqual(snapshot["records_kept"], 1)
        self.assertIs(MODULE.validate_snapshot(snapshot), snapshot)

    def test_preserves_full_official_description_dates_authority_and_urls(self):
        snapshot = MODULE.build_snapshot(
            self.rows,
            "2026-08-26T00:00:00Z",
            pages_fetched=1,
            total_reported=2,
        )
        record = snapshot["records"][0]
        source = self.rows[1]
        self.assertEqual(record["tender_id"], "316516")
        self.assertEqual(record["tender_reference"], source["tenderNumber"])
        self.assertEqual(record["title"], source["description"])
        self.assertGreater(len(record["title"]), len(source["title"]))
        self.assertEqual(record["official_fields"], source)
        self.assertEqual(record["published_at"], "2026-07-31T14:05:24+05:30")
        self.assertEqual(record["closure_at"], "2026-08-07T16:00:00+05:30")
        self.assertEqual(record["department_name"], "Public Works Department")
        self.assertEqual(record["organisation_chain"], "Public Works Department")
        self.assertEqual(record["organisation_path"], ["Public Works Department"])
        self.assertEqual(record["source_url"], MODULE.API)
        self.assertEqual(record["listing_url"], MODULE.API)
        self.assertIsNone(record["detail_url"])
        self.assertEqual(record["source_status"], "AWARDED")
        self.assertEqual(record["lifecycle"], "procurement_record")

    def test_roadside_plantation_and_social_forestry_are_never_road_surface(self):
        plantation = self.rows[0]
        self.assertFalse(
            MODULE.is_kppp_road_surface_record(
                plantation["description"], plantation["tenderNumber"]
            )
        )
        self.assertFalse(
            MODULE.is_kppp_road_surface_record(
                "Maintenance of road side plantations in Social Forestry Range",
                "KFD/WORKS/1",
            )
        )
        self.assertFalse(
            MODULE.is_kppp_road_surface_record(
                "Maintenance of One year oldPlantation from Chittekyathanahally "
                "Road sideof 2 Km at HunsurSocial Forestry Range",
                "KFD/WORKS/2",
            )
        )
        self.assertTrue(
            MODULE.is_kppp_road_surface_record(
                "Construction of BT road from Market Road to Canal Road",
                "PWD/RD/WORKS/1",
            )
        )
        self.assertTrue(
            MODULE.is_kppp_road_surface_record(
                "Construction of CC road with plantation in Ward 7",
                "DMA/RD/WORKS/2",
            )
        )

    def test_validator_rejects_invented_contract_and_warranty_fields(self):
        snapshot = MODULE.build_snapshot(
            self.rows,
            "2026-08-26T00:00:00Z",
            pages_fetched=1,
            total_reported=2,
        )
        for field in (
            "contractor",
            "winning_bidder",
            "award_date",
            "dlp",
            "warranty",
            "segment_verified",
        ):
            corrupted = copy.deepcopy(snapshot)
            corrupted["records"][0][field] = "invented"
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, "forbidden inference"
            ):
                MODULE.validate_snapshot(corrupted)

    def test_checked_in_live_receipt_preserves_audited_accounting(self):
        path = (
            ROOT
            / "data"
            / "custom-road-tenders"
            / "ka"
            / "in-ka-kppp-awarded-works.receipt.json"
        )
        receipt = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            receipt["format"], "official-road-surface-procurement-record-receipt"
        )
        self.assertEqual(receipt["source_id"], MODULE.SOURCE_ID)
        self.assertEqual(receipt["source_url"], MODULE.API)
        self.assertEqual(receipt["query"], MODULE.QUERY)
        self.assertTrue(receipt["anonymous_access"])
        self.assertFalse(receipt["runtime_published"])
        self.assertEqual(
            receipt["rows_received"],
            receipt["rows_excluded_by_scope"]
            + receipt["rows_excluded_invalid"]
            + receipt["records_kept"],
        )
        self.assertGreater(receipt["records_kept"], 0)
        self.assertRegex(receipt["generated_snapshot_sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(receipt["generated_snapshot_bytes"], 40_000_000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
