#!/usr/bin/env python3
"""Focused tests for the CHiPS open-tender parser and normalizer."""

import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "pull_chhattisgarh_chips_road_tenders",
    ROOT / "tools" / "pull-chhattisgarh-chips-road-tenders.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class ChhattisgarhChipsRoadTenderPullerTest(unittest.TestCase):
    def setUp(self):
        fixture = ROOT / "tests" / "fixtures" / "chhattisgarh-chips-open-tenders.html"
        self.rows = MODULE.parse_open_tender_rows(fixture.read_text(encoding="utf-8"))

    def test_parser_preserves_ids_titles_dates_and_organisation_group(self):
        self.assertEqual(len(self.rows), 5)
        self.assertEqual(self.rows[0]["tender_id"], "197112")
        self.assertEqual(self.rows[0]["tender_reference"], "197112")
        self.assertIn("B.T. Renewal", self.rows[0]["title"])
        self.assertEqual(self.rows[0]["organisation"], "Public Works Department (PWD)")
        self.assertEqual(
            self.rows[2]["organisation"],
            "Chhattisgarh Road Development Corporation Limited(CGRDC)",
        )

    def test_only_explicit_physical_road_work_is_retained(self):
        snapshot = MODULE.build_snapshot(self.rows, "2026-08-26T00:00:00Z")
        self.assertEqual(snapshot["format"], "official-road-surface-procurement-notices")
        self.assertEqual(snapshot["source_id"], "in-ct-chips")
        self.assertEqual(snapshot["rows_scanned"], 5)
        self.assertEqual(snapshot["rows_excluded_by_scope"], 4)
        self.assertEqual(len(snapshot["notices"]), 1)
        notice = snapshot["notices"][0]
        self.assertEqual(notice["tender_id"], "197112")
        self.assertEqual(notice["bid_submission_start_at"], "2026-08-10T17:00:00+05:30")
        self.assertEqual(notice["closing_at"], "2026-09-07T17:00:00+05:30")
        self.assertIsNone(notice["published_at"])
        self.assertIsNone(notice["opening_at"])
        self.assertNotIn("contractor", notice)

    def test_detail_parser_preserves_exact_nit_reference_and_opening_date(self):
        fixture = ROOT / "tests" / "fixtures" / "chhattisgarh-chips-tender-detail.html"
        detail = MODULE.parse_detail_page(fixture.read_text(encoding="utf-8"))
        self.assertEqual(detail["tender_reference"], "792/TC/2026-27")
        self.assertEqual(detail["organisation"], "Public Works Department (PWD)")
        self.assertEqual(detail["division_or_district"], "Durg")
        self.assertEqual(detail["section_or_circle"], "Durg Circle")
        self.assertEqual(detail["office_or_division"], "Executive Engineer Durg Division")

        enriched = dict(self.rows[0], **detail)
        snapshot = MODULE.build_snapshot([enriched], "2026-08-26T00:00:00Z")
        notice = snapshot["notices"][0]
        self.assertEqual(notice["tender_reference"], "792/TC/2026-27")
        self.assertEqual(notice["opening_at"], "2026-09-02T10:00:00+05:30")
        self.assertEqual(
            notice["organisation_path"],
            [
                "Public Works Department (PWD)",
                "Durg",
                "Durg Circle",
                "Executive Engineer Durg Division",
            ],
        )

    def test_reference_parser_does_not_swallow_the_work_title(self):
        self.assertEqual(
            MODULE.tender_reference_from_description(
                "NIT 1177 Construction & Maintenance Road, SCA Fund"
            ),
            "1177",
        )
        self.assertEqual(
            MODULE.tender_reference_from_description(
                "Notice Inviting Tender NIT No. 03 N.P.P/PWD/202 Dated 06-08-2026"
            ),
            "03 N.P.P/PWD/202",
        )
        serial = "Sr no/b6/V S AREA AHIWARA/67-1/26-27/MANDI-273/2801/raipur/11.08.2026"
        self.assertEqual(MODULE.tender_reference_from_description(serial), serial)


if __name__ == "__main__":
    unittest.main(verbosity=2)
