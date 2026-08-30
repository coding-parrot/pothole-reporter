#!/usr/bin/env python3
"""Materialize the private video evidence as a durable, local evaluation dataset.

The resulting JPEGs and indexes live below ``eval/.private-drive-corpus/dataset`` and
are ignored by Git. Every image is an exact, complete frame already sealed by one of
the two committed private-media manifests. Model output is never used as a label.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

import private_drive_corpus as desktop_corpus
import private_release_gate as release_gate


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DESKTOP_MANIFEST = ROOT / "eval" / "private_drive_corpus.json"
DEFAULT_RELEASE_MANIFEST = ROOT / "eval" / "private_release_gate.json"
DEFAULT_GROUND_TRUTH_MANIFEST = ROOT / "eval" / "private_eval_ground_truth.json"
DEFAULT_MANUAL_SOURCE_DIR = ROOT / "eval" / "images"
DEFAULT_WORK_ROOT = ROOT / "eval" / ".private-drive-corpus"
DATASET_SCHEMA_VERSION = "private-materialized-eval-v1"
FRAME_SCHEMA_VERSION = "private-eval-frame-v1"
BURST_SCHEMA_VERSION = "private-eval-burst-v1"
POOL_SCHEMA_VERSION = "private-desktop-pool-v1"
IMAGE_SUFFIXES = {".jpg", ".jpeg"}


class DatasetError(RuntimeError):
    """A fail-closed materialization or validation error."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_sha256(value: Any) -> str:
    return desktop_corpus.sha256_bytes(canonical_json(value).encode())


def jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return "".join(canonical_json(record) + "\n" for record in records).encode()


def validate_ground_truth_manifest(value: Any, release_manifest: dict[str, Any]
                                   ) -> dict[str, Any]:
    manifest = desktop_corpus.require_keys(value, {
        "schema_version", "label_policy", "release_events",
        "excluded_release_events", "manual_images",
    }, "private eval ground truth")
    if manifest["schema_version"] != "private-eval-ground-truth-v1":
        raise DatasetError("unsupported private eval ground-truth schema")
    if not isinstance(manifest["label_policy"], str) or not manifest["label_policy"].strip():
        raise DatasetError("private eval ground-truth policy is missing")
    desktop_corpus.reject_absolute_paths(manifest, "private eval ground truth")

    release_by_id = {event["id"]: event for event in release_manifest["events"]}
    selected_ids: set[str] = set()
    group_ids: set[str] = set()
    for index, selection in enumerate(manifest["release_events"]):
        selection = desktop_corpus.require_keys(selection, {
            "event_id", "physical_event_group_id", "semantic_class", "label_provenance",
        }, f"ground-truth release event {index}")
        event_id = selection["event_id"]
        group_id = selection["physical_event_group_id"]
        if (event_id not in release_by_id or event_id in selected_ids
                or not isinstance(group_id, str) or not group_id or group_id in group_ids
                or selection["label_provenance"] != "owner_confirmed"
                or not isinstance(selection["semantic_class"], str)
                or not selection["semantic_class"]):
            raise DatasetError("ground-truth release selection is invalid")
        selected_ids.add(event_id)
        group_ids.add(group_id)
    excluded_ids: set[str] = set()
    for index, exclusion in enumerate(manifest["excluded_release_events"]):
        exclusion = desktop_corpus.require_keys(
            exclusion, {"event_id", "reason"}, f"excluded release event {index}")
        event_id = exclusion["event_id"]
        if (event_id not in release_by_id or event_id in excluded_ids
                or event_id in selected_ids or not isinstance(exclusion["reason"], str)
                or not exclusion["reason"].strip()):
            raise DatasetError("excluded release selection is invalid")
        excluded_ids.add(event_id)
    if selected_ids | excluded_ids != set(release_by_id):
        raise DatasetError("every release event must be explicitly selected or excluded")
    if len(selected_ids) != 4 or sum(
            len(release_by_id[event_id]["phases"]) for event_id in selected_ids) != 20:
        raise DatasetError("owner release tier must retain exactly four events and 20 phases")

    manual = manifest["manual_images"]
    if not isinstance(manual, list) or len(manual) != 7:
        raise DatasetError("owner manual tier must contain exactly seven images")
    manual_ids: set[str] = set()
    manual_hashes: set[str] = set()
    for index, image in enumerate(manual):
        image = desktop_corpus.require_keys(image, {
            "id", "sha256", "width", "height", "label", "semantic_class",
            "label_provenance", "capture_provenance",
            "raw_camerax_accuracy_eligible", "rationale",
        }, f"owner manual image {index}")
        if (not isinstance(image["id"], str) or not image["id"] or image["id"] in manual_ids
                or not desktop_corpus.HEX_64.fullmatch(str(image["sha256"]))
                or image["sha256"] in manual_hashes
                or any(type(image[field]) is not int or image[field] <= 0
                       for field in ("width", "height"))
                or image["label"] != "pothole"
                or image["label_provenance"] != "owner_confirmed"
                or image["raw_camerax_accuracy_eligible"] is not False
                or any(not isinstance(image[field], str) or not image[field].strip()
                       for field in ("semantic_class", "capture_provenance", "rationale"))):
            raise DatasetError("owner manual ground truth is invalid")
        manual_ids.add(image["id"])
        manual_hashes.add(image["sha256"])
    return manifest


