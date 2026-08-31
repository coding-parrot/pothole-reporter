#!/usr/bin/env python3
"""Exhaustively replay two exact private Drive videos at production VOD cadence.

Private MP4s and generated JPEGs stay below an ignored work root.  The committed source
receipt contains no absolute path.  Owner truth and independent assistant annotations live
in a second manifest and are never inferred from detector output.  Network calls require
``--paid-run`` plus an explicit call ceiling; completed exact-request results can be resumed.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

import historical_detection_contracts as historical_contracts
import private_drive_corpus as media
import private_release_gate as release_gate
import run_eval as production_eval


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_MANIFEST = ROOT / "eval" / "exhaustive_video_sources.json"
DEFAULT_LABEL_MANIFEST = ROOT / "eval" / "exhaustive_video_visual_labels.json"
DEFAULT_AUDIT_RECEIPT = ROOT / "eval" / "exhaustive_video_audit.json"
DEFAULT_RESULTS_RECEIPT = ROOT / "eval" / "exhaustive_video_results.json"
# This parent is already ignored.  Keep all source-derived pixels and API output below it.
DEFAULT_WORK_ROOT = media.DEFAULT_WORK_ROOT / "downloads-exhaustive"
SOURCE_SCHEMA = "private-exhaustive-video-sources-v1"
LABEL_SCHEMA = "private-exhaustive-video-labels-v2"
DATASET_SCHEMA = "private-exhaustive-video-materialized-v2"
WINDOW_SCHEMA = "private-exhaustive-video-window-v1"
LABEL_BINDING_SCHEMA = "private-exhaustive-video-label-binding-v1"
RESULT_SCHEMA = "private-exhaustive-video-result-v2"
PUBLIC_RESULTS_SCHEMA = "private-exhaustive-video-results-v1"
PUBLIC_RESULT_ROW_SCHEMA = "private-exhaustive-video-result-receipt-row-v1"
AUDIT_SCHEMA = "private-exhaustive-video-audit-v2"
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_ASSISTANT_REGRESSION_WINDOW_IDS = frozenset({
    "downloads-segment-0002--w00073--c0036900",
    "downloads-segment-0002--w00074--c0037400",
    "downloads-segment-0002--w00075--c0037900",
})


class ExhaustiveEvalError(RuntimeError):
    """A fail-closed, user-actionable exhaustive-evaluation error."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return media.sha256_file(path)


