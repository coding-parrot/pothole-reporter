#!/usr/bin/env python3
"""Offline contract for the exact private-media release gate."""

import copy
import inspect
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
check("three exact source fingerprints are retained", len(manifest["sources"]) == 3)
check("two positives and three traffic-calming negatives are retained",
      [event["label"] for event in events].count("pothole") == 2
      and [event["label"] for event in events].count("not_pothole") == 3)
check("MediaRecorder positives and external-recording negatives stay truthfully separated",
      all(event["capture_provenance"] == (
              "native_mediarecorder_reconstruction" if event["label"] == "pothole"
              else "external_recording_of_test_device")
          for event in events))
check("private reconstructed fixtures never claim raw CameraX accuracy eligibility",
      all(event["raw_camerax_accuracy_eligible"] is False for event in events))
check("every event locks all five source-frame phases",
      len(events) == 5 and all(len(event["phases"]) == 5 for event in events))
check("every phase locks a chronological three-JPEG burst",
      all(len(phase["timestamps_seconds"]) == 3
          and phase["timestamps_seconds"] == sorted(phase["timestamps_seconds"])
          and len(phase["fixture_sha256"]) == 3
          for event in events for phase in event["phases"]))

contract = gate.load_production_contract()
check("gate reads the current native production contract",
      contract["model"] == "gpt-5.6"
      and contract["detail"] == "original"
      and contract["prompt_version"] == "pothole-binary-v15"
      and contract["schema_version"] == 7
      and contract["schema"] == gate.production_eval.SCHEMA)
prepare_event_source = inspect.getsource(gate.production_eval.prepare_event)
check("production evaluator sends full frames and rejects crop-specific preparation",
      gate.production_eval.MAX_PREPARED_FRAME_DIMENSION == 1280
      and not hasattr(gate.production_eval, "select_road_region")
      and "complete camera frames" in prepare_event_source
      and "No image is cropped, tiled, masked, or limited to a region of interest." in prepare_event_source)
check("one strict trial is the safe default", gate.make_parser().parse_args([]).trials == 1)

valid_positive = {
    "is_pothole": True,
    "looks_like_speed_breaker": False,
    "image_quality": "usable",
    "surface_type": "bituminous_asphalt",
    "on_drivable_surface": True,
    "has_localized_cavity": True,
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

if FAILURES:
    raise SystemExit(f"\n{len(FAILURES)} private release gate check(s) failed")

print("\nPRIVATE RELEASE GATE TEST PASS")
