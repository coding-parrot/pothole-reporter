#!/usr/bin/env python3
"""Focused checks for Telangana's public Live Tenders normalizer."""

import importlib.util
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "pull_telangana_road_tenders", ROOT / "tools" / "pull-telangana-road-tenders.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class TelanganaRoadTenderPullerTest(unittest.TestCase):
    def setUp(self):
        fixture = ROOT / "tests" / "fixtures" / "telangana-road-tenders.json"
        self.rows = json.loads(fixture.read_text(encoding="utf-8"))["rows"]

    def test_keeps_only_current_explicit_carriageway_notice(self):
        pack = MODULE.build_pack(self.rows, "2026-08-26T00:00:00Z")
        self.assertEqual(pack["format"], "official-road-surface-procurement-notices")
        self.assertEqual(pack["source_id"], "in-tg-eprocurement")
        self.assertEqual(pack["state_code"], "TG")
        self.assertEqual(pack["rows_scanned"], 4)
        self.assertEqual(pack["records_kept"], 1)
        notice = pack["notices"][0]
        self.assertEqual(notice["tender_id"], "728100")
        self.assertEqual(notice["tender_reference"], "NIT No.42/SE/R&B/HYD/2026-27")
        self.assertEqual(notice["scope"], "road_surface")
        self.assertEqual(notice["lifecycle"], "procurement_notice")
        self.assertEqual(notice["published_at"], "2026-08-20T13:23:00Z")
        self.assertEqual(notice["closing_at"], "2026-09-05T11:30:00Z")
        self.assertIsNone(notice["opening_at"])
        self.assertIsNone(notice["detail_url"])
        for forbidden in ("contractor", "award_verified", "segment_verified", "dlp_verified"):
            self.assertNotIn(forbidden, notice)

    def test_drain_footpath_and_consultancy_are_rejected(self):
        self.assertEqual(MODULE.normalise(self.rows[1:3], "2026-08-26T00:00:00Z"), [])

    def test_row_accounting_balances(self):
        pack = MODULE.build_pack(self.rows, "2026-08-26T00:00:00Z")
        self.assertEqual(pack["rows_excluded_by_scope"], 2)
        self.assertEqual(pack["rows_excluded_by_deadline"], 1)
        self.assertEqual(pack["rows_excluded_invalid"], 0)
        self.assertIs(MODULE.validate_pack(pack), pack)

    def test_invalid_date_and_duplicate_fail_closed(self):
        invalid = list(self.rows[0])
        invalid[6] = "not a date"
        duplicate = list(self.rows[0])
        pack = MODULE.build_pack([self.rows[0], invalid, duplicate], "2026-08-26T00:00:00Z")
        self.assertEqual(pack["records_kept"], 1)
        self.assertEqual(pack["rows_excluded_invalid"], 2)
        MODULE.validate_pack(pack)

    def test_validator_rejects_invented_contractor(self):
        pack = MODULE.build_pack(self.rows, "2026-08-26T00:00:00Z")
        pack["notices"][0]["contractor"] = "Invented Roads Ltd"
        with self.assertRaisesRegex(ValueError, "canonical contract"):
            MODULE.validate_pack(pack)

    def test_checked_in_live_snapshot_validates(self):
        snapshot = ROOT / "data" / "custom-road-tenders" / "tg" / "in-tg-eprocurement.json"
        if not snapshot.exists():
            self.skipTest("live Telangana snapshot has not been pulled yet")
        pack = json.loads(snapshot.read_text(encoding="utf-8"))
        MODULE.validate_pack(pack)
        self.assertGreater(pack["rows_scanned"], 0)
        self.assertGreater(pack["records_kept"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
