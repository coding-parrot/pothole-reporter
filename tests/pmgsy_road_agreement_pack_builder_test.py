#!/usr/bin/env python3
"""Focused tests for content-addressed nationwide PMGSY runtime packs."""

import copy
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


PULLER = load_module(
    "pull_pmgsy_road_agreements_for_builder",
    ROOT / "tools" / "pull-pmgsy-road-agreements.py",
)
BUILDER = load_module(
    "build_pmgsy_road_agreement_packs",
    ROOT / "tools" / "build-pmgsy-road-agreement-packs.py",
)


class PmgsyRoadAgreementPackBuilderTest(unittest.TestCase):
    def setUp(self):
        fixture = ROOT / "tests" / "fixtures" / "pmgsy-road-agreements.json"
        self.rows = json.loads(fixture.read_text(encoding="utf-8"))
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        directory = self.root / BUILDER.SOURCE_DIRECTORY
        directory.mkdir(parents=True)
        for feed in PULLER.ALL_STATE_FEEDS:
            state_id, code, _name = feed
            rows = [row for row in self.rows if row.get("MAST_STATE_CODE") == state_id]
            snapshot = PULLER.source_snapshot(rows, "2026-08-26T09:00:00Z", feed)
            path = directory / f"{PULLER.source_id(state_id, code)}.json.gz"
            PULLER.write_json_atomic(path, snapshot)

    def tearDown(self):
        self.temporary.cleanup()

    def test_build_emits_all_36_state_ut_packs_and_merges_both_dh_sources(self):
        packs, manifest_bytes = BUILDER.plan_build(self.root)
        manifest = json.loads(manifest_bytes)
        self.assertEqual(len(packs), 36)
        self.assertEqual(len(manifest["resources"]), 36)
        self.assertEqual(manifest["inference_policy"]["candidate_only"], True)
        self.assertTrue(manifest["inference_policy"]["agreement_verified"])
        self.assertFalse(manifest["inference_policy"]["award_verified"])
        dh = manifest["resources"]["in-road-agreements-dh"]
        self.assertEqual(dh["sources"], 2)
        self.assertEqual(dh["records"], 0)
        an = manifest["resources"]["in-road-agreements-an"]
        self.assertEqual(an["records"], 0)

    def test_pack_is_content_addressed_and_never_claims_segment_or_contractor(self):
        packs, _manifest_bytes = BUILDER.plan_build(self.root)
        br_path = next(path for path in packs if "/br/" in path.as_posix())
        content = packs[br_path]
        digest = __import__("hashlib").sha256(content).hexdigest()
        self.assertEqual(br_path.name, f"agreements-{digest}.json")
        pack = json.loads(content)
        self.assertEqual(len(pack["agreements"]), 1)
        agreement = dict(zip(pack["agreement_fields"], pack["agreements"][0]))
        self.assertEqual(agreement["reference_value"], "BR01P3R100")
        self.assertEqual(agreement["agreement_number"], "09/SBD/PMGSY-III-2025-26")
        self.assertTrue(pack["inference_policy"]["agreement_verified"])
        self.assertFalse(pack["inference_policy"]["award_verified"])
        self.assertFalse(pack["inference_policy"]["segment_verified"])
        self.assertFalse(pack["inference_policy"]["contractor_assignment_verified"])
        self.assertFalse(pack["inference_policy"]["dlp_verified"])

    def test_build_and_check_keep_three_manifest_mirrors_identical(self):
        outputs = BUILDER.build_all(self.root)
        self.assertTrue(outputs)
        BUILDER.verify_all(self.root)
        mirrors = [
            (self.root / path).read_bytes() for path in BUILDER.MANIFEST_PATHS
        ]
        self.assertEqual(len(set(mirrors)), 1)

    def test_strict_source_contract_rejects_invented_contractor(self):
        path = self.root / BUILDER.SOURCE_DIRECTORY / "in-br-pmgsy-05.json.gz"
        source = json.loads(gzip.decompress(path.read_bytes()))
        tampered = copy.deepcopy(source)
        tampered["agreements"][0]["contractor"] = "Invented contractor"
        PULLER.write_json_atomic(path, tampered)
        with self.assertRaisesRegex(BUILDER.BuildError, "invents a contractor"):
            BUILDER.plan_build(self.root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
