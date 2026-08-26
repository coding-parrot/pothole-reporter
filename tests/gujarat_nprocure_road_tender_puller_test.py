#!/usr/bin/env python3
"""Focused tests for Gujarat nProcure's public-notice normalizer."""

import importlib.util
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "pull_gujarat_nprocure_road_tenders",
    ROOT / "tools" / "pull-gujarat-nprocure-road-tenders.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class GujaratNprocureRoadTenderPullerTest(unittest.TestCase):
    def setUp(self):
        fixture = ROOT / "tests" / "fixtures" / "gujarat-nprocure-tenders.json"
        self.rows = json.loads(fixture.read_text(encoding="utf-8"))["rows"]

    def test_keeps_only_current_physical_road_work(self):
        snapshot = MODULE.build_snapshot(self.rows, "2026-08-26T00:00:00Z")
        self.assertEqual(snapshot["format"], "official-road-surface-procurement-notices")
        self.assertEqual(snapshot["source_id"], "in-gj-nprocure")
        self.assertEqual(snapshot["rows_scanned"], 17)
        self.assertEqual(snapshot["rows_excluded_by_scope"], 13)
        self.assertEqual(len(snapshot["notices"]), 4)
        notices = {notice["tender_id"]: notice for notice in snapshot["notices"]}
        notice = notices["338986"]
        self.assertEqual(notice["tender_id"], "338986")
        self.assertEqual(notice["tender_reference"], "Thara/NP/2026/Aug/04")
        self.assertEqual(notice["organisation_path"], ["NAGARPALIKA-Thara Nagar Seva Sadan"])
        self.assertEqual(notice["closing_at"], "2026-09-08T18:00:00+05:30")
        self.assertIsNone(notice["published_at"])
        self.assertIsNone(notice["opening_at"])
        self.assertEqual(notice["detail_form"], {"tenderid": "338986"})

    def test_drain_consultancy_and_expired_work_fail_closed(self):
        self.assertEqual(MODULE.normalise(self.rows[1:4], "2026-08-26T00:00:00Z"), [])

    def test_incidental_road_words_fail_closed_but_real_surface_work_survives(self):
        snapshot = MODULE.build_snapshot(self.rows, "2026-08-26T00:00:00Z")
        retained = {notice["tender_id"] for notice in snapshot["notices"]}
        self.assertEqual(retained, {"338986", "335052", "337960", "316613"})
        self.assertTrue(
            {
                "324145",
                "335449",
                "335232",
                "336678",
                "337800",
                "337599",
                "338638",
                "337048",
                "335196",
                "335798",
            }.isdisjoint(retained)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
