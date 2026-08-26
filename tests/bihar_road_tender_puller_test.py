#!/usr/bin/env python3
"""Focused checks for Bihar's official public active-tender normalizer."""

import importlib.util
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "pull_bihar_road_tenders", ROOT / "tools" / "pull-bihar-road-tenders.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class BiharRoadTenderPullerTest(unittest.TestCase):
    def setUp(self):
        fixture = ROOT / "tests" / "fixtures" / "bihar-road-tenders.json"
        self.rows = json.loads(fixture.read_text(encoding="utf-8"))["rows"]

    def test_keeps_only_current_explicit_carriageway_notice(self):
        pack = MODULE.build_pack(self.rows, "2026-08-26T00:00:00Z")
        self.assertEqual(pack["format"], "official-road-surface-procurement-notices")
        self.assertEqual(pack["source_id"], "in-br-eproc2")
        self.assertEqual(pack["state_code"], "BR")
        self.assertEqual(pack["lifecycle"], "procurement_notice")
        self.assertEqual(len(pack["notices"]), 1)
        notice = pack["notices"][0]
        self.assertEqual(notice["tender_reference"], "NIT-15/BIADA/2026-27")
        self.assertEqual(notice["tender_id"], "138319")
        self.assertEqual(notice["state_code"], "BR")
        self.assertEqual(notice["lifecycle"], "procurement_notice")
        self.assertEqual(notice["scope"], "road_surface")
        self.assertIsNone(notice["detail_url"])
        self.assertIsNone(notice["organisation_chain"])
        self.assertEqual(notice["organisation_path"], [])
        for forbidden in ("contractor", "award_verified", "segment_verified", "dlp_verified"):
            self.assertNotIn(forbidden, notice)

    def test_row_accounting_is_exact(self):
        pack = MODULE.build_pack(self.rows, "2026-08-26T00:00:00Z")
        self.assertEqual(pack["rows_scanned"], 3)
        self.assertEqual(pack["records_kept"], 1)
        self.assertEqual(pack["rows_excluded_by_scope"], 1)
        self.assertEqual(pack["rows_excluded_by_deadline"], 1)
        self.assertEqual(pack["rows_excluded_cancelled"], 0)
        self.assertEqual(pack["rows_excluded_invalid"], 0)
        self.assertIs(MODULE.validate_pack(pack), pack)

    def test_epoch_fields_and_deadline_are_preserved_as_instants(self):
        notice = MODULE.normalise(self.rows[:1], "2026-08-26T00:00:00Z")[0]
        self.assertEqual(notice["published_at"], "2026-08-17T14:41:32Z")
        self.assertEqual(notice["closing_at"], "2026-08-26T11:30:00Z")
        self.assertEqual(notice["retrieved_at"], "2026-08-26T00:00:00Z")
        self.assertEqual(
            notice["official_fields"]["currentTenderPublishDate"], 1786977692000
        )
        self.assertEqual(notice["official_fields"]["currentbidEndDate"], 1787743800000)

    def test_drain_location_is_not_mislabelled_as_road_scope(self):
        notices = MODULE.normalise(self.rows[1:2], "2026-08-26T00:00:00Z")
        self.assertEqual(notices, [])

    def test_cancelled_duplicate_and_missing_reference_fail_closed(self):
        current = dict(self.rows[0])
        cancelled = dict(current, currenttenderid=999001, currentTenderCancelDate=1787000000000)
        duplicate = dict(current)
        missing_reference = dict(current, currenttenderid=999002, currenttenderrefno=None)
        pack = MODULE.build_pack(
            [current, cancelled, duplicate, missing_reference], "2026-08-26T00:00:00Z"
        )
        self.assertEqual(pack["records_kept"], 1)
        self.assertEqual(pack["rows_excluded_cancelled"], 1)
        self.assertEqual(pack["rows_excluded_invalid"], 2)
        MODULE.validate_pack(pack)

    def test_validator_rejects_invented_contractor(self):
        pack = MODULE.build_pack(self.rows, "2026-08-26T00:00:00Z")
        pack["notices"][0]["contractor"] = "Invented Roads Ltd"
        with self.assertRaisesRegex(ValueError, "canonical contract"):
            MODULE.validate_pack(pack)

    def test_checked_in_live_snapshot_validates(self):
        snapshot = ROOT / "data" / "custom-road-tenders" / "br" / "in-br-eproc2.json"
        if not snapshot.exists():
            self.skipTest("live Bihar snapshot has not been pulled yet")
        pack = json.loads(snapshot.read_text(encoding="utf-8"))
        MODULE.validate_pack(pack)
        self.assertGreater(pack["rows_scanned"], 0)
        self.assertGreater(pack["records_kept"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
