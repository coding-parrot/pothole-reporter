#!/usr/bin/env python3
"""Offline contract test for Android-prepared live and durable-burst requests."""

import base64
import copy
import importlib.util
import json
import pathlib
import re
import sys
import tempfile

from PIL import Image


ROOT = pathlib.Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "eval"
sys.path.insert(0, str(EVAL_DIR))
spec = importlib.util.spec_from_file_location(
    "prepared_eval", EVAL_DIR / "run_prepared_eval.py"
)
prepared_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepared_eval)
FAILURES = []


def check(name, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        FAILURES.append(name)


def write_manifest(event_dir, manifest):
    (event_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def source_frame_manifest(event_dir):
    result = []
    source_dir = event_dir / "source"
    source_dir.mkdir(exist_ok=True)
    for index in prepared_eval.ROLLING_SOURCE_FRAME_INDICES:
        source_path = source_dir / f"f{index}.jpg"
        dimensions = (8 + index, 6 + index)
        Image.new("RGB", dimensions, (30 + index, 60, 90)).save(
            source_path, format="JPEG", quality=91
        )
        raw = source_path.read_bytes()
        result.append({
            "index": index,
            "file": f"source/f{index}.jpg",
            "sha256": prepared_eval.run_eval.sha(raw),
            "bytes": len(raw),
            "width": dimensions[0],
            "height": dimensions[1],
        })
    return result


def make_manifest(event_dir, mode, timestamps, live_primary, request_sources, primary, raws):
    primary_source = prepared_eval.ROLLING_SOURCE_FRAME_INDICES[live_primary]
    roles = ["context"] + ["full_frame"] * len(request_sources)
    sources = [f"f{primary_source}.jpg"] + [f"f{index}.jpg" for index in request_sources]
    images = []
    for order, (role, source, raw) in enumerate(zip(roles, sources, raws)):
        source_index = source.removeprefix("f").removesuffix(".jpg")
        suffix = "context-primary" if role == "context" else "full-frame"
        filename = f"{order:02d}-{suffix}-f{source_index}.jpg"
        (event_dir / filename).write_bytes(raw)
        images.append({
            "order": order,
            "role": role,
            "source_frame": source,
            "file": filename,
            "sha256": prepared_eval.run_eval.sha(raw),
            "bytes": len(raw),
            "width": 1,
            "height": 1,
        })
    manifest = {
        "event": event_dir.name,
        "source_mode": mode,
        "timestamp_provenance": "instrumentation arguments",
        "primary_index": primary,
        "live_primary_index": live_primary,
        "primary_source_index": primary_source,
        "rolling_source_frame_indices": list(prepared_eval.ROLLING_SOURCE_FRAME_INDICES),
        "source_frame_indices": request_sources,
        "captured_at_elapsed_ms": timestamps,
        # CameraX timestamps are a separate monotonic domain from elapsedRealtime.
        "source_timestamps_ns": [
            9_000_000_000 + index * 267_000_000 for index in range(3)
        ],
        "capture_request_elapsed_ms": timestamps[-1] + 37,
        "request_captured_at_elapsed_ms": [timestamps[index] for index in request_sources],
        "capture_policy": copy.deepcopy(prepared_eval.CAPTURE_POLICY),
        "source_frames": source_frame_manifest(event_dir),
        "quality_scores": [1.0, 2.0, 1.5],
        "image_count": len(raws),
        "image_order": "test fixture",
        # Explicit order is authoritative; array position is deliberately shuffled.
        "images": list(reversed(images)),
    }
    if mode == "durable-burst":
        manifest["durable_persistence"] = copy.deepcopy(
            prepared_eval.DURABLE_PERSISTENCE
        )
    write_manifest(event_dir, manifest)
    return manifest


def expect_manifest_rejection(name, event_dir, manifest, mode, message_fragment):
    write_manifest(event_dir, manifest)
    try:
        prepared_eval.load_event(event_dir, mode)
    except ValueError as error:
        check(name, message_fragment in str(error))
    else:
        check(name, False)


def kotlin_constant(source, name):
    found = re.search(rf"const val {name}\s*=\s*([0-9_]+)L?", source)
    return int(found.group(1).replace("_", "")) if found else None


rolling_source = (
    ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/"
    "NativeRollingBurstWindow.kt"
).read_text()
stored_image_source = (
    ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/"
    "NativeStoredImagePolicy.kt"
).read_text()
service_source = (
    ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/"
    "DriveForegroundService.kt"
).read_text()
check(
    "prepared-eval capture constants mirror the production Kotlin policy",
    prepared_eval.CAPTURE_POLICY == {
        "capacity": kotlin_constant(rolling_source, "CAPACITY"),
        "output_count": kotlin_constant(rolling_source, "OUTPUT_COUNT"),
        "source_frame_stride": kotlin_constant(rolling_source, "SOURCE_FRAME_STRIDE"),
        "max_sample_gap_ms": kotlin_constant(rolling_source, "MAX_SAMPLE_GAP_MS"),
        "sample_spacing_ms": kotlin_constant(rolling_source, "SAMPLE_SPACING_MS"),
        "min_window_span_ms": kotlin_constant(rolling_source, "MIN_WINDOW_SPAN_MS"),
        "max_oldest_age_ms": kotlin_constant(rolling_source, "MAX_OLDEST_AGE_MS"),
    },
)
max_image_match = re.search(
    r"MAX_KEYFRAME_IMAGE_BYTES\s*=\s*([0-9_]+)L\s*\*\s*([0-9_]+)L",
    stored_image_source,
)
burst_match = re.search(
    r"MAX_KEYFRAME_BURST_BYTES\s*=\s*([0-9_]+)L\s*\*\s*MAX_KEYFRAME_IMAGE_BYTES",
    stored_image_source,
)
max_image_bytes = (
    int(max_image_match.group(1).replace("_", ""))
    * int(max_image_match.group(2).replace("_", ""))
    if max_image_match else None
)
burst_multiplier = (
    int(burst_match.group(1).replace("_", "")) if burst_match else None
)
check(
    "prepared-eval persistence constants mirror the production Kotlin policy",
    prepared_eval.DURABLE_PERSISTENCE == {
        "image_count": prepared_eval.CAPTURE_POLICY["output_count"],
        "max_bytes_per_image": max_image_bytes,
        "max_total_bytes": (
            burst_multiplier * max_image_bytes
            if burst_multiplier is not None and max_image_bytes is not None else None
        ),
        "max_dimension": kotlin_constant(stored_image_source, "KEYFRAME_MAX_DIMENSION"),
        "initial_quality": kotlin_constant(service_source, "KEYFRAME_JPEG_QUALITY"),
    },
)
check(
    "prepared-eval uses the current complete-frame detection contract",
    prepared_eval.run_eval.PROMPT_VERSION == "pothole-binary-v15"
    and "leaving the final full frame" in prepared_eval.run_eval.prompts()["baseline"]
    and "leaving the final crop" not in prepared_eval.run_eval.prompts()["baseline"],
)


with tempfile.TemporaryDirectory(prefix="prepared-eval-contract-") as temporary:
    temporary = pathlib.Path(temporary)
    prepared_root = temporary / "prepared"
    live_dir = prepared_root / "live-event"
    durable_dir = prepared_root / "durable-event"
    live_dir.mkdir(parents=True)
    durable_dir.mkdir(parents=True)

    # Distinct bytes prove requests follow manifest order. The runner intentionally does
    # not decode or re-encode Android's already-prepared JPEGs.
    live_bytes = [
        b"\xff\xd8\xfflive-context-primary\xff\xd9",
        b"\xff\xd8\xfflive-full-f0\xff\xd9",
        b"\xff\xd8\xfflive-full-f1\xff\xd9",
        b"\xff\xd8\xfflive-full-f2\xff\xd9",
    ]
    live_manifest = make_manifest(
        live_dir,
        "live",
        [1_000, 1_267, 1_534],
        live_primary=1,
        request_sources=[0, 1, 2],
        primary=1,
        raws=live_bytes,
    )

    durable_bytes = [
        b"\xff\xd8\xffdurable-context-primary\xff\xd9",
        b"\xff\xd8\xffdurable-reencoded-full-f0\xff\xd9",
        b"\xff\xd8\xffdurable-reencoded-full-f1\xff\xd9",
        b"\xff\xd8\xffdurable-reencoded-full-f2\xff\xd9",
    ]
    durable_manifest = make_manifest(
        durable_dir,
        "durable-burst",
        [2_000, 2_267, 2_534],
        live_primary=2,
        request_sources=[0, 1, 2],
        primary=2,
        raws=durable_bytes,
    )

    _, _, live_views, live_note = prepared_eval.load_event(live_dir, "live")
    decoded_live = [base64.b64decode(view.split(",", 1)[1]) for view in live_views]
    check("live manifest order controls prepared byte order", decoded_live == live_bytes)
    check("live primary is reflected in the Drive layout note",
          "chronological frame 2 is the sharpest" in live_note
          and "No image is cropped, tiled, masked" in live_note)

    _, _, durable_views, durable_note = prepared_eval.load_event(
        durable_dir, "durable-burst"
    )
    decoded_durable = [base64.b64decode(view.split(",", 1)[1]) for view in durable_views]
    check("durable runner sends all three reloaded frames unchanged",
          decoded_durable == durable_bytes)
    check("durable manifest preserves all chronological source frames and primary",
          durable_manifest["source_frame_indices"] == [0, 1, 2]
          and durable_manifest["primary_index"] == 2
          and "Images 2-4" in durable_note
          and "chronological frame 3 is the sharpest" in durable_note
          and "No image is cropped, tiled, masked" in durable_note)

    expect_manifest_rejection(
        "live and durable prepared roots cannot be confused",
        live_dir,
        live_manifest,
        "durable-burst",
        "source_mode does not match",
    )
    write_manifest(live_dir, live_manifest)

    bad_role = copy.deepcopy(live_manifest)
    next(item for item in bad_role["images"] if item["order"] == 1)["role"] = "context"
    expect_manifest_rejection(
        "wrong live role sequence is rejected", live_dir, bad_role, "live",
        "one context and 3 complete-frame images",
    )

    duplicate_order = copy.deepcopy(live_manifest)
    next(item for item in duplicate_order["images"] if item["order"] == 3)["order"] = 2
    expect_manifest_rejection(
        "duplicate or gapped live order is rejected", live_dir, duplicate_order, "live",
        "unique sequence 0,1,2,3",
    )

    wrong_selection = copy.deepcopy(live_manifest)
    wrong_selection["rolling_source_frame_indices"] = [0, 2, 4]
    expect_manifest_rejection(
        "retired five-source selection is rejected", live_dir, wrong_selection, "live",
        "production sequence 0,1,2",
    )

    wrong_capture_policy = copy.deepcopy(live_manifest)
    wrong_capture_policy["capture_policy"]["source_frame_stride"] = 6
    expect_manifest_rejection(
        "capture-policy drift is rejected", live_dir, wrong_capture_policy, "live",
        "capture policy is not production-exact",
    )

    too_close = copy.deepcopy(live_manifest)
    too_close["source_timestamps_ns"] = [9_000_000_000, 9_139_000_000, 9_500_000_000]
    expect_manifest_rejection(
        "sub-140ms CameraX source spacing is rejected", live_dir, too_close, "live",
        "production analyzer cadence",
    )

    independent_clocks = copy.deepcopy(live_manifest)
    independent_clocks["captured_at_elapsed_ms"] = [1_000, 1_001, 1_534]
    independent_clocks["request_captured_at_elapsed_ms"] = [1_000, 1_001, 1_534]
    independent_clocks["capture_request_elapsed_ms"] = 1_571
    write_manifest(live_dir, independent_clocks)
    try:
        prepared_eval.load_event(live_dir, "live")
    except ValueError:
        check("elapsed and CameraX clocks are validated independently", False)
    else:
        check("elapsed and CameraX clocks are validated independently", True)

    missing_source_clock = copy.deepcopy(live_manifest)
    missing_source_clock.pop("source_timestamps_ns")
    expect_manifest_rejection(
        "CameraX source clock is mandatory",
        live_dir, missing_source_clock, "live", "source_timestamps_ns",
    )

    too_old = copy.deepcopy(live_manifest)
    too_old["captured_at_elapsed_ms"] = [1_000, 1_600, 2_300]
    too_old["capture_request_elapsed_ms"] = 2_301
    too_old["request_captured_at_elapsed_ms"] = [1_000, 1_600, 2_300]
    expect_manifest_rejection(
        "over-age rolling windows are rejected", live_dir, too_old, "live",
        "production window age",
    )

    missing_capture_request = copy.deepcopy(live_manifest)
    missing_capture_request.pop("capture_request_elapsed_ms")
    expect_manifest_rejection(
        "capture request monotonic time is mandatory",
        live_dir, missing_capture_request, "live", "capture_request_elapsed_ms",
    )

    stale_at_request = copy.deepcopy(live_manifest)
    stale_at_request["capture_request_elapsed_ms"] = (
        stale_at_request["captured_at_elapsed_ms"][0]
        + prepared_eval.CAPTURE_POLICY["max_oldest_age_ms"] + 1
    )
    expect_manifest_rejection(
        "actual capture-request age rejects a stale narrow burst",
        live_dir, stale_at_request, "live", "production window age",
    )

    bad_source_frame = copy.deepcopy(live_manifest)
    bad_source_frame["source_frames"][1]["sha256"] = "not-a-sha"
    expect_manifest_rejection(
        "source-frame provenance must be complete", live_dir, bad_source_frame, "live",
        "invalid source-frame manifest entry f1",
    )

    source_zero = live_dir / "source" / "f0.jpg"
    source_zero_bytes = source_zero.read_bytes()
    source_zero.unlink()
    expect_manifest_rejection(
        "missing exported source bytes are rejected",
        live_dir, live_manifest, "live", "missing or unreadable",
    )
    source_zero.write_bytes(source_zero_bytes)

    source_one = live_dir / "source" / "f1.jpg"
    source_one_bytes = source_one.read_bytes()
    tampered_source_one = bytearray(source_one_bytes)
    tampered_source_one[len(tampered_source_one) // 2] ^= 1
    source_one.write_bytes(tampered_source_one)
    expect_manifest_rejection(
        "tampered exported source bytes are rejected",
        live_dir, live_manifest, "live", "source-frame SHA-256 mismatch",
    )
    source_one.write_bytes(source_one_bytes)

    wrong_source_dimensions = copy.deepcopy(live_manifest)
    wrong_source_dimensions["source_frames"][2]["width"] += 1
    expect_manifest_rejection(
        "source-frame dimensions are verified from exported bytes",
        live_dir, wrong_source_dimensions, "live", "dimensions mismatch",
    )

    wrong_primary_source = copy.deepcopy(live_manifest)
    wrong_primary_source["primary_source_index"] = 2
    expect_manifest_rejection(
        "primary source must match the selected ordinal",
        live_dir, wrong_primary_source, "live", "does not match primary_index",
    )

    bad_hash = copy.deepcopy(live_manifest)
    next(item for item in bad_hash["images"] if item["order"] == 2)["sha256"] = "0" * 64
    expect_manifest_rejection(
        "prepared-byte hash mismatch is rejected", live_dir, bad_hash, "live",
        "SHA-256 mismatch",
    )
    write_manifest(live_dir, live_manifest)

    wrong_durable_primary = copy.deepcopy(durable_manifest)
    wrong_durable_primary["primary_index"] = 0
    expect_manifest_rejection(
        "durable primary must retain the reloaded burst position",
        durable_dir, wrong_durable_primary, "durable-burst", "correctly remapped",
    )

    dropped_durable_frame = copy.deepcopy(durable_manifest)
    dropped_durable_frame["source_frame_indices"] = [0, 2]
    dropped_durable_frame["request_captured_at_elapsed_ms"] = [2_000, 2_534]
    expect_manifest_rejection(
        "durable replay cannot drop a temporal source frame",
        durable_dir, dropped_durable_frame, "durable-burst", "do not match source mode",
    )

    synthetic_durable = copy.deepcopy(durable_manifest)
    synthetic_durable["timestamp_provenance"] = "production-cadence fallback"
    expect_manifest_rejection(
        "durable evaluation requires real source timestamps",
        durable_dir, synthetic_durable, "durable-burst", "real instrumentation timestamps",
    )

    wrong_persistence = copy.deepcopy(durable_manifest)
    wrong_persistence["durable_persistence"]["image_count"] = 2
    expect_manifest_rejection(
        "retired two-frame durable persistence is rejected",
        durable_dir, wrong_persistence, "durable-burst", "not production-exact",
    )
    write_manifest(durable_dir, durable_manifest)

    # Exercise main(), not a parallel test-only request builder. Service calls and key
    # lookup are replaced locally; urlopen is separately booby-trapped as an offline guard.
    captured_calls = []
    network_calls = []
    key_loads = []
    original_call = prepared_eval.run_eval.call
    original_load_key = prepared_eval.run_eval.load_key
    original_urlopen = prepared_eval.run_eval.urllib.request.urlopen
    original_argv = sys.argv

    def fake_load_key():
        key_loads.append(True)
        return "offline-test-key"

    def fake_call(key, body, cache_dir, cache_slot):
        captured_calls.append((key, body, pathlib.Path(cache_dir), cache_slot))
        return {"is_pothole": False}, False, "offline-request-hash"

    def forbidden_urlopen(*args, **kwargs):
        network_calls.append((args, kwargs))
        raise AssertionError("offline prepared-eval test attempted network access")

    output_dirs = {
        "live": temporary / "live-results",
        "durable-burst": temporary / "durable-results",
    }
    try:
        prepared_eval.run_eval.load_key = fake_load_key
        prepared_eval.run_eval.call = fake_call
        prepared_eval.run_eval.urllib.request.urlopen = forbidden_urlopen
        for mode, event in (("live", "live-event"), ("durable-burst", "durable-event")):
            sys.argv = [
                "run_prepared_eval.py",
                "--prepared-root", str(prepared_root),
                "--events", event,
                "--trials", "1",
                "--concurrency", "1",
                "--source-mode", mode,
                "--out", str(output_dirs[mode]),
            ]
            prepared_eval.main()
    finally:
        sys.argv = original_argv
        prepared_eval.run_eval.urllib.request.urlopen = original_urlopen
        prepared_eval.run_eval.call = original_call
        prepared_eval.run_eval.load_key = original_load_key

    check("CLI evaluation paths stay offline", not network_calls)
    check("CLI creates one exact request per source mode",
          len(key_loads) == 2 and len(captured_calls) == 2)

    for call_index, (mode, event, raw_images, note, expected_primary) in enumerate((
        ("live", "live-event", live_bytes, live_note, 1),
        ("durable-burst", "durable-event", durable_bytes, durable_note, 2),
    )):
        _, request, cache_dir, cache_slot = captured_calls[call_index]
        content = request["input"][0]["content"]
        request_images = [item for item in content if item["type"] == "input_image"]
        request_texts = [item for item in content if item["type"] == "input_text"]
        check(f"{mode} uses shipped gpt-5.6 low reasoning and no storage",
              request.get("model") == "gpt-5.6"
              and request.get("reasoning") == {"effort": "low"}
              and request.get("store") is False
              and request.get("max_output_tokens") == 1536)
        check(f"{mode} sends every exported JPEG unchanged at original detail",
              len(request_images) == len(raw_images)
              and all(item.get("detail") == "original" for item in request_images)
              and [base64.b64decode(item["image_url"].split(",", 1)[1])
                   for item in request_images] == raw_images)
        expected_prompt = prepared_eval.run_eval.effective_prompt(
            prepared_eval.run_eval.prompts()["baseline"], "drive", note
        )
        expected_request_text = (
            expected_prompt
            + f"\n\nThe {len(raw_images)} supplied image(s) are ordered exactly as labelled "
              "by the capture pipeline."
        )
        check(f"{mode} request contains the exact shipped Drive prompt once and last",
              len(request_texts) == 1
              and content[-1] is request_texts[0]
              and request_texts[0].get("text") == expected_request_text)
        request_format = request.get("text", {}).get("format", {})
        check(f"{mode} request uses the strict shipped assessment schema",
              request_format.get("type") == "json_schema"
              and request_format.get("name") == "pothole_binary_assessment"
              and request_format.get("strict") is True
              and request_format.get("schema") == prepared_eval.run_eval.SCHEMA)
        check(f"{mode} CLI cache slot is isolated by source mode",
              cache_dir == output_dirs[mode] / "cache"
              and cache_slot == f"android-prepared|{mode}|{event}|0")
        rows = [json.loads(line) for line in
                (output_dirs[mode] / "raw.jsonl").read_text().splitlines()]
        check(f"{mode} raw result reports the request primary index",
              len(rows) == 1 and rows[0]["primary_index"] == expected_primary)
        check(f"{mode} CLI emits summary artifact",
              json.loads((output_dirs[mode] / "summary.json").read_text())[event]
              == {"accepts": 0, "trials": 1, "errors": 0})


if FAILURES:
    raise SystemExit(f"\n{len(FAILURES)} prepared-eval contract check(s) failed")

print("\nPREPARED EVAL CONTRACT TEST PASS")
