#!/usr/bin/env python3
"""Focused tests for deterministic, notice-only State/UT GePNIC packs."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
TOOL = TOOLS / "build-gepnic-road-notice-packs.py"
SPEC = importlib.util.spec_from_file_location("gepnic_road_notice_pack_builder", TOOL)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILDER
SPEC.loader.exec_module(BUILDER)


def notice(source_id: str, source_name: str, state_code: str, tender_id: str) -> dict:
    portal = f"https://{source_id}.example.gov.in/nicgep/app"
    detail = portal + f"?page=FrontEndViewTender&service=direct&id={tender_id}"
    return {
        "closing_at": "2026-09-04T17:00:00+05:30",
        "detail_url": detail,
        "lifecycle": "procurement_notice",
        "listing_url": portal + "?page=FrontEndTendersByOrganisation&service=direct",
        "opening_at": "2026-09-05T11:00:00+05:30",
        "organisation_chain": "Public Works Department||Road Division",
        "organisation_path": ["Public Works Department", "Road Division"],
        "published_at": "2026-08-25T10:00:00+05:30",
        "retrieved_at": "2026-08-26T00:00:00Z",
        "scope": "road_surface",
        "source_name": source_name,
        "source_url": detail,
        "state_code": state_code,
        "tender_id": tender_id,
        "tender_reference": f"NIT/{tender_id}",
        "title": f"Special repairs and resurfacing of NH 44 for {tender_id}",
    }


def source(
    source_id: str,
    state_code: str,
    tender_ids: list[str],
    *,
    excluded: int = 1,
) -> dict:
    source_name = f"{state_code} official e-Procurement Portal"
    portal = f"https://{source_id}.example.gov.in/nicgep/app"
    notices = [
        notice(source_id, source_name, state_code, tender_id)
        for tender_id in tender_ids
    ]
    return {
        "format": "gepnic-road-surface-procurement-notices",
        "schema_version": 1,
        "source_id": source_id,
        "source_name": source_name,
        "source_url": portal
        + "?component=clear&page=FrontEndTendersByOrganisation&service=direct",
        "retrieved_at": "2026-08-26T00:00:00Z",
        "state_code": state_code,
        "lifecycle": "procurement_notice",
        "organisations": ["Public Works Department"],
        "rows_scanned": excluded + len(notices),
        "rows_excluded_by_scope": excluded,
        "notices": notices,
    }


def default_sources() -> list[dict]:
    return [
        source("in-as-gepnic", "AS", ["2026_ASPWD_2", "2026_ASPWD_1"]),
        source("in-dh-gepnic", "DH", [], excluded=3),
        source("in-wb-gepnic-current", "WB", ["2026_WBPWD_2"]),
        source("in-wb-gepnic-legacy", "WB", ["2026_WBPWD_1"], excluded=2),
    ]


def write_registry(root: Path, values: list[dict]) -> None:
    by_state: dict[str, list[dict]] = {}
    for value in values:
        by_state.setdefault(value["state_code"], []).append(value)
    registry = {
        "jurisdictions": [
            {
                "code": f"IN-{state_code}",
                "sources": [
                    {
                        "id": value["source_id"],
                        "portal_family": "nic_gepnic",
                    }
                    for value in sorted(values, key=lambda item: item["source_id"])
                ],
            }
            for state_code, values in sorted(by_state.items())
        ]
    }
    path = root / BUILDER.REGISTRY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")


def add_custom_registry_source(
    root: Path, source_id: str, state_code: str, portal_family: str
) -> None:
    path = root / BUILDER.REGISTRY_PATH
    registry = json.loads(path.read_text(encoding="utf-8"))
    jurisdiction = next(
        item for item in registry["jurisdictions"] if item["code"] == f"IN-{state_code}"
    )
    jurisdiction["sources"].append(
        {"id": source_id, "portal_family": portal_family}
    )
    path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")


def state_summaries(values: list[dict]) -> dict[str, dict[str, int]]:
    summaries: dict[str, dict[str, int]] = {}
    for value in values:
        summary = summaries.setdefault(
            value["state_code"],
            {"sources": 0, "rows_scanned": 0, "rows_excluded_by_scope": 0, "notices": 0},
        )
        summary["sources"] += 1
        summary["rows_scanned"] += value["rows_scanned"]
        summary["rows_excluded_by_scope"] += value["rows_excluded_by_scope"]
        summary["notices"] += len(value["notices"])
    return summaries


def write_report(
    root: Path,
    values: list[dict],
    *,
    failures: list[dict] | None = None,
) -> bytes:
    failures = failures or []
    report = {
        "failures": failures,
        "format": "india-gepnic-road-surface-crawl-report",
        "retrieved_at": "2026-08-26T00:00:00Z",
        "schema_version": 1,
        "source_count_failed": len(failures),
        "source_count_requested": len(values),
        "source_count_succeeded": len(values) - len(failures),
        "states": state_summaries(values),
    }
    path = root / BUILDER.CRAWL_REPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = (json.dumps(report, indent=4, sort_keys=False) + "\n").encode()
    path.write_bytes(rendered)
    return rendered


def write_sources(root: Path, values: list[dict] | None = None) -> list[dict]:
    values = values or default_sources()
    directory = root / BUILDER.SOURCE_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    for value in values:
        (directory / f"{value['source_id']}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    write_registry(root, values)
    write_report(root, values)
    return values


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


CRAWL_AT = "2026-08-26T00:00:00Z"
# The manifest the builder wrote for default_sources() at 014d71b, before a failed
# portal could keep its receipt. A crawl with no failures must still build these bytes.
CLEAN_CRAWL_MANIFEST_SHA256 = "e9ae51260cd30a7f905b49d33d83e26063c5f36561859a97033bde04ee4baa6a"


def fetched_at(value: dict, retrieved_at: str) -> dict:
    """The same source as a crawl at another time fetched it."""
    older = copy.deepcopy(value)
    older["retrieved_at"] = retrieved_at
    for row in older["notices"]:
        row["retrieved_at"] = retrieved_at
    return older


def render_receipt(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def rewrite_report(root: Path, report: dict) -> bytes:
    rendered = (json.dumps(report, indent=4, sort_keys=False) + "\n").encode()
    (root / BUILDER.CRAWL_REPORT_PATH).write_bytes(rendered)
    return rendered


class GePNICRoadNoticePackBuilderTest(unittest.TestCase):
    def test_merges_declared_custom_portal_notice_without_inventing_organisation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = [source("in-br-gepnic", "BR", ["GEP-1"])]
            write_sources(root, values)
            add_custom_registry_source(root, "in-br-eproc2", "BR", "bihar_eproc2")
            custom = {
                "format": "official-road-surface-procurement-notices",
                "schema_version": 1,
                "source_id": "in-br-eproc2",
                "source_name": "Bihar eProc2.0 public active tenders",
                "source_url": "https://eproc2.bihar.gov.in/EPSV2Web/",
                "retrieved_at": "2026-08-26T06:20:18Z",
                "state_code": "BR",
                "lifecycle": "procurement_notice",
                "rows_scanned": 2,
                "rows_excluded_by_scope": 1,
                "records_kept": 1,
                "notices": [{
                    "tender_id": "138319",
                    "tender_reference": "NIT-15/BIADA/2026-27",
                    "title": "Construction of flexible pavement at MGC Udakishanganj",
                    "organisation_chain": None,
                    "organisation_path": [],
                    "organisation_id": 538,
                    "department_id": 1874,
                    "published_at": None,
                    "closing_at": "2026-08-26T11:30:00Z",
                    "opening_at": None,
                    "detail_url": None,
                    "listing_url": "https://eproc2.bihar.gov.in/EPSV2Web/openarea/tenderListingPage.action",
                    "retrieved_at": "2026-08-26T06:20:18Z",
                    "state_code": "BR",
                    "lifecycle": "procurement_notice",
                    "scope": "road_surface",
                }],
            }
            custom_path = root / BUILDER.CUSTOM_SOURCE_DIRECTORY / "br" / "in-br-eproc2.json"
            custom_path.parent.mkdir(parents=True, exist_ok=True)
            custom_path.write_text(json.dumps(custom, indent=2) + "\n", encoding="utf-8")

            BUILDER.build_all(root)
            manifest = json.loads((root / BUILDER.MANIFEST_PATHS[0]).read_text())
            resource = manifest["resources"]["in-road-notices-br"]
            pack = json.loads((root / "docs" / resource["path"]).read_text())
            self.assertEqual(resource["sources"], 2)
            row = next(item for item in pack["notices"] if item["tender_id"] == "138319")
            self.assertEqual(
                row["organisation_chain"], "Organisation ID 538||Department ID 1874"
            )
            self.assertEqual(
                row["source_url"],
                "https://eproc2.bihar.gov.in/EPSV2Web/openarea/tenderListingPage.action",
            )
            self.assertIsNone(row["published_at"])
            self.assertIsNone(row["opening_at"])
            report = json.loads((root / BUILDER.CRAWL_REPORT_PATH).read_text())
            self.assertEqual(report["source_count_succeeded"], 1)
            self.assertEqual(report["states"]["BR"]["sources"], 1)

    def test_builds_compact_notice_only_packs_manifests_and_full_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_sources(root)
            report_before = (root / BUILDER.CRAWL_REPORT_PATH).read_bytes()

            outputs = BUILDER.build_all(root)
            self.assertEqual(len(outputs), 6)  # 3 packs and 3 manifests
            self.assertEqual(
                (root / BUILDER.CRAWL_REPORT_PATH).read_bytes(), report_before
            )
            manifest_paths = [root / path for path in BUILDER.MANIFEST_PATHS]
            manifest_bytes = [path.read_bytes() for path in manifest_paths]
            self.assertEqual(manifest_bytes[0], manifest_bytes[1])
            self.assertEqual(manifest_bytes[0], manifest_bytes[2])
            manifest = json.loads(manifest_bytes[0])
            self.assertEqual(manifest["format"], "pothole-road-notice-manifest")
            self.assertEqual(manifest["generated_at"], "2026-08-26")
            self.assertEqual(
                set(manifest["resources"]),
                {"in-road-notices-as", "in-road-notices-dh", "in-road-notices-wb"},
            )
            self.assertEqual(manifest["inference_policy"], {
                "candidate_only": True,
                "lifecycle": "procurement_notice",
                "scope": "road_surface",
                "segment_verified": False,
                "award_verified": False,
                "dlp_verified": False,
            })

            for pack_id, resource in manifest["resources"].items():
                self.assertEqual(resource["lifecycle"], "procurement_notice")
                self.assertTrue(resource["candidate_only"])
                self.assertEqual(resource["adapter"], "official-road-notices-v2")
                self.assertEqual(resource["url"], BUILDER.PUBLIC_BASE_URL + resource["path"])
                pack_path = root / "docs" / resource["path"]
                content = pack_path.read_bytes()
                self.assertEqual(resource["bytes"], len(content))
                self.assertEqual(resource["sha256"], hashlib.sha256(content).hexdigest())
                self.assertTrue(content.endswith(b"\n"))
                self.assertEqual(content.count(b"\n"), 1)
                self.assertIn(resource["sha256"], pack_path.name)
                pack = json.loads(content)
                self.assertEqual(pack["pack_id"], pack_id)
                self.assertEqual(pack["inference_policy"], manifest["inference_policy"])
                self.assertEqual(len(pack["notices"]), resource["records"])
                for row in pack["notices"]:
                    self.assertEqual(set(row), BUILDER.RUNTIME_NOTICE_FIELDS)
                    self.assertFalse(set(row) & BUILDER.FORBIDDEN_INFERENCE_FIELDS)
                    self.assertEqual(row["lifecycle"], "procurement_notice")
                    self.assertEqual(row["scope"], "road_surface")
                    self.assertFalse(row["segment_verified"])
                    self.assertFalse(row["award_verified"])
                    self.assertFalse(row["dlp_verified"])

            dh_resource = manifest["resources"]["in-road-notices-dh"]
            dh_pack = json.loads((root / "docs" / dh_resource["path"]).read_bytes())
            self.assertEqual(dh_pack["notices"], [])
            wb_resource = manifest["resources"]["in-road-notices-wb"]
            wb_pack = json.loads((root / "docs" / wb_resource["path"]).read_bytes())
            self.assertEqual(len(wb_pack["sources"]), 2)
            self.assertEqual(
                [row["tender_id"] for row in wb_pack["notices"]],
                ["2026_WBPWD_1", "2026_WBPWD_2"],
            )

            report = json.loads((root / BUILDER.CRAWL_REPORT_PATH).read_bytes())
            self.assertEqual(report["source_count_requested"], 4)
            self.assertEqual(report["source_count_succeeded"], 4)
            self.assertEqual(report["source_count_failed"], 0)
            self.assertEqual(report["failures"], [])
            self.assertEqual(set(report["states"]), {"AS", "DH", "WB"})
            self.assertEqual(report["states"]["DH"]["notices"], 0)
            self.assertEqual(report["states"]["WB"]["sources"], 2)

            first = snapshot(root)
            BUILDER.build_all(root)
            self.assertEqual(snapshot(root), first)
            BUILDER.verify_all(root)

    def test_check_detects_missing_report_without_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_sources(root)
            BUILDER.build_all(root)
            (root / BUILDER.CRAWL_REPORT_PATH).unlink()
            before = snapshot(root)
            with self.assertRaisesRegex(BUILDER.BuildError, "missing canonical crawl report"):
                BUILDER.verify_all(root)
            self.assertEqual(snapshot(root), before)

    def test_stricter_runtime_scope_removes_old_false_positive_without_rewriting_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = source("in-as-gepnic", "AS", ["2026_ASPWD_1"])
            payload["notices"][0]["title"] = (
                "Construction of drain and footpath at MG Road"
            )
            write_sources(root, [payload])
            source_path = root / BUILDER.SOURCE_DIRECTORY / "in-as-gepnic.json"
            source_before = source_path.read_bytes()
            BUILDER.build_all(root)
            self.assertEqual(source_path.read_bytes(), source_before)
            manifest = json.loads((root / BUILDER.MANIFEST_PATHS[0]).read_bytes())
            resource = manifest["resources"]["in-road-notices-as"]
            pack = json.loads((root / "docs" / resource["path"]).read_bytes())
            self.assertEqual(pack["notices"], [])
            self.assertEqual(resource["records"], 0)
            self.assertEqual(resource["rows_excluded_by_scope"], 2)
            self.assertEqual(pack["sources"][0]["rows_excluded_by_scope"], 2)
            BUILDER.verify_all(root)

    def test_rejects_contract_inference_schema_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = source("in-as-gepnic", "AS", ["2026_ASPWD_1"])
            payload["notices"][0]["contractor"] = "Unverified Builder Ltd"
            write_sources(root, [payload])
            with self.assertRaisesRegex(BUILDER.BuildError, "fields differ"):
                BUILDER.plan_build(root)

    def test_missing_registry_source_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = write_sources(root)
            missing_id = "in-dh-gepnic"
            (root / BUILDER.SOURCE_DIRECTORY / f"{missing_id}.json").unlink()
            before = snapshot(root)
            with self.assertRaisesRegex(
                BUILDER.BuildError,
                rf"missing expected GePNIC source files: {missing_id}",
            ):
                BUILDER.build_all(root)
            self.assertEqual(snapshot(root), before)
            self.assertEqual(len(values), 4)

    def _one_failed_source(self, root: Path) -> tuple[dict, bytes]:
        values = write_sources(root)
        failed_source = next(
            value for value in values if value["source_id"] == "in-dh-gepnic"
        )
        (root / BUILDER.SOURCE_DIRECTORY / "in-dh-gepnic.json").unlink()
        succeeded = [value for value in values if value is not failed_source]
        report = {
            "failures": [
                {
                    "source_id": failed_source["source_id"],
                    "source_name": failed_source["source_name"],
                    "state_code": failed_source["state_code"],
                    "organisation_url": failed_source["source_url"],
                    "error": "portal unavailable",
                }
            ],
            "format": "india-gepnic-road-surface-crawl-report",
            "retrieved_at": "2026-08-26T00:00:00Z",
            "schema_version": 1,
            "source_count_failed": 1,
            "source_count_requested": len(values),
            "source_count_succeeded": len(succeeded),
            "states": state_summaries(succeeded),
        }
        rendered = (json.dumps(report, indent=4, sort_keys=False) + "\n").encode()
        (root / BUILDER.CRAWL_REPORT_PATH).write_bytes(rendered)
        return failed_source, rendered

    def test_one_failed_portal_costs_only_its_own_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed_source, report_before = self._one_failed_source(root)
            BUILDER.build_all(root)
            BUILDER.verify_all(root)
            manifest = json.loads(
                (root / "static" / "road-notice-manifest-v1.36.json").read_text()
            )
            states = {value["state_code"] for value in manifest["resources"].values()}
            self.assertNotIn(failed_source["state_code"], states)
            self.assertEqual(
                states,
                {value["state_code"] for value in default_sources()}
                - {failed_source["state_code"]},
            )
            self.assertEqual(
                (root / BUILDER.CRAWL_REPORT_PATH).read_bytes(), report_before
            )

    def test_more_failures_than_tolerated_block_production(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, report_before = self._one_failed_source(root)
            with mock.patch.object(BUILDER, "MAX_FAILED_SOURCES", 0):
                with self.assertRaisesRegex(
                    BUILDER.BuildError,
                    "tolerates at most 0 crawler failures",
                ):
                    BUILDER.build_all(root)
            self.assertEqual(
                (root / BUILDER.CRAWL_REPORT_PATH).read_bytes(), report_before
            )

    def test_failed_portal_with_a_stale_receipt_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed_source, _ = self._one_failed_source(root)
            (root / BUILDER.SOURCE_DIRECTORY / "in-dh-gepnic.json").write_text(
                json.dumps(failed_source), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                BUILDER.BuildError, "recorded as failed still have receipts"
            ):
                BUILDER.build_all(root)

    def _one_carried_source(
        self,
        root: Path,
        source_id: str = "in-wb-gepnic-current",
        retrieved_at: str = "2026-08-24T00:00:00Z",
    ) -> dict:
        """The 26 Aug crawl lost one portal and kept the receipt of an earlier crawl."""
        values = write_sources(root)
        failed_source = next(
            value for value in values if value["source_id"] == source_id
        )
        kept = render_receipt(fetched_at(failed_source, retrieved_at))
        (root / BUILDER.SOURCE_DIRECTORY / f"{source_id}.json").write_bytes(kept)
        succeeded = [value for value in values if value is not failed_source]
        report = {
            "carried_over": [
                {
                    "retrieved_at": retrieved_at,
                    "sha256": hashlib.sha256(kept).hexdigest(),
                    "source_id": source_id,
                    "state_code": failed_source["state_code"],
                }
            ],
            "failures": [
                {
                    "source_id": source_id,
                    "source_name": failed_source["source_name"],
                    "state_code": failed_source["state_code"],
                    "organisation_url": failed_source["source_url"],
                    "error": "portal unavailable",
                }
            ],
            "format": "india-gepnic-road-surface-crawl-report",
            "retrieved_at": CRAWL_AT,
            "schema_version": 1,
            "source_count_failed": 1,
            "source_count_requested": len(values),
            "source_count_succeeded": len(succeeded),
            "states": state_summaries(succeeded),
        }
        rewrite_report(root, report)
        return report

    def test_failed_portal_keeps_a_two_day_old_receipt_under_its_own_dates(self) -> None:
        # Run 37573977613 (7 Oct 2026) lost West Bengal's main portal to a timeout and
        # the State fell from 874 notices to 55, with a receipt two days old at hand.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._one_carried_source(root)
            report_before = (root / BUILDER.CRAWL_REPORT_PATH).read_bytes()
            BUILDER.build_all(root)
            BUILDER.verify_all(root)
            self.assertEqual(
                (root / BUILDER.CRAWL_REPORT_PATH).read_bytes(), report_before
            )
            self.assertEqual(report["states"]["WB"]["sources"], 1)
            manifest = json.loads((root / BUILDER.MANIFEST_PATHS[0]).read_bytes())
            self.assertEqual(manifest["generated_at"], "2026-08-26")
            resource = manifest["resources"]["in-road-notices-wb"]
            pack = json.loads((root / "docs" / resource["path"]).read_bytes())
            self.assertEqual(
                [row["tender_id"] for row in pack["notices"]],
                ["2026_WBPWD_1", "2026_WBPWD_2"],
            )
            self.assertEqual(resource["records"], 2)
            self.assertEqual(resource["sources"], 2)
            # One source is fresh and one is two days old: the State takes the older
            # date for both fields, and the pack agrees, as the client requires.
            self.assertEqual(resource["source_retrieved_at"], "2026-08-24")
            self.assertEqual(resource["review_after"], "2026-08-31")
            self.assertEqual(pack["generated_at"], "2026-08-24")
            self.assertEqual(
                {item["source_id"]: item["retrieved_at"] for item in pack["sources"]},
                {
                    "in-wb-gepnic-current": "2026-08-24T00:00:00Z",
                    "in-wb-gepnic-legacy": "2026-08-26T00:00:00Z",
                },
            )
            for pack_id in ("in-road-notices-as", "in-road-notices-dh"):
                untouched = manifest["resources"][pack_id]
                self.assertEqual(untouched["source_retrieved_at"], "2026-08-26")
                self.assertEqual(untouched["review_after"], "2026-09-02")

    def test_state_with_only_a_carried_receipt_keeps_the_pack_it_already_had(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            earlier = [
                fetched_at(value, "2026-08-24T00:00:00Z") for value in default_sources()
            ]
            write_sources(root, earlier)
            report = json.loads((root / BUILDER.CRAWL_REPORT_PATH).read_bytes())
            report["retrieved_at"] = "2026-08-24T00:00:00Z"
            rewrite_report(root, report)
            BUILDER.build_all(root)
            before = json.loads((root / BUILDER.MANIFEST_PATHS[0]).read_bytes())
            old_resource = before["resources"]["in-road-notices-as"]
            old_pack = (root / "docs" / old_resource["path"]).read_bytes()

            self._one_carried_source(root, "in-as-gepnic")
            BUILDER.build_all(root)
            after = json.loads((root / BUILDER.MANIFEST_PATHS[0]).read_bytes())
            self.assertEqual(after["resources"]["in-road-notices-as"], old_resource)
            self.assertEqual(old_resource["source_retrieved_at"], "2026-08-24")
            self.assertEqual(old_resource["review_after"], "2026-08-31")
            self.assertEqual(
                (root / "docs" / old_resource["path"]).read_bytes(), old_pack
            )
            self.assertEqual(
                after["resources"]["in-road-notices-dh"]["source_retrieved_at"],
                "2026-08-26",
            )

    def test_carried_receipt_is_kept_to_the_last_day_of_the_review_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._one_carried_source(root, retrieved_at="2026-08-19T00:00:00Z")
            BUILDER.build_all(root)
            manifest = json.loads((root / BUILDER.MANIFEST_PATHS[0]).read_bytes())
            resource = manifest["resources"]["in-road-notices-wb"]
            self.assertEqual(resource["source_retrieved_at"], "2026-08-19")
            self.assertEqual(resource["review_after"], "2026-08-26")

    def test_failed_portal_with_an_eight_day_old_receipt_is_refused(self) -> None:
        for retrieved_at in ("2026-08-18T00:00:00Z", "2026-08-18T23:59:59Z"):
            with self.subTest(retrieved_at=retrieved_at):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self._one_carried_source(root, retrieved_at=retrieved_at)
                    before = snapshot(root)
                    with self.assertRaisesRegex(
                        BUILDER.BuildError, "outside the 7-day review window"
                    ):
                        BUILDER.build_all(root)
                    self.assertEqual(snapshot(root), before)

    def test_carried_receipt_changed_after_the_crawl_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._one_carried_source(root)
            path = root / BUILDER.SOURCE_DIRECTORY / "in-wb-gepnic-current.json"
            tampered = json.loads(path.read_bytes())
            tampered["notices"][0]["title"] = "Resurfacing of NH 44 at another place"
            path.write_bytes(render_receipt(tampered))
            before = snapshot(root)
            with self.assertRaisesRegex(
                BUILDER.BuildError, "sha256 differs from its receipt"
            ):
                BUILDER.build_all(root)
            self.assertEqual(snapshot(root), before)

    def test_carried_receipt_that_does_not_match_the_ledger_is_refused(self) -> None:
        source_id = "in-wb-gepnic-current"

        def another_date(root: Path, report: dict) -> None:
            report["carried_over"][0]["retrieved_at"] = "2026-08-25T00:00:00Z"

        def newer_than_the_crawl(root: Path, report: dict) -> None:
            kept = render_receipt(fetched_at(
                source(source_id, "WB", ["2026_WBPWD_2"]), "2026-08-27T00:00:00Z"
            ))
            (root / BUILDER.SOURCE_DIRECTORY / f"{source_id}.json").write_bytes(kept)
            report["carried_over"][0].update(
                retrieved_at="2026-08-27T00:00:00Z",
                sha256=hashlib.sha256(kept).hexdigest(),
            )

        def fetched_by_this_crawl(root: Path, report: dict) -> None:
            kept = render_receipt(source(source_id, "WB", ["2026_WBPWD_2"]))
            (root / BUILDER.SOURCE_DIRECTORY / f"{source_id}.json").write_bytes(kept)
            report["carried_over"][0].update(
                retrieved_at=CRAWL_AT, sha256=hashlib.sha256(kept).hexdigest()
            )

        def receipt_of_another_source(root: Path, report: dict) -> None:
            kept = render_receipt(fetched_at(
                source("in-as-gepnic", "AS", ["2026_ASPWD_9"]), "2026-08-24T00:00:00Z"
            ))
            (root / BUILDER.SOURCE_DIRECTORY / f"{source_id}.json").write_bytes(kept)
            report["carried_over"][0]["sha256"] = hashlib.sha256(kept).hexdigest()

        def no_receipt(root: Path, report: dict) -> None:
            (root / BUILDER.SOURCE_DIRECTORY / f"{source_id}.json").unlink()

        def source_that_did_not_fail(root: Path, report: dict) -> None:
            report["carried_over"][0]["source_id"] = "in-wb-gepnic-legacy"

        def another_state(root: Path, report: dict) -> None:
            report["carried_over"][0]["state_code"] = "AS"

        def listed_twice(root: Path, report: dict) -> None:
            report["carried_over"].append(dict(report["carried_over"][0]))

        def undocumented_field(root: Path, report: dict) -> None:
            report["carried_over"][0]["reason"] = "trust me"

        def not_a_list(root: Path, report: dict) -> None:
            (root / BUILDER.SOURCE_DIRECTORY / f"{source_id}.json").unlink()
            report["carried_over"] = {}

        cases = (
            (another_date, "retrieved_at differs from its receipt"),
            (newer_than_the_crawl, "is not older than the crawl"),
            (fetched_by_this_crawl, "is not older than the crawl"),
            (receipt_of_another_source, "filename must equal source_id"),
            (no_receipt, "has no receipt"),
            (source_that_did_not_fail, "recorded as failed still have receipts"),
            (another_state, "state_code differs from the registry"),
            (listed_twice, "carried-over ledger contains duplicate source IDs"),
            (undocumented_field, "fields differ from the crawler contract"),
            (not_a_list, "carried_over must be an array"),
        )
        for change, message in cases:
            with self.subTest(change=change.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    report = self._one_carried_source(root)
                    change(root, report)
                    rewrite_report(root, report)
                    before = snapshot(root)
                    with self.assertRaisesRegex(BUILDER.BuildError, message):
                        BUILDER.build_all(root)
                    self.assertEqual(snapshot(root), before)

    def test_only_a_failed_source_may_be_carried_over(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = write_sources(root)
            older = "2026-08-24T00:00:00Z"
            kept = render_receipt(fetched_at(values[0], older))
            (root / BUILDER.SOURCE_DIRECTORY / "in-as-gepnic.json").write_bytes(kept)
            report = json.loads((root / BUILDER.CRAWL_REPORT_PATH).read_bytes())
            report["carried_over"] = [{
                "retrieved_at": older,
                "sha256": hashlib.sha256(kept).hexdigest(),
                "source_id": "in-as-gepnic",
                "state_code": "AS",
            }]
            rewrite_report(root, report)
            with self.assertRaisesRegex(
                BUILDER.BuildError, "names a source this crawl did not record as failed"
            ):
                BUILDER.build_all(root)

    def test_fresh_sources_must_still_share_one_retrieved_at(self) -> None:
        for carried in (False, True):
            with self.subTest(carried=carried):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    if carried:
                        self._one_carried_source(root)
                    else:
                        write_sources(root)
                    # An older receipt nobody recorded as carried over is not a crawl.
                    stray = fetched_at(
                        source("in-dh-gepnic", "DH", [], excluded=3),
                        "2026-08-24T00:00:00Z",
                    )
                    (root / BUILDER.SOURCE_DIRECTORY / "in-dh-gepnic.json").write_bytes(
                        render_receipt(stray)
                    )
                    with self.assertRaisesRegex(
                        BUILDER.BuildError, "must share one retrieved_at"
                    ):
                        BUILDER.build_all(root)

    def test_carried_sources_still_count_as_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._one_carried_source(root)
            with mock.patch.object(BUILDER, "MAX_FAILED_SOURCES", 0):
                with self.assertRaisesRegex(
                    BUILDER.BuildError, "tolerates at most 0 crawler failures"
                ):
                    BUILDER.build_all(root)

    def test_receipt_of_an_undeclared_source_is_refused_even_if_carried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._one_carried_source(root)
            older = "2026-08-24T00:00:00Z"
            kept = render_receipt(
                fetched_at(source("in-zz-gepnic", "ZZ", ["2026_ZZ_1"]), older)
            )
            (root / BUILDER.SOURCE_DIRECTORY / "in-zz-gepnic.json").write_bytes(kept)
            report["carried_over"].append({
                "retrieved_at": older,
                "sha256": hashlib.sha256(kept).hexdigest(),
                "source_id": "in-zz-gepnic",
                "state_code": "ZZ",
            })
            rewrite_report(root, report)
            with self.assertRaisesRegex(
                BUILDER.BuildError, "not declared by registry: in-zz-gepnic"
            ):
                BUILDER.build_all(root)

    def test_clean_crawl_builds_the_bytes_it_built_before_carry_over(self) -> None:
        for ledger_names_none_carried in (False, True):
            with self.subTest(ledger_names_none_carried=ledger_names_none_carried):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    write_sources(root)
                    if ledger_names_none_carried:
                        report = json.loads(
                            (root / BUILDER.CRAWL_REPORT_PATH).read_bytes()
                        )
                        report["carried_over"] = []
                        rewrite_report(root, report)
                    BUILDER.build_all(root)
                    manifest = (root / BUILDER.MANIFEST_PATHS[0]).read_bytes()
                    # The manifest names every pack by its SHA-256, so one digest
                    # covers the packs too.
                    self.assertEqual(
                        hashlib.sha256(manifest).hexdigest(),
                        CLEAN_CRAWL_MANIFEST_SHA256,
                    )

    def _custom_with(self, root: Path, **counts: int) -> Path:
        values = [source("in-br-gepnic", "BR", ["GEP-1"])]
        write_sources(root, values)
        add_custom_registry_source(root, "in-br-eproc2", "BR", "bihar_eproc2")
        custom = {
            "format": "official-road-surface-procurement-notices",
            "schema_version": 1,
            "source_id": "in-br-eproc2",
            "source_name": "Bihar eProc2.0 public active tenders",
            "source_url": "https://eproc2.bihar.gov.in/EPSV2Web/",
            "retrieved_at": "2026-08-26T06:20:18Z",
            "state_code": "BR",
            "lifecycle": "procurement_notice",
            "rows_excluded_by_scope": 1,
            "records_kept": 0,
            "notices": [],
            **counts,
        }
        path = root / BUILDER.CUSTOM_SOURCE_DIRECTORY / "br" / "in-br-eproc2.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(custom, indent=2) + "\n", encoding="utf-8")
        return path

    def test_rows_dropped_for_deadline_or_cancellation_are_accounted_for(self) -> None:
        # Bihar's puller also drops expired, cancelled and invalid rows. The first live
        # pull with one expired row failed the whole national build on "row accounting".
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._custom_with(
                root, rows_scanned=4, rows_excluded_by_deadline=1,
                rows_excluded_cancelled=1, rows_excluded_invalid=1,
            )
            BUILDER.build_all(root)

    def test_rows_that_vanish_without_a_reason_still_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._custom_with(root, rows_scanned=4, rows_excluded_by_deadline=1)
            with self.assertRaisesRegex(BUILDER.BuildError, "row accounting is inconsistent"):
                BUILDER.build_all(root)

if __name__ == "__main__":
    unittest.main(verbosity=2)
