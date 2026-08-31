#!/usr/bin/env python3
"""Focused offline contracts for the fail-closed RAD production evaluator."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import urllib.error

from PIL import Image


ROOT = pathlib.Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
sys.path.insert(0, str(EVAL))
spec = importlib.util.spec_from_file_location("rad_eval", EVAL / "run_rad_eval.py")
rad_eval = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rad_eval
spec.loader.exec_module(rad_eval)
FAILURES = []


def check(name, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        FAILURES.append(name)


def expect_error(name, function, fragment):
    try:
        function()
    except rad_eval.RadEvalError as error:
        check(name, fragment in str(error))
    else:
        check(name, False)


def write_image(path, colour):
    Image.new("RGB", (32, 20), colour).save(path, "JPEG", quality=91)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frame(path, sequence, sha):
    return {
        "path": path.name,
        "sequence": sequence,
        "timestamp_ms": sequence * 40,
        "sha256": sha,
        "width": 32,
        "height": 20,
        "view": "full_frame",
        "full_frame": True,
    }


def manifest(events):
    return {
        "version": 1,
        "dataset": {
            "name": "RAD—Road Anomaly Detection",
            "source_url": "https://example.invalid/rad",
            "license": "MIT",
        },
        "events": events,
    }


def accepted_assessment():
    return {
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
        "description": "localized cavity",
    }


contract = rad_eval.load_contract()
check("RAD evaluator pins the exact gpt-5.6 production Drive model",
      contract.model == "gpt-5.6" == rad_eval.production_eval.DRIVE_DEFAULT_MODEL)
check("RAD evaluator pins original image detail for the Drive model",
      contract.detail == "original" == rad_eval.production_eval.DRIVE_DEFAULT_DETAIL)
check("RAD evaluator imports the exact production prompt and strict schema",
      contract.prompt == rad_eval.production_eval.prompts()["baseline"]
      and contract.schema == rad_eval.production_eval.SCHEMA)


with tempfile.TemporaryDirectory(prefix="rad-eval-contract-") as temporary:
    root = pathlib.Path(temporary)
    paths = [root / f"frame-{index}.jpg" for index in range(4)]
    hashes = [write_image(path, (20 + index, 40, 60)) for index, path in enumerate(paths)]
    three = [frame(paths[index], index + 10, hashes[index]) for index in range(3)]
    base = {
        "id": "locked-pothole",
        "split": "locked_test",
        "source_video_id": "drive-1",
        "audit": {"label": "pothole", "status": "locked", "reviewer": "reviewer-1"},
        "source_label": "RoadDamages",
        "capture_provenance": "rad_full_frame_extraction",
        "primary_index": 1,
        "frames": three,
    }
    metadata, events = rad_eval.validate_manifest(manifest([base]), root)
    event = events[0]
    check("audited pothole label and source RAD class stay distinct",
          event.label == "pothole" and event.source_label == "RoadDamages" and event.locked)
    check("three source frames preserve strict chronology",
          [item.sequence for item in event.frames] == [10, 11, 12])

    body, transforms = rad_eval.build_production_request(event, contract)
    images = [item for item in body["input"][0]["content"]
              if item["type"] == "input_image"]
    check("RAD uses production's context plus all three chronological Drive frames",
          len(images) == 4 and len(transforms) == 4
          and [item.get("frame_index") for item in transforms[1:]] == [0, 1, 2])
    check("every RAD request transform is complete-frame",
          all(item.get("full_frame") is True for item in transforms))
    check("RAD request matches production reasoning, schema and token limit",
          body["model"] == "gpt-5.6"
          and all(item["detail"] == "original" for item in images)
          and body["reasoning"] == {"effort": "low"}
          and body["store"] is False
          and body["max_output_tokens"] == contract.max_output_tokens
          and body["text"]["format"]["schema"] == contract.schema)
    check("RAD decision is the exact production decision",
          rad_eval.production_eval.decision(accepted_assessment(), "drive", 3) == "accept")

    job = rad_eval.make_jobs([event], contract)[0]
    request_sha256 = rad_eval.sha256_bytes(rad_eval.canonical_json(body).encode())
    cache_dir = root / "cache"
    cache_dir.mkdir()
    cache_path = cache_dir / f"{job.cache_key}.json"
    cache_path.write_text(json.dumps({
        "job_id": job.job_id,
        "request_sha256": request_sha256,
        "response_id": "resp_cache",
        "retry_count": 0,
        "assessment": accepted_assessment(),
    }))
    cached = rad_eval.load_success_cache(cache_path, job, contract)
    cached_row = rad_eval.evaluate_job(job, contract, cache_dir, "", 10, 0)
    check("cache hit rebuilds and verifies the exact production request",
          cached is not None and cached["request_sha256"] == request_sha256)
    check("cache hit returns all complete-frame production transforms",
          cached_row["cache_hit"] is True
          and cached_row["transforms"] == transforms
          and all(item.get("full_frame") is True for item in cached_row["transforms"]))
    tampered_cache = json.loads(cache_path.read_text())
    tampered_cache["request_sha256"] = "0" * 64
    cache_path.write_text(json.dumps(tampered_cache))
    check("cache with a mismatched request hash is rejected",
          rad_eval.load_success_cache(cache_path, job, contract) is None)

    valid_row = rad_eval._base_row(job)
    valid_row.update({
        "status": "ok",
        "decision": "accept",
        "cache_hit": True,
        "request_sha256": request_sha256,
        "response_id": "resp_cache",
        "retry_count": 0,
        "transforms": transforms,
        "assessment": accepted_assessment(),
    })
    check("persisted result row rebinds to the exact selected job",
          rad_eval.validate_result_rows(
              [valid_row], [job], contract, require_complete=True
          ) == {job.job_id})
    unknown_row = json.loads(json.dumps(valid_row))
    unknown_row["job_id"] = "0" * 64
    expect_error(
        "unknown persisted job IDs fail closed",
        lambda: rad_eval.validate_result_rows([unknown_row], [job], contract),
        "unknown job ID",
    )
    expect_error(
        "duplicate persisted job IDs fail closed",
        lambda: rad_eval.validate_result_rows([valid_row, valid_row], [job], contract),
        "duplicate job ID",
    )
    for label, field, value, fragment in (
        ("label", "label", "not_pothole", "immutable job field label"),
        ("audit", "audit_status", "unreviewed", "immutable job field audit_status"),
        ("provenance", "capture_provenance", "forged_full_frame", "immutable job field capture_provenance"),
        ("decision", "decision", "reject", "decision does not match"),
        ("request", "request_sha256", "f" * 64, "request hash does not match"),
        ("transforms", "transforms", [], "transforms do not match"),
    ):
        changed = json.loads(json.dumps(valid_row))
        changed[field] = value
        expect_error(
            f"persisted {label} tampering fails closed",
            lambda changed=changed: rad_eval.validate_result_rows(
                [changed], [job], contract
            ),
            fragment,
        )
    error_row = rad_eval._base_row(job)
    error_row.update({
        "status": "error", "decision": "error", "cache_hit": False,
        "error_type": "RadEvalError", "error": "synthetic failure",
    })
    check("persisted API errors remain valid fail-closed denominator rows",
          rad_eval.validate_result_rows([error_row], [job], contract) == {job.job_id})
    mixed_error = json.loads(json.dumps(error_row))
    mixed_error["assessment"] = accepted_assessment()
    expect_error(
        "an error row cannot smuggle success evidence",
        lambda: rad_eval.validate_result_rows([mixed_error], [job], contract),
        "mixes error and success evidence",
    )
    raw_path = root / "raw.jsonl"
    raw_path.write_text(rad_eval.canonical_json(valid_row) + "\n")
    check("resume accepts only a fully rebound existing result row",
          rad_eval.load_completed_job_ids(raw_path, [job], contract) == {job.job_id})
    raw_path.write_text(rad_eval.canonical_json(unknown_row) + "\n")
    expect_error(
        "resume rejects a tampered existing result row before skipping work",
        lambda: rad_eval.load_completed_job_ids(raw_path, [job], contract),
        "unknown job ID",
    )

    bad_order = json.loads(json.dumps(base))
    bad_order["frames"][1]["sequence"] = 9
    expect_error(
        "out-of-order RAD frames fail closed",
        lambda: rad_eval.validate_manifest(manifest([bad_order]), root),
        "not strictly chronological",
    )
    alternate_input = json.loads(json.dumps(base))
    alternate_input["frames"][0]["crop_path"] = "alternate.jpg"
    expect_error(
        "alternate spatial image inputs fail closed",
        lambda: rad_eval.validate_manifest(manifest([alternate_input]), root),
        "prohibited alternate image",
    )
    two_frames = json.loads(json.dumps(base))
    two_frames["frames"] = two_frames["frames"][:2]
    expect_error(
        "RAD events cannot collapse to one or two frames",
        lambda: rad_eval.validate_manifest(manifest([two_frames]), root),
        "exactly three",
    )
    broad = json.loads(json.dumps(base))
    broad["id"] = "broad-rad"
    broad["audit"] = {"label": "broad_unreviewed_anomaly", "status": "unreviewed"}
    _, broad_events = rad_eval.validate_manifest(manifest([broad]), root)
    check("original broad RAD damage remains outside audited pothole truth",
          broad_events[0].label == rad_eval.BROAD_LABEL and not broad_events[0].locked)
    unaudited_positive = json.loads(json.dumps(base))
    unaudited_positive["audit"] = {"label": "pothole", "status": "unreviewed"}
    expect_error(
        "RoadDamages cannot become pothole truth without a named audit",
        lambda: rad_eval.validate_manifest(manifest([unaudited_positive]), root),
        "requires audited status and reviewer",
    )

    index_frames = []
    for number, (path, digest) in enumerate(zip(paths[:3], hashes[:3]), 10):
        index_frames.append({
            "id": f"drive-1:{number}",
            "source_video": "drive-1",
            "frame_number": number,
            "evaluation_split": "locked_test",
            "image_path": path.name,
            "image_sha256": digest,
            "width": 32,
            "height": 20,
            "full_frame": True,
        })
    index_payload = {
        "schema_version": rad_eval.rad_dataset.INDEX_SCHEMA_VERSION,
        "dataset": {
            "name": "RAD—Road Anomaly Detection",
            "source_url": "https://example.invalid/rad",
            "license": "MIT",
        },
        "provenance": {
            "ambiguous_source_videos": [],
            "ambiguous_sources_are_excluded_from_events": True,
        },
        "class_semantics": {
            "RoadDamages": "unreviewed_road_anomaly",
            "SpeedBump": "speed_breaker",
        },
        "input_policy": {
            "full_frame_only": True,
            "boxes_are_metadata_only": True,
            "spatial_transforms": [],
            "duplicate_target_semantics": "union across all source-frame variants",
            "event_size": 3,
            "chronology": "strict consecutive numeric source-frame IDs",
        },
        "counts": {},
        "source_dataset_complete": True,
        "frames": index_frames,
        "events": [
            {
                "id": "index-event",
                "source_video": "drive-1",
                "evaluation_split": "locked_test",
                "frame_numbers": [10, 11, 12],
                "frame_ids": [item["id"] for item in index_frames],
                "semantic_labels": ["unreviewed_road_anomaly"],
                "full_frame": True,
            },
            {
                "id": "speed-with-context",
                "source_video": "drive-1",
                "evaluation_split": "locked_test",
                "frame_numbers": [10, 11, 12],
                "frame_ids": [item["id"] for item in index_frames],
                "semantic_labels": ["non_target_context", "speed_breaker"],
                "full_frame": True,
            },
            {
                "id": "mixed-damage-and-speed",
                "source_video": "drive-1",
                "evaluation_split": "locked_test",
                "frame_numbers": [10, 11, 12],
                "frame_ids": [item["id"] for item in index_frames],
                "semantic_labels": [
                    "non_target_context", "speed_breaker", "unreviewed_road_anomaly"
                ],
                "full_frame": True,
            },
        ],
    }
    index_raw = rad_eval.canonical_json(index_payload).encode()
    audit_overlay = {
        "version": 1,
        "index_sha256": rad_eval.sha256_bytes(index_raw),
        "events": [{
            "event_id": "index-event",
            "label": "pothole",
            "status": "locked",
            "reviewer": "reviewer-1",
        }],
    }
    adapted = rad_eval._index_to_manifest(index_payload, index_raw, audit_overlay)
    _, adapted_events = rad_eval.validate_manifest(adapted, root)
    adapted_by_id = {event.event_id: event for event in adapted_events}
    check("sealed RAD index plus audit overlay yields locked full-frame truth",
          adapted_by_id["index-event"].label == "pothole"
          and adapted_by_id["index-event"].locked)
    check("SpeedBump plus vehicles or pedestrians remains a hard negative",
          adapted_by_id["speed-with-context"].label == "speed_breaker"
          and adapted_by_id["speed-with-context"].audit_status == "audited")
    check("RoadDamages plus SpeedBump remains broad and unreviewed",
          adapted_by_id["mixed-damage-and-speed"].label == rad_eval.BROAD_LABEL
          and adapted_by_id["mixed-damage-and-speed"].audit_status == "unreviewed")
    expect_error(
        "release gate rejects a standalone audited fixture",
        lambda: rad_eval._index_to_manifest(
            manifest([base]), json.dumps(manifest([base])).encode(), None,
            require_official_release=True,
        ),
        "standalone manifests are test fixtures only",
    )
    release_fake = json.loads(json.dumps(index_payload))
    release_fake["dataset"].update({
        "ref": rad_eval.rad_dataset.DATASET_REF,
        "version": rad_eval.rad_dataset.DATASET_VERSION,
    })
    release_fake["source_dataset_counts"] = {}
    release_fake["class_names"] = list(rad_eval.rad_dataset.EXPECTED_CLASSES)
    release_fake["class_semantics"] = dict(rad_eval.rad_dataset.CLASS_SEMANTICS)
    expect_error(
        "release gate rejects a fake complete three-frame RAD index",
        lambda: rad_eval._index_to_manifest(
            release_fake, index_raw, audit_overlay, require_official_release=True
        ),
        "source counts",
    )
    incomplete_index = json.loads(json.dumps(index_payload))
    incomplete_index["source_dataset_complete"] = False
    expect_error(
        "partial RAD indexes cannot enter production accuracy evaluation",
        lambda: rad_eval._index_to_manifest(incomplete_index, index_raw, None),
        "complete, count-verified v3",
    )
    stale_index = json.loads(json.dumps(index_payload))
    stale_index["schema_version"] = "rad-dataset-index-v1"
    expect_error(
        "stale first-variant-wins RAD indexes fail closed",
        lambda: rad_eval._index_to_manifest(stale_index, index_raw, None),
        "unsupported RAD dataset index schema",
    )
    if rad_eval.rad_dataset is not None:
        sealed_index = dict(index_payload)
        sealed_index["index_sha256"] = rad_eval.rad_dataset.sha256_bytes(
            rad_eval.rad_dataset.canonical_json_bytes(sealed_index)
        )
        index_path = root / "index.json"
        index_path.write_bytes(rad_eval.rad_dataset.canonical_json_bytes(sealed_index))
        audit_path = root / "audit.json"
        audit_path.write_text(json.dumps({
            "version": 1,
            "index_sha256": sealed_index["index_sha256"],
            "events": audit_overlay["events"],
        }))
        _, loaded_metadata, loaded_events = rad_eval.load_manifest(
            index_path, root, audit_path
        )
        check("runner validates the dataset builder's sealed index API",
              loaded_metadata["index_sha256"] == sealed_index["index_sha256"]
              and {event.event_id: event for event in loaded_events}[
                  "index-event"].label == "pothole")
    bad_index = json.loads(json.dumps(index_payload))
    bad_index["events"][0]["frame_numbers"] = [10, 12, 13]
    expect_error(
        "dataset-index Drive events must be three truly consecutive frames",
        lambda: rad_eval._index_to_manifest(bad_index, index_raw, audit_overlay),
        "not three consecutive full frames",
    )
    ambiguous = json.loads(json.dumps(base))
    ambiguous["id"] = "ambiguous-collision"
    ambiguous["ambiguous_source"] = True
    _, ambiguous_events = rad_eval.validate_manifest(manifest([base, ambiguous]), root)
    check("ambiguous source IDs are excluded from Drive accuracy selection",
          [event.event_id for event in rad_eval.select_events(
              ambiguous_events, set(), set(), set())] == ["locked-pothole"])
    expect_error(
        "an explicitly requested ambiguous source fails closed",
        lambda: rad_eval.select_events(
            ambiguous_events, set(), set(), {"ambiguous-collision"}),
        "cannot be treated as chronological Drive evidence",
    )

    speed = json.loads(json.dumps(base))
    speed["id"] = "speed-breaker"
    speed["source_video_id"] = "drive-2"
    speed["audit"] = {"label": "speed_breaker", "status": "locked", "reviewer": "reviewer-1"}
    negative = json.loads(json.dumps(base))
    negative["id"] = "audited-non-pothole"
    negative["source_video_id"] = "drive-3"
    negative["audit"] = {"label": "not_pothole", "status": "locked", "reviewer": "reviewer-1"}
    _, selected = rad_eval.validate_manifest(manifest([base, speed, negative, broad]), root)
    jobs = rad_eval.make_jobs(selected, contract)
    rows = []
    for job, decision, status in zip(
            jobs, ("accept", "reject", "reject", "accept"), ("ok", "ok", "ok", "ok")):
        row = rad_eval._base_row(job)
        row.update({"status": status, "decision": decision})
        rows.append(row)
    summary = rad_eval.summarise(rows, selected)
    check("strict gate accepts perfect locked positives and negatives",
          rad_eval.evaluate_gate(summary, 1.0, 0, 0) == [])
    check("broad RAD accepts are descriptive, never pothole accuracy truth",
          summary["broad_unreviewed_anomaly"]["accepted"] == 1
          and summary["audited"]["pothole"]["total"] == 1)
    rows[1]["decision"] = "accept"
    summary = rad_eval.summarise(rows, selected)
    check("one accepted speed breaker blocks the release gate",
          any("speed breakers" in item for item in rad_eval.evaluate_gate(summary, 1.0, 0, 0)))
    rows[1]["decision"] = "reject"
    rows[2]["decision"] = "accept"
    summary = rad_eval.summarise(rows, selected)
    check("one accepted audited non-pothole blocks the release gate",
          any("audited non-potholes" in item
              for item in rad_eval.evaluate_gate(summary, 1.0, 0, 0)))
    rows[2]["decision"] = "reject"
    rows[1].update({"status": "error", "decision": "error"})
    summary = rad_eval.summarise(rows, selected)
    failures = rad_eval.evaluate_gate(summary, 1.0, 0, 0)
    check("API/parse/schema errors stay in metrics and block the gate",
          summary["errors"] == 1 and summary["audited"]["speed_breaker"]["total"] == 1
          and any("errors" in item for item in failures))


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


api_payload = {
    "id": "resp_test",
    "output": [{"type": "message", "content": [{
        "type": "output_text", "text": json.dumps(accepted_assessment())
    }]}],
}
attempts = []
delays = []


def retrying_opener(_request, timeout):
    attempts.append(timeout)
    if len(attempts) == 1:
        raise urllib.error.HTTPError(
            rad_eval.API_URL, 429, "rate limit", {"Retry-After": "3"}, io.BytesIO()
        )
    return FakeResponse(api_payload)


assessment, response_id, retry_count = rad_eval.call_api(
    "test-key", {}, contract.schema, 10, 2,
    opener=retrying_opener, sleeper=delays.append,
)
check("API backoff honors Retry-After before a bounded retry",
      delays == [3.0] and retry_count == 1 and len(attempts) == 2)
check("retried API output still receives strict schema validation",
      assessment == accepted_assessment() and response_id == "resp_test")


submitted = []
active = 0
peak = 0


def worker(value):
    global active, peak
    active += 1
    peak = max(peak, active)
    submitted.append(value)
    active -= 1
    return {"value": value}


parallel_rows = list(rad_eval.bounded_parallel_map(range(11), worker, 3))
check("lazy executor returns every job without exceeding its bound",
      len(parallel_rows) == 11 and peak <= 3 and sorted(submitted) == list(range(11)))


check("CLI requires one explicit non-paid or paid execution mode",
      rad_eval.make_parser().parse_args(["--validate-only"]).validate_only is True)
paid_args = rad_eval.make_parser().parse_args(["--paid-run"])
expect_error("paid runs require an explicit positive call cap",
             lambda: rad_eval._validate_args(paid_args), "positive --max-calls")
non_paid_gate_args = rad_eval.make_parser().parse_args([
    "--validate-only", "--gate", "--audit-manifest", "audit.json",
])
expect_error("release gate cannot be claimed by validate-only or dry-run mode",
             lambda: rad_eval._validate_args(non_paid_gate_args), "requires --paid-run")
missing_audit_args = rad_eval.make_parser().parse_args([
    "--paid-run", "--max-calls", "1", "--gate",
])
expect_error("release gate requires an index-pinned audit manifest",
             lambda: rad_eval._validate_args(missing_audit_args), "requires an audit manifest")
provenance_args = rad_eval.make_parser().parse_args([
    "--paid-run", "--max-calls", "1", "--audit-manifest", "audit.json",
])
run_provenance = rad_eval.provenance(b"{}", {}, contract, selected, provenance_args)
check("run provenance pins both RAD runner and dataset adapter bytes",
      run_provenance["rad_eval_sha256"]
      == rad_eval.sha256_file(pathlib.Path(rad_eval.__file__).resolve())
      and run_provenance["rad_dataset_sha256"]
      == rad_eval.sha256_file(pathlib.Path(rad_eval.rad_dataset.__file__).resolve()))


if FAILURES:
    print(f"RAD EVAL CONTRACT FAIL ({len(FAILURES)} failures)", file=sys.stderr)
    for failure in FAILURES:
        print(f"- {failure}", file=sys.stderr)
    raise SystemExit(1)
print("RAD EVAL CONTRACT PASS")
