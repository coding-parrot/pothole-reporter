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


def load_event(path):
    manifest_path = path / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict):
        raise ValueError(f"{path.name}: manifest root must be an object")
    raw_images = manifest.get("images")
    if not isinstance(raw_images, list) or len(raw_images) != 4:
        raise ValueError(f"{path.name}: expected exactly four manifest images")
    if any(not isinstance(item, dict) for item in raw_images):
        raise ValueError(f"{path.name}: every manifest image must be an object")
    orders = [item.get("order") for item in raw_images]
    if any(type(order) is not int for order in orders) or sorted(orders) != [0, 1, 2, 3]:
        raise ValueError(f"{path.name}: image order must be the unique sequence 0,1,2,3")
    images = sorted(raw_images, key=lambda item: item["order"])
    if [item.get("role") for item in images] != ["context", "road_band", "road_band", "road_band"]:
        raise ValueError(f"{path.name}: expected one context and three road-band images")
    primary = manifest.get("primary_index")
    if type(primary) is not int or primary not in {0, 1, 2}:
        raise ValueError(f"{path.name}: invalid primary_index")
    expected_sources = [f"f{primary}.jpg", "f0.jpg", "f1.jpg", "f2.jpg"]
    if [item.get("source_frame") for item in images] != expected_sources:
        raise ValueError(f"{path.name}: source frames do not match primary context then f0..f2")
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
    note = ("\n- Capture layout: image 1 is full-frame context from the sharpest burst frame. "
            "Images 2-4 are orientation-aware road-region crops in chronological order; "
            f"the sharpest crop is chronological frame {primary + 1}.")
    return manifest, manifest_bytes, views, note


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", required=True)
    parser.add_argument("--events", required=True, help="comma-separated exporter event names")
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=4)
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
        prepared[event] = load_event(event_dir)

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
        for trial in range(args.trials):
            jobs.append((event, trial, manifest, body))

    key = run_eval.load_key()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        responses = pool.map(
            lambda job: run_eval.call(key, job[3], cache, f"android-prepared|{job[0]}|{job[1]}"),
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
                "decision": run_eval.decision(result, "drive", 3),
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