def load_ground_truth_manifest(path: Path, release_manifest: dict[str, Any]) -> dict[str, Any]:
    try:
        return validate_ground_truth_manifest(json.loads(path.read_text()), release_manifest)
    except (OSError, json.JSONDecodeError) as error:
        raise DatasetError(f"cannot load private eval ground truth: {error}") from error
    except desktop_corpus.CorpusError as error:
        raise DatasetError(str(error)) from error


def image_reference(fingerprint: str, width: int, height: int) -> dict[str, Any]:
    return {
        "path": f"images/{fingerprint}.jpg",
        "sha256": fingerprint,
        "mime_type": "image/jpeg",
        "width": width,
        "height": height,
        "whole_frame": True,
        "spatial_crop_tile_mask_or_roi": False,
    }


def desktop_records(manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources = {source["source_id"]: source for source in manifest["sources"]}
    labels: list[dict[str, Any]] = []
    bursts: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        source = sources[case["source_id"]]
        for phase_index, phase in enumerate(case["phases"]):
            burst_id = f"desktop::{phase['custom_id']}"
            sample_ids: list[str] = []
            image_paths: list[str] = []
            image_hashes: list[str] = []
            for sequence_index, fingerprint in enumerate(phase["full_frame_jpeg_sha256"]):
                sample_id = f"{burst_id}::f{sequence_index}"
                image = image_reference(
                    fingerprint, source["presented_width"], source["presented_height"])
                labels.append({
                    "schema_version": FRAME_SCHEMA_VERSION,
                    "sample_id": sample_id,
                    "burst_id": burst_id,
                    "collection": "desktop_curated",
                    "eval_tier": "diagnostic_expected_reject",
                    "split": "private_eval",
                    "source_group_id": case["source_id"],
                    "source_sha256": source["sha256"],
                    "physical_event_group_id": f"desktop::{case['id']}",
                    "case_id": case["id"],
                    "sequence_index": sequence_index,
                    "requested_timestamp_seconds": phase["requested_timestamps_seconds"][sequence_index],
                    "selected_pts_seconds": phase["selected_pts_seconds"][sequence_index],
                    "selected_frame_index": phase["selected_frame_indices"][sequence_index],
                    "label": None,
                    "is_pothole": None,
                    "binary_decision": None,
                    "expected_decision": "reject",
                    "semantic_class": case["semantic_class"],
                    "label_provenance": None,
                    "decision_provenance": case["evidence_status"],
                    "ground_truth_scope": None,
                    "diagnostic_review_scope": case["ground_truth_scope"],
                    "capture_provenance": manifest["corpus"]["capture_provenance"],
                    "raw_camerax_accuracy_eligible": False,
                    "training_eligible": False,
                    "training_exclusion_reason": "locked_private_eval_holdout",
                    "accuracy_metric_eligible": False,
                    "semantic_regression_eligible": True,
                    "metric_scope": "diagnostic_expected_decision_only",
                    "model_output_used_as_label": False,
                    "image": image,
                })
                sample_ids.append(sample_id)
                image_paths.append(image["path"])
                image_hashes.append(fingerprint)
            bursts.append({
                "schema_version": BURST_SCHEMA_VERSION,
                "burst_id": burst_id,
                "collection": "desktop_curated",
                "eval_tier": "diagnostic_expected_reject",
                "split": "private_eval",
                "source_group_id": case["source_id"],
                "source_sha256": source["sha256"],
                "physical_event_group_id": f"desktop::{case['id']}",
                "case_id": case["id"],
                "phase_index": phase_index,
                "center_seconds": phase["center_seconds"],
                "source_interval_seconds": case["source_interval_seconds"],
                "label": None,
                "is_pothole": None,
                "binary_decision": None,
                "expected_decision": "reject",
                "semantic_class": case["semantic_class"],
                "rationale": case["rationale"],
                "label_provenance": None,
                "decision_provenance": case["evidence_status"],
                "ground_truth_scope": None,
                "diagnostic_review_scope": case["ground_truth_scope"],
                "capture_provenance": manifest["corpus"]["capture_provenance"],
                "raw_camerax_accuracy_eligible": False,
                "training_eligible": False,
                "training_exclusion_reason": "locked_private_eval_holdout",
                "accuracy_metric_eligible": False,
                "semantic_regression_eligible": True,
                "metric_scope": "diagnostic_expected_decision_only",
                "model_output_used_as_label": False,
                "chronological_complete_frames": True,
                "sample_ids": sample_ids,
                "image_paths": image_paths,
                "image_sha256": image_hashes,
            })
    return labels, bursts


def release_records(manifest: dict[str, Any], ground_truth: dict[str, Any]
                    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    labels: list[dict[str, Any]] = []
    bursts: list[dict[str, Any]] = []
    events = {event["id"]: event for event in manifest["events"]}
    for selection in ground_truth["release_events"]:
        event = events[selection["event_id"]]
        source = manifest["sources"][event["source"]]
        is_pothole = event["label"] == "pothole"
        semantic_class = selection["semantic_class"]
        provenance = selection["label_provenance"]
        physical_group = selection["physical_event_group_id"]
        for phase_index, phase in enumerate(event["phases"]):
            burst_id = f"release::{event['id']}::p{phase_index}"
            sample_ids: list[str] = []
            image_paths: list[str] = []
            image_hashes: list[str] = []
            for sequence_index, fingerprint in enumerate(phase["fixture_sha256"]):
                sample_id = f"{burst_id}::f{sequence_index}"
                image = image_reference(fingerprint, source["width"], source["height"])
                labels.append({
                    "schema_version": FRAME_SCHEMA_VERSION,
                    "sample_id": sample_id,
                    "burst_id": burst_id,
                    "collection": "private_release_gate",
                    "eval_tier": "owner_ground_truth",
                    "split": "private_eval",
                    "source_group_id": event["source"],
                    "source_sha256": source["sha256"],
                    "physical_event_group_id": physical_group,
                    "case_id": event["id"],
                    "sequence_index": sequence_index,
                    "requested_timestamp_seconds": phase["timestamps_seconds"][sequence_index],
                    "selected_pts_seconds": None,
                    "selected_frame_index": None,
                    "label": event["label"],
                    "is_pothole": is_pothole,
                    "binary_decision": "YES" if is_pothole else "NO",
                    "expected_decision": "accept" if is_pothole else "reject",
                    "semantic_class": semantic_class,
                    "label_provenance": provenance,
                    "ground_truth_scope": "reviewed_event",
                    "capture_provenance": event["capture_provenance"],
                    "raw_camerax_accuracy_eligible": event["raw_camerax_accuracy_eligible"],
                    "training_eligible": False,
                    "training_exclusion_reason": "locked_private_eval_holdout",
                    "accuracy_metric_eligible": (
                        event["capture_provenance"] == "native_mediarecorder_reconstruction"),
                    "semantic_regression_eligible": True,
                    "metric_scope": (
                        "saved_video_reconstruction_semantic"
                        if event["capture_provenance"] == "native_mediarecorder_reconstruction"
                        else "external_recording_semantic_only"),
                    "model_output_used_as_label": False,
                    "image": image,
                })
                sample_ids.append(sample_id)
                image_paths.append(image["path"])
                image_hashes.append(fingerprint)
            bursts.append({
                "schema_version": BURST_SCHEMA_VERSION,
                "burst_id": burst_id,
                "collection": "private_release_gate",
                "eval_tier": "owner_ground_truth",
                "split": "private_eval",
                "source_group_id": event["source"],
                "source_sha256": source["sha256"],
                "physical_event_group_id": physical_group,
                "case_id": event["id"],
                "phase_index": phase_index,
                "center_seconds": phase["timestamps_seconds"][1],
                "source_interval_seconds": [phase["timestamps_seconds"][0], phase["timestamps_seconds"][2]],
                "label": event["label"],
                "is_pothole": is_pothole,
                "binary_decision": "YES" if is_pothole else "NO",
                "expected_decision": "accept" if is_pothole else "reject",
                "semantic_class": semantic_class,
                "rationale": "Locked owner/tester-confirmed release-gate event.",
                "label_provenance": provenance,
                "ground_truth_scope": "reviewed_event",
                "capture_provenance": event["capture_provenance"],
                "raw_camerax_accuracy_eligible": event["raw_camerax_accuracy_eligible"],
                "training_eligible": False,
                "training_exclusion_reason": "locked_private_eval_holdout",
                "accuracy_metric_eligible": (
                    event["capture_provenance"] == "native_mediarecorder_reconstruction"),
                "semantic_regression_eligible": True,
                "metric_scope": (
                    "saved_video_reconstruction_semantic"
                    if event["capture_provenance"] == "native_mediarecorder_reconstruction"
                    else "external_recording_semantic_only"),
                "model_output_used_as_label": False,
                "chronological_complete_frames": True,
                "sample_ids": sample_ids,
                "image_paths": image_paths,
                "image_sha256": image_hashes,
            })
    return labels, bursts


def manual_records(ground_truth: dict[str, Any]
                   ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    labels: list[dict[str, Any]] = []
    bursts: list[dict[str, Any]] = []
    for item in ground_truth["manual_images"]:
        burst_id = f"manual::{item['id']}"
        sample_id = f"{burst_id}::f0"
        image = image_reference(item["sha256"], item["width"], item["height"])
        common = {
            "collection": "owner_manual_photos",
            "eval_tier": "owner_ground_truth",
            "split": "private_eval",
            "source_group_id": item["id"],
            "source_sha256": item["sha256"],
            "physical_event_group_id": item["id"],
            "case_id": item["id"],
            "label": "pothole",
            "is_pothole": True,
            "binary_decision": "YES",
            "expected_decision": "accept",
            "semantic_class": item["semantic_class"],
            "label_provenance": item["label_provenance"],
            "ground_truth_scope": "complete_manual_photo",
            "capture_provenance": item["capture_provenance"],
            "raw_camerax_accuracy_eligible": item["raw_camerax_accuracy_eligible"],
            "training_eligible": False,
            "training_exclusion_reason": "locked_private_eval_holdout",
            "accuracy_metric_eligible": True,
            "semantic_regression_eligible": True,
            "metric_scope": "manual_photo_semantic",
            "model_output_used_as_label": False,
        }
        labels.append({
            "schema_version": FRAME_SCHEMA_VERSION,
            "sample_id": sample_id,
            "burst_id": burst_id,
            **common,
            "sequence_index": 0,
            "requested_timestamp_seconds": None,
            "selected_pts_seconds": None,
            "selected_frame_index": None,
            "image": image,
        })
        bursts.append({
            "schema_version": BURST_SCHEMA_VERSION,
            "burst_id": burst_id,
            **common,
            "phase_index": 0,
            "center_seconds": None,
            "source_interval_seconds": None,
            "rationale": item["rationale"],
            "chronological_complete_frames": False,
            "sample_ids": [sample_id],
            "image_paths": [image["path"]],
            "image_sha256": [item["sha256"]],
        })
    return labels, bursts


def desktop_pool_records(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    labelled = {
        phase["custom_id"]: f"desktop::{phase['custom_id']}"
        for case in manifest["cases"] for phase in case["phases"]
    }
    records: list[dict[str, Any]] = []
    step = manifest["sampling"]["center_step_seconds"]
    half_span = manifest["sampling"]["burst_half_span_seconds"]
    for source in manifest["sources"]:
        duration = float(source["duration_seconds"])
        for window_index, center in enumerate(desktop_corpus.vod_sample_times(duration, step)):
            custom_id = f"{source['source_id']}--w{window_index:05d}--c{round(center * 1000):07d}"
            label_burst_id = labelled.get(custom_id)
            records.append({
                "schema_version": POOL_SCHEMA_VERSION,
                "pool_burst_id": f"desktop-pool::{custom_id}",
                "source_group_id": source["source_id"],
                "source_sha256": source["sha256"],
                "window_index": window_index,
                "center_seconds": center,
                "requested_timestamps_seconds": desktop_corpus.vod_burst_times(
                    center, duration, half_span),
                "annotation_status": (
                    "diagnostic_expected_reject" if label_burst_id else "unlabelled"),
                "label_burst_id": label_burst_id,
                "label": None,
                "is_pothole": None,
                "model_output_used_as_label": False,
                "materialized_in_labeled_eval": bool(label_burst_id),
            })
    return records


def expected_records(desktop_manifest: dict[str, Any], release_manifest: dict[str, Any],
                     ground_truth: dict[str, Any]
                     ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    desktop_labels, desktop_bursts = desktop_records(desktop_manifest)
    gate_labels, gate_bursts = release_records(release_manifest, ground_truth)
    photo_labels, photo_bursts = manual_records(ground_truth)
    labels = sorted(
        desktop_labels + gate_labels + photo_labels, key=lambda item: item["sample_id"])
    bursts = sorted(
        desktop_bursts + gate_bursts + photo_bursts, key=lambda item: item["burst_id"])
    pool = desktop_pool_records(desktop_manifest)
    return labels, bursts, pool


def dataset_metadata(desktop_manifest: dict[str, Any], release_manifest: dict[str, Any],
                     ground_truth: dict[str, Any],
                     labels: list[dict[str, Any]], bursts: list[dict[str, Any]],
                     pool: list[dict[str, Any]], labels_raw: bytes, bursts_raw: bytes,
                     pool_raw: bytes) -> dict[str, Any]:
    image_inventory = sorted({
        (record["image"]["path"], record["image"]["sha256"])
        for record in labels
    })
    image_inventory_value = [
        {"path": path, "sha256": fingerprint} for path, fingerprint in image_inventory
    ]
    artifact_hashes = {
        "labels_jsonl_sha256": desktop_corpus.sha256_bytes(labels_raw),
        "bursts_jsonl_sha256": desktop_corpus.sha256_bytes(bursts_raw),
        "desktop_pool_jsonl_sha256": desktop_corpus.sha256_bytes(pool_raw),
        "image_inventory_sha256": canonical_sha256(image_inventory_value),
    }
    diagnostic_pool = sum(
        record["annotation_status"] == "diagnostic_expected_reject" for record in pool)
    owner_labels = [record for record in labels if record["eval_tier"] == "owner_ground_truth"]
    owner_bursts = [record for record in bursts if record["eval_tier"] == "owner_ground_truth"]
    diagnostic_labels = [
        record for record in labels if record["eval_tier"] == "diagnostic_expected_reject"]
    diagnostic_bursts = [
        record for record in bursts if record["eval_tier"] == "diagnostic_expected_reject"]
    owner_positive_frames = sum(record["is_pothole"] is True for record in owner_labels)
    owner_positive_cases = sum(record["is_pothole"] is True for record in owner_bursts)
    identity = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "desktop_manifest_canonical_sha256": canonical_sha256(desktop_manifest),
        "desktop_source_inventory_sha256": desktop_manifest["corpus"]["source_inventory_sha256"],
        "release_manifest_canonical_sha256": canonical_sha256(release_manifest),
        "ground_truth_manifest_canonical_sha256": canonical_sha256(ground_truth),
        **artifact_hashes,
    }
    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "split": "private_eval",
        "source_manifests": {
            "desktop_manifest": "eval/private_drive_corpus.json",
            "desktop_manifest_canonical_sha256": identity["desktop_manifest_canonical_sha256"],
            "desktop_source_inventory_sha256": identity["desktop_source_inventory_sha256"],
            "release_manifest": "eval/private_release_gate.json",
            "release_manifest_canonical_sha256": identity["release_manifest_canonical_sha256"],
            "ground_truth_manifest": "eval/private_eval_ground_truth.json",
            "ground_truth_manifest_canonical_sha256": identity[
                "ground_truth_manifest_canonical_sha256"],
        },
        "constraints": {
            "private_media_committed": False,
            "absolute_paths_stored": False,
            "whole_frame_only": True,
            "spatial_crop_tile_mask_or_roi": False,
            "model_output_used_as_label": False,
            "raw_camerax_accuracy_claimed": False,
            "owner_metrics_grouped_by_physical_event": True,
            "training_eligible": False,
            "training_exclusion_reason": "locked_private_eval_holdout",
        },
        "counts": {
            "frame_records": len(labels),
            "unique_images": len(image_inventory),
            "bursts": len(bursts),
            "owner_ground_truth_frames": len(owner_labels),
            "owner_ground_truth_positive_frames": owner_positive_frames,
            "owner_ground_truth_negative_frames": len(owner_labels) - owner_positive_frames,
            "owner_ground_truth_cases": len(owner_bursts),
            "owner_ground_truth_positive_cases": owner_positive_cases,
            "owner_ground_truth_negative_cases": len(owner_bursts) - owner_positive_cases,
            "owner_ground_truth_physical_event_groups": len({
                record["physical_event_group_id"] for record in owner_bursts}),
            "owner_accuracy_eligible_frames": sum(
                record["accuracy_metric_eligible"] is True for record in owner_labels),
            "owner_accuracy_eligible_cases": sum(
                record["accuracy_metric_eligible"] is True for record in owner_bursts),
            "owner_external_recording_semantic_frames": sum(
                record["metric_scope"] == "external_recording_semantic_only"
                for record in owner_labels),
            "owner_external_recording_semantic_cases": sum(
                record["metric_scope"] == "external_recording_semantic_only"
                for record in owner_bursts),
            "diagnostic_expected_reject_frames": len(diagnostic_labels),
            "diagnostic_expected_reject_cases": len(diagnostic_bursts),
            "diagnostic_physical_event_groups": len({
                record["physical_event_group_id"] for record in diagnostic_bursts}),
            "desktop_inventory_videos": len(desktop_manifest["sources"]),
            "release_gate_videos": len(release_manifest["sources"]),
            "desktop_pool_windows": len(pool),
            "desktop_pool_diagnostic_windows": diagnostic_pool,
            "desktop_pool_unlabelled_windows": len(pool) - diagnostic_pool,
            "desktop_pool_frame_references": len(pool) * 3,
        },
        "artifacts": {
            "labels": {"path": "labels.jsonl", "records": len(labels),
                       "sha256": artifact_hashes["labels_jsonl_sha256"]},
            "bursts": {"path": "bursts.jsonl", "records": len(bursts),
                       "sha256": artifact_hashes["bursts_jsonl_sha256"]},
            "desktop_pool": {"path": "desktop_pool.jsonl", "records": len(pool),
                            "sha256": artifact_hashes["desktop_pool_jsonl_sha256"]},
            "images": {"directory": "images", "files": len(image_inventory),
                       "inventory_sha256": artifact_hashes["image_inventory_sha256"]},
        },
        "dataset_content_sha256": canonical_sha256(identity),
    }


def copy_verified_frames(frame_paths: list[Path], expected_hashes: list[str],
                         expected_size: tuple[int, int], images_dir: Path) -> None:
    if not frame_paths or len(frame_paths) != len(expected_hashes):
        raise DatasetError("evaluation frame paths and fingerprints do not match")
    images_dir.mkdir(parents=True, exist_ok=True)
    for path, expected_hash in zip(frame_paths, expected_hashes):
        if desktop_corpus.sha256_file(path) != expected_hash:
            raise DatasetError("source frame fingerprint changed during dataset export")
        try:
            with Image.open(path) as image:
                size, image_format = image.size, image.format
                image.verify()
        except (OSError, UnidentifiedImageError) as error:
            raise DatasetError("dataset source contains an invalid JPEG") from error
        if image_format != "JPEG" or size != expected_size:
            raise DatasetError("dataset source is not the complete expected frame")
        destination = images_dir / f"{expected_hash}.jpg"
        if destination.exists():
            if desktop_corpus.sha256_file(destination) != expected_hash:
                raise DatasetError("content-addressed dataset image collision")
        else:
            shutil.copyfile(path, destination)


def write_indexes(dataset_dir: Path, desktop_manifest: dict[str, Any],
                  release_manifest: dict[str, Any], ground_truth: dict[str, Any]
                  ) -> dict[str, Any]:
    labels, bursts, pool = expected_records(
        desktop_manifest, release_manifest, ground_truth)
    labels_raw, bursts_raw, pool_raw = map(jsonl_bytes, (labels, bursts, pool))
    (dataset_dir / "labels.jsonl").write_bytes(labels_raw)
    (dataset_dir / "bursts.jsonl").write_bytes(bursts_raw)
    (dataset_dir / "desktop_pool.jsonl").write_bytes(pool_raw)
    metadata = dataset_metadata(
        desktop_manifest, release_manifest, ground_truth, labels, bursts, pool,
        labels_raw, bursts_raw, pool_raw)
    (dataset_dir / "dataset.json").write_text(
        json.dumps(metadata, sort_keys=True, indent=2) + "\n")
    return metadata


def load_canonical_jsonl(path: Path) -> tuple[list[dict[str, Any]], bytes]:
    if path.is_symlink() or not path.is_file():
        raise DatasetError(f"materialized dataset index {path.name} is missing or unsafe")
    try:
        raw = path.read_bytes()
        records = [json.loads(line) for line in raw.decode().splitlines()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DatasetError(f"cannot read materialized dataset index {path.name}") from error
    if raw != jsonl_bytes(records):
        raise DatasetError(f"materialized dataset index {path.name} is not canonical JSONL")
    return records, raw


def validate_dataset(dataset_dir: Path, desktop_manifest: dict[str, Any],
                     release_manifest: dict[str, Any], ground_truth: dict[str, Any]
                     ) -> dict[str, Any]:
    if not dataset_dir.is_dir() or dataset_dir.is_symlink():
        raise DatasetError("materialized dataset directory is missing or unsafe")
    labels, labels_raw = load_canonical_jsonl(dataset_dir / "labels.jsonl")
    bursts, bursts_raw = load_canonical_jsonl(dataset_dir / "bursts.jsonl")
    pool, pool_raw = load_canonical_jsonl(dataset_dir / "desktop_pool.jsonl")
    expected_labels, expected_bursts, expected_pool = expected_records(
        desktop_manifest, release_manifest, ground_truth)
    if labels != expected_labels or bursts != expected_bursts or pool != expected_pool:
        raise DatasetError("materialized labels or burst grouping drifted from the sealed manifests")
    try:
        metadata_path = dataset_dir / "dataset.json"
        if metadata_path.is_symlink() or not metadata_path.is_file():
            raise DatasetError("materialized dataset metadata is missing or unsafe")
        metadata = json.loads(metadata_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise DatasetError("cannot read materialized dataset metadata") from error
    expected_metadata = dataset_metadata(
        desktop_manifest, release_manifest, ground_truth, labels, bursts, pool,
        labels_raw, bursts_raw, pool_raw)
    if metadata != expected_metadata:
        raise DatasetError("materialized dataset metadata or content seal drifted")
    desktop_corpus.reject_absolute_paths({
        "metadata": metadata, "labels": labels, "bursts": bursts, "pool": pool,
    }, "materialized dataset")

    expected_images = {record["image"]["path"]: record["image"] for record in labels}
    images_dir = dataset_dir / "images"
    if not images_dir.is_dir() or images_dir.is_symlink():
        raise DatasetError("materialized image directory is missing or unsafe")
    image_entries = list(images_dir.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in image_entries):
        raise DatasetError("materialized image directory contains an unsafe entry")
    actual_files = {path.relative_to(dataset_dir).as_posix() for path in image_entries}
    if actual_files != set(expected_images):
        raise DatasetError("materialized image inventory is incomplete or contains extra files")
    for relative_path, expected in expected_images.items():
        path = dataset_dir / relative_path
        if desktop_corpus.sha256_file(path) != expected["sha256"]:
            raise DatasetError(f"materialized image fingerprint mismatch: {relative_path}")
        try:
            with Image.open(path) as image:
                size, image_format = image.size, image.format
                image.verify()
        except (OSError, UnidentifiedImageError) as error:
            raise DatasetError(f"materialized image is invalid: {relative_path}") from error
        if image_format != "JPEG" or size != (expected["width"], expected["height"]):
            raise DatasetError(f"materialized image geometry changed: {relative_path}")
    return metadata


def install_staged_dataset(staging: Path, target: Path) -> None:
    if target.name != "dataset" or target.is_symlink():
        raise DatasetError("refusing an unsafe materialized dataset target")
    backup = target.parent / f".dataset-backup-{os.getpid()}"
    if backup.exists():
        raise DatasetError("a stale dataset backup exists; inspect it before retrying")
    if target.exists():
        if not target.is_dir():
            raise DatasetError("materialized dataset target is not a directory")
        target.replace(backup)
    try:
        staging.replace(target)
    except OSError as error:
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise DatasetError("could not atomically install the materialized dataset") from error
    if backup.exists():
        shutil.rmtree(backup)


def discover_manual_images(directory: Path, ground_truth: dict[str, Any]) -> dict[str, Path]:
    if not directory.is_dir():
        raise DatasetError("--manual-source-dir is not a directory")
    wanted = {item["sha256"]: item["id"] for item in ground_truth["manual_images"]}
    discovered: dict[str, Path] = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        fingerprint = desktop_corpus.sha256_file(path)
        item_id = wanted.pop(fingerprint, None)
        if item_id:
            discovered[item_id] = path
        if not wanted:
            break
    if wanted:
        missing = sorted(wanted.values())
        raise DatasetError("missing owner manual image(s): " + ", ".join(missing))
    return discovered


def export_dataset(desktop_manifest: dict[str, Any], release_manifest: dict[str, Any],
                   ground_truth: dict[str, Any], desktop_source_dir: Path,
                   release_source_dir: Path, manual_source_dir: Path,
                   work_root: Path) -> dict[str, Any]:
    work_root.mkdir(parents=True, exist_ok=True)
    target = work_root / "dataset"
    staging = Path(tempfile.mkdtemp(prefix=".dataset-build-", dir=work_root))
    try:
        images_dir = staging / "images"
        desktop_sources = {source["source_id"]: source for source in desktop_manifest["sources"]}

        def collect_desktop(case: dict[str, Any], _phase_index: int,
                            phase: dict[str, Any], fixture: dict[str, Any],
                            _request: dict[str, Any]) -> None:
            source = desktop_sources[case["source_id"]]
            copy_verified_frames(
                fixture["frame_paths"], phase["full_frame_jpeg_sha256"],
                (source["presented_width"], source["presented_height"]), images_dir)

        desktop_corpus.validate_media(
            desktop_manifest, desktop_source_dir, work_root, collect_desktop)

        try:
            verified_release_sources = release_gate.verify_sources(
                release_manifest, {}, release_source_dir)
            with tempfile.TemporaryDirectory(prefix="release-media-", dir=work_root) as temporary:
                fixtures = release_gate.extract_fixtures(
                    release_manifest, verified_release_sources, Path(temporary))
                selected_release_ids = {
                    item["event_id"] for item in ground_truth["release_events"]}
                for fixture in fixtures:
                    event = fixture["event"]
                    if event["id"] not in selected_release_ids:
                        continue
                    phase = event["phases"][fixture["phase_index"]]
                    source = release_manifest["sources"][event["source"]]
                    copy_verified_frames(
                        fixture["frame_paths"], phase["fixture_sha256"],
                        (source["width"], source["height"]), images_dir)
        except release_gate.GateError as error:
            raise DatasetError(str(error)) from error

        manual_paths = discover_manual_images(manual_source_dir, ground_truth)
        for item in ground_truth["manual_images"]:
            copy_verified_frames(
                [manual_paths[item["id"]]], [item["sha256"]],
                (item["width"], item["height"]), images_dir)

        write_indexes(staging, desktop_manifest, release_manifest, ground_truth)
        metadata = validate_dataset(
            staging, desktop_manifest, release_manifest, ground_truth)
        install_staged_dataset(staging, target)
        metadata = validate_dataset(target, desktop_manifest, release_manifest, ground_truth)
        print(
            f"MATERIALIZED PRIVATE EVAL PASS ({metadata['counts']['unique_images']} images, "
            f"{metadata['counts']['owner_ground_truth_cases']} owner-ground-truth cases, "
            f"{metadata['counts']['diagnostic_expected_reject_cases']} diagnostic cases, "
            f"{metadata['counts']['desktop_pool_unlabelled_windows']} unlabelled pool windows, "
            f"seal {metadata['dataset_content_sha256']})")
        return metadata
    except desktop_corpus.CorpusError as error:
        raise DatasetError(str(error)) from error
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desktop-manifest", type=Path, default=DEFAULT_DESKTOP_MANIFEST)
    parser.add_argument("--release-manifest", type=Path, default=DEFAULT_RELEASE_MANIFEST)
    parser.add_argument("--ground-truth-manifest", type=Path,
                        default=DEFAULT_GROUND_TRUTH_MANIFEST)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--desktop-source-dir", type=Path,
                        help="directory containing the 30 exact Desktop drive segments")
    parser.add_argument("--release-source-dir", type=Path,
                        help="directory containing the three exact private release-gate videos")
    parser.add_argument("--manual-source-dir", type=Path, default=DEFAULT_MANUAL_SOURCE_DIR,
                        help="directory containing the seven exact owner manual photos")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--export-dataset", action="store_true",
                      help="verify sources and atomically materialize the complete private eval")
    mode.add_argument("--check-dataset", action="store_true",
                      help="verify an already-materialized dataset without source videos")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    desktop_manifest = desktop_corpus.load_manifest(args.desktop_manifest)
    try:
        release_manifest = release_gate.load_manifest(args.release_manifest)
    except release_gate.GateError as error:
        raise DatasetError(str(error)) from error
    ground_truth = load_ground_truth_manifest(args.ground_truth_manifest, release_manifest)
    target = args.work_root / "dataset"
    if args.check_dataset:
        metadata = validate_dataset(target, desktop_manifest, release_manifest, ground_truth)
        print(
            f"MATERIALIZED PRIVATE EVAL CHECK PASS ({metadata['counts']['unique_images']} images, "
            f"seal {metadata['dataset_content_sha256']})")
        return 0
    if args.desktop_source_dir is None or args.release_source_dir is None:
        raise DatasetError("--export-dataset requires both private source directories")
    export_dataset(
        desktop_manifest, release_manifest, ground_truth, args.desktop_source_dir,
        args.release_source_dir, args.manual_source_dir, args.work_root)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DatasetError as error:
        print(f"MATERIALIZED PRIVATE EVAL FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
