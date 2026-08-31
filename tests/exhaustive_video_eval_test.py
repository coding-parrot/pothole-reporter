#!/usr/bin/env python3
"""Source-free contract checks for the exhaustive private-video evaluator."""

from __future__ import annotations

import base64
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
import exhaustive_video_eval as exhaustive  # noqa: E402
import private_drive_corpus as media  # noqa: E402


FAILURES: list[str] = []


def check(name: str, condition: bool) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        FAILURES.append(name)


source_manifest, source_raw = exhaustive.load_historical_v15_source_manifest(
    ROOT / "eval" / "exhaustive_video_sources.json")
labels, label_raw = exhaustive.load_label_manifest(
    ROOT / "eval" / "exhaustive_video_visual_labels.json", source_manifest)
sources = source_manifest["sources"]
inventory = source_manifest["inventory"]

expected_sources = [
    {
        "source_id": "downloads-segment-0001",
        "expected_basename": "segment_0001.mp4",
        "sha256": "feea330adaa09a5573f7d0b379170c5a03bb6a5ae07484d44299c4703ef74686",
        "bytes": 18_743_739,
        "frames": 1_780,
        "duration_seconds": 59.991878,
        "coded_width": 480,
        "coded_height": 720,
        "presented_width": 480,
        "presented_height": 720,
        "rotation_degrees": 0.0,
        "avg_frame_rate": "160200000/5399269",
        "r_frame_rate": "30/1",
    },
    {
        "source_id": "downloads-segment-0002",
        "expected_basename": "segment_0002.mp4",
        "sha256": "f9d51250ccdb955159db337490e30c46bc0f5422564aebdbe7b8b4ee48506c7d",
        "bytes": 15_255_272,
        "frames": 1_470,
        "duration_seconds": 48.990511,
        "coded_width": 480,
        "coded_height": 720,
        "presented_width": 480,
        "presented_height": 720,
        "rotation_degrees": 0.0,
        "avg_frame_rate": "9450000/314939",
        "r_frame_rate": "30/1",
    },
]
for actual, expected in zip(sources, expected_sources):
    check(f"{expected['expected_basename']} exact receipt is pinned",
          all(actual[field] == value for field, value in expected.items()))
    check(f"{expected['expected_basename']} codec is pinned", actual["codec"] == "h264")

check("exact two-video inventory seal is pinned",
      inventory == {
          "source_count": 2,
          "total_bytes": 33_999_011,
          "total_frames": 3_250,
          "total_duration_seconds": 108.982389,
          "expected_windows": 218,
          "expected_frame_references": 654,
          "source_inventory_sha256":
              "e9969f40d5df1a7cc902bc335fd8f616c55778c54f71e3966875ed05e2134014",
      })

centers = [media.vod_sample_times(source["duration_seconds"], 0.5) for source in sources]
check("production cadence enumerates every one of the 218 windows",
      [len(items) for items in centers] == [120, 98]
      and sum(map(len, centers)) == 218)
check("each exact-video window retains a three-frame minus/centre/plus burst",
      all(len(media.vod_burst_times(center, source["duration_seconds"], 0.4)) == 3
          for source, source_centers in zip(sources, centers)
          for center in source_centers))
check("first production burst is the boundary-safe 0.001/0.4/0.8 sequence",
      media.vod_burst_times(centers[0][0], sources[0]["duration_seconds"], 0.4)
      == [0.001, 0.4, 0.8])
check("final production bursts stay inside each exact source",
      all(media.vod_burst_times(source_centers[-1], source["duration_seconds"], 0.4)[-1]
          < source["duration_seconds"]
          for source, source_centers in zip(sources, centers)))

archive_contract, archive_receipt = exhaustive.historical_v15_contract()
results, results_raw = exhaustive.load_historical_v15_results_receipt(
    source_manifest, source_raw, labels, label_raw,
    ROOT / "eval" / "exhaustive_video_results.json")
audit = exhaustive.load_historical_v15_audit_receipt(
    results, results_raw, source_manifest, source_raw, labels, label_raw,
    ROOT / "eval" / "exhaustive_video_audit.json")
