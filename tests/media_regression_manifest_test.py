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
        "path": "tester-speed-breaker-native-cadence/later/f0.jpg",
        "frames": [
            "tester-speed-breaker-native-cadence/later/f0.jpg",
            "tester-speed-breaker-native-cadence/later/f1.jpg",
            "tester-speed-breaker-native-cadence/later/f2.jpg",
        ],
        "source_file": "tester-speed-breaker-clip",
        "source_interval_seconds": [6.1, 7.8],
        "source_timestamps_seconds": [6.578333, 6.978333, 7.378333],
        "source_sample_timestamps_seconds": [6.578333, 6.778333, 6.978333,
                                               7.178333, 7.378333],
        "selected_source_indices": [0, 2, 4],
        "capture_cadence_ms": 180,
        "observed_source_spacing_ms": [200, 200, 200, 200],
        "observed_frame_spacing_ms": [400, 400],
        "fixture_sha256": [
            "dd59f703b2ba228e6e3a88082c1a46b6c7add0df8b40c26396bde9b0f38b5a83",
            "73848930b991ab233c77932c63dd9d89829eac36691b925c5f37df20d3fc72f6",
            "637545ba6696b8353849408368f0f5ea1e7c3c604af08cbd9d7a1a4b1d2605f1",
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
        primary_index = event.get("primary_index")
        check(f"{event_id} primary path matches primary_index",
              type(primary_index) is int and primary_index in range(3)
              and event.get("path") == image_paths[primary_index])
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
      later_breaker.get("path") == "tester-speed-breaker-native-cadence/later/f0.jpg"
      and all(path.startswith("tester-speed-breaker-native-cadence/later/")
              for path in later_breaker.get("frames", []))
      and "whatsapp" not in json.dumps(later_breaker).lower())
check("later tester breaker enforces five-source sampling and selected-view spacing",
      later_breaker.get("capture_cadence_ms") == 180
      and later_breaker.get("selected_source_indices") == [0, 2, 4]
      and later_breaker.get("observed_source_spacing_ms") == [200, 200, 200, 200]
      and later_breaker.get("observed_frame_spacing_ms") == [400, 400]
      and later_observed_spacing == [400, 400]
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
