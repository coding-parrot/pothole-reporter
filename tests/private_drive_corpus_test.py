#!/usr/bin/env python3
"""Source-free contract tests for the private Desktop drive corpus."""

import copy
import inspect
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
import private_drive_corpus as corpus_runner  # noqa: E402


FAILURES = []


def check(name, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        FAILURES.append(name)


manifest_path = ROOT / "eval" / "private_drive_corpus.json"
manifest = corpus_runner.load_historical_v15_manifest(manifest_path)
sources = manifest["sources"]
cases = manifest["cases"]
phases = [phase for case in cases for phase in case["phases"]]

archive_contract = corpus_runner.historical_contracts.v15_contract()
archive_receipt = corpus_runner.historical_contracts.v15_contract_receipt()
check("immutable v15 prompt and schema recompute the committed archive receipt",
      manifest["production_contract"] == archive_receipt
      and archive_contract["prompt_version"] == "pothole-binary-v15"
      and archive_receipt["prompt_sha256"] ==
          "621866cba94700717358426bba70b274c8f23df081ae2da7db6e9199c97e1c98"
      and archive_receipt["schema_sha256"] ==
          "4a71363f4a78f0da8af8cdc58301c688bd434308733664680a5f5ef59d14945a")
try:
    corpus_runner.load_manifest(manifest_path)
    current_loader_rejected_archive = False
except corpus_runner.CorpusError:
    current_loader_rejected_archive = True
check("current-v19 loader rejects the archived v15 corpus",
      current_loader_rejected_archive)

check("all 30 private source fingerprints are retained", len(sources) == 30)
check("source IDs and source hashes are unique",
      len({source["source_id"] for source in sources}) == 30
      and len({source["sha256"] for source in sources}) == 30)
check("the exact supplied corpus totals are retained",
      sum(source["bytes"] for source in sources) == 473_009_755
      and sum(source["frames"] for source in sources) == 49_736
      and abs(sum(source["duration_seconds"] for source in sources) - 1657.639381) < 1e-6)
check("ordered source inventory is sealed",
      corpus_runner.source_inventory_sha256(sources)
      == "f27d04faa327a2d4ddeff34c1a2668093778a5e65bdf5d783c87cfc8f2087467"
      == manifest["corpus"]["source_inventory_sha256"])
check("all source videos retain their real rotation and presentation dimensions",
      all(source["coded_width"] == 720 and source["coded_height"] == 480
          and source["presented_width"] == 480 and source["presented_height"] == 720
          and source["rotation_degrees"] == -90.0 for source in sources))

window_counts = [len(corpus_runner.vod_sample_times(
    source["duration_seconds"], manifest["sampling"]["center_step_seconds"]))
                 for source in sources]
check("production VOD cadence deterministically yields all 3,314 windows",
      sum(window_counts) == 3314
      and manifest["audit_receipt"]["requests"] == 3314
      and manifest["audit_receipt"]["raw_frames_extracted"] == 9942)
check("VOD boundary sampling mirrors the shipped pure functions",
      corpus_runner.vod_sample_times(59.990244, 0.5)[0] == 0.4
      and len(corpus_runner.vod_sample_times(59.990244, 0.5)) == 120
      and corpus_runner.vod_burst_times(0.4, 59.990244, 0.4) == [0.001, 0.4, 0.8])

check("17 reviewed cases retain 50 exact three-frame phases",
      len(cases) == 17 and len(phases) == 50
      and manifest["corpus"]["curated_case_count"] == 17
      and manifest["corpus"]["curated_phase_count"] == 50)
fixture_hashes = [fingerprint for phase in phases
                  for fingerprint in phase["full_frame_jpeg_sha256"]]
check("all 150 regenerated full-frame fixtures have unique locked hashes",
      len(fixture_hashes) == 150 and len(set(fixture_hashes)) == 150)
check("every curated Desktop case is a conservative negative or abstention",
      all(case["expected_is_pothole"] is False for case in cases)
      and manifest["companion_suite"]["desktop_corpus_role"]
      == "hard_negative_and_abstention_regression_only")
check("saved MP4 fixtures never claim raw CameraX accuracy",
      manifest["corpus"]["capture_provenance"] == "native_mediarecorder_reconstruction"
      and manifest["corpus"]["raw_camerax_accuracy_eligible"] is False
      and manifest["corpus"]["gps_or_session_metadata_present"] is False)

classes = {case["semantic_class"] for case in cases}
check("hard negatives cover the observed real-drive failure classes",
      {"speed_breaker_or_rumble_strips", "raised_loose_debris",
       "traffic_cone_or_marker", "temporary_roadworks_surface",
       "rough_or_patchy_surface_without_cavity",
       "tunnel_glare_and_low_light_transition", "road_occluded_by_traffic",
       "unpaved_or_dusty_shoulder_beside_intact_lane", "clear_paved_road"}
      <= classes)
check("the exhaustive model scan is explicitly not used as ground truth",
      manifest["audit_receipt"]["model_output_is_ground_truth"] is False
      and "must not" in manifest["audit_receipt"]["interpretation"].lower())
check("exhaustive request and result receipts are pinned",
      manifest["audit_receipt"]["aggregate_request_stream_sha256"]
      == "be3c437f5bb03f128f737451c5d979a720c1dd9da2fd920ec13c96aa7e2983b8"
      and manifest["audit_receipt"]["canonical_direct_records_sha256"]
      == "9532b7c6a62f917a850a6a30f46a37b26bb2e4d2b8c0322389c22c6e41bdf4ab"
      and manifest["audit_receipt"]["result_analysis_sha256"]
      == "49eb5f866e9a1e86af8f20c6b8fc91b85048e67ac8863e25b15a79bfbdd3adb8")
check("owner-confirmed positives remain a separate named companion suite",
      manifest["companion_suite"]["owner_confirmed_drive_positives"]["manifest"]
      == "private_release_gate.json"
      and len(manifest["companion_suite"]["owner_confirmed_drive_positives"]["event_ids"]) == 2)

extraction = manifest["extraction"]
check("fixture preparation is complete-frame only",
      extraction["ffmpeg_default_autorotate"] is True
      and extraction["whole_frame_only"] is True
      and extraction["spatial_crop_tile_mask_or_roi"] is False
      and extraction["presented_dimensions"] == [480, 720])
runner_source = inspect.getsource(corpus_runner)
check("the corpus runner contains no spatial-subset transform",
      all(term not in runner_source for term in (
          ".crop(", "ImageOps.fit", "select_road_region", "road_band", "region_of_interest")))
check("generated private media and responses stay under an ignored work root",
      corpus_runner.DEFAULT_WORK_ROOT == ROOT / "eval" / ".private-drive-corpus")

bad_absolute = copy.deepcopy(manifest)
bad_absolute["corpus"]["private_path"] = "/Users/example/private.mp4"
try:
    corpus_runner.validate_historical_v15_manifest(bad_absolute)
    absolute_rejected = False
except corpus_runner.CorpusError:
    absolute_rejected = True
check("manifest validation rejects absolute private paths", absolute_rejected)

bad_hash = copy.deepcopy(manifest)
bad_hash["cases"][0]["phases"][0]["full_frame_jpeg_sha256"][0] = "bad"
try:
    corpus_runner.validate_historical_v15_manifest(bad_hash)
    bad_hash_rejected = False
except corpus_runner.CorpusError:
    bad_hash_rejected = True
check("manifest validation fails closed on an invalid fixture hash", bad_hash_rejected)

bad_interval = copy.deepcopy(manifest)
bad_interval["cases"][0]["source_interval_seconds"] = [0.0, 1.0]
try:
    corpus_runner.validate_historical_v15_manifest(bad_interval)
    bad_interval_rejected = False
except corpus_runner.CorpusError:
    bad_interval_rejected = True
check("manifest validation rejects a phase outside its reviewed interval",
      bad_interval_rejected)

invented_positive = copy.deepcopy(manifest)
invented_positive["cases"][0]["expected_is_pothole"] = True
try:
    corpus_runner.validate_historical_v15_manifest(invented_positive)
    invented_positive_rejected = False
except corpus_runner.CorpusError:
    invented_positive_rejected = True
check("Desktop model output cannot manufacture a positive ground-truth case",
      invented_positive_rejected)

environment = dict(os.environ)
environment.pop("OPENAI_API_KEY", None)
manifest_check = subprocess.run(
    [sys.executable, str(ROOT / "eval" / "private_drive_corpus.py"), "--check-manifest"],
    cwd=ROOT, env=environment, text=True, capture_output=True,
)
check("current CLI refuses to present the v15 archive as a current manifest",
      manifest_check.returncode != 0
      and "production contract has drifted" in manifest_check.stderr
      and "OPENAI_API_KEY" not in manifest_check.stderr)

unbounded_paid = subprocess.run(
    [sys.executable, str(ROOT / "eval" / "private_drive_corpus.py"),
     "--paid-run", "--source-dir", str(ROOT / "does-not-exist")],
    cwd=ROOT, env=environment, text=True, capture_output=True,
)
check("paid inference rejects the archived manifest before budget or API access",
      unbounded_paid.returncode != 0
      and "production contract has drifted" in unbounded_paid.stderr
      and "OPENAI_API_KEY" not in unbounded_paid.stderr)
check("paid preflight reserves the exact bounded 2-of-3 worst case",
      corpus_runner.maximum_policy_calls(len(phases)) == 150
      and corpus_runner.maximum_policy_calls(0) == 0)

contract, _receipt = corpus_runner.current_contract_receipt()
phase = phases[0]
valid_assessment = {
    "is_pothole": False,
    "looks_like_speed_breaker": True,
    "image_quality": "usable",
    "surface_type": "bituminous_asphalt",
    "on_drivable_surface": True,
    "has_localized_cavity": False,
    "has_unambiguous_lower_interior": False,
    "has_broken_edge_or_rim": False,
    "has_depth_or_surface_loss": False,
    "temporal_consistency": "consistent",
    "size": None,
    "description": "Raised traffic-calming feature, not a cavity.",
}
valid_cache = {
    "format_version": 1,
    "custom_id": phase["custom_id"],
    "request_sha256": "a" * 64,
    "response_id": "resp_test",
    "assessment": valid_assessment,
}
try:
    corpus_runner.validate_cached_record(
        valid_cache, phase, "a" * 64, contract["schema"])
    valid_cache_accepted = True
except corpus_runner.CorpusError:
    valid_cache_accepted = False
check("paid-run cache accepts only the current sealed record shape", valid_cache_accepted)

attempt_two_cache = {
    **valid_cache,
    "format_version": 2,
    "attempt_number": 2,
}
try:
    corpus_runner.validate_cached_record(
        attempt_two_cache, phase, "a" * 64, contract["schema"], 2)
    distinct_attempt_cache_accepted = True
except corpus_runner.CorpusError:
    distinct_attempt_cache_accepted = False
check("confirmation caches are attempt-bound and cannot replay attempt one",
      distinct_attempt_cache_accepted
      and corpus_runner.cache_path(Path("work"), phase, "a" * 64, 1)
      != corpus_runner.cache_path(Path("work"), phase, "a" * 64, 2))

cache_mutations = []
for field, replacement in (
        ("format_version", 2), ("custom_id", "wrong-phase"),
        ("request_sha256", "b" * 64), ("response_id", "")):
    mutated = copy.deepcopy(valid_cache)
    mutated[field] = replacement
    cache_mutations.append(mutated)
extra_cache = copy.deepcopy(valid_cache)
extra_cache["unexpected"] = True
cache_mutations.append(extra_cache)
bad_assessment_cache = copy.deepcopy(valid_cache)
bad_assessment_cache["assessment"].pop("is_pothole")
cache_mutations.append(bad_assessment_cache)
cache_failures_are_clean = True
for mutated in cache_mutations:
    try:
        corpus_runner.validate_cached_record(
            mutated, phase, "a" * 64, contract["schema"])
        cache_failures_are_clean = False
    except corpus_runner.CorpusError:
        pass
check("paid-run cache rejects mismatches and schema corruption cleanly",
      cache_failures_are_clean)

with tempfile.TemporaryDirectory() as temporary:
    with patch.object(
            corpus_runner.release_gate, "fresh_api_assessment",
            side_effect=corpus_runner.release_gate.GateError("simulated API failure")):
        try:
            corpus_runner.cached_or_fresh_assessment(
                Path(temporary), phase, {"sealed": "request"}, contract,
                "test-key", 1)
            fresh_failure_is_clean = False
        except corpus_runner.CorpusError:
            fresh_failure_is_clean = True
check("paid-run converts API failures into a clean corpus failure", fresh_failure_is_clean)

with patch.object(
        corpus_runner.release_gate, "load_api_key",
        side_effect=corpus_runner.release_gate.GateError("must not be called")) as load_key:
    try:
        corpus_runner.main([
            "--paid-run", "--source-dir", "missing", "--max-calls",
            str(corpus_runner.maximum_policy_calls(len(phases))),
        ])
        archived_paid_path_rejected = False
    except corpus_runner.CorpusError as error:
        archived_paid_path_rejected = (
            "production contract has drifted" in str(error)
            and load_key.call_count == 0)
check("historical archive API cannot be routed into the current paid path",
      archived_paid_path_rejected)

if FAILURES:
    raise SystemExit(f"\n{len(FAILURES)} private drive corpus check(s) failed")

print("\nPRIVATE DRIVE CORPUS TEST PASS")
