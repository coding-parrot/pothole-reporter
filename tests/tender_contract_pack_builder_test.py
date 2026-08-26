#!/usr/bin/env python3
"""Focused tests for the deterministic national-highway contract-pack builder."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "build-highway-contract-packs.py"
SPEC = importlib.util.spec_from_file_location("highway_contract_pack_builder", TOOL)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)

RUNTIME_RECORD_FIELDS = {
    "record_id",
    "reference_label",
    "reference_value",
    "state_code",
    "agency",
    "lifecycle",
    "lifecycle_status",
    "title",
    "highway_refs",
    "chainages",
    "contractor",
    "published_at",
    "start_date",
    "likely_completion_date",
    "division",
    "source_name",
    "source_url",
    "retrieved_at",
    "scope_verified",
    "segment_verified",
    "award_verified",
    "dlp_verified",
}


def contract(
    record_id: str,
    state_code: str,
    *,
    reference_label: str,
    agency: str,
    lifecycle: str,
    contractor_name: str | None,
    award_verified: bool,
    bid_due_at: str | None,
) -> dict:
    return {
        "record_id": record_id,
        "reference_label": reference_label,
        "reference_value": f"REF-{record_id}",
        "state_code": state_code,
        "agency": agency,
        "lifecycle": lifecycle,
        "lifecycle_status": "Under construction" if lifecycle == "current_project" else "Open",
        "title": f"Road maintenance on NH-44 for {record_id}",
        "highway_refs": ["NH-44"],
        "chainages": [{"start_km": 20.0, "end_km": 30}],
        "contractor": contractor_name,
        "published_at": None,
        "start_date": "2026-01-10" if lifecycle == "current_project" else None,
        "likely_completion_date": (
            "2027-01-10" if lifecycle == "current_project" else None
        ),
        "bid_due_at": bid_due_at,
        "division": "Project Implementation Unit",
        "source_name": "Official public project page",
        "source_url": f"https://example.gov.in/projects/{record_id}",
        "retrieved_at": "2026-08-26",
        "scope_verified": True,
        "segment_verified": False,
        "award_verified": award_verified,
        "dlp_verified": False,
    }


def fixture_payload() -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-08-26T09:30:00Z",
        "sources": [
            {
                "source_name": "Official NHAI/MoRTH project records",
                "source_url": "https://example.gov.in/projects",
                "retrieved_at": "2026-08-26",
                "input_mode": "official_snapshot",
                "sha256": "a" * 64,
                "records_seen": 3,
                "records_kept": 3,
            }
        ],
        "contracts": [
            contract(
                "mh-project-z",
                "MH",
                reference_label="UPC",
                agency="MoRTH",
                lifecycle="current_project",
                contractor_name="Road Builder Limited",
                award_verified=True,
                bid_due_at=None,
            ),
            contract(
                "as-tender-a",
                "AS",
                reference_label="Tender ID",
                agency="MoRTH",
                lifecycle="procurement_notice",
                contractor_name=None,
                award_verified=False,
                bid_due_at="2026-09-10T17:00:00+05:30",
            ),
            contract(
                "mh-notice-a",
                "MH",
                reference_label="Official notice fingerprint",
                agency="NHIDCL",
                lifecycle="procurement_notice",
                contractor_name=None,
                award_verified=False,
                bid_due_at="2026-09-12T17:00:00+05:30",
            ),
        ],
    }


def write_fixture(root: pathlib.Path, payload: dict | None = None) -> None:
    source = root / "data" / "tenders-national-highways.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        json.dumps(payload or fixture_payload(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def snapshot(root: pathlib.Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class TenderContractPackBuilderTest(unittest.TestCase):
    def test_builds_deterministic_runtime_valid_packs_and_identical_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_fixture(root)

            outputs = BUILDER.build_all(root)
            self.assertEqual(len(outputs), 5)  # two packs and three manifest mirrors

            manifest_paths = [root / path for path in BUILDER.MANIFEST_RELATIVE_PATHS]
            manifest_bytes = [path.read_bytes() for path in manifest_paths]
            self.assertEqual(manifest_bytes[0], manifest_bytes[1])
            self.assertEqual(manifest_bytes[0], manifest_bytes[2])
            manifest = json.loads(manifest_bytes[0])
            self.assertEqual(manifest["format"], "pothole-contract-manifest")
            self.assertEqual(manifest["generated_at"], "2026-08-26")
            self.assertEqual(
                set(manifest["resources"]),
                {"in-nh-contracts-as", "in-nh-contracts-mh"},
            )

            for pack_id, resource in manifest["resources"].items():
                self.assertEqual(resource["adapter"], "nhai-nhidcl-public-projects-v1")
                self.assertEqual(resource["url"], BUILDER.PUBLIC_BASE_URL + resource["path"])
                pack_path = root / "docs" / resource["path"]
                pack_bytes = pack_path.read_bytes()
                self.assertEqual(resource["bytes"], len(pack_bytes))
                self.assertEqual(resource["sha256"], hashlib.sha256(pack_bytes).hexdigest())
                self.assertEqual(pack_bytes.count(b"\n"), 1)
                self.assertTrue(pack_bytes.endswith(b"\n"))

                pack = json.loads(pack_bytes)
                self.assertEqual(pack["format"], "pothole-highway-contract-pack")
                self.assertEqual(pack["pack_id"], pack_id)
                self.assertEqual(pack["generated_at"], resource["source_retrieved_at"])
                self.assertEqual(resource["records"], len(pack["contracts"]))
                for row in pack["contracts"]:
                    self.assertEqual(set(row), RUNTIME_RECORD_FIELDS)
                    self.assertNotIn("bid_due_at", row)

            mh_resource = manifest["resources"]["in-nh-contracts-mh"]
            mh_pack = json.loads((root / "docs" / mh_resource["path"]).read_bytes())
            self.assertEqual(
                [row["record_id"] for row in mh_pack["contracts"]],
                ["mh-notice-a", "mh-project-z"],
            )
            self.assertEqual(mh_pack["contracts"][0]["reference_label"], "NHIDCL notice")

            first_build = snapshot(root)
            BUILDER.build_all(root)
            self.assertEqual(snapshot(root), first_build)
            BUILDER.verify_all(root)

    def test_check_detects_missing_outputs_without_mutating(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_fixture(root)
            before = snapshot(root)
            with self.assertRaisesRegex(BUILDER.BuildError, "missing generated contract pack"):
                BUILDER.verify_all(root)
            self.assertEqual(snapshot(root), before)

    def test_check_detects_manifest_drift_without_repairing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_fixture(root)
            BUILDER.build_all(root)
            manifest = root / BUILDER.MANIFEST_RELATIVE_PATHS[0]
            manifest.write_bytes(manifest.read_bytes() + b" ")
            before = snapshot(root)
            with self.assertRaisesRegex(BUILDER.BuildError, "contract manifest differs"):
                BUILDER.verify_all(root)
            self.assertEqual(snapshot(root), before)

    def test_rejects_source_schema_drift_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            payload = copy.deepcopy(fixture_payload())
            payload["contracts"][0]["unexpected"] = True
            write_fixture(root, payload)
            before = snapshot(root)
            with self.assertRaisesRegex(BUILDER.BuildError, "fields differ"):
                BUILDER.build_all(root)
            self.assertEqual(snapshot(root), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
