#!/usr/bin/env python3
"""Focused tests for Lakshadweep's official tender-notice parser."""

import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "pull_lakshadweep_road_tenders",
    ROOT / "tools" / "pull-lakshadweep-road-tenders.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class LakshadweepRoadTenderPullerTest(unittest.TestCase):
    def setUp(self):
        fixture = ROOT / "tests" / "fixtures" / "lakshadweep-tender-notices.html"
        self.rows, self.next_url = MODULE.parse_notice_page(
            fixture.read_text(encoding="utf-8")
        )

    def test_parser_preserves_official_document_and_pagination(self):
        self.assertEqual(len(self.rows), 3)
        self.assertEqual(
            self.rows[0]["document_url"],
            "https://cdn.s3waas.gov.in/example/road-notice.pdf",
        )
        self.assertEqual(
            self.next_url,
            "https://lakshadweep.gov.in/notice_category/tenders/page/2",
        )

    def test_missing_structured_tender_fields_are_not_invented(self):
        snapshot = MODULE.build_snapshot(self.rows, "2026-08-26T00:00:00Z")
        self.assertEqual(snapshot["format"], "official-road-surface-procurement-notices")
        self.assertEqual(snapshot["source_id"], "in-ld-official-tender-notices")
        self.assertEqual(snapshot["rows_scanned"], 3)
        self.assertEqual(snapshot["rows_excluded_by_scope"], 2)
        self.assertEqual(len(snapshot["notices"]), 1)
        notice = snapshot["notices"][0]
        self.assertIsNone(notice["tender_id"])
        self.assertIsNone(notice["tender_reference"])
        self.assertIsNone(notice["published_at"])
        self.assertIsNone(notice["closing_at"])
        self.assertIsNone(notice["opening_at"])
        self.assertEqual(notice["closing_date"], "2026-09-10")
        self.assertEqual(notice["scope"], "road_surface")


if __name__ == "__main__":
    unittest.main(verbosity=2)