check("immutable v15 prompt and schema recompute the committed archive receipt",
      source_manifest["production_contract"] == archive_receipt
      and archive_receipt["model"] == "gpt-5.6"
      and archive_receipt["detail"] == "original"
      and archive_receipt["prompt_version"] == "pothole-binary-v15"
      and archive_receipt["schema_version"] == 7
      and archive_receipt["prompt_sha256"] ==
          "621866cba94700717358426bba70b274c8f23df081ae2da7db6e9199c97e1c98"
      and archive_receipt["schema_sha256"] ==
          "4a71363f4a78f0da8af8cdc58301c688bd434308733664680a5f5ef59d14945a")
try:
    exhaustive.load_source_manifest(ROOT / "eval" / "exhaustive_video_sources.json")
    current_source_loader_rejected_archive = False
except exhaustive.ExhaustiveEvalError:
    current_source_loader_rejected_archive = True
current_contract, current_receipt = exhaustive.current_contract()
try:
    exhaustive.validate_results_receipt(
        results, source_manifest, source_raw, labels, label_raw, current_receipt,
        contract=current_contract)
    current_results_loader_rejected_archive = False
except exhaustive.ExhaustiveEvalError:
    current_results_loader_rejected_archive = True
check("current-v19 source and result validators reject every v15 receipt",
      current_source_loader_rejected_archive and current_results_loader_rejected_archive)
check("archived exhaustive receipts record the historical 218-window detector run",
      audit["dataset_id"] == results["dataset_id"]
      and audit["execution"]["completed_requests"] == 218
      and audit["execution"]["accepted_windows"] == 13
      and audit["execution"]["rejected_windows"] == 205
      and audit["execution"]["error_windows"] == 0
      and audit["owner_confirmed_event_recall"]["physical_potholes"] == 2
      and audit["owner_confirmed_event_recall"]["physical_potholes_detected"] == 2
      and audit["owner_confirmed_event_recall"]["passed"] is True)
check("all 218 archived decisions remain committed without private payloads",
      len(results["results"]) == 218
      and results["constraints"]["response_ids_committed"] is False
      and results["constraints"]["model_free_text_committed"] is False
      and all(set(row) == {
          "schema_version", "window_id", "source_id", "window_index",
          "center_seconds", "request_sha256", "assessment_sha256",
          "decision_inputs", "decision",
      } for row in results["results"])
      and all("description" not in row and "response_id" not in row
              and "description" not in row["decision_inputs"]
              for row in results["results"]))
check("audit keeps eight assistant conflicts diagnostic instead of inventing truth",
      audit["assistant_diagnostics"]["ground_truth"] is False
      and audit["assistant_diagnostics"]["expected_reject_windows_accepted"] == 8
      and audit["constraints"]["assistant_annotations_are_ground_truth"] is False)

# All executable request/cache checks below use the current production contract.
contract, receipt = current_contract, current_receipt

policy = labels["policy"]
check("owner truth and assistant diagnostics remain separate from detector predictions",
      policy["owner_event_labels_are_human_confirmed"] is True
      and policy["window_annotations_are_independent_assistant_review"] is True
      and policy["assistant_candidate_events_are_ground_truth"] is False
      and policy["assistant_window_annotations_are_ground_truth"] is False
      and policy["production_detector_output_used_as_annotation"] is False
      and policy["unlabelled_windows_are_not_negatives"] is True
      and all(event["evidence_tier"] == "owner_ground_truth"
              and event["review_kind"] == "human_visual_review"
              and event["label_provenance"] == "owner_confirmed"
              for event in labels["events"]))
check("the two already owner-confirmed pothole events remain bound to exact sources",
      {event["origin_event_id"] for event in labels["events"]}
      >= {
          "owner-construction-drive-2026-08-28-a",
          "owner-construction-drive-2026-08-28-segment-2-second-4",
      })

review_by_source = {review["source_id"]: review for review in labels["reviews"]}
check("both full videos were decoded and every production window was independently reviewed",
      review_by_source["downloads-segment-0001"]["decoded_frames"] == 1_780
      and review_by_source["downloads-segment-0001"]["production_windows_reviewed"] == 120
      and review_by_source["downloads-segment-0002"]["decoded_frames"] == 1_470
      and review_by_source["downloads-segment-0002"]["production_windows_reviewed"] == 98
      and all(review["all_source_frames_decoded"] is True
              and review["whole_frame_only"] is True
              and review["spatial_crop_tile_mask_or_roi"] is False
              and review["review_provenance"] == "independent_assistant_full_video_review"
              for review in labels["reviews"]))
