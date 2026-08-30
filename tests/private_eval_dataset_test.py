#!/usr/bin/env python3
"""Source-free contract tests for the durable private evaluation export."""

import copy
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
import materialize_private_eval as dataset  # noqa: E402
import private_drive_corpus as desktop_corpus  # noqa: E402
import private_release_gate as release_gate  # noqa: E402


FAILURES = []


def check(name, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        FAILURES.append(name)


desktop_manifest = desktop_corpus.load_manifest(dataset.DEFAULT_DESKTOP_MANIFEST)
release_manifest = release_gate.load_manifest(dataset.DEFAULT_RELEASE_MANIFEST)
ground_truth = dataset.load_ground_truth_manifest(
    dataset.DEFAULT_GROUND_TRUTH_MANIFEST, release_manifest)
labels, bursts, pool = dataset.expected_records(
    desktop_manifest, release_manifest, ground_truth)

owner_labels = [item for item in labels if item["eval_tier"] == "owner_ground_truth"]
owner_bursts = [item for item in bursts if item["eval_tier"] == "owner_ground_truth"]
diagnostic_labels = [
    item for item in labels if item["eval_tier"] == "diagnostic_expected_reject"]
diagnostic_bursts = [
    item for item in bursts if item["eval_tier"] == "diagnostic_expected_reject"]

check("owner-ground-truth tier has the exact audited size",
      len(owner_labels) == 67 and len(owner_bursts) == 27
      and len({item["physical_event_group_id"] for item in owner_bursts}) == 11)
check("owner tier includes both potholes and confirmed speed breakers",
      sum(item["is_pothole"] is True for item in owner_labels) == 37
      and sum(item["is_pothole"] is False for item in owner_labels) == 30)
external_owner = [
    item for item in owner_labels
    if item["metric_scope"] == "external_recording_semantic_only"]
check("external phone recordings are semantic regressions, not production accuracy",
      len(external_owner) == 30
      and all(item["semantic_regression_eligible"] is True
              and item["accuracy_metric_eligible"] is False
              and item["raw_camerax_accuracy_eligible"] is False
              for item in external_owner))
check("assistant-labelled opening grid is excluded from owner truth",
      all("tester-opening-grid-calming" not in item["case_id"] for item in owner_labels))
check("Desktop review is a separate diagnostic expected-reject tier",
      len(diagnostic_labels) == 150 and len(diagnostic_bursts) == 50
      and all(item["label"] is None and item["is_pothole"] is None
              and item["expected_decision"] == "reject" for item in diagnostic_labels)
      and all(item["accuracy_metric_eligible"] is False for item in diagnostic_labels))
check("all 3,314 Desktop windows survive as a separate annotation pool",
      len(pool) == 3314
      and sum(item["annotation_status"] == "diagnostic_expected_reject" for item in pool) == 50
      and sum(item["annotation_status"] == "unlabelled" for item in pool) == 3264)
check("model output is never promoted into a label",
      all(item["model_output_used_as_label"] is False for item in labels + bursts + pool))
check("every eval and annotation-pool record retains its exact source fingerprint",
      all(desktop_corpus.HEX_64.fullmatch(item["source_sha256"])
          for item in labels + bursts + pool))
desktop_source_hashes = {
    item["source_id"]: item["sha256"] for item in desktop_manifest["sources"]}
release_source_hashes = {
    source_id: item["sha256"] for source_id, item in release_manifest["sources"].items()}
check("source fingerprints resolve to the correct sealed source collection",
      all(item["source_sha256"] == desktop_source_hashes[item["source_group_id"]]
          for item in labels + bursts if item["collection"] == "desktop_curated")
      and all(item["source_sha256"] == release_source_hashes[item["source_group_id"]]
              for item in labels + bursts
              if item["collection"] == "private_release_gate")
      and all(item["source_sha256"] == item["image"]["sha256"]
              for item in labels if item["collection"] == "owner_manual_photos")
      and all(item["source_sha256"] == desktop_source_hashes[item["source_group_id"]]
              for item in pool))
check("every exported eval image is SHA-addressed and complete-frame only",
      len(labels) == 217 and len({item["image"]["sha256"] for item in labels}) == 217
      and all(item["image"]["path"] == f"images/{item['image']['sha256']}.jpg"
              and item["image"]["whole_frame"] is True
              and item["image"]["spatial_crop_tile_mask_or_roi"] is False
              for item in labels))
check("private eval is a locked holdout, not accidental training data",
      all(item["split"] == "private_eval" and item["training_eligible"] is False
              for item in labels + bursts))
check("indexes contain no absolute private path",
      not any(value.startswith("/") for value in json.dumps([labels, bursts, pool]).split('"')))

bad_ground_truth = copy.deepcopy(ground_truth)
bad_ground_truth["release_events"][0]["event_id"] = "tester-opening-grid-calming"
try:
    dataset.validate_ground_truth_manifest(bad_ground_truth, release_manifest)
    assistant_label_rejected = False
except dataset.DatasetError:
    assistant_label_rejected = True
check("ground-truth validation rejects overlapping selected/excluded events",
      assistant_label_rejected)

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    source = root / "source.jpg"
    Image.new("RGB", (32, 48), (25, 50, 75)).save(source, format="JPEG", quality=92)
    fingerprint = desktop_corpus.sha256_file(source)
    dataset_dir = root / "dataset"
    images_dir = dataset_dir / "images"
    dataset.copy_verified_frames([source], [fingerprint], (32, 48), images_dir)
    image = dataset.image_reference(fingerprint, 32, 48)
    sample = {
        "sample_id": "sample", "burst_id": "burst", "eval_tier": "owner_ground_truth",
        "is_pothole": True, "physical_event_group_id": "event", "image": image,
        "source_sha256": fingerprint,
        "accuracy_metric_eligible": True, "metric_scope": "manual_photo_semantic",
    }
    burst = {
        "burst_id": "burst", "eval_tier": "owner_ground_truth", "is_pothole": True,
        "physical_event_group_id": "event", "accuracy_metric_eligible": True,
        "source_sha256": fingerprint,
        "metric_scope": "manual_photo_semantic",
    }
    fake_desktop = {"corpus": {"source_inventory_sha256": "a" * 64}, "sources": []}
    fake_release = {"sources": []}
    fake_ground_truth = {}
    with patch.object(dataset, "expected_records", return_value=([sample], [burst], [])):
        dataset.write_indexes(
            dataset_dir, fake_desktop, fake_release, fake_ground_truth)
        try:
            dataset.validate_dataset(
                dataset_dir, fake_desktop, fake_release, fake_ground_truth)
            local_export_valid = True
        except dataset.DatasetError:
            local_export_valid = False
        (images_dir / f"{fingerprint}.jpg").write_bytes(b"tampered")
        try:
            dataset.validate_dataset(
                dataset_dir, fake_desktop, fake_release, fake_ground_truth)
            tamper_rejected = False
        except dataset.DatasetError:
            tamper_rejected = True
check("materialized index and complete image validate together", local_export_valid)
check("materialized image tampering fails closed", tamper_rejected)

if FAILURES:
    raise SystemExit(f"\n{len(FAILURES)} private eval dataset check(s) failed")

print("\nPRIVATE EVAL DATASET TEST PASS")
