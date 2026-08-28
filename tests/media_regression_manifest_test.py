#!/usr/bin/env python3
"""Guard the private-media regression manifest without requiring private media in git."""

import json
import hashlib
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LABELS = json.loads((ROOT / "eval" / "labels.json").read_text())["images"]
EVENTS = {entry["event_id"]: entry for entry in LABELS if entry.get("event_id")}
FAILURES = []


def check(name, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        FAILURES.append(name)


EXPECTED = {
    "owner-construction-drive-2026-08-28-a": {
        "label": "pothole",
        "mode": "drive",
        "source_file": "construction-drive-segment-1",
        "source_interval_seconds": [33.6, 35.8],
        "source_timestamps_seconds": [35.4, 35.6, 35.8],
    },
    "owner-construction-drive-2026-08-28-mid": {
        "label": "pothole",
        "mode": "drive",
        "source_file": "construction-drive-segment-1",
        "source_interval_seconds": [44.3, 45.8],
        "source_timestamps_seconds": [45.3, 45.5, 45.7],
    },
    "owner-construction-drive-2026-08-28-b": {
        "label": "not_pothole",
        "mode": "drive",
        "source_file": "construction-drive-segment-1",
        "source_interval_seconds": [53.3, 55.8],
        "source_timestamps_seconds": [55.2, 55.4, 55.6],
    },
    "owner-construction-drive-2026-08-28-borderline-negative": {
        "label": "not_pothole",
        "mode": "drive",
        "source_file": "construction-drive-segment-2",
        "source_interval_seconds": [0.0, 48.99],
        "source_timestamps_seconds": [44.0, 44.2, 44.4],
    },
    "tester-opening-grid-calming-marking-2026-08-25": {
        "label": "not_pothole",
        "mode": "drive",
        "source_file": "tester-speed-breaker-clip",
        "source_interval_seconds": [0.0, 1.2],
        "source_timestamps_seconds": [0.3, 0.5, 0.7],
    },
    "tester-zebra-raised-speed-breaker-2026-08-25": {
        "label": "not_pothole",
        "mode": "drive",
        "source_file": "tester-speed-breaker-clip",
        "source_interval_seconds": [2.4, 4.8],
        "source_timestamps_seconds": [3.4, 3.6, 3.8],
    },
    "tester-second-speed-breaker-2026-08-25": {
        "label": "not_pothole",
        "mode": "drive",
        "path": "tester-speed-breaker-native-cadence/later/f1.jpg",
        "frames": [
            "tester-speed-breaker-native-cadence/later/f0.jpg",
            "tester-speed-breaker-native-cadence/later/f1.jpg",
            "tester-speed-breaker-native-cadence/later/f2.jpg",
        ],
        "source_file": "tester-speed-breaker-clip",
        "source_interval_seconds": [6.1, 7.8],
        "source_timestamps_seconds": [6.545, 6.745, 6.945],
        "capture_cadence_ms": 180,
        "observed_frame_spacing_ms": [200, 200],
        "fixture_sha256": [
            "941556eb7456ef900c5b87addf9d620febf79fa19e6fa0c3db7708409b98135d",
            "f0096847b7ca11b555808b2192123611ecda05b053cf715e2a0a1e4ec8edbdee",
            "11b003a26846a465816564dcd90e5828e33fade16fc556b80636e0d2aee87c2f",
        ],
    },
    "owner-kanjur-drivable-edge-pothole-2026-08-25": {
        "label": "pothole",
        "mode": "manual",
        "source_file": "owner-manual-edge-photo",
    },
}


event_ids = [entry["event_id"] for entry in LABELS if entry.get("event_id")]
duplicates = sorted(event_id for event_id, count in Counter(event_ids).items() if count > 1)
check("event IDs are unique", not duplicates)

for event_id, expected in EXPECTED.items():
    event = EVENTS.get(event_id, {})
    check(f"{event_id} is retained", bool(event))
    for field, value in expected.items():
        check(f"{event_id} has exact {field}", event.get(field) == value)

    image_paths = event.get("frames") or ([event["path"]] if event.get("path") else [])
    expected_count = 3 if expected["mode"] == "drive" else 1
    check(f"{event_id} has {expected_count} manifest image path(s)",
          len(image_paths) == expected_count)
    check(f"{event_id} uses only private relative fixture paths",
          bool(image_paths)
          and all(not Path(path).is_absolute() and ".." not in Path(path).parts
                  for path in image_paths)
          and str(event.get("licence", "")).startswith("private evaluation only"))
    if expected["mode"] == "drive" and len(image_paths) == 3:
        check(f"{event_id} primary path matches primary_index",
              event.get("primary_index") == 1 and event.get("path") == image_paths[1])
    fixture_hashes = event.get("fixture_sha256", [])
    check(f"{event_id} records one SHA-256 per private fixture",
          len(fixture_hashes) == len(image_paths)
          and all(len(value) == 64 for value in fixture_hashes))
    for relative_path, expected_hash in zip(image_paths, fixture_hashes):
        local_path = ROOT / "eval" / "images" / relative_path
        if local_path.is_file():
            actual_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()
            check(f"{event_id} local fixture hash matches {Path(relative_path).name}",
                  actual_hash == expected_hash)

event_b_notes = EVENTS.get("owner-construction-drive-2026-08-28-b", {}).get("notes", "").lower()
check("ambiguous event B is documented as a strict negative",
      "ambiguous" in event_b_notes and "strict" in event_b_notes
      and "no" in event_b_notes)

segment_two_notes = EVENTS.get(
    "owner-construction-drive-2026-08-28-borderline-negative", {}).get("notes", "").lower()
check("segment_0002 is documented as all-negative", "all-negative" in segment_two_notes)

traffic_ids = (
    "tester-opening-grid-calming-marking-2026-08-25",
    "tester-zebra-raised-speed-breaker-2026-08-25",
    "tester-second-speed-breaker-2026-08-25",
)
check("all three traffic-calming intervals are retained as negatives",
      all(EVENTS.get(event_id, {}).get("label") == "not_pothole"
          for event_id in traffic_ids))
check("only the two tester-identified speed breakers claim owner verification",
      EVENTS[traffic_ids[0]].get("labelled_by") == "independent assistant frame review"
      and all(EVENTS[event_id].get("labelled_by") == "owner"
              for event_id in traffic_ids[1:]))

later_breaker = EVENTS["tester-second-speed-breaker-2026-08-25"]
later_timestamps = later_breaker.get("source_timestamps_seconds", [])
later_observed_spacing = [
    round((right - left) * 1000)
    for left, right in zip(later_timestamps, later_timestamps[1:])
]
check("later tester breaker uses the neutral native-cadence fixture path",
      later_breaker.get("path") == "tester-speed-breaker-native-cadence/later/f1.jpg"
      and all(path.startswith("tester-speed-breaker-native-cadence/later/")
              for path in later_breaker.get("frames", []))
      and "whatsapp" not in json.dumps(later_breaker).lower())
check("later tester breaker enforces the 180 ms request cadence and source quantisation",
      later_breaker.get("capture_cadence_ms") == 180
      and later_breaker.get("observed_frame_spacing_ms") == [200, 200]
      and later_observed_spacing == [200, 200]
      and all(spacing >= later_breaker["capture_cadence_ms"]
              for spacing in later_observed_spacing))

kanjur = EVENTS.get("owner-kanjur-drivable-edge-pothole-2026-08-25", {})
kanjur_notes = kanjur.get("notes", "").lower()
check("Kanjur is an owner-verified manual positive",
      kanjur.get("label") == "pothole" and kanjur.get("mode") == "manual"
      and kanjur.get("labelled_by") == "owner")
check("Kanjur preserves the drivable-edge ground truth",
      "road-edge cavity" in kanjur_notes and "drivable surface" in kanjur_notes)

if FAILURES:
    raise SystemExit(f"\n{len(FAILURES)} media manifest check(s) failed")

print("\nMEDIA REGRESSION MANIFEST TEST PASS")