expanded_labels = exhaustive._window_visual_label_map(labels)
check("the exhaustive assistant review covers all 218 windows without default negatives",
      len(expanded_labels) == 218
      and sum(item["label"] == "pothole" for item in expanded_labels.values()) == 7
      and sum(item["label"] == "not_pothole" for item in expanded_labels.values()) == 200
      and sum(item["label"] is None for item in expanded_labels.values()) == 11
      and all(item["production_detector_output_used_as_annotation"] is False
              for item in expanded_labels.values()))
check("owner-confirmed physical events bind only the independently reviewed positive windows",
      [index for (source_id, index), item in expanded_labels.items()
       if source_id == "downloads-segment-0001" and item["label"] == "pothole"]
      == [69, 70, 71]
      and [index for (source_id, index), item in expanded_labels.items()
           if source_id == "downloads-segment-0002" and item["label"] == "pothole"]
      == [7, 8, 9, 10])

tampered_labels = copy.deepcopy(labels)
tampered_labels["events"][0]["review_kind"] = "model_output"
try:
    exhaustive.validate_label_manifest(
        tampered_labels, {source["source_id"]: source for source in sources})
    model_truth_rejected = False
except exhaustive.ExhaustiveEvalError:
    model_truth_rejected = True
check("detector-derived owner truth is rejected", model_truth_rejected)

overlapping_labels = copy.deepcopy(labels)
overlapping_labels["window_ranges"][1]["window_indices_inclusive"][0] = 7
try:
    exhaustive.validate_label_manifest(
        overlapping_labels, {source["source_id"]: source for source in sources})
    overlap_rejected = False
except exhaustive.ExhaustiveEvalError:
    overlap_rejected = True
check("overlapping assistant review ranges are rejected", overlap_rejected)

candidate_labels = copy.deepcopy(labels)
candidate_source = sources[0]
candidate_labels["events"].append({
    "event_id": "assistant-candidate-segment-1-opening",
    "source_id": candidate_source["source_id"],
    "source_sha256": candidate_source["sha256"],
    "source_interval_seconds": [0.1, 0.8],
    "label": "pothole",
    "evidence_tier": "assistant_candidate",
    "label_provenance": "independent_assistant_candidate",
    "review_kind": "independent_assistant_full_video_review",
    "origin_manifest": None,
    "origin_event_id": None,
    "capture_provenance": "native_mediarecorder_reconstruction",
    "raw_camerax_accuracy_eligible": False,
})
candidate_labels["window_ranges"][0]["window_indices_inclusive"] = [1, 7]
candidate_labels["window_ranges"].append({
    "source_id": candidate_source["source_id"],
    "window_indices_inclusive": [0, 0],
    "visual_label": "pothole",
    "expected_decision": "accept",
    "semantic_class": "assistant_candidate_pothole",
    "label_provenance": "independent_assistant_candidate_event",
    "physical_event_or_hard_negative_group_id": "assistant-candidate-segment-1-opening",
    "uncertainty_reason": None,
})
candidate_review = next(review for review in candidate_labels["reviews"]
                        if review["source_id"] == candidate_source["source_id"])
candidate_review["positive_windows"] += 1
candidate_review["negative_windows"] -= 1
candidate_review["distinct_assistant_candidate_potholes"] += 1
try:
    exhaustive.validate_label_manifest(
        candidate_labels, {source["source_id"]: source for source in sources})
    candidate_schema_supported = True
except exhaustive.ExhaustiveEvalError:
    candidate_schema_supported = False
check("independent review can record a candidate without promoting it to owner truth",
      candidate_schema_supported)
if candidate_schema_supported:
    candidate_expanded = exhaustive._window_visual_label_map(candidate_labels)
    candidate_windows = [{
        **stub,
        "visual_label": candidate_expanded[(stub["source_id"], stub["window_index"])],
    } for stub in exhaustive.expected_window_stubs(source_manifest)]
    candidate_bindings = exhaustive._label_bindings(candidate_labels, candidate_windows)
else:
    candidate_bindings = []
check("assistant candidates are bound for diagnostics but excluded from the accuracy gate",
      len(candidate_bindings) == 3
      and sum(binding["accuracy_metric_eligible"] is True
              for binding in candidate_bindings) == 2
      and next(binding for binding in candidate_bindings
               if binding["event_id"] == "assistant-candidate-segment-1-opening")[
                   "accuracy_metric_eligible"] is False)

