#!/usr/bin/env python3
"""Offline contract for the exact private-media release gate."""

import copy
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
import private_release_gate as gate  # noqa: E402


FAILURES = []


def check(name, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        FAILURES.append(name)


manifest = gate.load_manifest(ROOT / "eval" / "private_release_gate.json")
events = manifest["events"]
expected_sources = {
    "construction-drive-segment-1":
        "feea330adaa09a5573f7d0b379170c5a03bb6a5ae07484d44299c4703ef74686",
    "construction-drive-segment-2":
        "f9d51250ccdb955159db337490e30c46bc0f5422564aebdbe7b8b4ee48506c7d",
    "tester-speed-breaker-clip":
        "3578f285f1aa45f66ccbd039f49ce23c8c2efdf203760966aaabfdb8cedf5bce",
}
expected_event_ids = {
    "owner-segment-1-second-35",
    "owner-segment-2-second-4",
    "tester-opening-grid-calming",
    "tester-zebra-raised-speed-breaker",
    "tester-second-speed-breaker",
    "assistant-segment-2-construction-roughness-37s",
}
check("three exact source fingerprints are retained", len(manifest["sources"]) == 3)
check("required private sources are pinned by ID and exact hash",
      {source_id: source["sha256"] for source_id, source in manifest["sources"].items()}
      == expected_sources)
check("required private event identities are immutable",
      {event["id"] for event in events} == expected_event_ids)
check("two positives and four strict negative regressions are retained",
      [event["label"] for event in events].count("pothole") == 2
      and [event["label"] for event in events].count("not_pothole") == 4)
check("owner truth and independent regressions stay truthfully separated",
      all(
          (event["evidence_tier"] == "owner_ground_truth"
           and event["label_provenance"] == "owner_confirmed")
          or (event["evidence_tier"] == "independent_assistant_regression"
              and event["label"] == "not_pothole"
              and event["label_provenance"] ==
                  "independent_assistant_full_video_review")
          for event in events))
construction_regression = next(
    event for event in events
    if event["id"] == "assistant-segment-2-construction-roughness-37s"
)
check("the live 37-second construction false positive is a required native-video regression",
      construction_regression["capture_provenance"] ==
          "native_mediarecorder_reconstruction"
      and construction_regression["label"] == "not_pothole")
check("private reconstructed fixtures never claim raw CameraX accuracy eligibility",
      all(event["raw_camerax_accuracy_eligible"] is False for event in events))
check("every event locks all five source-frame phases",
      len(events) == 6 and all(len(event["phases"]) == 5 for event in events))
check("every phase locks a chronological three-JPEG burst",
      all(len(phase["timestamps_seconds"]) == 3
          and phase["timestamps_seconds"] == sorted(phase["timestamps_seconds"])
          and len(phase["fixture_sha256"]) == 3
          for event in events for phase in event["phases"]))
ground_truth = json.loads((ROOT / "eval" / "private_eval_ground_truth.json").read_text())
ground_truth_ids = {event["event_id"] for event in ground_truth["release_events"]}
excluded_ids = {event["event_id"] for event in ground_truth["excluded_release_events"]}
check("release manifest stays aligned with explicit owner truth and exclusions",
      ground_truth_ids | excluded_ids == expected_event_ids
      and all((event["id"] in ground_truth_ids) ==
              (event["evidence_tier"] == "owner_ground_truth") for event in events))

contract = gate.load_production_contract()
check("gate reads the current native production contract",
      contract["model"] == "gpt-5.6"
      and contract["detail"] == "original"
      and contract["prompt_version"] == "pothole-binary-v19"
      and contract["schema_version"] == 9
      and contract["retry_max_attempts"] == 3
      and contract["schema"] == gate.production_eval.SCHEMA)
prepare_event_source = inspect.getsource(gate.production_eval.prepare_event)
check("production evaluator sends full frames and rejects crop-specific preparation",
      gate.production_eval.MAX_PREPARED_FRAME_DIMENSION == 1280
      and not hasattr(gate.production_eval, "select_road_region")
      and "complete camera frames" in prepare_event_source
      and "No image is cropped, tiled, masked, or limited to a region of interest." in prepare_event_source)
defaults = gate.make_parser().parse_args([])
check("one strict trial and native network deadline are the safe defaults",
      defaults.trials == 1 and defaults.api_timeout == 30)

valid_positive = {
    "is_pothole": True,
    "looks_like_speed_breaker": False,
    "image_quality": "usable",
    "surface_type": "bituminous_asphalt",
    "on_drivable_surface": True,
    "has_localized_cavity": True,
    "has_unambiguous_lower_interior": True,
    "has_broken_edge_or_rim": True,
    "has_depth_or_surface_loss": True,
    "temporal_consistency": "consistent",
    "size": "medium",
    "description": "Localized cavity with a broken edge.",
}
check("strict result validator accepts an exact production-schema result",
      gate.validate_assessment(valid_positive, contract["schema"]) == valid_positive)
for mutation_name, mutate in (
    ("additional field", lambda value: value.update({"confidence": 0.99})),
    ("wrong boolean type", lambda value: value.update({"is_pothole": 1})),
    ("enum drift", lambda value: value.update({"size": "huge"})),
):
    candidate = copy.deepcopy(valid_positive)
    mutate(candidate)
    try:
        gate.validate_assessment(candidate, contract["schema"])
        rejected = False
    except gate.GateError:
        rejected = True
    check(f"strict result validator rejects {mutation_name}", rejected)

malformed = copy.deepcopy(manifest)
del malformed["events"][0]["phases"][0]["fixture_sha256"]
try:
    gate.validate_manifest(malformed)
    malformed_rejected = False
except gate.GateError:
    malformed_rejected = True
check("manifest validation fails closed on missing fixture metadata", malformed_rejected)

environment = dict(os.environ)
environment.pop("OPENAI_API_KEY", None)
manifest_check = subprocess.run(
    [sys.executable, str(ROOT / "eval" / "private_release_gate.py"), "--check-manifest"],
    cwd=ROOT, env=environment, text=True, capture_output=True,
)
check("offline CLI contract check succeeds without media or API key",
      manifest_check.returncode == 0
      and "PRIVATE RELEASE GATE MANIFEST PASS" in manifest_check.stdout)

missing_media = subprocess.run(
    [sys.executable, str(ROOT / "eval" / "private_release_gate.py"), "--validate-only"],
    cwd=ROOT, env=environment, text=True, capture_output=True,
)
check("real gate invocation fails before API access when private sources are absent",
      missing_media.returncode != 0
      and "missing private source mapping" in missing_media.stderr
      and "OPENAI_API_KEY" not in missing_media.stderr)

check("binary release verdict requires every positive accept and every negative reject",
      gate.expected_decision("pothole") == "accept"
      and gate.expected_decision("not_pothole") == "reject")

near_miss = {
    **valid_positive,
    "is_pothole": False,
    "surface_type": "temporary_drivable_surface",
    "has_unambiguous_lower_interior": False,
    "size": None,
}
accepted_temporary = {
    **valid_positive,
    "surface_type": "temporary_drivable_surface",
}


def run_sequence(sequence):
    remaining = list(sequence)
    calls = 0

    def next_assessment():
        nonlocal calls
        calls += 1
        item = remaining.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    decision, attempts = gate.production_decision_with_retries(next_assessment)
    return decision, attempts, calls


check("release gate requires two complete temporary rejects",
      run_sequence([near_miss, near_miss]) == ("reject", 2, 2))
check("release gate accepts two independent temporary positives",
      run_sequence([accepted_temporary, accepted_temporary]) == ("accept", 2, 2))
check("release gate resolves a split temporary vote with one tie-breaker",
      run_sequence([accepted_temporary, near_miss, accepted_temporary]) ==
          ("accept", 3, 3)
      and run_sequence([near_miss, accepted_temporary, near_miss]) ==
          ("reject", 3, 3))
check("speed-breaker and ordinary-surface rejects each use one decision",
      run_sequence([{**near_miss, "looks_like_speed_breaker": True}]) ==
          ("reject", 1, 1)
      and run_sequence([{**near_miss, "surface_type": "bituminous_asphalt"}]) ==
          ("reject", 1, 1))
check("an ineligible or failed confirmation fails a temporary YES closed",
      run_sequence([
          accepted_temporary,
          {**accepted_temporary, "surface_type": "bituminous_asphalt"},
      ]) == ("reject", 2, 2)
      and run_sequence([
          accepted_temporary,
          gate.GateError("simulated confirmation failure"),
      ]) == ("reject", 2, 2))

if FAILURES:
    raise SystemExit(f"\n{len(FAILURES)} private release gate check(s) failed")

print("\nPRIVATE RELEASE GATE TEST PASS")