def _exact_object(value: Any, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExhaustiveEvalError(f"{where} must be an object")
    if set(value) != keys:
        missing = sorted(keys - set(value))
        extra = sorted(set(value) - keys)
        raise ExhaustiveEvalError(
            f"{where} keys mismatch (missing={missing}, extra={extra})")
    return value


def _finite(value: Any) -> bool:
    return type(value) in {int, float} and float("-inf") < value < float("inf")


def _read_json(path: Path, description: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise ExhaustiveEvalError(f"cannot read {description}: {error}") from error
    if not isinstance(value, dict):
        raise ExhaustiveEvalError(f"{description} root must be an object")
    return value, raw


def reject_private_locator_strings(value: Any) -> None:
    """Reject local-file locators, including common forms Path.is_absolute misses."""
    if isinstance(value, dict):
        for item in value.values():
            reject_private_locator_strings(item)
        return
    if isinstance(value, list):
        for item in value:
            reject_private_locator_strings(item)
        return
    if not isinstance(value, str):
        return
    stripped = value.strip()
    if (re.search(r"(?i)(?<![A-Za-z0-9_])file:", value)
            or re.search(r"(?<![A-Za-z0-9_])~[\\/]", value)
            or re.search(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]", value)
            or re.search(r"(?<![A-Za-z0-9_])\\\\[^\\\s]", value)
            or re.search(r"(?<![A-Za-z0-9_\\])\\[^\\\s]+\\", value)
            or re.search(r"(?<![A-Za-z0-9_:/])//[^/\s]", value)
            or re.search(r"(?<![A-Za-z0-9_./-])/(?!/)[^\s]", value)
            or stripped.startswith(("/", "~/", "~\\", "\\\\"))):
        raise ExhaustiveEvalError("committed exhaustive metadata contains a private locator")


def current_contract() -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        return media.current_contract_receipt()
    except (media.CorpusError, release_gate.GateError) as error:
        raise ExhaustiveEvalError("production detector contract is unavailable or drifted") from error


def historical_v15_contract() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the sealed archive contract; never selected by current execution."""
    try:
        return (historical_contracts.v15_contract(),
                historical_contracts.v15_contract_receipt())
    except historical_contracts.HistoricalContractError as error:
        raise ExhaustiveEvalError("historical v15 contract seal is invalid") from error


def _validate_source_manifest_for_receipt(
    value: Any, expected_receipt: dict[str, Any], archive: bool,
) -> dict[str, Any]:
    manifest = _exact_object(value, {
        "schema_version", "suite_id", "description", "privacy", "sampling",
        "production_contract", "inventory", "sources",
    }, "source manifest")
    if manifest["schema_version"] != SOURCE_SCHEMA:
        raise ExhaustiveEvalError("unsupported exhaustive source-manifest schema")
    try:
        media.reject_absolute_paths(manifest)
        reject_private_locator_strings(manifest)
    except media.CorpusError as error:
        raise ExhaustiveEvalError(str(error)) from error
    if not isinstance(manifest["suite_id"], str) or not manifest["suite_id"]:
        raise ExhaustiveEvalError("source manifest has no suite ID")
    if not isinstance(manifest["description"], str) or not manifest["description"].strip():
        raise ExhaustiveEvalError("source manifest has no description")

    privacy = _exact_object(manifest["privacy"], {
        "media_committed", "absolute_paths_stored", "capture_provenance",
        "raw_camerax_accuracy_eligible",
        "production_detector_output_may_create_annotations",
    }, "source privacy policy")
    if (privacy["media_committed"] is not False
            or privacy["absolute_paths_stored"] is not False
            or privacy["capture_provenance"] != "native_mediarecorder_reconstruction"
            or privacy["raw_camerax_accuracy_eligible"] is not False
            or privacy["production_detector_output_may_create_annotations"] is not False):
        raise ExhaustiveEvalError("source privacy/provenance policy is unsafe")

    sampling = _exact_object(manifest["sampling"], {
        "center_start_seconds", "center_step_seconds", "burst_half_span_seconds",
        "boundary_epsilon_seconds", "dedupe_tolerance_seconds", "sample_function",
        "burst_function", "frame_selection",
    }, "sampling policy")
    if (sampling["center_start_seconds"] != 0.4
            or sampling["center_step_seconds"] != 0.5
            or sampling["burst_half_span_seconds"] != 0.4
            or sampling["boundary_epsilon_seconds"] != 0.001
            or sampling["dedupe_tolerance_seconds"] != 0.04
            or sampling["sample_function"] != "static/standalone.js:vodSampleTimes"
            or sampling["burst_function"] != "static/standalone.js:vodBurstTimes"):
        raise ExhaustiveEvalError("sampling is not the exact production VOD policy")
    if "first decoded presentation frame" not in str(sampling["frame_selection"]):
        raise ExhaustiveEvalError("frame-selection semantics are not explicit")

    if manifest["production_contract"] != expected_receipt:
        target = "historical v15 archive" if archive else "production"
        raise ExhaustiveEvalError(f"committed source receipt has drifted from {target}")

    sources = manifest["sources"]
    if not isinstance(sources, list) or len(sources) != 2:
        raise ExhaustiveEvalError("the exact Downloads suite must contain two sources")
    source_ids: set[str] = set()
    source_hashes: set[str] = set()
    source_keys = {
        "source_id", "expected_basename", "sha256", "codec", "coded_width",
        "coded_height", "presented_width", "presented_height", "rotation_degrees",
        "frames", "avg_frame_rate", "r_frame_rate", "duration_seconds", "bytes",
    }
    for index, source_value in enumerate(sources):
        source = _exact_object(source_value, source_keys, f"source {index}")
        source_id = source["source_id"]
        fingerprint = source["sha256"]
        if (not isinstance(source_id, str) or not source_id or source_id in source_ids
                or not isinstance(source["expected_basename"], str)
                or not source["expected_basename"].endswith(".mp4")):
            raise ExhaustiveEvalError(f"source {index} has invalid identity metadata")
        if (not isinstance(fingerprint, str) or not HEX_64.fullmatch(fingerprint)
                or fingerprint in source_hashes):
            raise ExhaustiveEvalError(f"source {source_id} has an invalid or duplicate hash")
        if source["codec"] != "h264":
            raise ExhaustiveEvalError(f"source {source_id} is not the pinned H.264 stream")
        if any(type(source[field]) is not int or source[field] <= 0 for field in (
                "coded_width", "coded_height", "presented_width", "presented_height",
                "frames", "bytes")):
            raise ExhaustiveEvalError(f"source {source_id} has invalid integer metadata")
        if (not _finite(source["rotation_degrees"])
                or not _finite(source["duration_seconds"])
                or source["duration_seconds"] <= 0
                or not all(isinstance(source[field], str) and source[field] for field in (
                    "avg_frame_rate", "r_frame_rate"))):
            raise ExhaustiveEvalError(f"source {source_id} has invalid timing metadata")
        source_ids.add(source_id)
        source_hashes.add(fingerprint)

    inventory = _exact_object(manifest["inventory"], {
        "source_count", "total_bytes", "total_frames", "total_duration_seconds",
        "expected_windows", "expected_frame_references", "source_inventory_sha256",
    }, "source inventory")
    expected_windows = sum(len(media.vod_sample_times(
        source["duration_seconds"], sampling["center_step_seconds"])) for source in sources)
    if (inventory["source_count"] != len(sources)
            or inventory["total_bytes"] != sum(source["bytes"] for source in sources)
            or inventory["total_frames"] != sum(source["frames"] for source in sources)
            or abs(inventory["total_duration_seconds"]
                   - sum(source["duration_seconds"] for source in sources)) > 1e-6
            or inventory["expected_windows"] != expected_windows
            or inventory["expected_frame_references"] != expected_windows * 3
            or inventory["source_inventory_sha256"] != media.source_inventory_sha256(sources)):
        raise ExhaustiveEvalError("source inventory counts or seal do not match")
    return manifest


def validate_source_manifest(value: Any) -> dict[str, Any]:
    """Validate only for current execution; archived v15 is rejected."""
    _contract, receipt = current_contract()
    return _validate_source_manifest_for_receipt(value, receipt, archive=False)


def validate_historical_v15_source_manifest(value: Any) -> dict[str, Any]:
    """Authenticate only the immutable v15 source archive."""
    _contract, receipt = historical_v15_contract()
    return _validate_source_manifest_for_receipt(value, receipt, archive=True)


def load_source_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    value, raw = _read_json(path, "exhaustive source manifest")
    return validate_source_manifest(value), raw


def load_historical_v15_source_manifest(
    path: Path = DEFAULT_SOURCE_MANIFEST,
) -> tuple[dict[str, Any], bytes]:
    value, raw = _read_json(path, "historical v15 exhaustive source manifest")
    return validate_historical_v15_source_manifest(value), raw


def validate_label_manifest(value: Any, sources: dict[str, Any]) -> dict[str, Any]:
    labels = _exact_object(value, {
        "schema_version", "suite_id", "policy", "reviews", "events", "window_ranges",
    }, "visual-label manifest")
    if labels["schema_version"] != LABEL_SCHEMA:
        raise ExhaustiveEvalError("unsupported visual-label schema")
    try:
        media.reject_absolute_paths(labels)
        reject_private_locator_strings(labels)
    except media.CorpusError as error:
        raise ExhaustiveEvalError(str(error)) from error
    policy = _exact_object(labels["policy"], {
        "owner_event_labels_are_human_confirmed",
        "window_annotations_are_independent_assistant_review",
        "assistant_candidate_events_are_ground_truth",
        "assistant_window_annotations_are_ground_truth",
        "production_detector_output_used_as_annotation",
        "unlabelled_windows_are_not_negatives", "metric_unit", "event_detection_rule",
    }, "visual-label policy")
    if (policy["owner_event_labels_are_human_confirmed"] is not True
            or policy["window_annotations_are_independent_assistant_review"] is not True
            or policy["assistant_candidate_events_are_ground_truth"] is not False
            or policy["assistant_window_annotations_are_ground_truth"] is not False
            or policy["production_detector_output_used_as_annotation"] is not False
            or policy["unlabelled_windows_are_not_negatives"] is not True
            or policy["metric_unit"] != "physical_event"
            or "at least one explicitly bound" not in
            str(policy["event_detection_rule"]).lower()):
        raise ExhaustiveEvalError("visual-label policy could turn model output into truth")

    source_window_counts = {
        source_id: len(media.vod_sample_times(source["duration_seconds"], 0.5))
        for source_id, source in sources.items()
    }
    review_keys = {
        "source_id", "source_sha256", "all_source_frames_decoded", "decoded_frames",
        "production_windows_reviewed", "whole_video_review_fps", "focused_review_ranges",
        "whole_frame_only", "spatial_crop_tile_mask_or_roi",
        "distinct_owner_confirmed_potholes", "distinct_assistant_candidate_potholes",
        "positive_windows", "negative_windows", "abstain_windows", "review_provenance",
    }
    reviews = labels["reviews"]
    if not isinstance(reviews, list) or len(reviews) != len(sources):
        raise ExhaustiveEvalError("every exact source must have one complete assistant review")
    review_by_source: dict[str, dict[str, Any]] = {}
    for index, raw_review in enumerate(reviews):
        review = _exact_object(raw_review, review_keys, f"source review {index}")
        source_id = review["source_id"]
        source = sources.get(source_id)
        if source is None or source_id in review_by_source:
            raise ExhaustiveEvalError(f"source review {index} has invalid identity")
        if (review["source_sha256"] != source["sha256"]
                or review["all_source_frames_decoded"] is not True
                or review["decoded_frames"] != source["frames"]
                or review["production_windows_reviewed"] != source_window_counts[source_id]
                or not _finite(review["whole_video_review_fps"])
                or review["whole_video_review_fps"] < 4
                or review["whole_frame_only"] is not True
                or review["spatial_crop_tile_mask_or_roi"] is not False
                or review["review_provenance"]
                != "independent_assistant_full_video_review"):
            raise ExhaustiveEvalError(f"source review {source_id} is incomplete or spatially altered")
        for field in ("distinct_owner_confirmed_potholes",
                      "distinct_assistant_candidate_potholes", "positive_windows",
                      "negative_windows", "abstain_windows"):
            if type(review[field]) is not int or review[field] < 0:
                raise ExhaustiveEvalError(f"source review {source_id} has invalid counts")
        focus_ranges = review["focused_review_ranges"]
        if not isinstance(focus_ranges, list) or not focus_ranges:
            raise ExhaustiveEvalError(f"source review {source_id} has no focused review")
        for focus_index, raw_focus in enumerate(focus_ranges):
            focus = _exact_object(raw_focus, {"interval_seconds", "fps"},
                                  f"source review {source_id} focus {focus_index}")
            interval = focus["interval_seconds"]
            if (not isinstance(interval, list) or len(interval) != 2
                    or any(not _finite(item) for item in interval)
                    or interval[0] < 0 or interval[0] >= interval[1]
                    or interval[1] > source["duration_seconds"]
                    or not _finite(focus["fps"]) or focus["fps"] < 10):
                raise ExhaustiveEvalError(f"source review {source_id} has invalid focus coverage")
        review_by_source[source_id] = review
    if set(review_by_source) != set(sources):
        raise ExhaustiveEvalError("source review coverage is incomplete")

    events = labels["events"]
    if not isinstance(events, list):
        raise ExhaustiveEvalError("visual-label events must be an array")
    event_keys = {
        "event_id", "source_id", "source_sha256", "source_interval_seconds", "label",
        "evidence_tier", "label_provenance", "review_kind",
        "origin_manifest", "origin_event_id",
        "capture_provenance", "raw_camerax_accuracy_eligible",
    }
    seen: set[str] = set()
    origin_cache: dict[str, dict[str, Any]] = {}
    for index, raw_event in enumerate(events):
        event = _exact_object(raw_event, event_keys, f"visual event {index}")
        event_id = event["event_id"]
        source = sources.get(event["source_id"])
        if not isinstance(event_id, str) or not event_id or event_id in seen or source is None:
            raise ExhaustiveEvalError(f"visual event {index} has invalid identity")
        if event["source_sha256"] != source["sha256"]:
            raise ExhaustiveEvalError(f"visual event {event_id} is bound to the wrong source")
        interval = event["source_interval_seconds"]
        if (not isinstance(interval, list) or len(interval) != 2
                or any(not _finite(item) for item in interval)
                or interval[0] < 0 or interval[0] >= interval[1]
                or interval[1] > source["duration_seconds"]):
            raise ExhaustiveEvalError(f"visual event {event_id} has an invalid interval")
        if (event["label"] != "pothole"
                or event["capture_provenance"] != "native_mediarecorder_reconstruction"
                or event["raw_camerax_accuracy_eligible"] is not False):
            raise ExhaustiveEvalError(f"visual event {event_id} has invalid event semantics")
        if event["evidence_tier"] == "owner_ground_truth":
            if (event["label_provenance"] != "owner_confirmed"
                    or event["review_kind"] != "human_visual_review"):
                raise ExhaustiveEvalError(
                    f"visual event {event_id} overstates owner ground truth")
            origin_path = event["origin_manifest"]
            origin_parts = Path(origin_path).parts if isinstance(origin_path, str) else ()
            if (not isinstance(origin_path, str) or not origin_path.startswith("eval/")
                    or ".." in origin_parts
                    or not isinstance(event["origin_event_id"], str)):
                raise ExhaustiveEvalError(f"visual event {event_id} has invalid origin metadata")
            if origin_path not in origin_cache:
                origin_value, _ = _read_json(ROOT / origin_path, "visual-label origin manifest")
                origin_cache[origin_path] = {
                    item.get("event_id"): item for item in origin_value.get("images", [])
                    if isinstance(item, dict) and item.get("event_id")
                }
            origin = origin_cache[origin_path].get(event["origin_event_id"])
            if (not origin or origin.get("label") != event["label"]
                    or origin.get("labelled_by") != "owner"
                    or origin.get("source_interval_seconds") != interval):
                raise ExhaustiveEvalError(
                    f"visual event {event_id} does not match its owner label")
        elif event["evidence_tier"] == "assistant_candidate":
            if (event["label_provenance"] != "independent_assistant_candidate"
                    or event["review_kind"] != "independent_assistant_full_video_review"
                    or event["origin_manifest"] is not None
                    or event["origin_event_id"] is not None):
                raise ExhaustiveEvalError(
                    f"visual event {event_id} incorrectly claims human confirmation")
        else:
            raise ExhaustiveEvalError(f"visual event {event_id} has an unknown evidence tier")
        seen.add(event_id)

    range_keys = {
        "source_id", "window_indices_inclusive", "visual_label", "expected_decision",
        "semantic_class", "label_provenance", "physical_event_or_hard_negative_group_id",
        "uncertainty_reason",
    }
    ranges = labels["window_ranges"]
    if not isinstance(ranges, list) or not ranges:
        raise ExhaustiveEvalError("complete per-window assistant review ranges are required")
    covered: dict[str, list[int | None]] = {
        source_id: [None] * count for source_id, count in source_window_counts.items()
    }
    range_counts = {
        source_id: {"pothole": 0, "not_pothole": 0, "abstain": 0}
        for source_id in sources
    }
    event_window_groups: set[str] = set()
    event_by_id = {event["event_id"]: event for event in events}
    for range_index, raw_range in enumerate(ranges):
        item = _exact_object(raw_range, range_keys, f"window range {range_index}")
        source_id = item["source_id"]
        indices = item["window_indices_inclusive"]
        if (source_id not in sources or not isinstance(indices, list) or len(indices) != 2
                or any(type(value) is not int for value in indices)
                or indices[0] < 0 or indices[0] > indices[1]
                or indices[1] >= source_window_counts[source_id]):
            raise ExhaustiveEvalError(f"window range {range_index} has invalid bounds")
        label = item["visual_label"]
        expected = item["expected_decision"]
        if ((label == "pothole" and expected != "accept")
                or (label == "not_pothole" and expected != "reject")
                or (label is None and expected is not None)
                or label not in {"pothole", "not_pothole", None}):
            raise ExhaustiveEvalError(f"window range {range_index} has inconsistent truth")
        if not all(isinstance(item[field], str) and item[field].strip() for field in (
                "semantic_class", "label_provenance",
                "physical_event_or_hard_negative_group_id")):
            raise ExhaustiveEvalError(f"window range {range_index} is missing review provenance")
        positive_event = event_by_id.get(item["physical_event_or_hard_negative_group_id"])
        expected_provenance = "independent_assistant_full_video_review"
        if label == "pothole" and positive_event is not None:
            expected_provenance = (
                "owner_confirmed_event_with_independent_assistant_window_extent"
                if positive_event["evidence_tier"] == "owner_ground_truth"
                else "independent_assistant_candidate_event")
        if item["label_provenance"] != expected_provenance:
            raise ExhaustiveEvalError(
                f"window range {range_index} overstates its review provenance")
        if ((label is None and (not isinstance(item["uncertainty_reason"], str)
                                or not item["uncertainty_reason"].strip()))
                or (label is not None and item["uncertainty_reason"] is not None)):
            raise ExhaustiveEvalError(f"window range {range_index} mishandles uncertainty")
        if label != "pothole" and positive_event is not None:
            raise ExhaustiveEvalError(
                f"window range {range_index} reuses an event ID for a non-positive window")
        for window_index in range(indices[0], indices[1] + 1):
            if covered[source_id][window_index] is not None:
                raise ExhaustiveEvalError(
                    f"window range {range_index} overlaps {source_id} window {window_index}")
            covered[source_id][window_index] = range_index
        count_key = label if label is not None else "abstain"
        range_counts[source_id][count_key] += indices[1] - indices[0] + 1
        if label == "pothole":
            if (positive_event is None or positive_event["source_id"] != source_id
                    or positive_event["label"] != "pothole"):
                raise ExhaustiveEvalError(
                    f"window range {range_index} is not bound to a reviewed pothole event")
            source = sources[source_id]
            centers = media.vod_sample_times(source["duration_seconds"], 0.5)
            event_start, event_end = positive_event["source_interval_seconds"]
            for window_index in range(indices[0], indices[1] + 1):
                burst = media.vod_burst_times(
                    centers[window_index], source["duration_seconds"], 0.4)
                if burst[-1] < event_start or burst[0] > event_end:
                    raise ExhaustiveEvalError(
                        f"window range {range_index} does not overlap its event interval")
            event_window_groups.add(item["physical_event_or_hard_negative_group_id"])

    for source_id, assignments in covered.items():
        if any(item is None for item in assignments):
            raise ExhaustiveEvalError(f"assistant review omits windows from {source_id}")
        review = review_by_source[source_id]
        counts = range_counts[source_id]
        if (counts["pothole"] != review["positive_windows"]
                or counts["not_pothole"] != review["negative_windows"]
                or counts["abstain"] != review["abstain_windows"]
                or sum(counts.values()) != review["production_windows_reviewed"]):
            raise ExhaustiveEvalError(f"assistant review counts drifted for {source_id}")
        owner_count = sum(event["source_id"] == source_id
                          and event["evidence_tier"] == "owner_ground_truth"
                          for event in events)
        candidate_count = sum(event["source_id"] == source_id
                              and event["evidence_tier"] == "assistant_candidate"
                              for event in events)
        if (review["distinct_owner_confirmed_potholes"] != owner_count
                or review["distinct_assistant_candidate_potholes"] != candidate_count):
            raise ExhaustiveEvalError(f"physical-event evidence counts drifted for {source_id}")
    if event_window_groups != seen:
        raise ExhaustiveEvalError(
            "positive window ranges do not bind every reviewed pothole event exactly")
    return labels


def load_label_manifest(path: Path, source_manifest: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    value, raw = _read_json(path, "exhaustive visual-label manifest")
    if value.get("suite_id") != source_manifest["suite_id"]:
        raise ExhaustiveEvalError("source and visual-label suite IDs differ")
    sources = {source["source_id"]: source for source in source_manifest["sources"]}
    return validate_label_manifest(value, sources), raw


def suite_identity(source_raw: bytes, label_raw: bytes,
                   contract_receipt: dict[str, Any]) -> str:
    """Seal the immutable manifests and production contract, but not decoded pixels."""
    return sha256_bytes(canonical_json({
        "identity_schema": "private-exhaustive-video-suite-identity-v1",
        "source_manifest_sha256": sha256_bytes(source_raw),
        "visual_labels_sha256": sha256_bytes(label_raw),
        "production_contract": contract_receipt,
    }).encode())


def materialized_identity(suite_id: str, windows_sha256: str,
                          bindings_sha256: str) -> str:
    """Seal the exact materialized full-frame/request index and event bindings."""
    if (not HEX_64.fullmatch(suite_id) or not HEX_64.fullmatch(windows_sha256)
            or not HEX_64.fullmatch(bindings_sha256)):
        raise ExhaustiveEvalError("materialized identity inputs must be SHA-256 values")
    return sha256_bytes(canonical_json({
        "identity_schema": "private-exhaustive-video-materialized-identity-v1",
        "suite_identity": suite_id,
        "materialized_windows_sha256": windows_sha256,
        "event_bindings_sha256": bindings_sha256,
    }).encode())


def expected_dataset_root(work_root: Path, identity: str) -> Path:
    return work_root / "datasets" / identity


def _nearest_existing(path: Path) -> Path:
    candidate = path.expanduser().resolve(strict=False)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate.parent if candidate.is_file() else candidate


def require_private_artifact_root(path: Path, purpose: str) -> Path:
    """Allow private artifacts outside Git, or only under a tracked ignore rule."""
    resolved = path.expanduser().resolve(strict=False)
    if resolved == resolved.parent:
        raise ExhaustiveEvalError(f"{purpose} cannot be a filesystem root")
    existing = _nearest_existing(resolved)
    probe = subprocess.run(
        ["git", "-C", str(existing), "rev-parse", "--show-toplevel"],
        text=True, capture_output=True)
    if probe.returncode != 0:
        return resolved
    git_root = Path(probe.stdout.strip()).resolve()
    try:
        relative = resolved.relative_to(git_root)
    except ValueError:
        return resolved
    if not relative.parts:
        raise ExhaustiveEvalError(f"{purpose} cannot be a Git worktree root")

    ignored = subprocess.run(
        ["git", "-C", str(git_root), "check-ignore", "--no-index", "-v", "--",
         relative.as_posix()], text=True, capture_output=True)
    if ignored.returncode != 0 or "\t" not in ignored.stdout:
        raise ExhaustiveEvalError(
            f"{purpose} is inside Git but is not protected by a tracked .gitignore: {relative}")
    rule_description = ignored.stdout.split("\t", 1)[0]
    rule_parts = rule_description.rsplit(":", 2)
    if len(rule_parts) != 3 or not rule_parts[1].isdigit():
        raise ExhaustiveEvalError(f"cannot verify the ignore rule protecting {purpose}")
    rule_source, rule_line, rule_pattern = rule_parts
    source_path = Path(rule_source)
    if source_path.is_absolute():
        try:
            source_relative = source_path.resolve().relative_to(git_root)
        except ValueError as error:
            raise ExhaustiveEvalError(
                f"{purpose} is ignored only by a non-repository rule") from error
    else:
        source_relative = source_path
    if source_relative.name != ".gitignore" or ".git" in source_relative.parts:
        raise ExhaustiveEvalError(
            f"{purpose} is ignored only by an untracked, local, or global rule")
    committed_rule = subprocess.run(
        ["git", "-C", str(git_root), "show", f"HEAD:{source_relative.as_posix()}"],
        text=True, capture_output=True)
    committed_lines = committed_rule.stdout.splitlines()
    line_index = int(rule_line) - 1
    if (committed_rule.returncode != 0 or not 0 <= line_index < len(committed_lines)
            or committed_lines[line_index] != rule_pattern):
        raise ExhaustiveEvalError(
            f"{purpose} is not protected by the committed .gitignore rule")
    tracked_content = subprocess.run(
        ["git", "-C", str(git_root), "ls-files", "-z", "--", relative.as_posix()],
        capture_output=True)
    if tracked_content.returncode != 0:
        raise ExhaustiveEvalError(f"cannot verify the Git privacy boundary for {purpose}")
    if tracked_content.stdout:
        raise ExhaustiveEvalError(f"{purpose} already contains tracked repository content")
    return resolved


def _paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = left.resolve(strict=False)
    right_resolved = right.resolve(strict=False)
    try:
        left_resolved.relative_to(right_resolved)
        return True
    except ValueError:
        pass
    try:
        right_resolved.relative_to(left_resolved)
        return True
    except ValueError:
        return False


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return ("".join(canonical_json(record) + "\n" for record in records)).encode()


def parse_source_mappings(values: list[str]) -> dict[str, Path]:
    mappings: dict[str, Path] = {}
    for value in values:
        source_id, separator, path = value.partition("=")
        if not separator or not source_id or not path or source_id in mappings:
            raise ExhaustiveEvalError("--source must be a unique SOURCE_ID=PATH mapping")
        mappings[source_id] = Path(path).expanduser()
    return mappings


def resolve_sources(manifest: dict[str, Any], source_dir: Path | None,
                    mappings: dict[str, Path]) -> dict[str, Path]:
    sources = manifest["sources"]
    known = {source["source_id"] for source in sources}
    unknown = sorted(set(mappings) - known)
    if unknown:
        raise ExhaustiveEvalError("unknown source mapping(s): " + ", ".join(unknown))
    missing_sources = [source for source in sources if source["source_id"] not in mappings]
    if missing_sources and source_dir is not None:
        try:
            discovered = media.discover_sources(source_dir, missing_sources)
        except media.CorpusError as error:
            raise ExhaustiveEvalError(str(error)) from error
        mappings.update(discovered)
    missing = sorted(known - set(mappings))
    if missing:
        raise ExhaustiveEvalError("missing private source(s): " + ", ".join(missing))
    for source in sources:
        try:
            media.verify_source(mappings[source["source_id"]], source)
        except media.CorpusError as error:
            raise ExhaustiveEvalError(str(error)) from error
    return mappings


def select_frame(pts: list[float], requested: float) -> tuple[int, float, bool]:
    if not pts:
        raise ExhaustiveEvalError("decoded source has no frame PTS values")
    selected = bisect.bisect_left(pts, requested)
    clamped = selected >= len(pts)
    selected = min(selected, len(pts) - 1)
    return selected, pts[selected], clamped


def build_production_request(frame_paths: list[Path], contract: dict[str, Any]
                             ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if len(frame_paths) != 3:
        raise ExhaustiveEvalError("a production VOD window must have exactly three frames")
    entry = {
        "path": str(frame_paths[1]),
        "frames": [str(path) for path in frame_paths],
        "primary_index": 1,
        "mode": "drive",
    }
    views, transforms, note = production_eval.prepare_event(entry, Path("/"), "drive")
    prompt = production_eval.effective_prompt(contract["prompt"], "drive", note)
    request = production_eval.build_request(
        views, prompt, contract["model"], contract["detail"], mode="drive")
    images = [item for item in request["input"][0]["content"]
              if item.get("type") == "input_image"]
    if (len(images) != 4 or len(transforms) != 4
            or any(item.get("detail") != contract["detail"] for item in images)
            or any(transform.get("full_frame") is not True for transform in transforms)
            or [transform.get("role") for transform in transforms]
            != ["primary_context"] + ["chronological_full_frame"] * 3
            or request.get("model") != contract["model"]
            or request.get("reasoning") != {"effort": "low"}
            or request.get("store") is not False
            or request.get("max_output_tokens") != contract["max_output_tokens"]
            or request.get("text", {}).get("format", {}).get("schema") != contract["schema"]):
        raise ExhaustiveEvalError("generated request is not the production full-frame contract")
    return request, transforms


def _validate_jpeg(path: Path, source: dict[str, Any]) -> tuple[int, str]:
    try:
        raw = path.read_bytes()
        with Image.open(path) as image:
            size, image_format = image.size, image.format
            image.verify()
    except (OSError, UnidentifiedImageError) as error:
        raise ExhaustiveEvalError(f"invalid extracted JPEG: {path.name}") from error
    if image_format != "JPEG" or size != (
            source["presented_width"], source["presented_height"]):
        raise ExhaustiveEvalError("extracted frame is not the complete presented video frame")
    return len(raw), sha256_bytes(raw)


def _validate_frame_record_metadata(frame: Any, ordinal: int, requested_time: float,
                                    source_id: str, source: dict[str, Any],
                                    capture_provenance: str) -> tuple[int, float]:
    frame = _exact_object(frame, {
        "ordinal", "requested_timestamp_seconds", "selected_frame_index",
        "selected_pts_seconds", "duration_boundary_clamped", "path", "sha256", "bytes",
        "width", "height", "format", "full_frame", "source_sha256",
        "capture_provenance",
    }, "materialized frame record")
    selected_index = frame.get("selected_frame_index") if isinstance(frame, dict) else None
    selected_pts = frame.get("selected_pts_seconds") if isinstance(frame, dict) else None
    clamped = frame.get("duration_boundary_clamped") if isinstance(frame, dict) else None
    expected_path = (f"sources/{source_id}/frames/f{selected_index:05d}.jpg"
                     if type(selected_index) is int and selected_index >= 0 else None)
    if (not isinstance(frame, dict) or frame.get("ordinal") != ordinal
            or abs(frame.get("requested_timestamp_seconds", -1) - requested_time) > 1e-9
            or type(selected_index) is not int
            or not 0 <= selected_index < source["frames"]
            or not _finite(selected_pts)
            or not 0 <= selected_pts < source["duration_seconds"]
            or type(clamped) is not bool
            or frame.get("path") != expected_path
            or (clamped and (selected_index != source["frames"] - 1
                             or selected_pts + 1e-9 >= requested_time))
            or (not clamped and selected_pts + 1e-9 < requested_time)
            or frame.get("width") != source["presented_width"]
            or frame.get("height") != source["presented_height"]
            or frame.get("format") != "JPEG" or frame.get("full_frame") is not True
            or frame.get("source_sha256") != source["sha256"]
            or frame.get("capture_provenance") != capture_provenance):
        raise ExhaustiveEvalError("materialized frame selection metadata drifted")
    return selected_index, selected_pts


def _window_visual_label_map(labels: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    """Expand the reviewed ranges without changing their human provenance."""
    expanded: dict[tuple[str, int], dict[str, Any]] = {}
    for item in labels["window_ranges"]:
        start, end = item["window_indices_inclusive"]
        visual_label = {
            "label": item["visual_label"],
            "expected_decision": item["expected_decision"],
            "semantic_class": item["semantic_class"],
            "label_provenance": item["label_provenance"],
            "physical_event_or_hard_negative_group_id":
                item["physical_event_or_hard_negative_group_id"],
            "uncertainty_reason": item["uncertainty_reason"],
            "production_detector_output_used_as_annotation": False,
        }
        for window_index in range(start, end + 1):
            expanded[(item["source_id"], window_index)] = visual_label
    return expanded


def _label_bindings(labels: dict[str, Any], windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_source: dict[str, list[dict[str, Any]]] = {}
    for window in windows:
        by_source.setdefault(window["source_id"], []).append(window)
    bindings: list[dict[str, Any]] = []
    for event in labels["events"]:
        candidates = [window["window_id"] for window in by_source[event["source_id"]]
                      if window["visual_label"]["label"] == event["label"]
                      and window["visual_label"][
                          "physical_event_or_hard_negative_group_id"] == event["event_id"]]
        if not candidates:
            raise ExhaustiveEvalError(
                f"annotated event {event['event_id']} has no production VOD window")
        bindings.append({
            "schema_version": LABEL_BINDING_SCHEMA,
            "event_id": event["event_id"],
            "source_id": event["source_id"],
            "source_sha256": event["source_sha256"],
            "source_interval_seconds": event["source_interval_seconds"],
            "label": event["label"],
            "evidence_tier": event["evidence_tier"],
            "label_provenance": event["label_provenance"],
            "review_kind": event["review_kind"],
            "candidate_window_ids": candidates,
            "accuracy_metric_eligible": event["evidence_tier"] == "owner_ground_truth",
            "production_detector_output_used_as_annotation": False,
        })
    return bindings


def materialize(source_manifest: dict[str, Any], source_raw: bytes,
                labels: dict[str, Any], label_raw: bytes, sources: dict[str, Path],
                work_root: Path) -> Path:
    contract, receipt = current_contract()
    suite_id = suite_identity(source_raw, label_raw, receipt)
    destination = expected_dataset_root(work_root, suite_id)
    if destination.is_dir():
        validate_materialized(destination, source_manifest, source_raw, labels, label_raw)
        print(f"EXHAUSTIVE VIDEO MATERIALIZATION ALREADY VALID: {destination}")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{suite_id}.tmp-{uuid4().hex[:8]}"
    staging.mkdir(parents=False, exist_ok=False)
    windows: list[dict[str, Any]] = []
    visual_labels = _window_visual_label_map(labels)
    try:
        for source in source_manifest["sources"]:
            source_id = source["source_id"]
            decoded = staging / "decoded" / source_id
            try:
                pts = media.decode_to_jpegs(sources[source_id], decoded, source["frames"])
            except media.CorpusError as error:
                raise ExhaustiveEvalError(str(error)) from error
            retained = staging / "sources" / source_id / "frames"
            retained.mkdir(parents=True, exist_ok=False)
            retained_indices: set[int] = set()
            centers = media.vod_sample_times(
                source["duration_seconds"], source_manifest["sampling"]["center_step_seconds"])
            for window_index, center in enumerate(centers):
                requested = media.vod_burst_times(
                    center, source["duration_seconds"],
                    source_manifest["sampling"]["burst_half_span_seconds"])
                if len(requested) != 3:
                    raise ExhaustiveEvalError("production VOD window collapsed below three frames")
                frame_records: list[dict[str, Any]] = []
                frame_paths: list[Path] = []
                for ordinal, requested_time in enumerate(requested):
                    frame_index, selected_pts, clamped = select_frame(pts, requested_time)
                    source_frame = decoded / f"f{frame_index:05d}.jpg"
                    retained_frame = retained / source_frame.name
                    if frame_index not in retained_indices:
                        shutil.copy2(source_frame, retained_frame)
                        retained_indices.add(frame_index)
                    byte_count, fingerprint = _validate_jpeg(retained_frame, source)
                    relative_path = retained_frame.relative_to(staging).as_posix()
                    frame_records.append({
                        "ordinal": ordinal,
                        "requested_timestamp_seconds": requested_time,
                        "selected_frame_index": frame_index,
                        "selected_pts_seconds": selected_pts,
                        "duration_boundary_clamped": clamped,
                        "path": relative_path,
                        "sha256": fingerprint,
                        "bytes": byte_count,
                        "width": source["presented_width"],
                        "height": source["presented_height"],
                        "format": "JPEG",
                        "full_frame": True,
                        "source_sha256": source["sha256"],
                        "capture_provenance": source_manifest["privacy"]["capture_provenance"],
                    })
                    frame_paths.append(retained_frame)
                request, transforms = build_production_request(frame_paths, contract)
                window_id = (
                    f"{source_id}--w{window_index:05d}--c{round(center * 1000):07d}")
                windows.append({
                    "schema_version": WINDOW_SCHEMA,
                    "window_id": window_id,
                    "source_id": source_id,
                    "source_sha256": source["sha256"],
                    "window_index": window_index,
                    "center_seconds": center,
                    "frames": frame_records,
                    "prepared_transforms": transforms,
                    "request_sha256": sha256_bytes(canonical_json(request).encode()),
                    "production_contract": receipt,
                    "visual_label": visual_labels[(source_id, window_index)],
                    "production_detector_output_used_as_annotation": False,
                })
            shutil.rmtree(decoded)

        bindings = _label_bindings(labels, windows)
        window_raw = _jsonl_bytes(windows)
        binding_raw = _jsonl_bytes(bindings)
        windows_sha = sha256_bytes(window_raw)
        bindings_sha = sha256_bytes(binding_raw)
        dataset_id = materialized_identity(suite_id, windows_sha, bindings_sha)
        _atomic_write(staging / "windows.jsonl", window_raw)
        _atomic_write(staging / "visual-label-bindings.jsonl", binding_raw)
        index = {
            "schema_version": DATASET_SCHEMA,
            "suite_identity": suite_id,
            "dataset_id": dataset_id,
            "suite_id": source_manifest["suite_id"],
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_manifest_sha256": sha256_bytes(source_raw),
            "visual_label_manifest_sha256": sha256_bytes(label_raw),
            "source_inventory_sha256": source_manifest["inventory"]["source_inventory_sha256"],
            "production_contract": receipt,
            "sampling": source_manifest["sampling"],
            "counts": {
                "sources": len(source_manifest["sources"]),
                "windows": len(windows),
                "frame_references": sum(len(window["frames"]) for window in windows),
                "unique_retained_frames": len({frame["path"] for window in windows
                                               for frame in window["frames"]}),
                "annotated_pothole_events": len(bindings),
                "reviewed_positive_windows": sum(
                    window["visual_label"]["label"] == "pothole" for window in windows),
                "reviewed_negative_windows": sum(
                    window["visual_label"]["label"] == "not_pothole" for window in windows),
                "abstain_windows": sum(
                    window["visual_label"]["label"] is None for window in windows),
            },
            "artifacts": {
                "windows": "windows.jsonl",
                "windows_sha256": windows_sha,
                "visual_label_bindings": "visual-label-bindings.jsonl",
                "visual_label_bindings_sha256": bindings_sha,
            },
            "constraints": {
                "private_media_committed": False,
                "absolute_source_paths_stored": False,
                "whole_frame_only": True,
                "spatial_crop_tile_mask_or_roi": False,
                "production_detector_output_used_as_annotation": False,
                "assistant_window_annotations_are_ground_truth": False,
                "unlabelled_windows_are_not_negatives": True,
                "raw_camerax_accuracy_claimed": False,
            },
        }
        _atomic_write(staging / "index.json", (json.dumps(
            index, sort_keys=True, indent=2) + "\n").encode())
        try:
            os.replace(staging, destination)
        except OSError:
            # Two local invocations may finish the same immutable dataset together.
            # macOS will not replace the other complete non-empty directory; accept
            # it only after the full hash/request validator proves it is identical.
            if not destination.is_dir():
                raise
            validate_materialized(
                destination, source_manifest, source_raw, labels, label_raw)
            shutil.rmtree(staging)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    validate_materialized(destination, source_manifest, source_raw, labels, label_raw)
    print(f"EXHAUSTIVE VIDEO MATERIALIZATION PASS ({len(windows)} windows): {destination}")
    return destination


def _read_jsonl(path: Path, description: str) -> tuple[list[dict[str, Any]], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ExhaustiveEvalError(f"cannot read {description}: {error}") from error
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ExhaustiveEvalError(
                f"{description} line {line_number} is invalid JSON") from error
        if not isinstance(value, dict):
            raise ExhaustiveEvalError(f"{description} line {line_number} is not an object")
        records.append(value)
    return records, raw


def _safe_relative(root: Path, value: Any, where: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts:
        raise ExhaustiveEvalError(f"{where} has an unsafe generated path")
    path = root / value
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise ExhaustiveEvalError(f"{where} escapes the dataset root") from error
    return path


def validate_materialized(dataset_root: Path, source_manifest: dict[str, Any],
                          source_raw: bytes, labels: dict[str, Any], label_raw: bytes,
                          rebuild_requests: bool = True
                          ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    index_value, _ = _read_json(dataset_root / "index.json", "materialized index")
    index = _exact_object(index_value, {
        "schema_version", "suite_identity", "dataset_id", "suite_id", "created_at_utc",
        "source_manifest_sha256", "visual_label_manifest_sha256",
        "source_inventory_sha256", "production_contract", "sampling", "counts",
        "artifacts", "constraints",
    }, "materialized index")
    contract, receipt = current_contract()
    expected_suite_identity = suite_identity(source_raw, label_raw, receipt)
    if (index["schema_version"] != DATASET_SCHEMA
            or index["suite_identity"] != expected_suite_identity
            or index["suite_id"] != source_manifest["suite_id"]
            or index["source_manifest_sha256"] != sha256_bytes(source_raw)
            or index["visual_label_manifest_sha256"] != sha256_bytes(label_raw)
            or index["source_inventory_sha256"]
            != source_manifest["inventory"]["source_inventory_sha256"]
            or index["production_contract"] != receipt
            or index["sampling"] != source_manifest["sampling"]):
        raise ExhaustiveEvalError("materialized dataset identity or contract drifted")
    constraints = index["constraints"]
    if (constraints.get("private_media_committed") is not False
            or constraints.get("absolute_source_paths_stored") is not False
            or constraints.get("whole_frame_only") is not True
            or constraints.get("spatial_crop_tile_mask_or_roi") is not False
            or constraints.get("production_detector_output_used_as_annotation") is not False
            or constraints.get("assistant_window_annotations_are_ground_truth") is not False
            or constraints.get("unlabelled_windows_are_not_negatives") is not True
            or constraints.get("raw_camerax_accuracy_claimed") is not False):
        raise ExhaustiveEvalError("materialized dataset safety constraints drifted")
    artifacts = index["artifacts"]
    windows_path = _safe_relative(dataset_root, artifacts.get("windows"), "windows artifact")
    bindings_path = _safe_relative(
        dataset_root, artifacts.get("visual_label_bindings"), "label-binding artifact")
    windows, window_raw = _read_jsonl(windows_path, "materialized windows")
    bindings, binding_raw = _read_jsonl(bindings_path, "visual-label bindings")
    windows_sha = sha256_bytes(window_raw)
    bindings_sha = sha256_bytes(binding_raw)
    expected_dataset_id = materialized_identity(
        expected_suite_identity, windows_sha, bindings_sha)
    if (windows_sha != artifacts.get("windows_sha256")
            or bindings_sha != artifacts.get("visual_label_bindings_sha256")
            or index["dataset_id"] != expected_dataset_id):
        raise ExhaustiveEvalError("materialized JSONL artifact fingerprint mismatch")
    expected_count = source_manifest["inventory"]["expected_windows"]
    expected_positive_windows = sum(
        review["positive_windows"] for review in labels["reviews"])
    expected_negative_windows = sum(
        review["negative_windows"] for review in labels["reviews"])
    expected_abstain_windows = sum(
        review["abstain_windows"] for review in labels["reviews"])
    if (len(windows) != expected_count
            or index["counts"].get("windows") != expected_count
            or index["counts"].get("frame_references") != expected_count * 3
            or index["counts"].get("sources") != len(source_manifest["sources"])
            or index["counts"].get("annotated_pothole_events") != len(labels["events"])
            or index["counts"].get("reviewed_positive_windows")
            != expected_positive_windows
            or index["counts"].get("reviewed_negative_windows")
            != expected_negative_windows
            or index["counts"].get("abstain_windows") != expected_abstain_windows):
        raise ExhaustiveEvalError("materialized dataset counts are incomplete")

    source_by_id = {source["source_id"]: source for source in source_manifest["sources"]}
    expected_visual_labels = _window_visual_label_map(labels)
    seen: set[str] = set()
    expected_windows: list[tuple[str, int, float]] = []
    for source in source_manifest["sources"]:
        for window_index, center in enumerate(media.vod_sample_times(
                source["duration_seconds"],
                source_manifest["sampling"]["center_step_seconds"])):
            expected_windows.append((source["source_id"], window_index, center))
    if len(expected_windows) != len(windows):
        raise ExhaustiveEvalError("materialized window enumeration is incomplete")
    unique_paths: set[str] = set()
    frame_metadata: dict[tuple[str, int], tuple[Any, ...]] = {}
    for record, (source_id, window_index, center) in zip(windows, expected_windows):
        source = source_by_id[source_id]
        required = {
            "schema_version", "window_id", "source_id", "source_sha256", "window_index",
            "center_seconds", "frames", "prepared_transforms", "request_sha256",
            "production_contract", "visual_label",
            "production_detector_output_used_as_annotation",
        }
        _exact_object(record, required, f"window {window_index}")
        expected_id = f"{source_id}--w{window_index:05d}--c{round(center * 1000):07d}"
        if (record["schema_version"] != WINDOW_SCHEMA or record["window_id"] != expected_id
                or record["window_id"] in seen or record["source_id"] != source_id
                or record["source_sha256"] != source["sha256"]
                or record["window_index"] != window_index
                or abs(record["center_seconds"] - center) > 1e-9
                or record["production_contract"] != receipt
                or record["visual_label"] != expected_visual_labels[(source_id, window_index)]
                or record["production_detector_output_used_as_annotation"] is not False
                or not isinstance(record["request_sha256"], str)
                or not HEX_64.fullmatch(record["request_sha256"])):
            raise ExhaustiveEvalError(f"materialized window identity drifted: {expected_id}")
        requested = media.vod_burst_times(
            center, source["duration_seconds"],
            source_manifest["sampling"]["burst_half_span_seconds"])
        frames = record["frames"]
        if not isinstance(frames, list) or len(frames) != 3:
            raise ExhaustiveEvalError(f"window {expected_id} does not have three frames")
        frame_paths: list[Path] = []
        selected_indices: list[int] = []
        selected_pts_values: list[float] = []
        for ordinal, (frame, requested_time) in enumerate(zip(frames, requested)):
            try:
                selected_index, selected_pts = _validate_frame_record_metadata(
                    frame, ordinal, requested_time, source_id, source,
                    source_manifest["privacy"]["capture_provenance"])
            except ExhaustiveEvalError as error:
                raise ExhaustiveEvalError(
                    f"window {expected_id} has invalid frame provenance") from error
            path = _safe_relative(dataset_root, frame.get("path"), f"window {expected_id}")
            byte_count, fingerprint = _validate_jpeg(path, source)
            if frame.get("bytes") != byte_count or frame.get("sha256") != fingerprint:
                raise ExhaustiveEvalError(f"window {expected_id} frame fingerprint mismatch")
            signature = (
                selected_pts, frame["path"], frame["sha256"], frame["bytes"],
                frame["width"], frame["height"])
            metadata_key = (source_id, selected_index)
            if metadata_key in frame_metadata and frame_metadata[metadata_key] != signature:
                raise ExhaustiveEvalError(
                    f"source frame {source_id}/{selected_index} has inconsistent metadata")
            frame_metadata[metadata_key] = signature
            unique_paths.add(frame["path"])
            frame_paths.append(path)
            selected_indices.append(selected_index)
            selected_pts_values.append(selected_pts)
        if (selected_indices != sorted(selected_indices)
                or selected_pts_values != sorted(selected_pts_values)):
            raise ExhaustiveEvalError(
                f"window {expected_id} frame selection is not chronological")
        transforms = record["prepared_transforms"]
        if (not isinstance(transforms, list) or len(transforms) != 4
                or any(item.get("full_frame") is not True for item in transforms)):
            raise ExhaustiveEvalError(f"window {expected_id} contains a spatial subset")
        if rebuild_requests:
            request, rebuilt_transforms = build_production_request(frame_paths, contract)
            if rebuilt_transforms != transforms or sha256_bytes(
                    canonical_json(request).encode()) != record["request_sha256"]:
                raise ExhaustiveEvalError(f"window {expected_id} production request drifted")
        seen.add(record["window_id"])
    if index["counts"].get("unique_retained_frames") != len(unique_paths):
        raise ExhaustiveEvalError("materialized unique-frame count drifted")
    expected_bindings = _label_bindings(labels, windows)
    if bindings != expected_bindings:
        raise ExhaustiveEvalError("materialized human visual-label bindings drifted")
    return index, windows, bindings


def _cache_path(out: Path, window: dict[str, Any]) -> Path:
    return out / "cache" / f"{window['window_id']}-{window['request_sha256'][:16]}.json"


def validate_result_record(value: Any, index: dict[str, Any], window: dict[str, Any],
                           schema: dict[str, Any],
                           allow_compatible_dataset: bool = False) -> dict[str, Any]:
    record = _exact_object(value, {
        "schema_version", "dataset_id", "window_id", "request_sha256", "response_id",
        "assessment", "decision", "attempt_count", "attempts",
    }, "cached exhaustive result")
    if (record["schema_version"] != RESULT_SCHEMA
            or not isinstance(record["dataset_id"], str)
            or not HEX_64.fullmatch(record["dataset_id"])
            or (record["dataset_id"] != index["dataset_id"]
                and not allow_compatible_dataset)
            or record["window_id"] != window["window_id"]
            or record["request_sha256"] != window["request_sha256"]
            or not isinstance(record["response_id"], str) or not record["response_id"]):
        raise ExhaustiveEvalError("cached exhaustive result identity drifted")
    raw_attempts = record["attempts"]
    if (not isinstance(raw_attempts, list)
            or record["attempt_count"] != len(raw_attempts)
            or not 1 <= len(raw_attempts) <= production_eval.TEMPORARY_SURFACE_MAX_ATTEMPTS):
        raise ExhaustiveEvalError("cached exhaustive policy attempt count drifted")
    assessments: list[dict[str, Any]] = []
    for ordinal, raw_attempt in enumerate(raw_attempts, 1):
        attempt = _exact_object(
            raw_attempt, {"attempt_number", "response_id", "assessment"},
            f"cached exhaustive policy attempt {ordinal}")
        if (attempt["attempt_number"] != ordinal
                or not isinstance(attempt["response_id"], str)
                or not attempt["response_id"]):
            raise ExhaustiveEvalError("cached exhaustive policy attempt identity drifted")
        try:
            assessments.append(release_gate.validate_assessment(
                attempt["assessment"], schema))
        except release_gate.GateError as error:
            raise ExhaustiveEvalError(
                "cached exhaustive policy assessment violates production schema") from error

    pending = list(assessments)

    def replay_assessment() -> dict[str, Any]:
        return pending.pop(0)

    outcome = production_eval.run_bounded_detection_policy(
        replay_assessment, mode="drive", source_view_count=3)
    if (outcome.confirmation_failed or pending
            or record["decision"] != outcome.decision
            or record["assessment"] != outcome.assessment):
        raise ExhaustiveEvalError("cached exhaustive bounded vote does not match production logic")
    representative = next((attempt for attempt in reversed(raw_attempts)
                           if attempt["assessment"] == outcome.assessment),
                          raw_attempts[-1])
    if record["response_id"] != representative["response_id"]:
        raise ExhaustiveEvalError("cached exhaustive representative response drifted")
    return record


def _load_cached(path: Path, index: dict[str, Any], window: dict[str, Any],
                 schema: dict[str, Any],
                 allow_compatible_dataset: bool = False) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value, _ = _read_json(path, "cached exhaustive result")
    record = validate_result_record(
        value, index, window, schema, allow_compatible_dataset)
    if record["dataset_id"] != index["dataset_id"]:
        # A visual-annotation correction changes the dataset identity but not the paid
        # request. Rebind only after the exact window ID, request SHA, response schema,
        # and production decision have all validated; never mutate the source cache.
        record = {**record, "dataset_id": index["dataset_id"]}
    return record


def _request_for_window(dataset_root: Path, window: dict[str, Any],
                        contract: dict[str, Any]) -> dict[str, Any]:
    frame_paths = [_safe_relative(dataset_root, frame["path"], window["window_id"])
                   for frame in window["frames"]]
    request, transforms = build_production_request(frame_paths, contract)
    if (transforms != window["prepared_transforms"]
            or sha256_bytes(canonical_json(request).encode()) != window["request_sha256"]):
        raise ExhaustiveEvalError(
            f"paid request no longer matches materialized window {window['window_id']}")
    return request


def _coverage(bindings: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decision_by_id = {row["window_id"]: row.get("decision") for row in rows
                      if row.get("status") == "ok"}
    coverage: list[dict[str, Any]] = []
    for binding in bindings:
        candidates = binding["candidate_window_ids"]
        decisions = [decision_by_id.get(item) for item in candidates]
        accepted = sum(decision == "accept" for decision in decisions)
        complete = all(decision in {"accept", "reject"} for decision in decisions)
        detected = accepted > 0 if binding["label"] == "pothole" else accepted == 0
        coverage.append({
            "event_id": binding["event_id"],
            "label": binding["label"],
            "evidence_tier": binding["evidence_tier"],
            "accuracy_metric_eligible": binding["accuracy_metric_eligible"],
            "candidate_windows": len(candidates),
            "accepted_windows": accepted,
            "complete": complete,
            "event_passed": complete and detected,
            "label_provenance": binding["label_provenance"],
        })
    return coverage


def accuracy_gate_passes(error_count: int, coverage: list[dict[str, Any]]) -> bool:
    """Gate only the human-confirmed physical-event recall invariant."""
    eligible = [item for item in coverage if item.get("accuracy_metric_eligible") is True]
    return error_count == 0 and bool(eligible) and all(
        item["event_passed"] for item in eligible)


def required_regression_gate_passes(
    rows: list[dict[str, Any]],
    required_ids: frozenset[str] = REQUIRED_ASSISTANT_REGRESSION_WINDOW_IDS,
) -> bool:
    """Gate named assistant-reviewed regressions without calling them ground truth."""
    decisions = {
        row.get("window_id"): row.get("decision")
        for row in rows if row.get("status") == "ok"
    }
    return bool(required_ids) and all(decisions.get(window_id) == "reject"
                                      for window_id in required_ids)


def validate_required_regression_labels(windows: list[dict[str, Any]]) -> None:
    by_id = {window.get("window_id"): window for window in windows}
    for window_id in REQUIRED_ASSISTANT_REGRESSION_WINDOW_IDS:
        window = by_id.get(window_id)
        label = window.get("visual_label") if isinstance(window, dict) else None
        if (not isinstance(label, dict)
                or label.get("label") != "not_pothole"
                or label.get("expected_decision") != "reject"
                or label.get("label_provenance") !=
                    "independent_assistant_full_video_review"
                or label.get("physical_event_or_hard_negative_group_id") !=
                    "seg2-hn-rough-aggregate-37-43s"):
            raise ExhaustiveEvalError(
                f"required independent regression label drifted: {window_id}"
            )


def release_gate_passes(
    error_count: int,
    coverage: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> bool:
    return (accuracy_gate_passes(error_count, coverage)
            and required_regression_gate_passes(rows))


def maximum_policy_calls(decisions: int) -> int:
    """Worst-case API calls for the shipped bounded temporary-surface vote."""
    if decisions < 0:
        raise ExhaustiveEvalError("decision count cannot be negative")
    return decisions * production_eval.TEMPORARY_SURFACE_MAX_ATTEMPTS


def run_paid(dataset_root: Path, index: dict[str, Any], windows: list[dict[str, Any]],
             bindings: list[dict[str, Any]], out: Path, max_calls: int,
             resume: bool, concurrency: int, timeout: int, gate: bool,
             reuse_cache_from: Path | None = None) -> dict[str, Any]:
    contract, _receipt = current_contract()
    validate_required_regression_labels(windows)
    if out.exists() and not resume and any(out.iterdir()):
        raise ExhaustiveEvalError("paid output already exists; use --resume or a new --out")
    out.mkdir(parents=True, exist_ok=True)
    cached: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    compatible_cache_imports = 0
    for window in windows:
        record = _load_cached(_cache_path(out, window), index, window, contract["schema"])
        if record is None and reuse_cache_from is not None:
            record = _load_cached(
                _cache_path(reuse_cache_from, window), index, window,
                contract["schema"], allow_compatible_dataset=True)
            if record is not None:
                _atomic_write(_cache_path(out, window), (
                    json.dumps(record, sort_keys=True, indent=2) + "\n").encode())
                compatible_cache_imports += 1
        if record is None:
            pending.append(window)
        else:
            cached[window["window_id"]] = record
    worst_case_calls = maximum_policy_calls(len(pending))
    if worst_case_calls > max_calls:
        raise ExhaustiveEvalError(
            f"paid run may need {worst_case_calls} policy attempts for {len(pending)} "
            f"uncached decisions, above --max-calls={max_calls}; nothing was sent")
    if not pending and not resume and compatible_cache_imports == 0:
        raise ExhaustiveEvalError("all exact requests are cached; use --resume to verify them")
    try:
        api_key = release_gate.load_api_key() if pending else ""
    except release_gate.GateError as error:
        raise ExhaustiveEvalError("OPENAI_API_KEY is required for uncached paid calls") from error

    def worker(window: dict[str, Any]) -> dict[str, Any]:
        attempts_started = 0
        try:
            request = _request_for_window(dataset_root, window, contract)
            attempts: list[dict[str, Any]] = []

            def get_assessment() -> dict[str, Any]:
                nonlocal attempts_started
                attempts_started += 1
                assessment, response_id = release_gate.fresh_api_assessment(
                    api_key, request, timeout, contract["schema"])
                if not response_id:
                    raise ExhaustiveEvalError("OpenAI response did not include an ID")
                attempts.append({
                    "attempt_number": attempts_started,
                    "response_id": response_id,
                    "assessment": assessment,
                })
                return assessment

            outcome = production_eval.run_bounded_detection_policy(
                get_assessment, mode="drive", source_view_count=3)
            if outcome.confirmation_failed:
                raise ExhaustiveEvalError(
                    "temporary-surface confirmation request failed closed")
            representative = next((
                item for item in reversed(attempts)
                if item["assessment"] == outcome.assessment), attempts[-1])
            record = {
                "schema_version": RESULT_SCHEMA,
                "dataset_id": index["dataset_id"],
                "window_id": window["window_id"],
                "request_sha256": window["request_sha256"],
                "response_id": representative["response_id"],
                "assessment": outcome.assessment,
                "decision": outcome.decision,
                "attempt_count": outcome.attempts_started,
                "attempts": attempts,
            }
            validate_result_record(record, index, window, contract["schema"])
            _atomic_write(_cache_path(out, window), (
                json.dumps(record, sort_keys=True, indent=2) + "\n").encode())
            return record
        except Exception as error:  # preserve exhaustive denominator; never cache failures
            message = str(error)
            if api_key:
                message = message.replace(api_key, "[REDACTED]")
            return {
                "schema_version": RESULT_SCHEMA,
                "dataset_id": index["dataset_id"],
                "window_id": window["window_id"],
                "request_sha256": window["request_sha256"],
                "status": "error",
                "attempt_count": attempts_started,
                "error_type": type(error).__name__,
                "error": message[:1000],
            }

    fresh: dict[str, dict[str, Any]] = {}
    if pending:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for number, (window, record) in enumerate(zip(pending, pool.map(worker, pending)), 1):
                fresh[window["window_id"]] = record
                if number % 25 == 0 or number == len(pending):
                    print(f"  paid detector {number}/{len(pending)}")
    rows: list[dict[str, Any]] = []
    for window in windows:
        record = cached.get(window["window_id"]) or fresh.get(window["window_id"])
        if record is None:
            raise ExhaustiveEvalError("internal result accounting omitted a window")
        if "status" not in record:
            record = {**record, "status": "ok",
                      "cache_hit": window["window_id"] in cached}
        rows.append(record)
    errors = [row for row in rows if row["status"] != "ok"]
    policy_attempts = sum(int(row.get("attempt_count", 0)) for row in rows)
    cached_policy_attempts = sum(
        int(row.get("attempt_count", 0)) for row in rows if row.get("cache_hit") is True)
    accepted = sum(row.get("decision") == "accept" for row in rows)
    coverage = _coverage(bindings, rows)
    owner_coverage = [item for item in coverage
                      if item["accuracy_metric_eligible"] is True]
    candidate_coverage = [item for item in coverage
                          if item["evidence_tier"] == "assistant_candidate"]
    labelled_pairs = list(zip(windows, rows))
    positive_pairs = [pair for pair in labelled_pairs
                      if pair[0]["visual_label"]["label"] == "pothole"]
    negative_pairs = [pair for pair in labelled_pairs
                      if pair[0]["visual_label"]["label"] == "not_pothole"]
    abstain_pairs = [pair for pair in labelled_pairs
                     if pair[0]["visual_label"]["label"] is None]
    positive_accepts = sum(row.get("decision") == "accept" for _, row in positive_pairs)
    negative_accepts = sum(row.get("decision") == "accept" for _, row in negative_pairs)
    abstain_accepts = sum(row.get("decision") == "accept" for _, row in abstain_pairs)
    summary = {
        "schema_version": "private-exhaustive-video-summary-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_id": index["dataset_id"],
        "counts": {
            "windows": len(windows),
            "completed": len(windows) - len(errors),
            "accepted": accepted,
            "rejected": len(windows) - len(errors) - accepted,
            "errors": len(errors),
            "fresh_calls": policy_attempts - cached_policy_attempts,
            "policy_attempts": policy_attempts,
            "cached_policy_attempts": cached_policy_attempts,
            "cache_hits": len(cached),
            "compatible_cache_imports": compatible_cache_imports,
        },
        "complete": not errors and len(rows) == len(windows),
        "owner_confirmed_event_coverage": owner_coverage,
        "assistant_candidate_event_diagnostics": candidate_coverage,
        "reviewed_window_results": {
            "positive_windows": len(positive_pairs),
            "positive_accepted": positive_accepts,
            "positive_rejected": sum(
                row.get("decision") == "reject" for _, row in positive_pairs),
            "independent_negative_windows": len(negative_pairs),
            "independent_negative_accepted": negative_accepts,
            "independent_negative_rejected": sum(
                row.get("decision") == "reject" for _, row in negative_pairs),
            "abstain_windows": len(abstain_pairs),
            "abstain_accepted_for_review": abstain_accepts,
            "distinct_owner_confirmed_potholes": len(owner_coverage),
            "distinct_owner_confirmed_potholes_detected": sum(
                item["event_passed"] for item in owner_coverage),
            "distinct_assistant_candidate_potholes": len(candidate_coverage),
        },
        "abstain_accepts_are_review_candidates_not_false_positives": True,
        "independent_negative_labels_are_not_owner_confirmed": True,
        "assistant_window_annotations_are_ground_truth": False,
        "production_detector_output_used_as_annotation": False,
        "gate": {
            "enabled": gate,
            "passed": release_gate_passes(len(errors), coverage, rows)
            if gate else None,
            "required_independent_regression_window_ids":
                sorted(REQUIRED_ASSISTANT_REGRESSION_WINDOW_IDS),
            "independent_regressions_are_owner_ground_truth": False,
        },
    }
    _atomic_write(out / "raw.jsonl", _jsonl_bytes(rows))
    _atomic_write(out / "summary.json", (
        json.dumps(summary, sort_keys=True, indent=2) + "\n").encode())
    if errors:
        raise ExhaustiveEvalError(
            f"exhaustive paid run incomplete: {len(errors)} of {len(windows)} windows failed")
    if gate and not summary["gate"]["passed"]:
        missed = [item["event_id"] for item in owner_coverage if not item["event_passed"]]
        failures = []
        if missed:
            failures.append("missed owner event(s): " + ", ".join(missed))
        failed_regressions = sorted(
            window_id for window_id in REQUIRED_ASSISTANT_REGRESSION_WINDOW_IDS
            if next((row.get("decision") for row in rows
                     if row.get("window_id") == window_id and row.get("status") == "ok"), None)
            != "reject"
        )
        if failed_regressions:
            failures.append(
                "accepted/missing independent regression window(s): " +
                ", ".join(failed_regressions)
            )
        raise ExhaustiveEvalError("human-reviewed accuracy gate failed: " + "; ".join(failures))
    return summary


def expected_window_stubs(source_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    stubs: list[dict[str, Any]] = []
    for source in source_manifest["sources"]:
        centers = media.vod_sample_times(
            source["duration_seconds"],
            source_manifest["sampling"]["center_step_seconds"])
        for window_index, center in enumerate(centers):
            stubs.append({
                "window_id": (
                    f"{source['source_id']}--w{window_index:05d}--"
                    f"c{round(center * 1000):07d}"),
                "source_id": source["source_id"],
                "window_index": window_index,
                "center_seconds": center,
            })
    return stubs


def _decision_inputs(assessment: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    return {field: assessment[field] for field in schema["required"]
            if field != "description"}


def _validate_decision_inputs(value: Any, schema: dict[str, Any],
                              where: str) -> dict[str, Any]:
    fields = [field for field in schema["required"] if field != "description"]
    inputs = _exact_object(value, set(fields), where)
    try:
        release_gate.validate_assessment({**inputs, "description": "[redacted]"}, schema)
    except release_gate.GateError as error:
        raise ExhaustiveEvalError(f"{where} violates the production schema") from error
    return inputs


def validate_results_receipt(value: Any, source_manifest: dict[str, Any],
                             source_raw: bytes, labels: dict[str, Any], label_raw: bytes,
                             contract_receipt: dict[str, Any], *,
                             contract: dict[str, Any] | None = None,
                             decision_function=None) -> dict[str, Any]:
    receipt = _exact_object(value, {
        "schema_version", "suite_id", "suite_identity",
        "dataset_id", "production_contract", "receipts", "counts", "results",
        "constraints",
    }, "exhaustive detector-results receipt")
    try:
        media.reject_absolute_paths(receipt)
        reject_private_locator_strings(receipt)
    except media.CorpusError as error:
        raise ExhaustiveEvalError(str(error)) from error
    expected_suite = suite_identity(source_raw, label_raw, contract_receipt)
    if (receipt["schema_version"] != PUBLIC_RESULTS_SCHEMA
            or receipt["suite_id"] != source_manifest["suite_id"]
            or receipt["suite_identity"] != expected_suite
            or receipt["production_contract"] != contract_receipt):
        raise ExhaustiveEvalError("detector-results receipt identity or contract drifted")
    hashes = _exact_object(receipt["receipts"], {
        "source_manifest_sha256", "assistant_annotation_manifest_sha256",
        "materialized_windows_sha256", "event_bindings_sha256",
        "normalized_private_results_sha256",
    }, "detector-results receipt hashes")
    if (any(not isinstance(item, str) or not HEX_64.fullmatch(item)
            for item in hashes.values())
            or hashes["source_manifest_sha256"] != sha256_bytes(source_raw)
            or hashes["assistant_annotation_manifest_sha256"] != sha256_bytes(label_raw)):
        raise ExhaustiveEvalError("detector-results receipt hashes drifted")
    expected_dataset = materialized_identity(
        expected_suite, hashes["materialized_windows_sha256"],
        hashes["event_bindings_sha256"])
    if receipt["dataset_id"] != expected_dataset:
        raise ExhaustiveEvalError("detector-results receipt is not bound to materialized content")

    counts = _exact_object(receipt["counts"], {
        "source_videos", "source_frames_fully_decoded", "production_windows",
        "full_frame_references", "unique_full_frames_retained", "completed_requests",
        "accepted_windows", "rejected_windows", "error_windows",
    }, "detector-results receipt counts")
    inventory = source_manifest["inventory"]
    if (counts["source_videos"] != inventory["source_count"]
            or counts["source_frames_fully_decoded"] != inventory["total_frames"]
            or counts["production_windows"] != inventory["expected_windows"]
            or counts["full_frame_references"] != inventory["expected_frame_references"]
            or type(counts["unique_full_frames_retained"]) is not int
            or not 0 < counts["unique_full_frames_retained"]
            <= counts["full_frame_references"]
            or counts["completed_requests"] != counts["production_windows"]
            or counts["error_windows"] != 0):
        raise ExhaustiveEvalError("detector-results receipt is incomplete")

    constraints = _exact_object(receipt["constraints"], {
        "private_media_committed", "absolute_private_paths_stored",
        "response_ids_committed", "model_free_text_committed",
        "image_or_request_payloads_committed",
        "production_detector_output_used_as_annotation",
    }, "detector-results receipt constraints")
    if constraints != {
            "private_media_committed": False,
            "absolute_private_paths_stored": False,
            "response_ids_committed": False,
            "model_free_text_committed": False,
            "image_or_request_payloads_committed": False,
            "production_detector_output_used_as_annotation": False,
    }:
        raise ExhaustiveEvalError("detector-results receipt privacy constraints drifted")

    rows = receipt["results"]
    expected_stubs = expected_window_stubs(source_manifest)
    if not isinstance(rows, list) or len(rows) != len(expected_stubs):
        raise ExhaustiveEvalError("detector-results receipt has the wrong denominator")
    accepted = 0
    if contract is None:
        contract, _current = current_contract()
    if decision_function is None:
        decision_function = production_eval.decision
    for ordinal, (raw_row, stub) in enumerate(zip(rows, expected_stubs)):
        row = _exact_object(raw_row, {
            "schema_version", "window_id", "source_id", "window_index",
            "center_seconds", "request_sha256", "assessment_sha256",
            "decision_inputs", "decision",
        }, f"detector-results row {ordinal}")
        if (row["schema_version"] != PUBLIC_RESULT_ROW_SCHEMA
                or row["window_id"] != stub["window_id"]
                or row["source_id"] != stub["source_id"]
                or row["window_index"] != stub["window_index"]
                or not _finite(row["center_seconds"])
                or abs(row["center_seconds"] - stub["center_seconds"]) > 1e-9
                or not isinstance(row["request_sha256"], str)
                or not HEX_64.fullmatch(row["request_sha256"])
                or not isinstance(row["assessment_sha256"], str)
                or not HEX_64.fullmatch(row["assessment_sha256"])):
            raise ExhaustiveEvalError(f"detector-results row {ordinal} identity drifted")
        inputs = _validate_decision_inputs(
            row["decision_inputs"], contract["schema"],
            f"detector-results row {ordinal} decision inputs")
        expected_decision = decision_function(inputs, "drive", source_view_count=3)
        if row["decision"] != expected_decision:
            raise ExhaustiveEvalError(
                f"detector-results row {ordinal} decision is inconsistent")
        accepted += row["decision"] == "accept"
    if (counts["accepted_windows"] != accepted
            or counts["rejected_windows"] != len(rows) - accepted
            or counts["accepted_windows"] + counts["rejected_windows"]
            + counts["error_windows"] != counts["production_windows"]):
        raise ExhaustiveEvalError("detector-results receipt decision counts drifted")
    return receipt


def load_results_receipt(path: Path, source_manifest: dict[str, Any], source_raw: bytes,
                         labels: dict[str, Any], label_raw: bytes,
                         contract_receipt: dict[str, Any], *,
                         contract: dict[str, Any] | None = None,
                         decision_function=None) -> tuple[dict[str, Any], bytes]:
    value, raw = _read_json(path, "exhaustive detector-results receipt")
    return validate_results_receipt(
        value, source_manifest, source_raw, labels, label_raw, contract_receipt,
        contract=contract, decision_function=decision_function), raw


def validate_historical_v15_results_receipt(
    value: Any, source_manifest: dict[str, Any], source_raw: bytes,
    labels: dict[str, Any], label_raw: bytes,
) -> dict[str, Any]:
    """Authenticate archived v15 rows without making them current-run cache data."""
    contract, receipt = historical_v15_contract()
    if source_manifest.get("production_contract") != receipt:
        raise ExhaustiveEvalError("historical results require the exact v15 source archive")
    return validate_results_receipt(
        value, source_manifest, source_raw, labels, label_raw, receipt,
        contract=contract, decision_function=historical_contracts.v15_decision)


def load_historical_v15_results_receipt(
    source_manifest: dict[str, Any], source_raw: bytes,
    labels: dict[str, Any], label_raw: bytes,
    path: Path = DEFAULT_RESULTS_RECEIPT,
) -> tuple[dict[str, Any], bytes]:
    """Authenticate archived v15 result rows; never usable as a current cache."""
    value, raw = _read_json(path, "historical v15 exhaustive detector-results receipt")
    return validate_historical_v15_results_receipt(
        value, source_manifest, source_raw, labels, label_raw), raw


def _event_candidate_window_ids(labels: dict[str, Any],
                                source_manifest: dict[str, Any]) -> dict[str, list[str]]:
    label_map = _window_visual_label_map(labels)
    candidates = {event["event_id"]: [] for event in labels["events"]}
    for stub in expected_window_stubs(source_manifest):
        annotation = label_map[(stub["source_id"], stub["window_index"])]
        event_id = annotation["physical_event_or_hard_negative_group_id"]
        if annotation["label"] == "pothole" and event_id in candidates:
            candidates[event_id].append(stub["window_id"])
    if any(not items for items in candidates.values()):
        raise ExhaustiveEvalError("an annotated event has no candidate production window")
    return candidates


def _event_audit(labels: dict[str, Any], source_manifest: dict[str, Any],
                 result_by_id: dict[str, dict[str, Any]], evidence_tier: str
                 ) -> list[dict[str, Any]]:
    candidates = _event_candidate_window_ids(labels, source_manifest)
    rows: list[dict[str, Any]] = []
    for event in labels["events"]:
        if event["evidence_tier"] != evidence_tier:
            continue
        candidate_ids = candidates[event["event_id"]]
        accepted_ids = [window_id for window_id in candidate_ids
                        if result_by_id[window_id]["decision"] == "accept"]
        rows.append({
            "event_id": event["event_id"],
            "candidate_window_ids": candidate_ids,
            "accepted_window_ids": accepted_ids,
            "detected": bool(accepted_ids),
        })
    return rows


def _accepted_clusters(labels: dict[str, Any], source_manifest: dict[str, Any],
                       result_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    annotation_map = _window_visual_label_map(labels)
    event_candidates = _event_candidate_window_ids(labels, source_manifest)
    event_by_id = {event["event_id"]: event for event in labels["events"]}
    accepted_by_source: dict[str, list[dict[str, Any]]] = {}
    for row in result_rows:
        if row["decision"] == "accept":
            accepted_by_source.setdefault(row["source_id"], []).append(row)
    clusters: list[dict[str, Any]] = []
    for source in source_manifest["sources"]:
        accepted = sorted(accepted_by_source.get(source["source_id"], []),
                          key=lambda item: item["window_index"])
        groups: list[list[dict[str, Any]]] = []
        for row in accepted:
            if not groups or row["window_index"] - groups[-1][-1]["window_index"] > 2:
                groups.append([row])
            else:
                groups[-1].append(row)
        for group in groups:
            ids = {row["window_id"] for row in group}
            intersecting_events = [event_id for event_id, candidate_ids in event_candidates.items()
                                   if ids.intersection(candidate_ids)]
            owner_ids = sorted(event_id for event_id in intersecting_events
                               if event_by_id[event_id]["evidence_tier"] == "owner_ground_truth")
            assistant_ids = sorted(event_id for event_id in intersecting_events
                                   if event_by_id[event_id]["evidence_tier"]
                                   == "assistant_candidate")
            annotations = [annotation_map[(row["source_id"], row["window_index"])]
                           for row in group]
            reject_classes = sorted({item["semantic_class"] for item in annotations
                                     if item["expected_decision"] == "reject"})
            abstain_classes = sorted({item["semantic_class"] for item in annotations
                                      if item["expected_decision"] is None})
            status = ("contains_owner_confirmed_event" if owner_ids else
                      "contains_assistant_candidate_event" if assistant_ids else
                      "unconfirmed_review_candidate")
            clusters.append({
                "source_id": source["source_id"],
                "accepted_window_indices": [row["window_index"] for row in group],
                "status": status,
                "owner_event_ids": owner_ids,
                "assistant_candidate_event_ids": assistant_ids,
                "assistant_expected_reject_semantic_classes": reject_classes,
                "assistant_abstain_semantic_classes": abstain_classes,
            })
    return clusters


def build_audit_receipt(results_receipt: dict[str, Any], results_raw: bytes,
                        source_manifest: dict[str, Any], source_raw: bytes,
                        labels: dict[str, Any], label_raw: bytes,
                        contract_receipt: dict[str, Any], *,
                        contract: dict[str, Any] | None = None,
                        decision_function=None) -> dict[str, Any]:
    validated = validate_results_receipt(
        results_receipt, source_manifest, source_raw, labels, label_raw, contract_receipt,
        contract=contract, decision_function=decision_function)
    rows = validated["results"]
    result_by_id = {row["window_id"]: row for row in rows}
    owner_events = _event_audit(
        labels, source_manifest, result_by_id, "owner_ground_truth")
    assistant_events = _event_audit(
        labels, source_manifest, result_by_id, "assistant_candidate")
    annotation_map = _window_visual_label_map(labels)
    annotated_rows = [(annotation_map[(row["source_id"], row["window_index"])], row)
                      for row in rows]
    positive = [pair for pair in annotated_rows if pair[0]["label"] == "pothole"]
    negative = [pair for pair in annotated_rows if pair[0]["label"] == "not_pothole"]
    abstain = [pair for pair in annotated_rows if pair[0]["label"] is None]
    counts = validated["counts"]
    hashes = validated["receipts"]
    detected_owner = sum(event["detected"] for event in owner_events)
    return {
        "schema_version": AUDIT_SCHEMA,
        "suite_id": source_manifest["suite_id"],
        "suite_identity": validated["suite_identity"],
        "dataset_id": validated["dataset_id"],
        "production_contract": contract_receipt,
        "receipts": {
            "source_manifest_sha256": sha256_bytes(source_raw),
            "assistant_annotation_manifest_sha256": sha256_bytes(label_raw),
            "materialized_windows_sha256": hashes["materialized_windows_sha256"],
            "event_bindings_sha256": hashes["event_bindings_sha256"],
            "detector_results_receipt_sha256": sha256_bytes(results_raw),
            "normalized_private_results_sha256":
                hashes["normalized_private_results_sha256"],
        },
        "execution": {
            **counts,
            "complete": counts["completed_requests"] == counts["production_windows"]
            and counts["error_windows"] == 0,
        },
        "owner_confirmed_event_recall": {
            "physical_potholes": len(owner_events),
            "physical_potholes_detected": detected_owner,
            "passed": bool(owner_events) and detected_owner == len(owner_events),
            "events": owner_events,
        },
        "assistant_diagnostics": {
            "ground_truth": False,
            "positive_windows": len(positive),
            "positive_windows_accepted": sum(
                row["decision"] == "accept" for _, row in positive),
            "expected_reject_windows": len(negative),
            "expected_reject_windows_accepted": sum(
                row["decision"] == "accept" for _, row in negative),
            "abstain_windows": len(abstain),
            "abstain_windows_accepted": sum(
                row["decision"] == "accept" for _, row in abstain),
            "assistant_candidate_events": assistant_events,
        },
        "accepted_candidate_clustering": {
            "max_adjacent_window_index_gap": 2,
            "clusters": _accepted_clusters(labels, source_manifest, rows),
        },
        "constraints": {
            "private_media_committed": False,
            "absolute_private_paths_stored": False,
            "whole_frame_only": True,
            "spatial_crop_tile_mask_or_roi": False,
            "assistant_annotations_are_ground_truth": False,
            "production_detector_output_used_as_annotation": False,
            "response_ids_committed": False,
            "model_free_text_committed": False,
        },
    }


def validate_audit_receipt(value: Any, results_receipt: dict[str, Any],
                           results_raw: bytes, source_manifest: dict[str, Any],
                           source_raw: bytes, labels: dict[str, Any], label_raw: bytes,
                           contract_receipt: dict[str, Any], *,
                           contract: dict[str, Any] | None = None,
                           decision_function=None) -> dict[str, Any]:
    expected = build_audit_receipt(
        results_receipt, results_raw, source_manifest, source_raw,
        labels, label_raw, contract_receipt,
        contract=contract, decision_function=decision_function)
    if value != expected:
        raise ExhaustiveEvalError(
            "exhaustive audit is not the exact deterministic derivation of its result rows")
    return value


def load_audit_receipt(path: Path, results_receipt: dict[str, Any],
                       results_raw: bytes, source_manifest: dict[str, Any],
                       source_raw: bytes, labels: dict[str, Any], label_raw: bytes,
                       contract_receipt: dict[str, Any], *,
                       contract: dict[str, Any] | None = None,
                       decision_function=None) -> dict[str, Any]:
    value, _raw = _read_json(path, "exhaustive audit receipt")
    return validate_audit_receipt(
        value, results_receipt, results_raw, source_manifest, source_raw,
        labels, label_raw, contract_receipt,
        contract=contract, decision_function=decision_function)


def validate_historical_v15_audit_receipt(
    value: Any, results_receipt: dict[str, Any], results_raw: bytes,
    source_manifest: dict[str, Any], source_raw: bytes,
    labels: dict[str, Any], label_raw: bytes,
) -> dict[str, Any]:
    """Authenticate the v15 audit while keeping it outside current release coverage."""
    contract, receipt = historical_v15_contract()
    if source_manifest.get("production_contract") != receipt:
        raise ExhaustiveEvalError("historical audit requires the exact v15 source archive")
    return validate_audit_receipt(
        value, results_receipt, results_raw, source_manifest, source_raw,
        labels, label_raw, receipt,
        contract=contract, decision_function=historical_contracts.v15_decision)


def load_historical_v15_audit_receipt(
    results_receipt: dict[str, Any], results_raw: bytes,
    source_manifest: dict[str, Any], source_raw: bytes,
    labels: dict[str, Any], label_raw: bytes,
    path: Path = DEFAULT_AUDIT_RECEIPT,
) -> dict[str, Any]:
    """Authenticate the deterministic v15 audit, never as current release coverage."""
    value, _raw = _read_json(path, "historical v15 exhaustive audit receipt")
    return validate_historical_v15_audit_receipt(
        value, results_receipt, results_raw, source_manifest, source_raw,
        labels, label_raw)


def validate_results_against_materialized(results_receipt: dict[str, Any],
                                          index: dict[str, Any],
                                          windows: list[dict[str, Any]]) -> None:
    hashes = results_receipt["receipts"]
    artifacts = index["artifacts"]
    if (results_receipt["dataset_id"] != index["dataset_id"]
            or results_receipt["suite_identity"] != index["suite_identity"]
            or hashes["materialized_windows_sha256"] != artifacts["windows_sha256"]
            or hashes["event_bindings_sha256"]
            != artifacts["visual_label_bindings_sha256"]
            or results_receipt["counts"]["unique_full_frames_retained"]
            != index["counts"]["unique_retained_frames"]):
        raise ExhaustiveEvalError("committed detector results do not seal this materialized dataset")
    for row, window in zip(results_receipt["results"], windows):
        if (row["window_id"] != window["window_id"]
                or row["source_id"] != window["source_id"]
                or row["window_index"] != window["window_index"]
                or abs(row["center_seconds"] - window["center_seconds"]) > 1e-9
                or row["request_sha256"] != window["request_sha256"]):
            raise ExhaustiveEvalError(
                f"committed detector result does not match {window['window_id']}")


def seal_run(run_root: Path, index: dict[str, Any], windows: list[dict[str, Any]],
             source_manifest: dict[str, Any], source_raw: bytes,
             labels: dict[str, Any], label_raw: bytes,
             results_path: Path, audit_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_rows, _raw_bytes = _read_jsonl(
        run_root / "raw.jsonl", "private exhaustive raw results")
    summary, _summary_bytes = _read_json(
        run_root / "summary.json", "private exhaustive result summary")
    contract, contract_receipt = current_contract()
    if (summary.get("schema_version") != "private-exhaustive-video-summary-v1"
            or summary.get("dataset_id") != index["dataset_id"]
            or summary.get("complete") is not True):
        raise ExhaustiveEvalError("private run summary is incomplete or bound to another dataset")
    if len(raw_rows) != len(windows):
        raise ExhaustiveEvalError("private raw run does not contain every production window")
    public_rows: list[dict[str, Any]] = []
    normalized_private_rows: list[dict[str, Any]] = []
    accepted = 0
    for ordinal, (raw_row, window) in enumerate(zip(raw_rows, windows)):
        allowed = {
            "schema_version", "dataset_id", "window_id", "request_sha256", "response_id",
            "assessment", "decision", "attempt_count", "attempts", "status", "cache_hit",
        }
        if not isinstance(raw_row, dict) or set(raw_row) != allowed:
            raise ExhaustiveEvalError(f"private raw result row {ordinal} has unexpected fields")
        if raw_row["status"] != "ok" or type(raw_row["cache_hit"]) is not bool:
            raise ExhaustiveEvalError(f"private raw result row {ordinal} is not complete")
        base_record = {key: raw_row[key] for key in (
            "schema_version", "dataset_id", "window_id", "request_sha256", "response_id",
            "assessment", "decision", "attempt_count", "attempts")}
        validated = validate_result_record(
            base_record, index, window, contract["schema"])
        normalized_private_rows.append({
            "schema_version": validated["schema_version"],
            "window_id": validated["window_id"],
            "request_sha256": validated["request_sha256"],
            "assessment": validated["assessment"],
            "decision": validated["decision"],
            "attempt_count": validated["attempt_count"],
            "attempt_assessment_sha256": [sha256_bytes(
                canonical_json(item["assessment"]).encode())
                for item in validated["attempts"]],
        })
        public_rows.append({
            "schema_version": PUBLIC_RESULT_ROW_SCHEMA,
            "window_id": window["window_id"],
            "source_id": window["source_id"],
            "window_index": window["window_index"],
            "center_seconds": window["center_seconds"],
            "request_sha256": window["request_sha256"],
            "assessment_sha256": sha256_bytes(
                canonical_json(validated["assessment"]).encode()),
            "decision_inputs": _decision_inputs(
                validated["assessment"], contract["schema"]),
            "decision": validated["decision"],
        })
        accepted += validated["decision"] == "accept"
    summary_counts = summary.get("counts", {})
    if (summary_counts.get("windows") != len(windows)
            or summary_counts.get("completed") != len(windows)
            or summary_counts.get("accepted") != accepted
            or summary_counts.get("rejected") != len(windows) - accepted
            or summary_counts.get("errors") != 0):
        raise ExhaustiveEvalError("private summary counts do not match the exact raw results")
    artifacts = index["artifacts"]
    results_receipt = {
        "schema_version": PUBLIC_RESULTS_SCHEMA,
        "suite_id": source_manifest["suite_id"],
        "suite_identity": index["suite_identity"],
        "dataset_id": index["dataset_id"],
        "production_contract": contract_receipt,
        "receipts": {
            "source_manifest_sha256": sha256_bytes(source_raw),
            "assistant_annotation_manifest_sha256": sha256_bytes(label_raw),
            "materialized_windows_sha256": artifacts["windows_sha256"],
            "event_bindings_sha256": artifacts["visual_label_bindings_sha256"],
            "normalized_private_results_sha256": sha256_bytes(
                _jsonl_bytes(normalized_private_rows)),
        },
        "counts": {
            "source_videos": source_manifest["inventory"]["source_count"],
            "source_frames_fully_decoded": source_manifest["inventory"]["total_frames"],
            "production_windows": len(windows),
            "full_frame_references": source_manifest["inventory"]["expected_frame_references"],
            "unique_full_frames_retained": index["counts"]["unique_retained_frames"],
            "completed_requests": len(windows),
            "accepted_windows": accepted,
            "rejected_windows": len(windows) - accepted,
            "error_windows": 0,
        },
        "results": public_rows,
        "constraints": {
            "private_media_committed": False,
            "absolute_private_paths_stored": False,
            "response_ids_committed": False,
            "model_free_text_committed": False,
            "image_or_request_payloads_committed": False,
            "production_detector_output_used_as_annotation": False,
        },
    }
    result_bytes = (json.dumps(results_receipt, sort_keys=True, indent=2) + "\n").encode()
    validate_results_receipt(
        results_receipt, source_manifest, source_raw, labels, label_raw, contract_receipt)
    audit = build_audit_receipt(
        results_receipt, result_bytes, source_manifest, source_raw,
        labels, label_raw, contract_receipt)
    audit_bytes = (json.dumps(audit, sort_keys=True, indent=2) + "\n").encode()
    _atomic_write(results_path, result_bytes)
    _atomic_write(audit_path, audit_bytes)
    loaded_results, loaded_raw = load_results_receipt(
        results_path, source_manifest, source_raw, labels, label_raw, contract_receipt)
    load_audit_receipt(
        audit_path, loaded_results, loaded_raw, source_manifest, source_raw,
        labels, label_raw, contract_receipt)
    validate_results_against_materialized(loaded_results, index, windows)
    return loaded_results, audit


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--visual-labels", type=Path, default=DEFAULT_LABEL_MANIFEST)
    parser.add_argument("--audit-receipt", type=Path, default=DEFAULT_AUDIT_RECEIPT)
    parser.add_argument("--results-receipt", type=Path, default=DEFAULT_RESULTS_RECEIPT)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--dataset-root", type=Path,
                        help="explicit generated dataset directory for validation/paid replay")
    parser.add_argument("--source-dir", type=Path,
                        help="directory searched recursively for exact private MP4 hashes")
    parser.add_argument("--source", action="append", default=[], metavar="ID=PATH",
                        help="explicit exact private-source mapping")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-manifest", action="store_true",
                      help="source-free, API-free receipt and label check")
    mode.add_argument("--materialize", action="store_true",
                      help="decode and retain all production VOD windows locally")
    mode.add_argument("--validate-materialized", action="store_true",
                      help="re-hash every generated frame and rebuild every request")
    mode.add_argument("--paid-run", action="store_true",
                      help="run every materialized window through the production detector")
    mode.add_argument("--seal-run", action="store_true",
                      help="derive committed content-free result and audit receipts")
    mode.add_argument("--verify-audit", action="store_true",
                      help="verify committed result/audit receipts without an API call")
    parser.add_argument("--max-calls", type=int,
                        help="hard ceiling for uncached paid calls")
    parser.add_argument("--resume", action="store_true",
                        help="reuse only exact validated cached results")
    parser.add_argument("--out", type=Path,
                        help="ignored paid-output directory; defaults below the work root")
    parser.add_argument("--run-root", type=Path,
                        help="ignored completed paid run consumed only by --seal-run")
    parser.add_argument("--reuse-cache-from", type=Path,
                        help="import only exact request-bound responses from another run")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--api-timeout", type=int, default=120)
    parser.add_argument("--gate", action="store_true",
                        help="require owner-event recall and named independent regressions")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    source_manifest, source_raw = load_source_manifest(args.source_manifest)
    labels, label_raw = load_label_manifest(args.visual_labels, source_manifest)
    _contract, receipt = current_contract()
    suite_id = suite_identity(source_raw, label_raw, receipt)
    dataset_root = args.dataset_root or expected_dataset_root(args.work_root, suite_id)
    if args.concurrency < 1 or args.concurrency > 8:
        raise ExhaustiveEvalError("--concurrency must be between 1 and 8")
    if args.api_timeout < 1:
        raise ExhaustiveEvalError("--api-timeout must be positive")
    if not args.paid_run and (args.max_calls is not None or args.resume or args.gate
                              or args.out or args.reuse_cache_from):
        raise ExhaustiveEvalError(
            "paid-run output/cache options are valid only with --paid-run")
    if not args.seal_run and args.run_root is not None:
        raise ExhaustiveEvalError("--run-root is valid only with --seal-run")
    if args.check_manifest:
        if args.source_dir is not None or args.source or args.dataset_root is not None:
            raise ExhaustiveEvalError("manifest checking does not read private media")
        print("EXHAUSTIVE VIDEO MANIFEST PASS (218 WINDOWS; NO MEDIA OR API)")
        return 0
    if args.verify_audit:
        if args.source_dir is not None or args.source:
            raise ExhaustiveEvalError("audit verification does not read source videos")
        results, results_raw = load_results_receipt(
            args.results_receipt, source_manifest, source_raw, labels, label_raw, receipt)
        load_audit_receipt(
            args.audit_receipt, results, results_raw, source_manifest, source_raw,
            labels, label_raw, receipt)
        if args.dataset_root is not None:
            guarded_dataset = require_private_artifact_root(
                args.dataset_root, "materialized dataset root")
            index, windows, _bindings = validate_materialized(
                guarded_dataset, source_manifest, source_raw, labels, label_raw)
            validate_results_against_materialized(results, index, windows)
            print("EXHAUSTIVE VIDEO AUDIT PASS (RECEIPTS + MATERIALIZED REQUESTS)")
        else:
            print("EXHAUSTIVE VIDEO AUDIT PASS (SOURCE-FREE RECEIPTS)")
        return 0
    if args.materialize:
        if args.dataset_root is not None:
            raise ExhaustiveEvalError(
                "--dataset-root is not used by --materialize; choose --work-root")
        work_root = require_private_artifact_root(args.work_root, "materialization work root")
        mappings = parse_source_mappings(args.source)
        resolved = resolve_sources(source_manifest, args.source_dir, mappings)
        materialize(source_manifest, source_raw, labels, label_raw, resolved, work_root)
        return 0
    if args.source_dir is not None or args.source:
        raise ExhaustiveEvalError(
            "private source paths are used only with --materialize; replay uses sealed JPEGs")
    if args.paid_run and (args.max_calls is None or args.max_calls < 0):
        # Check the spending guard before touching a generated dataset.  A paid mode
        # invocation is never valid without an explicit finite call ceiling.
        raise ExhaustiveEvalError("--paid-run requires a non-negative --max-calls ceiling")
    dataset_root = require_private_artifact_root(
        dataset_root, "materialized dataset root")
    index, windows, bindings = validate_materialized(
        dataset_root, source_manifest, source_raw, labels, label_raw)
    if args.validate_materialized:
        print(f"EXHAUSTIVE VIDEO MATERIALIZED DATASET PASS ({len(windows)} windows)")
        return 0
    if args.seal_run:
        run_root = args.run_root or args.work_root / "runs" / index["dataset_id"]
        run_root = require_private_artifact_root(run_root, "private paid-run root")
        results, audit = seal_run(
            run_root, index, windows, source_manifest, source_raw, labels, label_raw,
            args.results_receipt, args.audit_receipt)
        print(json.dumps({
            "dataset_id": results["dataset_id"],
            "counts": results["counts"],
            "owner_confirmed_event_recall": audit["owner_confirmed_event_recall"],
            "accepted_candidate_clusters": len(
                audit["accepted_candidate_clustering"]["clusters"]),
        }, indent=2))
        print("EXHAUSTIVE VIDEO RUN SEAL PASS")
        return 0
    out = args.out or args.work_root / "runs" / index["dataset_id"]
    out = require_private_artifact_root(out, "paid output root")
    if _paths_overlap(dataset_root, out):
        raise ExhaustiveEvalError("materialized dataset and paid output roots must not overlap")
    reuse_cache_from = (require_private_artifact_root(
        args.reuse_cache_from, "reused paid-cache root")
        if args.reuse_cache_from is not None else None)
    summary = run_paid(
        dataset_root, index, windows, bindings, out, args.max_calls,
        args.resume, args.concurrency, args.api_timeout, args.gate,
        reuse_cache_from)
    print(json.dumps({
        "complete": summary["complete"],
        "counts": summary["counts"],
        "owner_confirmed_event_coverage": summary["owner_confirmed_event_coverage"],
        "gate": summary["gate"],
    }, indent=2))
    print("EXHAUSTIVE VIDEO PAID RUN PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExhaustiveEvalError as error:
        print(f"EXHAUSTIVE VIDEO EVAL FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