misbound_candidate = copy.deepcopy(candidate_labels)
misbound_candidate["events"][-1]["source_interval_seconds"] = [10.0, 11.0]
try:
    exhaustive.validate_label_manifest(
        misbound_candidate, {source["source_id"]: source for source in sources})
    temporally_misbound_event_rejected = False
except exhaustive.ExhaustiveEvalError:
    temporally_misbound_event_rejected = True
check("positive windows must overlap their claimed physical-event interval",
      temporally_misbound_event_rejected)

tampered_audit = copy.deepcopy(audit)
tampered_audit["accepted_candidate_clustering"]["clusters"][0][
    "accepted_window_indices"].pop()
try:
    exhaustive.validate_historical_v15_audit_receipt(
        tampered_audit, results, results_raw, source_manifest, source_raw,
        labels, label_raw)
    incomplete_audit_rejected = False
except exhaustive.ExhaustiveEvalError:
    incomplete_audit_rejected = True
check("audit rejects an omitted accepted detector window", incomplete_audit_rejected)

tampered_results = copy.deepcopy(results)
tampered_results["results"][0]["decision"] = "accept"
try:
    exhaustive.validate_historical_v15_results_receipt(
        tampered_results, source_manifest, source_raw, labels, label_raw)
    inconsistent_decision_rejected = False
except exhaustive.ExhaustiveEvalError:
    inconsistent_decision_rejected = True
check("result receipt rejects a decision inconsistent with its sealed inputs",
      inconsistent_decision_rejected)

duplicated_results = copy.deepcopy(results)
duplicated_results["results"][1] = copy.deepcopy(duplicated_results["results"][0])
try:
    exhaustive.validate_historical_v15_results_receipt(
        duplicated_results, source_manifest, source_raw, labels, label_raw)
    duplicate_result_rejected = False
except exhaustive.ExhaustiveEvalError:
    duplicate_result_rejected = True
check("result receipt rejects duplicate or reordered production windows",
      duplicate_result_rejected)

check("audit generation is deterministic and derives all four exact candidate clusters",
      exhaustive.build_audit_receipt(
          results, results_raw, source_manifest, source_raw, labels, label_raw,
          archive_receipt, contract=archive_contract,
          decision_function=exhaustive.historical_contracts.v15_decision) == audit
      and [cluster["accepted_window_indices"] for cluster in
           audit["accepted_candidate_clustering"]["clusters"]]
      == [[65, 66, 68, 69, 70], [89, 90, 91], [8, 9], [73, 74, 75]])

private_locators_rejected = True
for private_locator in (
        "/Users/private/segment_0001.mp4",
        " /Users/private/segment_0001.mp4",
        "~/Downloads/segment_0001.mp4",
        "file:///Users/private/segment_0001.mp4",
        r"\\server\share\segment_0001.mp4",
        "Path: /Users/private/segment_0001.mp4",
        "source=/Users/private/segment_0001.mp4",
        "safe\n/Users/private/segment_0001.mp4",
        "Note: file:///Users/private/segment_0001.mp4",
        "file:/Users/private/segment_0001.mp4",
        "file:C:/Users/private/segment_0001.mp4",
        r"Path: \Users\private\segment_0001.mp4"):
    tampered_sources = copy.deepcopy(source_manifest)
    tampered_sources["description"] = private_locator
    try:
        exhaustive.validate_historical_v15_source_manifest(tampered_sources)
        private_locators_rejected = False
    except exhaustive.ExhaustiveEvalError:
        pass
check("committed receipts reject absolute, URI, tilde and UNC private locators",
      private_locators_rejected)

check("frame selection is deterministic and records end clamping",
      exhaustive.select_frame([0.0, 0.5, 1.0], 0.5) == (1, 0.5, False)
      and exhaustive.select_frame([0.0, 0.5, 1.0], 0.6) == (2, 1.0, False)
      and exhaustive.select_frame([0.0, 0.5, 1.0], 1.1) == (2, 1.0, True))

