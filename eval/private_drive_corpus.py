#!/usr/bin/env python3
"""Validate and replay the private Desktop drive corpus without committing its media.

Normal test runs use ``--check-manifest`` and need neither the videos nor an API key.
Media validation finds files by SHA-256, decodes every source, and regenerates the exact
three complete frames pinned by each curated case. Network inference is impossible unless
``--paid-run`` and a sufficient ``--max-calls`` budget are both supplied explicitly.
"""

from __future__ import annotations

import argparse
import base64
import bisect
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from PIL import Image, UnidentifiedImageError

import private_release_gate as release_gate
import run_eval as production_eval


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "eval" / "private_drive_corpus.json"
DEFAULT_WORK_ROOT = ROOT / "eval" / ".private-drive-corpus"
VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov"}
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class CorpusError(RuntimeError):
    """A fail-closed, user-actionable corpus error."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_inventory_sha256(sources: list[dict[str, Any]]) -> str:
    """Seal the ordered, identity-bearing source metadata (not private paths)."""
    fields = (
        "source_id", "sha256", "codec", "coded_width", "coded_height",
        "presented_width", "presented_height", "rotation_degrees", "frames",
        "duration_seconds", "bytes",
    )
    inventory = [{field: source[field] for field in fields} for source in sources]
    return sha256_bytes(canonical_json(inventory).encode())


def require_keys(value: Any, required: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CorpusError(f"{where} must be an object")
    missing = sorted(required - set(value))
    if missing:
        raise CorpusError(f"{where} is missing: {', '.join(missing)}")
    return value


def finite_number(value: Any) -> bool:
    return type(value) in {int, float} and float("-inf") < float(value) < float("inf")


def reject_absolute_paths(value: Any, where: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            reject_absolute_paths(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_absolute_paths(item, f"{where}[{index}]")
    elif isinstance(value, str) and (value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value)):
        raise CorpusError(f"{where} must not contain an absolute path")


def vod_sample_times(duration: float, step: float) -> list[float]:
    """Mirror standalone.js vodSampleTimes, including its floating-point loop."""
    if not duration > 0 or not step > 0:
        return []
    values: list[float] = []
    at = min(0.4, duration / 2)
    while at < duration:
        values.append(at)
        at += step
    return values


def vod_burst_times(center: float, duration: float, half_span: float) -> list[float]:
    """Mirror standalone.js vodBurstTimes exactly."""
    if not duration > 0 or half_span < 0:
        return []
    last = max(0.001, duration - 0.001)
    candidates = [max(0.001, center - half_span),
                  min(last, max(0.001, center)), min(last, center + half_span)]
    values: list[float] = []
    for candidate in candidates:
        if not any(abs(other - candidate) < 0.04 for other in values):
            values.append(candidate)
    return values


def current_contract_receipt() -> tuple[dict[str, Any], dict[str, Any]]:
    contract = release_gate.load_production_contract()
    receipt = {
        "model": contract["model"],
        "detail": contract["detail"],
        "prompt_version": contract["prompt_version"],
        "schema_version": contract["schema_version"],
        "max_output_tokens": contract["max_output_tokens"],
        "prompt_sha256": sha256_bytes(contract["prompt"].encode()),
        "schema_sha256": sha256_bytes(canonical_json(contract["schema"]).encode()),
    }
    return contract, receipt


def validate_manifest(value: Any) -> dict[str, Any]:
    manifest = require_keys(value, {
        "schema_version", "created_at_utc", "corpus", "production_contract",
        "sampling", "extraction", "audit_receipt", "companion_suite", "sources", "cases",
    }, "manifest")
    if manifest["schema_version"] != "private-drive-corpus-v1":
        raise CorpusError("unsupported private drive corpus schema")
    reject_absolute_paths(manifest)

    corpus = require_keys(manifest["corpus"], {
        "media_committed", "absolute_paths_stored", "source_video_count",
        "capture_provenance", "raw_camerax_accuracy_eligible",
        "gps_or_session_metadata_present",
        "source_inventory_sha256", "total_bytes", "total_frames",
        "total_duration_seconds", "expected_vod_windows", "curated_case_count",
        "curated_phase_count", "label_policy",
    }, "corpus")
    if corpus["media_committed"] is not False or corpus["absolute_paths_stored"] is not False:
        raise CorpusError("private media and absolute paths must remain outside the manifest")
    if (corpus["capture_provenance"] != "native_mediarecorder_reconstruction"
            or corpus["raw_camerax_accuracy_eligible"] is not False
            or corpus["gps_or_session_metadata_present"] is not False):
        raise CorpusError("saved-video provenance or accuracy scope is overstated")
    if (not isinstance(corpus["source_inventory_sha256"], str)
            or not HEX_64.fullmatch(corpus["source_inventory_sha256"])
            or any(type(corpus[field]) is not int or corpus[field] <= 0 for field in (
                "source_video_count", "total_bytes", "total_frames", "expected_vod_windows",
                "curated_case_count", "curated_phase_count"))
            or not finite_number(corpus["total_duration_seconds"])
            or corpus["total_duration_seconds"] <= 0):
        raise CorpusError("corpus totals or inventory seal are invalid")

    sampling = require_keys(manifest["sampling"], {
        "center_start_seconds", "center_step_seconds", "burst_half_span_seconds",
        "sample_function", "burst_function",
    }, "sampling")
    if (sampling["center_start_seconds"] != 0.4
            or sampling["center_step_seconds"] != 0.5
            or sampling["burst_half_span_seconds"] != 0.4
            or sampling["sample_function"] != "standalone.js vodSampleTimes"
            or sampling["burst_function"] != "standalone.js vodBurstTimes"):
        raise CorpusError("corpus sampling is not the exact production VOD cadence")

    extraction = require_keys(manifest["extraction"], {
        "fixture_hash_kind", "selection", "selected_frame_index_base",
        "ffmpeg_default_autorotate", "whole_frame_only", "spatial_crop_tile_mask_or_roi",
        "prepared_model_input_hashes_in_this_manifest",
    }, "extraction")
    if (extraction["fixture_hash_kind"]
            != "raw_ffmpeg_extracted_autorotated_full_frame_jpeg"
            or extraction["selected_frame_index_base"] != 0
            or extraction["ffmpeg_default_autorotate"] is not True
            or extraction["whole_frame_only"] is not True
            or extraction["spatial_crop_tile_mask_or_roi"] is not False
            or extraction["prepared_model_input_hashes_in_this_manifest"] is not False):
        raise CorpusError("corpus extraction must use uncropped autorotated complete frames")

    sources = manifest["sources"]
    cases = manifest["cases"]
    if not isinstance(sources, list) or not sources:
        raise CorpusError("sources must be a non-empty array")
    if not isinstance(cases, list) or not cases:
        raise CorpusError("cases must be a non-empty array")
    if corpus["source_video_count"] != len(sources) or corpus["curated_case_count"] != len(cases):
        raise CorpusError("corpus source/case totals do not match the manifest")

    source_by_id: dict[str, dict[str, Any]] = {}
    source_hashes: set[str] = set()
    for index, raw_source in enumerate(sources):
        source = require_keys(raw_source, {
            "source_id", "sha256", "codec", "coded_width", "coded_height",
            "presented_width", "presented_height", "rotation_degrees", "frames",
            "avg_frame_rate", "r_frame_rate", "duration_seconds", "bytes",
        }, f"source {index}")
        source_id = source["source_id"]
        fingerprint = source["sha256"]
        if not isinstance(source_id, str) or not source_id or source_id in source_by_id:
            raise CorpusError(f"source {index} has a missing or duplicate source_id")
        if not isinstance(fingerprint, str) or not HEX_64.fullmatch(fingerprint):
            raise CorpusError(f"source {source_id} has an invalid SHA-256")
        if fingerprint in source_hashes:
            raise CorpusError(f"source {source_id} duplicates another source fingerprint")
        if source["codec"] != "h264":
            raise CorpusError(f"source {source_id} must be the audited H.264 stream")
        if any(type(source[field]) is not int or source[field] <= 0 for field in (
                "coded_width", "coded_height", "presented_width", "presented_height",
                "frames", "bytes")):
            raise CorpusError(f"source {source_id} has invalid integer metadata")
        if not finite_number(source["rotation_degrees"]) or not finite_number(source["duration_seconds"]):
            raise CorpusError(f"source {source_id} has invalid rotation or duration")
        if source["duration_seconds"] <= 0:
            raise CorpusError(f"source {source_id} has a non-positive duration")
        if not all(isinstance(source[field], str) and source[field] for field in (
                "avg_frame_rate", "r_frame_rate")):
            raise CorpusError(f"source {source_id} has invalid frame-rate metadata")
        source_by_id[source_id] = source
        source_hashes.add(fingerprint)

    if (corpus["source_inventory_sha256"] != source_inventory_sha256(sources)
            or corpus["total_bytes"] != sum(source["bytes"] for source in sources)
            or corpus["total_frames"] != sum(source["frames"] for source in sources)
            or abs(corpus["total_duration_seconds"]
                   - sum(source["duration_seconds"] for source in sources)) > 1e-6):
        raise CorpusError("corpus inventory seal or source totals do not match")
    if (extraction.get("coded_dimensions") != [sources[0]["coded_width"],
                                                sources[0]["coded_height"]]
            or extraction.get("presented_dimensions")
            != [sources[0]["presented_width"], sources[0]["presented_height"]]
            or extraction.get("source_rotation_degrees") != sources[0]["rotation_degrees"]
            or any((source["coded_width"], source["coded_height"],
                    source["presented_width"], source["presented_height"],
                    source["rotation_degrees"])
                   != (sources[0]["coded_width"], sources[0]["coded_height"],
                       sources[0]["presented_width"], sources[0]["presented_height"],
                       sources[0]["rotation_degrees"])
                   for source in sources)):
        raise CorpusError("corpus extraction geometry does not match every source")

    seen_case_ids: set[str] = set()
    seen_custom_ids: set[str] = set()
    phase_count = 0
    for index, raw_case in enumerate(cases):
        case = require_keys(raw_case, {
            "id", "source_id", "expected_is_pothole", "semantic_class",
            "evidence_status", "ground_truth_scope", "source_interval_seconds",
            "rationale", "phases",
        }, f"case {index}")
        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id or case_id in seen_case_ids:
            raise CorpusError(f"case {index} has a missing or duplicate ID")
        if case["source_id"] not in source_by_id:
            raise CorpusError(f"case {case_id} refers to an unknown source")
        if type(case["expected_is_pothole"]) is not bool:
            raise CorpusError(f"case {case_id} has no binary expectation")
        if case["expected_is_pothole"] is not False:
            raise CorpusError(f"case {case_id} cannot invent a Desktop positive label")
        if not all(isinstance(case[field], str) and case[field].strip() for field in (
                "semantic_class", "evidence_status", "ground_truth_scope", "rationale")):
            raise CorpusError(f"case {case_id} is missing review provenance")
        interval = case["source_interval_seconds"]
        source_duration = float(source_by_id[case["source_id"]]["duration_seconds"])
        if (not isinstance(interval, list) or len(interval) != 2
                or any(not finite_number(item) for item in interval)
                or interval[0] < 0 or interval[0] >= interval[1]
                or interval[1] > source_duration):
            raise CorpusError(f"case {case_id} has an invalid source interval")
        phases = case["phases"]
        if not isinstance(phases, list) or not phases:
            raise CorpusError(f"case {case_id} must have at least one exact phase")
        sample_centers = vod_sample_times(source_duration, sampling["center_step_seconds"])
        for phase_index, raw_phase in enumerate(phases):
            phase = require_keys(raw_phase, {
                "custom_id", "center_seconds", "requested_timestamps_seconds",
                "selected_frame_indices", "selected_pts_seconds", "full_frame_jpeg_sha256",
                "duration_boundary_clamped",
            }, f"case {case_id} phase {phase_index}")
            custom_id = phase["custom_id"]
            if not isinstance(custom_id, str) or not custom_id or custom_id in seen_custom_ids:
                raise CorpusError(f"case {case_id} has a missing or duplicate custom_id")
            center = phase["center_seconds"]
            if not finite_number(center) or not any(abs(center - item) < 1e-9 for item in sample_centers):
                raise CorpusError(f"case {case_id} phase {phase_index} is not a production VOD center")
            if center < interval[0] or center > interval[1]:
                raise CorpusError(f"case {case_id} phase {phase_index} is outside its reviewed interval")
            window_index = next(i for i, item in enumerate(sample_centers) if abs(center - item) < 1e-9)
            expected_id = f"{case['source_id']}--w{window_index:05d}--c{round(center * 1000):07d}"
            if custom_id != expected_id:
                raise CorpusError(f"case {case_id} phase {phase_index} custom_id is not deterministic")
            requested = phase["requested_timestamps_seconds"]
            expected_requested = vod_burst_times(
                center, source_duration, sampling["burst_half_span_seconds"])
            if (not isinstance(requested, list) or len(requested) != 3
                    or len(expected_requested) != 3
                    or any(not finite_number(item) for item in requested)
                    or any(abs(left - right) > 1e-9
                           for left, right in zip(requested, expected_requested))):
                raise CorpusError(f"case {case_id} phase {phase_index} timestamps drifted")
            indices = phase["selected_frame_indices"]
            pts = phase["selected_pts_seconds"]
            hashes = phase["full_frame_jpeg_sha256"]
            clamped = phase["duration_boundary_clamped"]
            if (not isinstance(indices, list) or len(indices) != 3
                    or any(type(item) is not int or item < 0 for item in indices)
                    or indices != sorted(indices)
                    or any(item >= source_by_id[case["source_id"]]["frames"] for item in indices)):
                raise CorpusError(f"case {case_id} phase {phase_index} has invalid frame indices")
            if (not isinstance(pts, list) or len(pts) != 3
                    or any(not finite_number(item) for item in pts) or pts != sorted(pts)
                    or any(item < 0 or item >= source_duration for item in pts)):
                raise CorpusError(f"case {case_id} phase {phase_index} has invalid PTS values")
            if (not isinstance(hashes, list) or len(hashes) != 3
                    or any(not isinstance(item, str) or not HEX_64.fullmatch(item) for item in hashes)):
                raise CorpusError(f"case {case_id} phase {phase_index} has invalid JPEG hashes")
            if (not isinstance(clamped, list) or len(clamped) != 3
                    or any(type(item) is not bool for item in clamped)):
                raise CorpusError(f"case {case_id} phase {phase_index} has invalid clamp flags")
            seen_custom_ids.add(custom_id)
            phase_count += 1
        seen_case_ids.add(case_id)
    if corpus["curated_phase_count"] != phase_count:
        raise CorpusError("corpus phase total does not match the manifest")

    audit = require_keys(manifest["audit_receipt"], {
        "kind", "repository_commit", "dryrun_manifest_sha256",
        "fixture_receipts_sha256", "canonical_direct_records_sha256",
        "result_analysis_sha256", "aggregate_request_stream_sha256",
        "requests", "raw_frames_extracted", "valid_responses", "accepted_windows",
        "embedded_images", "rejected_windows", "error_windows", "complete",
        "speed_breaker_flags", "unusable_image_flags", "model_output_is_ground_truth",
        "interpretation",
    }, "audit_receipt")
    receipt_hashes = (
        "dryrun_manifest_sha256", "fixture_receipts_sha256",
        "canonical_direct_records_sha256", "result_analysis_sha256",
        "aggregate_request_stream_sha256",
    )
    if (audit["kind"] != "exhaustive_production_contract_model_scan"
            or not isinstance(audit["repository_commit"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", audit["repository_commit"])
            or any(not isinstance(audit[field], str) or not HEX_64.fullmatch(audit[field])
                   for field in receipt_hashes)
            or any(type(audit[field]) is not int or audit[field] < 0 for field in (
                "requests", "raw_frames_extracted", "embedded_images", "valid_responses",
                "accepted_windows", "rejected_windows", "error_windows",
                "speed_breaker_flags", "unusable_image_flags"))):
        raise CorpusError("audit receipt identity or counts are invalid")
    expected_windows = sum(len(vod_sample_times(
        float(source["duration_seconds"]), sampling["center_step_seconds"])) for source in sources)
    if (corpus["expected_vod_windows"] != expected_windows
            or audit["requests"] != expected_windows
            or audit["raw_frames_extracted"] != expected_windows * 3
            or audit.get("embedded_images") != expected_windows * 4
            or audit["valid_responses"] != expected_windows
            or audit["accepted_windows"] + audit["rejected_windows"] != expected_windows
            or audit["error_windows"] != 0
            or audit.get("complete") is not True
            or audit["model_output_is_ground_truth"] is not False):
        raise CorpusError("audit receipt totals or ground-truth limitation are invalid")
    if "must not" not in str(audit["interpretation"]).lower():
        raise CorpusError("audit receipt must explicitly forbid treating model output as truth")

    companion = require_keys(manifest["companion_suite"], {
        "owner_confirmed_drive_positives", "owner_confirmed_manual_positives",
        "desktop_corpus_role",
    }, "companion_suite")
    if companion["desktop_corpus_role"] != "hard_negative_and_abstention_regression_only":
        raise CorpusError("Desktop corpus cannot replace the positive recall suites")

    _contract, actual_receipt = current_contract_receipt()
    if manifest["production_contract"] != actual_receipt:
        raise CorpusError("private corpus production contract has drifted")
    return manifest


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        return validate_manifest(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError) as error:
        raise CorpusError(f"cannot load private drive corpus: {error}") from error


def run_json(command: list[str], error_message: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        return json.loads(completed.stdout)
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        raise CorpusError(error_message) from error


def probe_video(path: Path) -> dict[str, Any]:
    payload = run_json([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=codec_name,width,height,nb_frames,avg_frame_rate,r_frame_rate:"
        "stream_tags=rotate:stream_side_data=rotation:format=duration,size",
        "-of", "json", str(path),
    ], "ffprobe could not inspect a private drive video")
    try:
        stream = payload["streams"][0]
        side_data = stream.get("side_data_list") or []
        rotations = [item["rotation"] for item in side_data if "rotation" in item]
        if not rotations and stream.get("tags", {}).get("rotate") is not None:
            rotations = [stream["tags"]["rotate"]]
        rotation = float(rotations[0]) if rotations else 0.0
        coded_width, coded_height = int(stream["width"]), int(stream["height"])
        if int(round(rotation)) % 360 in {90, 270}:
            presented_width, presented_height = coded_height, coded_width
        else:
            presented_width, presented_height = coded_width, coded_height
        return {
            "codec": stream["codec_name"], "coded_width": coded_width,
            "coded_height": coded_height, "presented_width": presented_width,
            "presented_height": presented_height, "rotation_degrees": rotation,
            "frames": int(stream["nb_frames"]), "avg_frame_rate": stream["avg_frame_rate"],
            "r_frame_rate": stream["r_frame_rate"],
            "duration_seconds": float(payload["format"]["duration"]),
            "bytes": int(payload["format"]["size"]),
        }
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise CorpusError("ffprobe returned incomplete private drive metadata") from error


def discover_sources(directory: Path, sources: list[dict[str, Any]]) -> dict[str, Path]:
    if not directory.is_dir():
        raise CorpusError("--source-dir is not a directory")
    by_size: dict[int, list[dict[str, Any]]] = {}
    for source in sources:
        by_size.setdefault(source["bytes"], []).append(source)
    discovered: dict[str, Path] = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        candidates = by_size.get(path.stat().st_size, [])
        if not candidates:
            continue
        fingerprint = sha256_file(path)
        for source in candidates:
            if source["sha256"] == fingerprint and source["source_id"] not in discovered:
                discovered[source["source_id"]] = path
                break
    missing = [source["source_id"] for source in sources if source["source_id"] not in discovered]
    if missing:
        raise CorpusError("missing private source(s): " + ", ".join(missing))
    return discovered


def verify_source(path: Path, expected: dict[str, Any]) -> None:
    if sha256_file(path) != expected["sha256"]:
        raise CorpusError(f"source {expected['source_id']} failed its SHA-256 check")
    actual = probe_video(path)
    exact = ("codec", "coded_width", "coded_height", "presented_width", "presented_height",
             "frames", "avg_frame_rate", "r_frame_rate", "bytes")
    if any(actual[field] != expected[field] for field in exact):
        raise CorpusError(f"source {expected['source_id']} failed its stream metadata check")
    if (abs(actual["rotation_degrees"] - float(expected["rotation_degrees"])) > 1e-9
            or abs(actual["duration_seconds"] - float(expected["duration_seconds"])) > 1e-6):
        raise CorpusError(f"source {expected['source_id']} failed rotation or duration checks")


def probe_frame_pts(path: Path) -> list[float]:
    payload = run_json([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(path),
    ], "ffprobe could not enumerate private drive frame timestamps")
    try:
        return [float(frame["best_effort_timestamp_time"]) for frame in payload["frames"]]
    except (KeyError, TypeError, ValueError) as error:
        raise CorpusError("private drive frame timestamps are incomplete") from error


def decode_to_jpegs(path: Path, output: Path, expected_frames: int) -> list[float]:
    output.mkdir(parents=True, exist_ok=False)
    try:
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-xerror", "-nostdin",
            "-i", str(path), "-map", "0:v:0", "-fps_mode", "passthrough", "-q:v", "2",
            "-start_number", "0", "-n", str(output / "f%05d.jpg"),
        ], check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise CorpusError("ffmpeg could not fully decode a curated private source") from error
    pts = probe_frame_pts(path)
    files = list(output.glob("f*.jpg"))
    if len(files) != expected_frames or len(pts) != expected_frames:
        raise CorpusError(
            f"decoded frame count mismatch: files={len(files)} pts={len(pts)} expected={expected_frames}")
    return pts


def decode_without_retaining(path: Path) -> None:
    try:
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-xerror", "-nostdin",
            "-i", str(path), "-map", "0:v:0", "-f", "null", "-",
        ], check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise CorpusError("ffmpeg found a corrupt or undecodable private source") from error


def validate_phase(case: dict[str, Any], phase_index: int, phase: dict[str, Any],
                   source: dict[str, Any], frame_dir: Path, pts: list[float],
                   contract: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    paths: list[Path] = []
    for item_index, requested in enumerate(phase["requested_timestamps_seconds"]):
        selected = bisect.bisect_left(pts, requested)
        clamped = selected >= len(pts)
        selected = min(selected, len(pts) - 1)
        if (selected != phase["selected_frame_indices"][item_index]
                or clamped != phase["duration_boundary_clamped"][item_index]
                or abs(pts[selected] - phase["selected_pts_seconds"][item_index]) > 1e-6):
            raise CorpusError(f"{case['id']} phase {phase_index} frame selection drifted")
        frame_path = frame_dir / f"f{selected:05d}.jpg"
        if sha256_file(frame_path) != phase["full_frame_jpeg_sha256"][item_index]:
            raise CorpusError(f"{case['id']} phase {phase_index} JPEG fingerprint drifted")
        try:
            with Image.open(frame_path) as image:
                size, image_format = image.size, image.format
                image.verify()
        except (OSError, UnidentifiedImageError) as error:
            raise CorpusError(f"{case['id']} phase {phase_index} contains an invalid JPEG") from error
        expected_size = (source["presented_width"], source["presented_height"])
        if image_format != "JPEG" or size != expected_size:
            raise CorpusError(f"{case['id']} phase {phase_index} is not a complete autorotated frame")
        paths.append(frame_path)

    fixture = {"event": case, "phase_index": phase_index, "frame_paths": paths}
    request = release_gate.build_production_request(fixture, contract)
    content = request["input"][0]["content"]
    images = [item for item in content if item.get("type") == "input_image"]
    if len(images) != 4 or any(item.get("detail") != contract["detail"] for item in images):
        raise CorpusError(f"{case['id']} phase {phase_index} did not build four production images")
    for item in images:
        prefix = "data:image/jpeg;base64,"
        if not str(item.get("image_url", "")).startswith(prefix):
            raise CorpusError(f"{case['id']} phase {phase_index} has a non-JPEG model input")
        try:
            with Image.open(io.BytesIO(base64.b64decode(item["image_url"][len(prefix):], validate=True))) as image:
                width, height = image.size
        except (ValueError, OSError, UnidentifiedImageError) as error:
            raise CorpusError(f"{case['id']} phase {phase_index} has an invalid model input") from error
        if width <= 0 or height <= 0 or width / height != source["presented_width"] / source["presented_height"]:
            raise CorpusError(f"{case['id']} phase {phase_index} changed the complete-frame aspect ratio")
    return fixture, request


def cache_path(work_root: Path, phase: dict[str, Any], request_hash: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", phase["custom_id"])
    return work_root / "cache" / f"{safe_id}-{request_hash[:16]}.json"


def validate_cached_record(value: Any, phase: dict[str, Any], request_hash: str,
                           schema: dict[str, Any]) -> dict[str, Any]:
    """Accept only a cache entry created for this exact phase and request contract."""
    record = require_keys(value, {
        "format_version", "custom_id", "request_sha256", "response_id", "assessment",
    }, "cached private-corpus response")
    if set(record) != {
            "format_version", "custom_id", "request_sha256", "response_id", "assessment"}:
        raise CorpusError("cached private-corpus response has unexpected fields")
    if record["format_version"] != 1:
        raise CorpusError("cached private-corpus response has an unsupported format version")
    if record["custom_id"] != phase["custom_id"]:
        raise CorpusError("cached private-corpus response belongs to a different phase")
    if record["request_sha256"] != request_hash:
        raise CorpusError("cached request fingerprint mismatch")
    response_id = record["response_id"]
    if response_id is not None and (not isinstance(response_id, str) or not response_id):
        raise CorpusError("cached private-corpus response has an invalid response ID")
    try:
        return release_gate.validate_assessment(record["assessment"], schema)
    except release_gate.GateError as error:
        raise CorpusError("cached private-corpus assessment violates the current schema") from error


def cached_or_fresh_assessment(work_root: Path, phase: dict[str, Any], request: dict[str, Any],
                               contract: dict[str, Any], api_key: str,
                               timeout: int) -> tuple[dict[str, Any], bool]:
    request_hash = sha256_bytes(canonical_json(request).encode())
    path = cache_path(work_root, phase, request_hash)
    if path.is_file():
        try:
            cached = json.loads(path.read_text())
            assessment = validate_cached_record(
                cached, phase, request_hash, contract["schema"])
            return assessment, True
        except (OSError, json.JSONDecodeError) as error:
            raise CorpusError("cached private-corpus response is unreadable") from error
    try:
        assessment, response_id = release_gate.fresh_api_assessment(
            api_key, request, timeout, contract["schema"])
    except release_gate.GateError as error:
        raise CorpusError("fresh private-corpus inference failed") from error
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "format_version": 1, "custom_id": phase["custom_id"],
        "request_sha256": request_hash, "response_id": response_id,
        "assessment": assessment,
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    temporary.replace(path)
    return assessment, False


def validate_media(manifest: dict[str, Any], source_dir: Path, work_root: Path,
                   paid_callback: Callable[[dict[str, Any], int, dict[str, Any],
                                            dict[str, Any], dict[str, Any]], None] | None = None) -> None:
    contract, _receipt = current_contract_receipt()
    sources = manifest["sources"]
    cases_by_source: dict[str, list[dict[str, Any]]] = {}
    for case in manifest["cases"]:
        cases_by_source.setdefault(case["source_id"], []).append(case)
    discovered = discover_sources(source_dir, sources)
    work_root.mkdir(parents=True, exist_ok=True)
    decoded_total = 0
    validated_phases = 0
    with tempfile.TemporaryDirectory(prefix="media-", dir=work_root) as temporary:
        temporary_root = Path(temporary)
        for source in sources:
            source_id = source["source_id"]
            path = discovered[source_id]
            verify_source(path, source)
            source_cases = cases_by_source.get(source_id, [])
            if not source_cases:
                decode_without_retaining(path)
                decoded_total += source["frames"]
                continue
            frame_dir = temporary_root / source_id
            pts = decode_to_jpegs(path, frame_dir, source["frames"])
            decoded_total += len(pts)
            for case in source_cases:
                for phase_index, phase in enumerate(case["phases"]):
                    fixture, request = validate_phase(
                        case, phase_index, phase, source, frame_dir, pts, contract)
                    validated_phases += 1
                    if paid_callback:
                        paid_callback(case, phase_index, phase, fixture, request)
            shutil.rmtree(frame_dir)
    print(f"Verified {len(sources)} exact videos, decoded {decoded_total} frames, "
          f"and rebuilt {validated_phases} curated full-frame phases")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-dir", type=Path,
                        help="directory searched recursively for exact videos by SHA-256")
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-manifest", action="store_true",
                      help="source-free, API-free manifest and production-contract check")
    mode.add_argument("--validate-only", action="store_true",
                      help="verify and fully decode the private media; no API calls")
    mode.add_argument("--paid-run", action="store_true",
                      help="run the curated cases through the production detector")
    parser.add_argument("--max-calls", type=int,
                        help="required paid-call ceiling; must cover every selected phase")
    parser.add_argument("--api-timeout", type=int, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    manifest = load_manifest(args.manifest)
    if args.check_manifest:
        if args.max_calls is not None:
            raise CorpusError("--max-calls is only valid with --paid-run")
        print("PRIVATE DRIVE CORPUS MANIFEST PASS (NO MEDIA OR API)")
        return 0
    if args.source_dir is None:
        raise CorpusError("--source-dir is required for media validation")
    if args.api_timeout <= 0:
        raise CorpusError("--api-timeout must be positive")
    if args.validate_only:
        if args.max_calls is not None:
            raise CorpusError("--max-calls is only valid with --paid-run")
        validate_media(manifest, args.source_dir, args.work_root)
        print("PRIVATE DRIVE CORPUS MEDIA PASS (API NOT RUN)")
        return 0

    phase_total = sum(len(case["phases"]) for case in manifest["cases"])
    if args.max_calls is None or args.max_calls < 1:
        raise CorpusError("--paid-run requires a positive --max-calls ceiling")
    if phase_total > args.max_calls:
        raise CorpusError(
            f"paid run needs {phase_total} calls but --max-calls is {args.max_calls}")
    try:
        api_key = release_gate.load_api_key()
    except release_gate.GateError as error:
        raise CorpusError("OPENAI_API_KEY is required for the paid corpus gate") from error
    paid_contract, _receipt = current_contract_receipt()
    failures: list[str] = []
    completed = cached_count = 0

    def run_phase(case: dict[str, Any], phase_index: int, phase: dict[str, Any],
                  _fixture: dict[str, Any], request: dict[str, Any]) -> None:
        nonlocal completed, cached_count
        assessment, cached = cached_or_fresh_assessment(
            args.work_root, phase, request, paid_contract,
            api_key, args.api_timeout)
        actual = production_eval.decision(assessment, "drive", source_view_count=3)
        expected = "accept" if case["expected_is_pothole"] else "reject"
        completed += 1
        cached_count += int(cached)
        status = "PASS" if actual == expected else "FAIL"
        print(f"{status} {case['id']} phase={phase_index} expected={expected} actual={actual}"
              + (" cached" if cached else ""))
        if actual != expected:
            failures.append(f"{case['id']} phase {phase_index}")

    validate_media(manifest, args.source_dir, args.work_root, run_phase)
    if failures:
        raise CorpusError(
            f"private drive corpus blocked: {len(failures)} of {completed} decisions failed; "
            + ", ".join(failures))
    print(f"PRIVATE DRIVE CORPUS GATE PASS ({completed} decisions, {cached_count} resumed)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CorpusError as error:
        print(f"PRIVATE DRIVE CORPUS FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
