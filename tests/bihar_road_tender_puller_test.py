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
        self.assertEqual(set(pack), {"schema_version", "generated_at", "sources", "notices"})
        self.assertEqual(len(pack["notices"]), 1)
        notice = pack["notices"][0]
        self.assertEqual(notice["reference_value"], "NIT-15/BIADA/2026-27")
        self.assertEqual(notice["state_code"], "BR")
        self.assertEqual(notice["lifecycle"], "procurement_notice")
        self.assertEqual(notice["lifecycle_status"], "active tender notice")
        self.assertTrue(notice["scope_verified"])
        self.assertFalse(notice["segment_verified"])
        self.assertFalse(notice["award_verified"])
        self.assertFalse(notice["dlp_verified"])
        self.assertIsNone(notice["contractor"])

    def test_epoch_fields_and_deadline_are_preserved_as_instants(self):
        notice = MODULE.normalise(self.rows[:1], "2026-08-26T00:00:00Z")[0]
        self.assertEqual(notice["published_at"], "2026-08-17T14:41:32Z")
        self.assertEqual(notice["bid_due_at"], "2026-08-26T11:30:00Z")
        self.assertEqual(notice["retrieved_at"], "2026-08-26")

    def test_drain_location_is_not_mislabelled_as_road_scope(self):
        notices = MODULE.normalise(self.rows[1:2], "2026-08-26T00:00:00Z")
        self.assertEqual(notices, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
