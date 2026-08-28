#!/usr/bin/env python3
"""Offline contract test for Android-prepared live and durable evaluation requests."""

import base64
import copy
import importlib.util
import json
import pathlib
import sys
import tempfile


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


def make_manifest(event_dir, mode, timestamps, live_primary, request_sources, primary, raws):
    primary_source = prepared_eval.ROLLING_SOURCE_FRAME_INDICES[live_primary]
    roles = ["context"] + ["road_band"] * len(request_sources)
    sources = [f"f{primary_source}.jpg"] + [f"f{index}.jpg" for index in request_sources]
    filenames = []
    images = []
    for order, (role, source, raw) in enumerate(zip(roles, sources, raws)):
        source_index = source.removeprefix("f").removesuffix(".jpg")
        suffix = "context-primary" if role == "context" else "road-band"
        filename = f"{order:02d}-{suffix}-f{source_index}.jpg"
        filenames.append(filename)
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
        "timestamp_provenance": "instrumentation argument",
        "primary_index": primary,
        "live_primary_index": live_primary,
        "primary_source_index": primary_source,
        "rolling_source_frame_indices": [0, 2, 4],
        "source_frame_indices": request_sources,
        "source_timestamps_ms": timestamps,
        "request_source_timestamps_ms": [timestamps[index] for index in request_sources],
        "quality_scores": [1.0, 2.0, 1.5],
        "image_count": len(raws),
        "image_order": "test fixture",
        # Explicit order is authoritative; array position is deliberately shuffled.
        "images": list(reversed(images)),
    }
    if mode == "durable-pair":
        manifest["durable_persistence"] = {
            "max_bytes_per_image": 900 * 1024,
            "max_dimension": 1280,
            "initial_quality": 88,
        }
    write_manifest(event_dir, manifest)
    return manifest, filenames


def expect_manifest_rejection(name, event_dir, manifest, mode, message_fragment):
    write_manifest(event_dir, manifest)
    try:
        prepared_eval.load_event(event_dir, mode)
    except ValueError as error:
        check(name, message_fragment in str(error))
    else:
        check(name, False)


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
        b"\xff\xd8\xfflive-road-f0\xff\xd9",
        b"\xff\xd8\xfflive-road-f2\xff\xd9",
        b"\xff\xd8\xfflive-road-f4\xff\xd9",
    ]
    live_manifest, _ = make_manifest(
        live_dir,
        "live",
        [1000, 1180, 1360, 1540, 1720],
        live_primary=1,
        request_sources=[0, 2, 4],
        primary=1,
        raws=live_bytes,
    )

    # The primary f2 is much farther in real time from f0 than f4. This deliberately
    # proves durable selection uses captured timestamps, not source ordinals.
    durable_bytes = [
        b"\xff\xd8\xffdurable-context-primary\xff\xd9",
        b"\xff\xd8\xffdurable-reencoded-road-f0\xff\xd9",
        b"\xff\xd8\xffdurable-reencoded-road-f2\xff\xd9",
    ]
    durable_manifest, _ = make_manifest(
        durable_dir,
        "durable-pair",
        [1000, 1500, 1900, 1950, 2000],
        live_primary=1,
        request_sources=[0, 2],
        primary=1,
        raws=durable_bytes,
    )

    _, _, live_views, live_note = prepared_eval.load_event(live_dir, "live")
    decoded_live = [base64.b64decode(view.split(",", 1)[1]) for view in live_views]
    check("live manifest order controls prepared byte order", decoded_live == live_bytes)
    check("live primary is reflected in the Drive layout note",
          "sharpest crop is chronological frame 2" in live_note)

    _, _, durable_views, durable_note = prepared_eval.load_event(
        durable_dir, "durable-pair"
    )
    decoded_durable = [base64.b64decode(view.split(",", 1)[1]) for view in durable_views]
    check("durable runner sends the exporter's persisted/reloaded pair unchanged",
          decoded_durable == durable_bytes)
    check("nonuniform timestamps choose the earlier farthest companion",
          durable_manifest["source_frame_indices"] == [0, 2])
    check("durable manifest carries the remapped request primary",
          durable_manifest["primary_index"] == 1
          and "Images 2-3" in durable_note
          and "sharpest crop is chronological frame 2" in durable_note)

    expect_manifest_rejection(
        "live and durable prepared roots cannot be confused",
        live_dir,
        live_manifest,
        "durable-pair",
        "source_mode does not match",
    )
    write_manifest(live_dir, live_manifest)

    bad_role = copy.deepcopy(live_manifest)
    next(item for item in bad_role["images"] if item["order"] == 1)["role"] = "context"
    expect_manifest_rejection(
        "wrong live role sequence is rejected", live_dir, bad_role, "live",
        "one context and 3 road-band images",
    )

    duplicate_order = copy.deepcopy(live_manifest)
    next(item for item in duplicate_order["images"] if item["order"] == 3)["order"] = 2
    expect_manifest_rejection(
        "duplicate or gapped live order is rejected", live_dir, duplicate_order, "live",
        "unique sequence 0,1,2,3",
    )

    wrong_selection = copy.deepcopy(live_manifest)
    wrong_selection["rolling_source_frame_indices"] = [0, 1, 2]
    expect_manifest_rejection(
        "non-production rolling selection is rejected", live_dir, wrong_selection, "live",
        "production sequence 0,2,4",
    )

    wrong_primary_source = copy.deepcopy(live_manifest)
    wrong_primary_source["primary_source_index"] = 4
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
        "durable primary must already be remapped by the exporter",
        durable_dir, wrong_durable_primary, "durable-pair", "correctly remapped",
    )

    ordinal_pair = copy.deepcopy(durable_manifest)
    ordinal_pair["source_frame_indices"] = [2, 4]
    ordinal_pair["request_source_timestamps_ms"] = [1900, 2000]
    expect_manifest_rejection(
        "ordinal-based durable companion selection is rejected",
        durable_dir, ordinal_pair, "durable-pair", "do not match source mode",
    )

    synthetic_durable = copy.deepcopy(durable_manifest)
    synthetic_durable["timestamp_provenance"] = "production-cadence fallback"
    expect_manifest_rejection(
        "durable evaluation requires real source timestamps",
        durable_dir, synthetic_durable, "durable-pair", "real instrumentation timestamps",
    )

    wrong_persistence = copy.deepcopy(durable_manifest)
    wrong_persistence["durable_persistence"]["max_dimension"] = 1920
    expect_manifest_rejection(
        "non-production durable persistence settings are rejected",
        durable_dir, wrong_persistence, "durable-pair", "not production-exact",
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
        "durable-pair": temporary / "durable-results",
    }
    try:
        prepared_eval.run_eval.load_key = fake_load_key
        prepared_eval.run_eval.call = fake_call
        prepared_eval.run_eval.urllib.request.urlopen = forbidden_urlopen
        for mode, event in (("live", "live-event"), ("durable-pair", "durable-event")):
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
        ("durable-pair", "durable-event", durable_bytes, durable_note, 1),
    )):
        _, request, cache_dir, cache_slot = captured_calls[call_index]
        content = request["input"][0]["content"]
        request_images = [item for item in content if item["type"] == "input_image"]
        request_texts = [item for item in content if item["type"] == "input_text"]
        check(f"{mode} uses shipped gpt-5.6 low reasoning and no storage",
              request.get("model") == "gpt-5.6"
              and request.get("reasoning") == {"effort": "low"}
              and request.get("store") is False)
        check(f"{mode} sends every exported JPEG unchanged at high detail",
              len(request_images) == len(raw_images)
              and all(item.get("detail") == "high" for item in request_images)
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