valid_frame_metadata = {
    "ordinal": 0,
    "requested_timestamp_seconds": 0.4,
    "selected_frame_index": 10,
    "selected_pts_seconds": 0.41,
    "duration_boundary_clamped": False,
    "path": "sources/downloads-segment-0001/frames/f00010.jpg",
    "sha256": "a" * 64,
    "bytes": 100,
    "width": sources[0]["presented_width"],
    "height": sources[0]["presented_height"],
    "format": "JPEG",
    "full_frame": True,
    "source_sha256": sources[0]["sha256"],
    "capture_provenance": source_manifest["privacy"]["capture_provenance"],
}
exhaustive._validate_frame_record_metadata(
    valid_frame_metadata, 0, 0.4, sources[0]["source_id"], sources[0],
    source_manifest["privacy"]["capture_provenance"])
invalid_frame_metadata_rejected = True
for field, invalid_value in (
        ("selected_frame_index", -999),
        ("selected_pts_seconds", 999999.0),
        ("duration_boundary_clamped", True),
        ("path", "sources/downloads-segment-0001/frames/f00999.jpg")):
    tampered_frame = copy.deepcopy(valid_frame_metadata)
    tampered_frame[field] = invalid_value
    try:
        exhaustive._validate_frame_record_metadata(
            tampered_frame, 0, 0.4, sources[0]["source_id"], sources[0],
            source_manifest["privacy"]["capture_provenance"])
        invalid_frame_metadata_rejected = False
    except exhaustive.ExhaustiveEvalError:
        pass
check("impossible frame indexes, PTS, clamp flags and paths fail closed",
      invalid_frame_metadata_rejected)

with tempfile.TemporaryDirectory(prefix="exhaustive-source-free-") as temporary:
    temporary_root = Path(temporary)
    frame_paths: list[Path] = []
    for index, colour in enumerate(((220, 20, 20), (20, 220, 20), (20, 20, 220))):
        path = temporary_root / f"frame-{index}.jpg"
        Image.new("RGB", (480, 720), colour).save(path, "JPEG", quality=96)
        frame_paths.append(path)
    request, transforms = exhaustive.build_production_request(frame_paths, contract)
    image_items = [item for item in request["input"][0]["content"]
                   if item["type"] == "input_image"]
    decoded_sizes = []
    for item in image_items:
        encoded = item["image_url"].partition(",")[2]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
            decoded_sizes.append(image.size)
    check("production request contains context plus three chronological images",
          len(image_items) == 4
          and [item["role"] for item in transforms]
          == ["primary_context"] + ["chronological_full_frame"] * 3)
    check("every prepared image preserves the complete exact-video frame geometry",
          decoded_sizes == [(480, 720)] * 4
          and all(item["full_frame"] is True for item in transforms))
    check("request uses exact production detail, schema and spending controls",
          all(item["detail"] == "original" for item in image_items)
          and request["model"] == receipt["model"]
          and request["store"] is False
          and request["max_output_tokens"] == receipt["max_output_tokens"]
          and request["text"]["format"]["schema"] == contract["schema"])

environment = dict(os.environ)
environment.pop("OPENAI_API_KEY", None)
offline_check = subprocess.run(
    [sys.executable, str(ROOT / "eval" / "exhaustive_video_eval.py"), "--check-manifest",
     "--audit-receipt", "/does/not/exist/audit.json",
     "--results-receipt", "/does/not/exist/results.json"],
    cwd=ROOT, env=environment, text=True, capture_output=True,
)
check("current manifest CLI rejects the v15 archive before receipts or API access",
      offline_check.returncode != 0
      and "drifted from production" in offline_check.stderr
      and "OPENAI_API_KEY" not in offline_check.stderr)

offline_audit = subprocess.run(
    [sys.executable, str(ROOT / "eval" / "exhaustive_video_eval.py"), "--verify-audit"],
    cwd=ROOT, env=environment, text=True, capture_output=True,
)
check("current audit CLI cannot present the v15 archive as v19 coverage",
      offline_audit.returncode != 0
      and "drifted from production" in offline_audit.stderr
      and "OPENAI_API_KEY" not in offline_audit.stderr)

unbounded_paid = subprocess.run(
    [sys.executable, str(ROOT / "eval" / "exhaustive_video_eval.py"), "--paid-run"],
    cwd=ROOT, env=environment, text=True, capture_output=True,
)
check("paid replay rejects archived input before budget, media, or API access",
      unbounded_paid.returncode != 0
      and "drifted from production" in unbounded_paid.stderr
      and "OPENAI_API_KEY" not in unbounded_paid.stderr)

