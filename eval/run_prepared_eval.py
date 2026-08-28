#!/usr/bin/env python3
"""Evaluate JPEGs exported by NativeEvalExporterInstrumentedTest without re-encoding.

The exporter has already applied Android's production crop, resize, enhancement and JPEG
pipeline. Reprocessing those files would invalidate the parity check, so this runner sends
their bytes in manifest order and otherwise reuses the shipped prompt, schema and decision
gate from run_eval.py.
"""
import argparse
import base64
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import run_eval


def data_url(raw):
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode()


SOURCE_MODES = {"live", "durable-pair"}
ROLLING_SOURCE_FRAME_INDICES = [0, 2, 4]


def temporal_companion_index(timestamps, primary):
    """Mirror NativeKeyframeFiles' real-timestamp choice, including its later tie-break."""
    selected = None
    selected_distance = 0
    for index, timestamp in enumerate(timestamps):
        if index == primary or timestamp == timestamps[primary]:
            continue
        distance = abs(timestamp - timestamps[primary])
        if (selected is None or distance > selected_distance
                or (distance == selected_distance and index > selected)):
            selected = index
            selected_distance = distance
    return selected


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
    expected_image_count = 4 if source_mode == "live" else 3
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
    expected_roles = ["context"] + ["road_band"] * (expected_image_count - 1)
    if [item.get("role") for item in images] != expected_roles:
        road_count = expected_image_count - 1
        raise ValueError(
            f"{path.name}: expected one context and {road_count} road-band images"
        )
    live_primary = manifest.get("live_primary_index")
    if type(live_primary) is not int or live_primary not in {0, 1, 2}:
        raise ValueError(f"{path.name}: invalid live_primary_index")
    rolling_sources = manifest.get("rolling_source_frame_indices")
    if rolling_sources != ROLLING_SOURCE_FRAME_INDICES:
        raise ValueError(
            f"{path.name}: rolling_source_frame_indices must be the production sequence 0,2,4"
        )
    timestamps = manifest.get("source_timestamps_ms")
    if not isinstance(timestamps, list) or len(timestamps) != 5 or any(
            type(value) is not int or value <= 0 for value in timestamps):
        raise ValueError(f"{path.name}: expected five positive source_timestamps_ms")
    if any(timestamps[index] <= timestamps[index - 1] for index in range(1, len(timestamps))):
        raise ValueError(f"{path.name}: source_timestamps_ms must be strictly chronological")

    selected_timestamps = [timestamps[index] for index in rolling_sources]
    if source_mode == "live":
        expected_sources = rolling_sources
        expected_primary = live_primary
    else:
        if manifest.get("timestamp_provenance") != "instrumentation argument":
            raise ValueError(
                f"{path.name}: durable manifest must use real instrumentation timestamps"
            )
        companion = temporal_companion_index(selected_timestamps, live_primary)
        if companion is None:
            raise ValueError(f"{path.name}: durable timestamps have no temporal companion")
        chronological = sorted((live_primary, companion))
        expected_sources = [rolling_sources[index] for index in chronological]
        expected_primary = chronological.index(live_primary)
        persistence = manifest.get("durable_persistence")
        if persistence != {
                "max_bytes_per_image": 900 * 1024,
                "max_dimension": 1280,
                "initial_quality": 88,
        }:
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
    request_timestamps = manifest.get("request_source_timestamps_ms")
    if request_timestamps != [timestamps[index] for index in source_indices]:
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
    note = ("\n- Capture layout: image 1 is full-frame context from the sharpest selected frame. "
            f"Images 2-{final_image} are orientation-aware road-region crops in chronological order; "
            f"the sharpest crop is chronological frame {primary + 1}.")
    return manifest, manifest_bytes, views, note


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", required=True)
    parser.add_argument("--events", required=True, help="comma-separated exporter event names")
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--reasoning", choices=("none", "low", "medium"), default="low")
    parser.add_argument("--source-mode", choices=("live", "durable-pair"), default="live")
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
