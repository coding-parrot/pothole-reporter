#!/usr/bin/env python3
"""Focused regression checks for the official national-highway data normalizer."""

import csv
import importlib.util
import io
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "pull_national_highway_contracts", ROOT / "tools" / "pull-national-highway-contracts.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class NationalHighwayContractsTest(unittest.TestCase):
    def test_highway_and_chainage_parsing(self):
        title = (
            "Widening of NH-13 & 15 (old NH-52) from Existing Km 745.600 "
            "(Design Km 0.000) to Existing Km 770.600 (Design Km 24.819)"
        )
        self.assertEqual(MODULE.parse_highway_refs(title), ["NH-13", "NH-15", "NH-52"])
        self.assertEqual(
            MODULE.parse_chainages(title),
            [
                {"start_km": 745.6, "end_km": 770.6},
                {"start_km": 0.0, "end_km": 24.819},
            ],
        )
        self.assertEqual(
            MODULE.parse_chainages("Realignment from km 15/4 to km 16/350 of NH-179A"),
            [{"start_km": 15.4, "end_km": 16.35}],
        )
        self.assertEqual(
            MODULE.parse_highway_refs("N.H.-353 and National Highway No. 44"),
            ["NH-353", "NH-44"],
        )

    def test_strict_scope_rejects_false_responsibility(self):
        rejected = [
            "Construction of drain and footpath beside NH-44",
            "Maintenance of road, drains and footpath in Ward 10",
            "Independent Engineer services for widening NH-29 to four lanes",
            "Construction of VUP at km 61.270 on existing 4 lane NH-48",
            "Supply and operation of street lighting on National Highways",
            "Reconstruction of a two lane major bridge at km 88/150 on NH-351K",
            "Construction of a 2-lane R.O.B. at Ch. 1800 on N.H.-353",
            "Construction of automated multilevel parking with ten years O&M",
            "Short Term Maintenance for landslide debris clearance on NH-07",
            "Hill side slope protection at km 386+550 under a paved-shoulder highway project",
        ]
        accepted = [
            "PBMC for strengthening and maintenance of NH-717 from km 1+000 to km 20+000",
            "Overlay and resurfacing of the carriageway on NH-44",
            "Widening and upgradation of existing carriageway into 2-lane paved shoulder NH-717B",
            "Construction of 4 lane highway from Ch. 0+000 to Ch. 150+971 along NH-15",
        ]
        for title in rejected:
            self.assertFalse(MODULE.is_strict_carriageway_scope(title), title)
        for title in accepted:
            self.assertTrue(MODULE.is_strict_carriageway_scope(title), title)

    def test_sources_keep_identifier_and_award_semantics_separate(self):
        retrieved = "2026-08-26T08:00:00Z"
        nhai_rows = {
            "data": [
                {
                    "state": "Tamil Nadu",
                    "current_project_stage": "CC Issued & O&M by Construction Agency",
                    "project_name": "Realignment from km 15/4 to km 16/350 of NH-179A",
                    "upc": "RA0179ABA004TN",
                    "start_date": "21/11/2025",
                    "likely_completion_date": "21/08/2026",
                    "name_piu": "NH Division Salem",
                    "ro": "RO-MoRTH-Chennai",
                    "name_of_concessionaire": "P SIVAPERUMAL",
                },
                {
                    "state": "Karnataka",
                    "current_project_stage": "Under Construction (AD issued)",
                    "project_name": "Construction of footpath and drain along NH-44",
                    "upc": "NOT-A-ROAD-CONTRACT",
                    "name_of_concessionaire": "Wrong Contractor",
                },
            ]
        }
        nhidcl_rows = [
            {
                "field_sub_title": "PBMC for maintenance of NH-717 from km 1+000 to km 20+000",
                "field_tender_id": "2026_NHIDC_921000_1",
                "field_tender_file": "rfp.pdf",
                "field_bid_due_date": "Tue, 09/08/2026 - 10:30",
                "field_corrigendum_file": "",
                "field_month": "September",
                "field_select_state": "West Bengal",
            },
            {
                # Same notice with an older due date must not create a duplicate.
                "field_sub_title": "PBMC for maintenance of NH-717 from km 1+000 to km 20+000",
                "field_tender_id": "2026_NHIDC_921000_1",
                "field_tender_file": "old-rfp.pdf",
                "field_bid_due_date": "Tue, 08/18/2026 - 10:30",
                "field_corrigendum_file": "",
                "field_month": "August",
                "field_select_state": "West Bengal",
            },
        ]
        csv_buffer = io.StringIO()
        writer = csv.DictWriter(csv_buffer, fieldnames=list(nhidcl_rows[0]))
        writer.writeheader()
        writer.writerows(nhidcl_rows)

        pack = MODULE.build_pack(
            json.dumps(nhai_rows).encode(), csv_buffer.getvalue().encode(), retrieved
        )
        self.assertEqual(set(pack), {"schema_version", "generated_at", "sources", "contracts"})
        self.assertEqual(pack["generated_at"], retrieved)
        self.assertTrue(all(source["retrieved_at"] == "2026-08-26" for source in pack["sources"]))
        self.assertEqual(len(pack["contracts"]), 2)

        project = next(row for row in pack["contracts"] if row["agency"] == "MoRTH")
        self.assertEqual(project["reference_label"], "UPC")
        self.assertEqual(project["reference_value"], "RA0179ABA004TN")
        self.assertNotEqual(project["reference_label"], "Tender ID")
        self.assertEqual(project["contractor"], "P SIVAPERUMAL")
        self.assertTrue(project["award_verified"])
        self.assertEqual(project["lifecycle"], "current_project")
        self.assertEqual(project["lifecycle_status"], "CC Issued & O&M by Construction Agency")
        self.assertFalse(project["segment_verified"])
        self.assertFalse(project["dlp_verified"])
        self.assertEqual(project["retrieved_at"], "2026-08-26")

        notice = next(row for row in pack["contracts"] if row["agency"] == "NHIDCL")
        self.assertEqual(notice["reference_label"], "Tender ID")
        self.assertEqual(notice["reference_value"], "2026_NHIDC_921000_1")
        self.assertIsNone(notice["contractor"])
        self.assertFalse(notice["award_verified"])
        self.assertEqual(notice["lifecycle"], "procurement_notice")
        self.assertFalse(notice["dlp_verified"])
        self.assertEqual(notice["lifecycle_status"], "active tender notice")
        self.assertEqual(notice["bid_due_at"], "2026-09-08T16:00+05:30")

    def test_completed_rows_are_not_relabelled_current(self):
        rows = [
            {
                "state": "Maharashtra",
                "current_project_stage": "UI Completed & Archived",
                "project_name": "Strengthening of NH-66 from km 1 to km 20",
                "upc": "ARCHIVED-1",
                "name_of_concessionaire": "Old Contractor",
            },
            {
                "state": "Maharashtra",
                "current_project_stage": "CC Issued",
                "project_name": "Short term maintenance of NH-48 from km 10 to km 30",
                "upc": "COMPLETED-2",
                "name_of_concessionaire": "Completed Contractor",
            },
            {
                "state": "Maharashtra",
                "current_project_stage": "CC Issued & O&M by Construction Agency",
                "project_name": "Maintenance of NH-48 from km 30 to km 50",
                "upc": "MAINTENANCE-3",
                "name_of_concessionaire": "Maintenance Contractor",
            },
        ]
        contracts = MODULE.normalise_nhai(rows, "2026-08-26T08:00:00Z")
        self.assertEqual([row["reference_value"] for row in contracts], ["MAINTENANCE-3"])
        self.assertEqual(contracts[0]["lifecycle_status"], "CC Issued & O&M by Construction Agency")

    def test_missing_tender_id_is_labeled_as_fingerprint(self):
        rows = [
            {
                "field_sub_title": "One Time Improvement of pavement on NH-17 from km 1 to km 5",
                "field_tender_file": "rfp.pdf",
                "field_bid_due_date": "Fri, 08/28/2026 - 11:30",
                "field_corrigendum_file": "",
                "field_month": "August",
                "field_select_state": "Assam",
            }
        ]
        contracts = MODULE.normalise_nhidcl(rows, "2026-08-26T08:00:00Z")
        self.assertEqual(len(contracts), 1)
        self.assertEqual(contracts[0]["reference_label"], "Official notice fingerprint")
        self.assertFalse(contracts[0]["reference_value"].startswith("2026_NHIDC"))
        self.assertFalse(contracts[0]["award_verified"])

    def test_runtime_codes_and_expired_notice_filter(self):
        self.assertEqual(MODULE.state_code("Telangana"), "TG")
        rows = [
            {
                "field_sub_title": "PBMC for maintenance of NH-44 from km 10 to km 20",
                "field_tender_file": "expired.pdf",
                "field_bid_due_date": "Tue, 08/25/2026 - 17:00",
                "field_corrigendum_file": "",
                "field_month": "August",
                "field_select_state": "Telangana",
            },
            {
                "field_sub_title": "PBMC for maintenance of NH-44 from km 20 to km 30",
                "field_tender_file": "open.pdf",
                "field_bid_due_date": "Wed, 08/26/2026 - 17:00",
                "field_corrigendum_file": "",
                "field_month": "August",
                "field_select_state": "Telangana",
            },
            {
                "field_sub_title": "PBMC for maintenance of NH-44 from km 30 to km 40",
                "field_tender_file": "unknown.pdf",
                "field_bid_due_date": "",
                "field_corrigendum_file": "",
                "field_month": "August",
                "field_select_state": "Telangana",
            },
        ]
        contracts = MODULE.normalise_nhidcl(rows, "2026-08-26T08:00:00Z")
        self.assertEqual(len(contracts), 1)
        self.assertEqual(contracts[0]["state_code"], "TG")
        self.assertEqual(contracts[0]["lifecycle"], "procurement_notice")
        self.assertEqual(contracts[0]["lifecycle_status"], "active tender notice")


if __name__ == "__main__":
    unittest.main(verbosity=2)