paid_args = exhaustive.make_parser().parse_args(
    ["--paid-run", "--max-calls", "654", "--resume",
     "--reuse-cache-from", "/private/old-run"])
check("paid replay exposes the exact 2-of-3 worst-case bound and resumable mode",
      paid_args.paid_run and paid_args.max_calls == 654 and paid_args.resume
      and paid_args.reuse_cache_from == Path("/private/old-run"))
check("exhaustive preflight accounts for every possible policy attempt",
      exhaustive.maximum_policy_calls(218) == 654
      and exhaustive.maximum_policy_calls(0) == 0)

with tempfile.TemporaryDirectory(prefix="exhaustive-cache-rebind-") as temporary:
    cache_path = Path(temporary) / "result.json"
    cache_window = {"window_id": "window-1", "request_sha256": "a" * 64}
    cache_index = {"dataset_id": "b" * 64}
    compatible_record = {
        "schema_version": exhaustive.RESULT_SCHEMA,
        "dataset_id": "c" * 64,
        "window_id": cache_window["window_id"],
        "request_sha256": cache_window["request_sha256"],
        "response_id": "resp_compatible",
        "assessment": {
            "is_pothole": False,
            "looks_like_speed_breaker": False,
            "image_quality": "usable",
            "surface_type": "bituminous_asphalt",
            "on_drivable_surface": True,
            "has_localized_cavity": False,
            "has_unambiguous_lower_interior": False,
            "has_broken_edge_or_rim": False,
            "has_depth_or_surface_loss": False,
            "temporal_consistency": "consistent",
            "size": None,
            "description": "No localized cavity is visible.",
        },
        "decision": "reject",
        "attempt_count": 1,
    }
    compatible_record["attempts"] = [{
        "attempt_number": 1,
        "response_id": compatible_record["response_id"],
        "assessment": compatible_record["assessment"],
    }]
    cache_path.write_text(json.dumps(compatible_record))
    try:
        exhaustive._load_cached(cache_path, cache_index, cache_window, contract["schema"])
        strict_dataset_rejected = False
    except exhaustive.ExhaustiveEvalError:
        strict_dataset_rejected = True
    rebound = exhaustive._load_cached(
        cache_path, cache_index, cache_window, contract["schema"],
        allow_compatible_dataset=True)
check("compatible-cache reuse requires an exact request before rebinding content identity",
      strict_dataset_rejected and rebound is not None
      and rebound["dataset_id"] == cache_index["dataset_id"]
      and rebound["request_sha256"] == cache_window["request_sha256"])

temporary_yes = {
    **compatible_record["assessment"],
    "is_pothole": True,
    "surface_type": "temporary_drivable_surface",
    "has_localized_cavity": True,
    "has_unambiguous_lower_interior": True,
    "has_broken_edge_or_rim": True,
    "has_depth_or_surface_loss": True,
    "size": "medium",
}
temporary_no = {
    **temporary_yes,
    "is_pothole": False,
    "has_localized_cavity": False,
    "has_unambiguous_lower_interior": False,
    "has_broken_edge_or_rim": False,
    "has_depth_or_surface_loss": False,
    "size": None,
}


def bounded_cache_record(assessments):
    remaining = list(assessments)
    outcome = exhaustive.production_eval.run_bounded_detection_policy(
        lambda: remaining.pop(0), mode="drive", source_view_count=3)
    attempts = [{
        "attempt_number": number,
        "response_id": f"resp_{number}",
        "assessment": assessment,
    } for number, assessment in enumerate(assessments, 1)]
    representative = next((
        item for item in reversed(attempts)
        if item["assessment"] == outcome.assessment), attempts[-1])
    return {
        "schema_version": exhaustive.RESULT_SCHEMA,
        "dataset_id": cache_index["dataset_id"],
        "window_id": cache_window["window_id"],
        "request_sha256": cache_window["request_sha256"],
        "response_id": representative["response_id"],
        "assessment": outcome.assessment,
        "decision": outcome.decision,
        "attempt_count": outcome.attempts_started,
        "attempts": attempts,
    }


