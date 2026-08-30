#!/usr/bin/env python3
"""Evaluate JPEGs exported by NativeEvalExporterInstrumentedTest without re-encoding.

The exporter has already applied Android's production full-frame resize, enhancement and JPEG
pipeline. Reprocessing those files would invalidate the parity check, so this runner sends
their bytes in manifest order and otherwise reuses the shipped prompt, schema and decision
gate from run_eval.py.
"""
import argparse
import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, UnidentifiedImageError

import run_eval


def data_url(raw):
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode()


SOURCE_MODES = {"live", "durable-burst"}
ROLLING_SOURCE_FRAME_INDICES = [0, 1, 2]
CAPTURE_POLICY = {
    "capacity": 3,
    "output_count": 3,
    "source_frame_stride": 5,
    "max_sample_gap_ms": 166,
    "sample_spacing_ms": 140,
    "min_window_span_ms": 280,
    "max_oldest_age_ms": 900,
}
DURABLE_PERSISTENCE = {
    "image_count": 3,
    "max_bytes_per_image": 900 * 1024,
    "max_total_bytes": 3 * 900 * 1024,
    "max_dimension": 1280,
    "initial_quality": 88,
}


def load_event(path, source_mode="live"):
    if source_mode not in SOURCE_MODES:
        raise ValueError(f"{path.name}: unsupported source mode")
    manifest_path = path / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict):
        raise ValueError(f"{path.name}: manifest root must be an object")
    if manifest.get("source_mode") != source_mode:
        raise ValueError(f"{path.name}: manifest source_mode does not match requested mode")
    expected_image_count = 4
    raw_images = manifest.get("images")
    if not isinstance(raw_images, list) or len(raw_images) != expected_image_count:
        raise ValueError(
            f"{path.name}: expected exactly {expected_image_count} manifest images"
        )
    if manifest.get("image_count") != expected_image_count:
        raise ValueError(f"{path.name}: image_count does not match source mode")
    if any(not isinstance(item, dict) for item in raw_images):
        raise ValueError(f"{path.name}: every manifest image must be an object")
    orders = [item.get("order") for item in raw_images]
    expected_orders = list(range(expected_image_count))
    if (any(type(order) is not int for order in orders)
            or sorted(orders) != expected_orders):
        sequence = ",".join(str(item) for item in expected_orders)
        raise ValueError(f"{path.name}: image order must be the unique sequence {sequence}")
    images = sorted(raw_images, key=lambda item: item["order"])
    expected_roles = ["context"] + ["full_frame"] * (expected_image_count - 1)
    if [item.get("role") for item in images] != expected_roles:
        frame_count = expected_image_count - 1
        raise ValueError(
            f"{path.name}: expected one context and {frame_count} complete-frame images"
        )
    live_primary = manifest.get("live_primary_index")
    if type(live_primary) is not int or live_primary not in {0, 1, 2}:
        raise ValueError(f"{path.name}: invalid live_primary_index")
    rolling_sources = manifest.get("rolling_source_frame_indices")
    if rolling_sources != ROLLING_SOURCE_FRAME_INDICES:
        raise ValueError(
            f"{path.name}: rolling_source_frame_indices must be the production sequence 0,1,2"
        )
    captured_at_elapsed_ms = manifest.get("captured_at_elapsed_ms")
    if not isinstance(captured_at_elapsed_ms, list) or len(captured_at_elapsed_ms) != 3 or any(
            type(value) is not int or value <= 0 for value in captured_at_elapsed_ms):
        raise ValueError(f"{path.name}: expected three positive captured_at_elapsed_ms")
    if any(captured_at_elapsed_ms[index] <= captured_at_elapsed_ms[index - 1]
           for index in range(1, len(captured_at_elapsed_ms))):
        raise ValueError(f"{path.name}: captured_at_elapsed_ms must be strictly chronological")
    source_timestamps_ns = manifest.get("source_timestamps_ns")
    if not isinstance(source_timestamps_ns, list) or len(source_timestamps_ns) != 3 or any(
            type(value) is not int or value <= 0 for value in source_timestamps_ns):
        raise ValueError(f"{path.name}: expected three positive source_timestamps_ns")
    if any(source_timestamps_ns[index] <= source_timestamps_ns[index - 1]
           for index in range(1, len(source_timestamps_ns))):
        raise ValueError(f"{path.name}: source_timestamps_ns must be strictly chronological")
    source_gaps_ns = [right - left for left, right in
                      zip(source_timestamps_ns, source_timestamps_ns[1:])]
    if any(gap < CAPTURE_POLICY["sample_spacing_ms"] * 1_000_000
           for gap in source_gaps_ns):
        raise ValueError(f"{path.name}: source timestamps violate production analyzer cadence")
    window_span = captured_at_elapsed_ms[-1] - captured_at_elapsed_ms[0]
    if window_span < CAPTURE_POLICY["min_window_span_ms"]:
        raise ValueError(f"{path.name}: source timestamps violate production window span")
    capture_request_elapsed_ms = manifest.get("capture_request_elapsed_ms")
    if type(capture_request_elapsed_ms) is not int or capture_request_elapsed_ms <= 0:
        raise ValueError(f"{path.name}: capture_request_elapsed_ms must be a positive integer")
    if capture_request_elapsed_ms < captured_at_elapsed_ms[-1]:
        raise ValueError(f"{path.name}: capture request precedes the newest source frame")
    if (capture_request_elapsed_ms - captured_at_elapsed_ms[0]
            > CAPTURE_POLICY["max_oldest_age_ms"]):
        raise ValueError(f"{path.name}: source timestamps exceed production window age")

    if manifest.get("capture_policy") != CAPTURE_POLICY:
        raise ValueError(f"{path.name}: capture policy is not production-exact")
    source_frames = manifest.get("source_frames")
    if not isinstance(source_frames, list) or len(source_frames) != 3:
        raise ValueError(f"{path.name}: expected exactly three source-frame manifest entries")
    for index, item in enumerate(source_frames):
        if not isinstance(item, dict):
            raise ValueError(f"{path.name}: every source-frame manifest entry must be an object")
        sha256 = item.get("sha256")
        expected_source_file = f"source/f{index}.jpg"
        if (item.get("index") != index or item.get("file") != expected_source_file
                or type(item.get("bytes")) is not int or item["bytes"] <= 0
                or type(item.get("width")) is not int or item["width"] <= 0
                or type(item.get("height")) is not int or item["height"] <= 0
                or not isinstance(sha256, str) or len(sha256) != 64
                or any(character not in "0123456789abcdef" for character in sha256)):
            raise ValueError(f"{path.name}: invalid source-frame manifest entry f{index}")
        source_path = path / expected_source_file
        try:
            source_raw = source_path.read_bytes()
        except OSError as error:
            raise ValueError(
                f"{path.name}: source frame f{index} is missing or unreadable"
            ) from error
        if len(source_raw) != item["bytes"]:
            raise ValueError(f"{path.name}: source-frame byte count mismatch for f{index}")
        if run_eval.sha(source_raw) != sha256:
            raise ValueError(f"{path.name}: source-frame SHA-256 mismatch for f{index}")
        try:
            with Image.open(io.BytesIO(source_raw)) as source_image:
                source_width, source_height = source_image.size
                source_format = source_image.format
                source_image.verify()
        except (OSError, UnidentifiedImageError) as error:
            raise ValueError(f"{path.name}: source frame f{index} is not a valid image") from error
        if source_format != "JPEG":
            raise ValueError(f"{path.name}: source frame f{index} is not JPEG")
        if source_width != item["width"] or source_height != item["height"]:
            raise ValueError(f"{path.name}: source-frame dimensions mismatch for f{index}")

    expected_sources = rolling_sources
    expected_primary = live_primary
    if source_mode == "durable-burst":
        if manifest.get("timestamp_provenance") != "instrumentation arguments":
            raise ValueError(
                f"{path.name}: durable manifest must use real instrumentation timestamps"
            )
        persistence = manifest.get("durable_persistence")
        if persistence != DURABLE_PERSISTENCE:
            raise ValueError(f"{path.name}: durable persistence settings are not production-exact")

    source_indices = manifest.get("source_frame_indices")
    if source_indices != expected_sources:
        raise ValueError(f"{path.name}: source_frame_indices do not match source mode")
    primary = manifest.get("primary_index")
    if type(primary) is not int or primary != expected_primary:
        raise ValueError(f"{path.name}: primary_index is not correctly remapped")
    primary_source = manifest.get("primary_source_index")
    if (type(primary_source) is not int
            or primary_source != rolling_sources[live_primary]
            or primary_source != source_indices[primary]):
        raise ValueError(f"{path.name}: primary_source_index does not match primary_index")
    request_timestamps = manifest.get("request_captured_at_elapsed_ms")
    if request_timestamps != [captured_at_elapsed_ms[index] for index in source_indices]:
        raise ValueError(f"{path.name}: request timestamps do not match request source frames")
    expected_image_sources = (
        [f"f{primary_source}.jpg"]
        + [f"f{index}.jpg" for index in source_indices]
    )
    if [item.get("source_frame") for item in images] != expected_image_sources:
        raise ValueError(f"{path.name}: source frames do not match the prepared request")
    views = []
    for image in images:
        filename = image.get("file")
        if not isinstance(filename, str) or not filename or Path(filename).name != filename:
            raise ValueError(f"{path.name}: invalid prepared image filename")
        image_path = path / filename
        raw = image_path.read_bytes()
        if run_eval.sha(raw) != image.get("sha256"):
            raise ValueError(f"{path.name}: SHA-256 mismatch for {filename}")
        views.append(data_url(raw))
    final_image = len(images)
    note = ("\n- Capture layout: image 1 is downscaled full-frame context from the "
            f"sharpest selected frame. Images 2-{final_image} are complete camera frames "
            f"in chronological order; chronological frame {primary + 1} is the sharpest. "
            "No image is cropped, tiled, masked, or limited to a region of interest.")
    return manifest, manifest_bytes, views, note


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", required=True)
    parser.add_argument("--events", required=True, help="comma-separated exporter event names")
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--reasoning", choices=("none", "low", "medium"), default="low")
    parser.add_argument("--source-mode", choices=("live", "durable-burst"), default="live")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.trials < 1:
        raise SystemExit("--trials must be positive")

    root = Path(args.prepared_root)
    selected = [item.strip() for item in args.events.split(",") if item.strip()]
    if not selected:
        raise SystemExit("--events must select at least one exporter event")

    prompt = run_eval.prompts()["baseline"]
    prepared = {}
    for event in selected:
        event_dir = root / event
        if not event_dir.is_dir():
            raise SystemExit(f"prepared event not found: {event_dir}")
        prepared[event] = load_event(event_dir, args.source_mode)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "cache"
    cache.mkdir(exist_ok=True)
    jobs = []
    for event in selected:
        manifest, _, views, note = prepared[event]
        body = run_eval.build_request(
            views,
            run_eval.effective_prompt(prompt, "drive", note),
            "gpt-5.6",
            "high",
        )
        body["reasoning"] = {"effort": args.reasoning}
        for trial in range(args.trials):
            jobs.append((event, trial, manifest, body))

    key = run_eval.load_key()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        responses = pool.map(
            lambda job: run_eval.call(
                key,
                job[3],
                cache,
                f"android-prepared|{args.source_mode}|{job[0]}|{job[1]}",
            ),
            jobs,
        )
        rows = []
        for job, response in zip(jobs, responses):
            event, trial, manifest, _ = job
            result, cached, request_hash = response
            rows.append({
                "event": event,
                "trial": trial,
                "primary_index": manifest["primary_index"],
                "decision": run_eval.decision(result, "drive", len(prepared[event][2]) - 1),
                "cached": cached,
                "request_hash": request_hash,
                **result,
            })

    (out / "raw.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    summary = {}
    for event in selected:
        event_rows = [row for row in rows if row["event"] == event]
        errors = [row for row in event_rows if "error" in row]
        accepts = sum(row["decision"] == "accept" for row in event_rows)
        summary[event] = {"accepts": accepts, "trials": len(event_rows), "errors": len(errors)}
        print(f"{event}: {accepts}/{len(event_rows)} accept; {len(errors)} errors")
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")


if __name__ == "__main__":
    main()
