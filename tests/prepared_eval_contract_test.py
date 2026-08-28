#!/usr/bin/env python3
"""Offline contract test for Android-prepared evaluation requests and manifests."""

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


def expect_manifest_rejection(name, event_dir, manifest, message_fragment):
    write_manifest(event_dir, manifest)
    try:
        prepared_eval.load_event(event_dir)
    except ValueError as error:
        check(name, message_fragment in str(error))
    else:
        check(name, False)


with tempfile.TemporaryDirectory(prefix="prepared-eval-contract-") as temporary:
    temporary = pathlib.Path(temporary)
    prepared_root = temporary / "prepared"
    event_dir = prepared_root / "event-a"
    event_dir.mkdir(parents=True)
    output_dir = temporary / "results"

    # Distinct bytes make it possible to prove the request follows manifest order. They
    # have JPEG SOI/EOI markers, but the runner intentionally does not decode or re-encode.
    image_bytes = [
        b"\xff\xd8\xffcontext-primary\xff\xd9",
        b"\xff\xd8\xffroad-frame-0\xff\xd9",
        b"\xff\xd8\xffroad-frame-1\xff\xd9",
        b"\xff\xd8\xffroad-frame-2\xff\xd9",
    ]
    filenames = [
        "00-context-primary-f1.jpg",
        "01-road-band-f0.jpg",
        "02-road-band-f1.jpg",
        "03-road-band-f2.jpg",
    ]
    roles = ["context", "road_band", "road_band", "road_band"]
    sources = ["f1.jpg", "f0.jpg", "f1.jpg", "f2.jpg"]
    images = []
    for order, (filename, role, source, raw) in enumerate(
            zip(filenames, roles, sources, image_bytes)):
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
    valid_manifest = {
        "event": "event-a",
        "primary_index": 1,
        "image_order": "primary context, then f0..f2 road bands",
        # JSON array position is deliberately shuffled: the explicit unique order field
        # is authoritative and must restore production request order.
        "images": list(reversed(images)),
    }

    write_manifest(event_dir, valid_manifest)
    _, _, valid_views, valid_note = prepared_eval.load_event(event_dir)
    decoded_views = [base64.b64decode(view.split(",", 1)[1]) for view in valid_views]
    check("manifest order controls prepared byte order", decoded_views == image_bytes)
    check("primary frame is reflected in the Drive layout note",
          "sharpest crop is chronological frame 2" in valid_note)

    bad_role = copy.deepcopy(valid_manifest)
    next(item for item in bad_role["images"] if item["order"] == 1)["role"] = "context"
    expect_manifest_rejection(
        "wrong role sequence is rejected", event_dir, bad_role,
        "one context and three road-band images",
    )

    duplicate_order = copy.deepcopy(valid_manifest)
    next(item for item in duplicate_order["images"] if item["order"] == 3)["order"] = 2
    expect_manifest_rejection(
        "duplicate or gapped order is rejected", event_dir, duplicate_order,
        "unique sequence 0,1,2,3",
    )

    wrong_chronology = copy.deepcopy(valid_manifest)
    frame_zero = next(item for item in wrong_chronology["images"] if item["order"] == 1)
    frame_one = next(item for item in wrong_chronology["images"] if item["order"] == 2)
    frame_zero["order"], frame_one["order"] = frame_one["order"], frame_zero["order"]
    expect_manifest_rejection(
        "road-frame chronology cannot be reordered", event_dir, wrong_chronology,
        "source frames do not match",
    )

    bad_hash = copy.deepcopy(valid_manifest)
    next(item for item in bad_hash["images"] if item["order"] == 2)["sha256"] = "0" * 64
    expect_manifest_rejection(
        "prepared-byte hash mismatch is rejected", event_dir, bad_hash,
        "SHA-256 mismatch",
    )

    # Exercise main(), not a parallel test-only request builder. The service call and key
    # lookup are replaced locally; urlopen is separately booby-trapped as an offline guard.
    write_manifest(event_dir, valid_manifest)
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

    try:
        prepared_eval.run_eval.load_key = fake_load_key
        prepared_eval.run_eval.call = fake_call
        prepared_eval.run_eval.urllib.request.urlopen = forbidden_urlopen
        sys.argv = [
            "run_prepared_eval.py",
            "--prepared-root", str(prepared_root),
            "--events", "event-a",
            "--trials", "1",
            "--concurrency", "1",
            "--out", str(output_dir),
        ]
        prepared_eval.main()
    finally:
        sys.argv = original_argv
        prepared_eval.run_eval.urllib.request.urlopen = original_urlopen
        prepared_eval.run_eval.call = original_call
        prepared_eval.run_eval.load_key = original_load_key

    check("CLI evaluation path stays offline", not network_calls)
    check("CLI creates exactly one prepared request", len(key_loads) == 1
          and len(captured_calls) == 1)
    _, request, cache_dir, cache_slot = captured_calls[0]
    content = request["input"][0]["content"]
    request_images = [item for item in content if item["type"] == "input_image"]
    request_texts = [item for item in content if item["type"] == "input_text"]
    check("request uses gpt-5.6 with no reasoning and no storage",
          request.get("model") == "gpt-5.6"
          and request.get("reasoning") == {"effort": "none"}
          and request.get("store") is False)
    check("all four Android JPEGs use high detail in manifest order",
          len(request_images) == 4
          and all(item.get("detail") == "high" for item in request_images)
          and [base64.b64decode(item["image_url"].split(",", 1)[1])
               for item in request_images] == image_bytes)
    expected_prompt = prepared_eval.run_eval.effective_prompt(
        prepared_eval.run_eval.prompts()["baseline"], "drive", valid_note
    )
    expected_request_text = (
        expected_prompt
        + "\n\nThe 4 supplied image(s) are ordered exactly as labelled by the capture pipeline."
    )
    check("request contains the exact shipped Drive prompt once and last",
          len(request_texts) == 1
          and content[-1] is request_texts[0]
          and request_texts[0].get("text") == expected_request_text)
    request_format = request.get("text", {}).get("format", {})
    check("request uses the strict shipped assessment schema",
          request_format.get("type") == "json_schema"
          and request_format.get("name") == "pothole_binary_assessment"
          and request_format.get("strict") is True
          and request_format.get("schema") == prepared_eval.run_eval.SCHEMA)
    check("CLI uses the isolated prepared-eval cache slot",
          cache_dir == output_dir / "cache"
          and cache_slot == "android-prepared|event-a|0")
    check("CLI emits raw and summary artifacts",
          (output_dir / "raw.jsonl").is_file()
          and json.loads((output_dir / "summary.json").read_text())["event-a"]
          == {"accepts": 0, "trials": 1, "errors": 0})


if FAILURES:
    raise SystemExit(f"\n{len(FAILURES)} prepared-eval contract check(s) failed")

print("\nPREPARED EVAL CONTRACT TEST PASS")