bounded_records_valid = True
for assessments in (
        [temporary_yes, temporary_yes],
        [temporary_no, temporary_no],
        [temporary_yes, temporary_no, temporary_yes],
        [temporary_no, temporary_yes, temporary_no]):
    try:
        exhaustive.validate_result_record(
            bounded_cache_record(assessments), cache_index, cache_window,
            contract["schema"])
    except exhaustive.ExhaustiveEvalError:
        bounded_records_valid = False
check("cached exhaustive results preserve 2-call agreement and 3-call split votes",
      bounded_records_valid)

incomplete_vote = bounded_cache_record([temporary_yes, temporary_yes])
incomplete_vote["attempts"] = incomplete_vote["attempts"][:1]
incomplete_vote["attempt_count"] = 1
incomplete_vote["assessment"] = {**temporary_yes, "is_pothole": False, "size": None}
incomplete_vote["decision"] = "reject"
incomplete_vote["response_id"] = "resp_1"
try:
    exhaustive.validate_result_record(
        incomplete_vote, cache_index, cache_window, contract["schema"])
    incomplete_vote_rejected = False
except exhaustive.ExhaustiveEvalError:
    incomplete_vote_rejected = True
check("one eligible temporary result cannot masquerade as a complete cached vote",
      incomplete_vote_rejected)
event_coverage = [
    {"event_passed": True, "accuracy_metric_eligible": True},
    {"event_passed": True, "accuracy_metric_eligible": True},
    {"event_passed": False, "accuracy_metric_eligible": False},
]
check("release gate uses only human-confirmed physical-event recall",
      exhaustive.accuracy_gate_passes(0, event_coverage) is True
      and exhaustive.accuracy_gate_passes(0, [{
          "event_passed": False, "accuracy_metric_eligible": True}]) is False
      and exhaustive.accuracy_gate_passes(1, event_coverage) is False
      and exhaustive.accuracy_gate_passes(0, []) is False)
regression_rows = [
    {"window_id": window_id, "status": "ok", "decision": "reject"}
    for window_id in exhaustive.REQUIRED_ASSISTANT_REGRESSION_WINDOW_IDS
]
check("release gate separately requires named assistant-reviewed hard negatives",
      exhaustive.required_regression_gate_passes(regression_rows) is True
      and exhaustive.required_regression_gate_passes([
          {**row, "decision": "accept"} if index == 0 else row
          for index, row in enumerate(regression_rows)
      ]) is False
      and exhaustive.release_gate_passes(0, event_coverage, regression_rows) is True
      and exhaustive.release_gate_passes(0, event_coverage, regression_rows[:-1]) is False)
regression_windows = [{
    "window_id": window_id,
    "visual_label": {
        "label": "not_pothole",
        "expected_decision": "reject",
        "label_provenance": "independent_assistant_full_video_review",
        "physical_event_or_hard_negative_group_id":
            "seg2-hn-rough-aggregate-37-43s",
    },
} for window_id in exhaustive.REQUIRED_ASSISTANT_REGRESSION_WINDOW_IDS]
try:
    exhaustive.validate_required_regression_labels(regression_windows)
    exact_regression_labels_valid = True
except exhaustive.ExhaustiveEvalError:
    exact_regression_labels_valid = False
invalid_regression_windows = copy.deepcopy(regression_windows)
invalid_regression_windows[0]["visual_label"]["label_provenance"] = "owner_confirmed"
try:
    exhaustive.validate_required_regression_labels(invalid_regression_windows)
    overstated_regression_rejected = False
except exhaustive.ExhaustiveEvalError:
    overstated_regression_rejected = True
check("named hard negatives are bound to independent review provenance",
      exact_regression_labels_valid and overstated_regression_rejected)

ignore_check = subprocess.run(
    ["git", "check-ignore", "-q", str(exhaustive.DEFAULT_WORK_ROOT / "sentinel")],
    cwd=ROOT,
)
check("all source-derived media and paid outputs live under an ignored work root",
      ignore_check.returncode == 0
      and exhaustive.DEFAULT_WORK_ROOT.is_relative_to(ROOT / "eval")
      and exhaustive.require_private_artifact_root(
          exhaustive.DEFAULT_WORK_ROOT / "custom", "test private root")
      == (exhaustive.DEFAULT_WORK_ROOT / "custom").resolve())

try:
    exhaustive.require_private_artifact_root(ROOT / "eval" / "unsafe-private", "unsafe test")
    unignored_repo_path_rejected = False
