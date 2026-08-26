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

    def test_crawler_failure_ledger_is_preserved_and_blocks_production(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = write_sources(root)
            failed_source = next(
                value for value in values if value["source_id"] == "in-dh-gepnic"
            )
            report_before = write_report(
                root,
                values,
                failures=[
                    {
                        "source_id": failed_source["source_id"],
                        "source_name": failed_source["source_name"],
                        "state_code": failed_source["state_code"],
                        "organisation_url": failed_source["source_url"],
                        "error": "portal unavailable",
                    }
                ],
            )
            with self.assertRaisesRegex(
                BUILDER.BuildError,
                "production GePNIC build requires zero crawler failures",
            ):
                BUILDER.build_all(root)
            self.assertEqual(
                (root / BUILDER.CRAWL_REPORT_PATH).read_bytes(), report_before
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