except exhaustive.ExhaustiveEvalError:
    unignored_repo_path_rejected = True
check("custom private artifacts cannot be written to an unignored repository path",
      unignored_repo_path_rejected)
with tempfile.TemporaryDirectory(prefix="exhaustive-private-outside-git-") as temporary:
    external_private_root = Path(temporary) / "artifacts"
    external_private_allowed = exhaustive.require_private_artifact_root(
        external_private_root, "external private test") == external_private_root.resolve()
check("private artifacts may live outside a Git worktree",
      external_private_allowed)

with tempfile.TemporaryDirectory(prefix="exhaustive-private-git-guard-") as temporary:
    guard_repo = Path(temporary) / "repo"
    guard_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=guard_repo, check=True)
    subprocess.run(["git", "config", "user.email", "eval@example.invalid"],
                   cwd=guard_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Eval Guard"],
                   cwd=guard_repo, check=True)
    (guard_repo / ".gitignore").write_text("safe/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=guard_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "tracked ignore"], cwd=guard_repo, check=True)
    committed_ignore_allowed = exhaustive.require_private_artifact_root(
        guard_repo / "safe" / "artifacts", "committed ignore test").is_absolute()
    (guard_repo / ".gitignore").write_text("safe/\nworking-only/\n")
    try:
        exhaustive.require_private_artifact_root(
            guard_repo / "working-only" / "artifacts", "working ignore test")
        working_only_ignore_rejected = False
    except exhaustive.ExhaustiveEvalError:
        working_only_ignore_rejected = True
    local_excludes = Path(temporary) / "local-excludes"
    local_excludes.write_text("config-only/\n")
    subprocess.run(["git", "config", "core.excludesFile", str(local_excludes)],
                   cwd=guard_repo, check=True)
    try:
        exhaustive.require_private_artifact_root(
            guard_repo / "config-only" / "artifacts", "local ignore test")
        local_only_ignore_rejected = False
    except exhaustive.ExhaustiveEvalError:
        local_only_ignore_rejected = True
check("privacy roots require the exact ignore rule committed in repository history",
      committed_ignore_allowed and working_only_ignore_rejected
      and local_only_ignore_rejected)

suite_id = exhaustive.suite_identity(source_raw, label_raw, archive_receipt)
windows_hash = results["receipts"]["materialized_windows_sha256"]
bindings_hash = results["receipts"]["event_bindings_sha256"]
content_id = exhaustive.materialized_identity(suite_id, windows_hash, bindings_hash)
changed_content_id = exhaustive.materialized_identity(
    suite_id, "0" * 64 if windows_hash != "0" * 64 else "1" * 64, bindings_hash)
check("suite and materialized-content identities are deterministic and separate",
      len(suite_id) == 64 and len(content_id) == 64
      and content_id == results["dataset_id"]
      and content_id != changed_content_id)

sealed_index = {
    "suite_identity": suite_id,
    "dataset_id": content_id,
    "artifacts": {
        "windows_sha256": windows_hash,
        "visual_label_bindings_sha256": bindings_hash,
    },
    "counts": {"unique_retained_frames": results["counts"]["unique_full_frames_retained"]},
}
sealed_windows = [{
    "window_id": row["window_id"],
    "source_id": row["source_id"],
    "window_index": row["window_index"],
    "center_seconds": row["center_seconds"],
    "request_sha256": row["request_sha256"],
} for row in results["results"]]
exhaustive.validate_results_against_materialized(results, sealed_index, sealed_windows)
tampered_index = copy.deepcopy(sealed_index)
tampered_index["artifacts"]["windows_sha256"] = (
    "0" * 64 if windows_hash != "0" * 64 else "1" * 64)
tampered_index["dataset_id"] = changed_content_id
try:
    exhaustive.validate_results_against_materialized(
        results, tampered_index, sealed_windows)
    old_audit_rejected_after_content_change = False
except exhaustive.ExhaustiveEvalError:
    old_audit_rejected_after_content_change = True
check("coherent materialized-content changes cannot retain the committed audit identity",
      old_audit_rejected_after_content_change)

if FAILURES:
    raise SystemExit(f"\n{len(FAILURES)} exhaustive-video evaluator check(s) failed")

print("\nEXHAUSTIVE VIDEO EVAL TEST PASS")
