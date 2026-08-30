#!/usr/bin/env python3
"""Evaluate audited RAD three-frame events with the exact shipped Drive contract.

The RAD source label ``RoadDamages`` is intentionally *not* treated as pothole truth:
it combines several anomaly types.  Accuracy gates use only named audited labels, while
unreviewed RAD anomalies are reported as a separate review queue.

Images are loaded lazily and every model view preserves its complete source frame.
This runner delegates image preparation, prompt assembly, schema, request construction,
and the final binary decision to ``eval/run_eval.py`` so an evaluation cannot silently
become easier than production.
"""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from PIL import Image, UnidentifiedImageError

import private_release_gate
import run_eval as production_eval

try:
    import rad_dataset
except ImportError:  # The standalone audited-manifest format does not need the index builder.
    rad_dataset = None


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "eval" / "rad" / "audited-manifest.json"
DEFAULT_DATASET_ROOT = ROOT / "eval" / ".rad-data"
DEFAULT_OUT = ROOT / "eval" / "results" / "rad-production"
API_URL = "https://api.openai.com/v1/responses"
EXACT_MODEL = "gpt-5.6"
EXACT_DETAIL = "original"
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
AUDITED_LABELS = {"pothole", "not_pothole", "speed_breaker"}
BROAD_LABEL = "broad_unreviewed_anomaly"
LABEL_ALIASES = {
    "broad_road_anomaly_unreviewed": BROAD_LABEL,
    "road_anomaly_unreviewed": BROAD_LABEL,
    "unreviewed_road_anomaly": BROAD_LABEL,
    "road_damages_unreviewed": BROAD_LABEL,
    "roaddamages": BROAD_LABEL,
}
ALL_LABELS = AUDITED_LABELS | {BROAD_LABEL}
AUDIT_STATES = {"audited", "locked"}
RETRYABLE_HTTP_STATUS = {408, 409, 429, 500, 502, 503, 504}
MAX_CONCURRENCY = 8


class RadEvalError(RuntimeError):
    """Expected fail-closed validation or evaluation failure."""


@dataclass(frozen=True)
class Frame:
    path: Path
    relative_path: str
    sequence: int
    timestamp_ms: float | None
    sha256: str
    width: int
    height: int


@dataclass(frozen=True)
class Event:
    event_id: str
    split: str
    source_video_id: str
    label: str
    audit_status: str
    labelled_by: str | None
    primary_index: int
    frames: tuple[Frame, Frame, Frame]
    source_label: str | None
    capture_provenance: str
    ambiguous_source: bool

    @property
    def locked(self) -> bool:
        return self.audit_status == "locked"


@dataclass(frozen=True)
class Contract:
    prompt: str
    schema: dict[str, Any]
    model: str
    detail: str
    prompt_version: str
    schema_version: int
    max_output_tokens: int
    prompt_sha256: str
    schema_sha256: str
    production_eval_sha256: str


@dataclass(frozen=True)
class Job:
    event: Event
    job_id: str
    cache_key: str


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


def git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _normalise_label(value: Any) -> str:
    label = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return LABEL_ALIASES.get(label, label)


def _within_root(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _positive_number(value: Any, where: str) -> float:
    if type(value) not in {int, float} or value < 0:
        raise RadEvalError(f"{where} must be a non-negative number")
    return float(value)


def _frame_sequence(raw: dict[str, Any], where: str) -> int:
    for field in ("sequence", "frame_index", "index"):
        if field in raw:
            value = raw[field]
            if type(value) is not int or value < 0:
                raise RadEvalError(f"{where}.{field} must be a non-negative integer")
            return value
    raise RadEvalError(f"{where} needs sequence/frame_index/index chronology")


def _frame_timestamp(raw: dict[str, Any], where: str) -> float | None:
    for field, multiplier in (("timestamp_ms", 1.0), ("time_ms", 1.0),
                              ("timestamp_seconds", 1000.0), ("time_seconds", 1000.0)):
        if field in raw:
            return _positive_number(raw[field], f"{where}.{field}") * multiplier
    return None


def _verify_frame(raw: Any, root: Path, where: str) -> Frame:
    if not isinstance(raw, dict):
        raise RadEvalError(f"{where} must be an object with full-frame provenance")
    forbidden_inputs = {"crop_path", "cropped_path", "roi_path", "tile_path", "masked_path"}
    bad = sorted(forbidden_inputs & set(raw))
    if bad:
        raise RadEvalError(f"{where} contains prohibited alternate image input(s): {bad}")
    view = raw.get("view", raw.get("role", "full_frame"))
    if view not in {"full_frame", "complete_frame"} or raw.get("full_frame", True) is not True:
        raise RadEvalError(f"{where} must identify the complete source frame")
    relative = raw.get("path") or raw.get("file")
    if not isinstance(relative, str) or not relative.strip():
        raise RadEvalError(f"{where}.path must be a non-empty string")
    supplied = Path(relative).expanduser()
    path = supplied if supplied.is_absolute() else root / supplied
    if not _within_root(root, path):
        raise RadEvalError(f"{where}.path escapes the dataset root")
    if not path.is_file():
        raise RadEvalError(f"{where}.path is missing: {relative}")
    expected_hash = str(raw.get("sha256") or "").lower()
    if not HEX_64.fullmatch(expected_hash):
        raise RadEvalError(f"{where}.sha256 must pin the exact full-frame bytes")
    actual_hash = sha256_file(path)
    if actual_hash != expected_hash:
        raise RadEvalError(f"{where}.sha256 does not match the source image")
    try:
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
    except (OSError, UnidentifiedImageError) as error:
        raise RadEvalError(f"{where}.path is not a valid image") from error
    if width <= 0 or height <= 0:
        raise RadEvalError(f"{where}.path has invalid dimensions")
    for field, actual in (("width", width), ("height", height)):
        if field in raw and (type(raw[field]) is not int or raw[field] != actual):
            raise RadEvalError(f"{where}.{field} does not match the image")
    return Frame(
        path=path.resolve(),
        relative_path=path.resolve().relative_to(root.resolve()).as_posix(),
        sequence=_frame_sequence(raw, where),
        timestamp_ms=_frame_timestamp(raw, where),
        sha256=actual_hash,
        width=width,
        height=height,
    )


def _event_label(raw: dict[str, Any]) -> tuple[str, str, str | None]:
    audit = raw.get("audit")
    if audit is not None and not isinstance(audit, dict):
        raise RadEvalError("event.audit must be an object")
    audit = audit or {}
    label = _normalise_label(
        audit.get("label", raw.get("audit_label", raw.get("label")))
    )
    audit_status = str(
        audit.get("status", raw.get("audit_status", "unreviewed"))
    ).strip().lower()
    labelled_by = audit.get("labelled_by", audit.get("reviewer", raw.get("labelled_by")))
    if labelled_by is not None:
        labelled_by = str(labelled_by).strip() or None
    return label, audit_status, labelled_by


def validate_manifest(payload: Any, dataset_root: Path) -> tuple[dict[str, Any], list[Event]]:
    if not isinstance(payload, dict):
        raise RadEvalError("RAD manifest must be a JSON object")
    version = payload.get("version", payload.get("schema_version"))
    if version != 1:
        raise RadEvalError("RAD manifest version must be 1")
    metadata = payload.get("dataset")
    if not isinstance(metadata, dict):
        raise RadEvalError("RAD manifest needs dataset provenance metadata")
    dataset_name = str(metadata.get("name") or "")
    if "road anomaly" not in dataset_name.lower() and dataset_name.strip().lower() != "rad":
        raise RadEvalError("dataset.name must identify RAD—Road Anomaly Detection")
    for field in ("source_url", "license"):
        if not isinstance(metadata.get(field), str) or not metadata[field].strip():
            raise RadEvalError(f"dataset.{field} is required provenance")
    raw_events = payload.get("events", payload.get("samples"))
    if not isinstance(raw_events, list) or not raw_events:
        raise RadEvalError("RAD manifest needs a non-empty events array")

    seen_ids: set[str] = set()
    events: list[Event] = []
    for index, raw in enumerate(raw_events):
        where = f"event {index}"
        if not isinstance(raw, dict):
            raise RadEvalError(f"{where} must be an object")
        event_id = str(raw.get("id", raw.get("event_id", ""))).strip()
        if not event_id or event_id in seen_ids:
            raise RadEvalError(f"{where} has a missing or duplicate ID")
        seen_ids.add(event_id)
        split = str(raw.get("split") or "").strip()
        if not split:
            raise RadEvalError(f"event {event_id} needs a split")
        source_video_id = str(
            raw.get("source_video_id", raw.get("source_video", raw.get("video_id", "")))
        ).strip()
        if not source_video_id:
            raise RadEvalError(f"event {event_id} needs source_video_id")
        label, audit_status, labelled_by = _event_label(raw)
        if label not in ALL_LABELS:
            raise RadEvalError(f"event {event_id} has unsupported label {label!r}")
        if label in AUDITED_LABELS:
            if audit_status not in AUDIT_STATES or not labelled_by:
                raise RadEvalError(
                    f"event {event_id} label {label} requires audited status and reviewer"
                )
        elif audit_status != "unreviewed":
            raise RadEvalError(
                f"event {event_id} broad RAD anomaly must remain explicitly unreviewed"
            )
        source_label = raw.get("source_label", raw.get("rad_label"))
        source_label = str(source_label).strip() if source_label is not None else None
        if (source_label or "").replace("_", "").lower() == "roaddamages" \
                and label in AUDITED_LABELS and audit_status not in AUDIT_STATES:
            raise RadEvalError(
                f"event {event_id} cannot convert RAD RoadDamages into pothole truth without audit"
            )
        capture_provenance = str(raw.get("capture_provenance", "rad_full_frame")).strip()
        if "full_frame" not in capture_provenance:
            raise RadEvalError(f"event {event_id} capture provenance must preserve full frames")
        ambiguous_source = raw.get("ambiguous_source", False)
        if type(ambiguous_source) is not bool:
            raise RadEvalError(f"event {event_id} ambiguous_source must be boolean")

        raw_frames = raw.get("frames")
        if not isinstance(raw_frames, list) or len(raw_frames) != 3:
            raise RadEvalError(f"event {event_id} must contain exactly three source frames")
        frames = tuple(
            _verify_frame(frame, dataset_root, f"event {event_id} frame {frame_index}")
            for frame_index, frame in enumerate(raw_frames)
        )
        sequences = [frame.sequence for frame in frames]
        if not all(left < right for left, right in zip(sequences, sequences[1:])):
            raise RadEvalError(f"event {event_id} frames are not strictly chronological")
        timestamps = [frame.timestamp_ms for frame in frames]
        if any(value is not None for value in timestamps):
            if any(value is None for value in timestamps) or not all(
                    left < right for left, right in zip(timestamps, timestamps[1:])):
                raise RadEvalError(f"event {event_id} timestamps are not strictly chronological")
        primary_index = raw.get("primary_index", 1)
        if type(primary_index) is not int or primary_index not in {0, 1, 2}:
            raise RadEvalError(f"event {event_id} primary_index must be 0, 1 or 2")
        events.append(Event(
            event_id=event_id,
            split=split,
            source_video_id=source_video_id,
            label=label,
            audit_status=audit_status,
            labelled_by=labelled_by,
            primary_index=primary_index,
            frames=frames,  # type: ignore[arg-type]
            source_label=source_label,
            capture_provenance=capture_provenance,
            ambiguous_source=ambiguous_source,
        ))
    return metadata, events


def _index_to_manifest(
    payload: dict[str, Any], index_raw: bytes, audit_payload: dict[str, Any] | None,
    *, require_official_release: bool = False,
) -> dict[str, Any]:
    """Adapt the deterministic ``rad_dataset.py`` index to the audited event schema."""
    schema_version = payload.get("schema_version")
    if not (isinstance(schema_version, str) and schema_version.startswith("rad-dataset-index-")):
        if require_official_release:
            raise RadEvalError(
                "release gate requires the sealed official RAD v3 dataset index; "
                "standalone manifests are test fixtures only"
            )
        return payload
    current_schema = getattr(rad_dataset, "INDEX_SCHEMA_VERSION", None)
    if schema_version != current_schema:
        raise RadEvalError(
            "unsupported RAD dataset index schema: "
            f"expected {current_schema or 'the installed dataset adapter'}, got {schema_version}"
        )
    dataset = payload.get("dataset")
    counts = payload.get("counts")
    frames = payload.get("frames")
    events = payload.get("events")
    if not isinstance(dataset, dict) or not isinstance(frames, list) or not isinstance(events, list):
        raise RadEvalError("RAD dataset index has invalid dataset/frames/events fields")
    if not isinstance(counts, dict) or payload.get("source_dataset_complete") is not True:
        raise RadEvalError(
            "RAD evaluation requires the complete, count-verified v3 image dataset"
        )
    if require_official_release:
        if rad_dataset is None:
            raise RadEvalError("release gate cannot validate the official RAD v3 index")
        try:
            rad_dataset.validate_official_release_index(payload)
        except rad_dataset.RadDatasetError as error:
            raise RadEvalError(str(error)) from error
    frame_by_id: dict[str, dict[str, Any]] = {}
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict) or not isinstance(frame.get("id"), str):
            raise RadEvalError(f"RAD dataset index frame {index} has no ID")
        if frame["id"] in frame_by_id:
            raise RadEvalError(f"RAD dataset index has duplicate frame ID {frame['id']}")
        frame_by_id[frame["id"]] = frame

    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise RadEvalError("RAD dataset index needs collision provenance")
    ambiguous_sources = provenance.get("ambiguous_source_videos")
    if (
        not isinstance(ambiguous_sources, list)
        or any(not isinstance(value, str) or not value for value in ambiguous_sources)
        or provenance.get("ambiguous_sources_are_excluded_from_events") is not True
    ):
        raise RadEvalError("RAD dataset index has invalid ambiguous-source provenance")
    ambiguous_source_ids = set(ambiguous_sources)

    audit_by_id: dict[str, dict[str, Any]] = {}
    if audit_payload is not None:
        if not isinstance(audit_payload, dict) or audit_payload.get("version") != 1:
            raise RadEvalError("RAD audit manifest version must be 1")
        expected_index_hash = audit_payload.get(
            "index_sha256", audit_payload.get("dataset_index_sha256")
        )
        acceptable_hashes = {
            sha256_bytes(index_raw),
            str(payload.get("index_sha256") or ""),
        }
        if expected_index_hash not in acceptable_hashes:
            raise RadEvalError("RAD audit manifest does not pin this dataset index")
        audit_events = audit_payload.get("events")
        if isinstance(audit_events, dict):
            if any(not isinstance(value, dict) for value in audit_events.values()):
                raise RadEvalError("every RAD audit event must be an object")
            audit_events = [dict(value, event_id=key) for key, value in audit_events.items()]
        if not isinstance(audit_events, list):
            raise RadEvalError("RAD audit manifest events must be an array or object")
        for index, audit in enumerate(audit_events):
            if not isinstance(audit, dict):
                raise RadEvalError(f"RAD audit event {index} must be an object")
            event_id = str(audit.get("event_id", audit.get("id", ""))).strip()
            if not event_id or event_id in audit_by_id:
                raise RadEvalError(f"RAD audit event {index} has a missing or duplicate ID")
            audit_by_id[event_id] = audit

    translated_events = []
    index_event_ids: set[str] = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise RadEvalError(f"RAD dataset index event {index} must be an object")
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            raise RadEvalError(f"RAD dataset index event {index} has no ID")
        index_event_ids.add(event_id)
        frame_ids = event.get("frame_ids")
        if not isinstance(frame_ids, list) or len(frame_ids) != 3:
            raise RadEvalError(f"RAD dataset event {event_id} must reference three frames")
        try:
            source_frames = [frame_by_id[frame_id] for frame_id in frame_ids]
        except (KeyError, TypeError) as error:
            raise RadEvalError(f"RAD dataset event {event_id} references an unknown frame") from error
        raw_semantics = event.get("semantic_labels", [])
        if not isinstance(raw_semantics, list):
            raise RadEvalError(f"RAD dataset event {event_id} semantics must be an array")
        semantics = {_normalise_label(item) for item in raw_semantics}
        target_semantics = semantics - {"non_target_context"}
        source_video = event.get("source_video")
        split = event.get("evaluation_split")
        listed_numbers = event.get("frame_numbers")
        actual_numbers = [frame.get("frame_number") for frame in source_frames]
        if (
            not isinstance(listed_numbers, list)
            or listed_numbers != actual_numbers
            or any(type(value) is not int for value in actual_numbers)
            or any(right - left != 1 for left, right in zip(actual_numbers, actual_numbers[1:]))
            or any(frame.get("source_video") != source_video for frame in source_frames)
            or any(frame.get("evaluation_split") != split for frame in source_frames)
        ):
            raise RadEvalError(
                f"RAD dataset event {event_id} is not three consecutive full frames "
                "from one source video and grouped split"
            )
        audit = audit_by_id.get(event_id)
        if audit:
            audit_block = {
                "label": audit.get("label"),
                "status": audit.get("status", audit.get("audit_status")),
                "reviewer": audit.get("reviewer", audit.get("labelled_by")),
            }
        elif target_semantics == {"speed_breaker"}:
            # SpeedBump is a distinct RAD ground-truth class. It is usable as an audited
            # hard negative, but is never promoted to a locked audited pothole label.
            audit_block = {
                "label": "speed_breaker",
                "status": "audited",
                "reviewer": "RAD SpeedBump ground-truth annotation",
            }
        else:
            audit_block = {"label": BROAD_LABEL, "status": "unreviewed"}
        translated_frames = []
        for frame in source_frames:
            if frame.get("full_frame") is not True or event.get("full_frame") is not True:
                raise RadEvalError(
                    f"RAD dataset event {event_id} does not attest complete source frames"
                )
            translated_frames.append({
                "path": frame.get("image_path"),
                "sequence": frame.get("frame_number"),
                "sha256": frame.get("image_sha256"),
                "width": frame.get("width"),
                "height": frame.get("height"),
                "view": "full_frame",
                "full_frame": True,
            })
        translated_events.append({
            "id": event_id,
            "split": split,
            "source_video_id": source_video,
            "audit": audit_block,
            "source_label": ",".join(sorted(str(item) for item in raw_semantics)),
            "capture_provenance": "rad_full_frame_dataset_index",
            "ambiguous_source": (
                event.get("ambiguous_source", False) or source_video in ambiguous_source_ids
            ),
            "primary_index": event.get("primary_index", 1),
            "frames": translated_frames,
        })
    unknown_audits = sorted(set(audit_by_id) - index_event_ids)
    if unknown_audits:
        raise RadEvalError(f"RAD audit references unknown event ID(s): {unknown_audits}")
    return {
        "version": 1,
        "dataset": {
            "name": dataset.get("name", "RAD—Road Anomaly Detection"),
            "source_url": dataset.get(
                "source_url",
                dataset.get("url", f"https://www.kaggle.com/datasets/{dataset.get('ref', '')}"),
            ),
            "license": dataset.get("license"),
            "dataset_ref": dataset.get("ref"),
            "dataset_version": dataset.get("version"),
            "index_schema_version": payload["schema_version"],
            "index_sha256": payload.get("index_sha256", sha256_bytes(index_raw)),
            "excluded_ambiguous_source_videos": sorted(ambiguous_source_ids),
        },
        "events": translated_events,
    }


def load_manifest(
    path: Path, dataset_root: Path, audit_path: Path | None = None,
    *, require_official_release: bool = False,
) -> tuple[bytes, dict[str, Any], list[Event]]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise RadEvalError(f"cannot load RAD manifest: {error}") from error
    schema_version = payload.get("schema_version")
    if isinstance(schema_version, str) and schema_version.startswith("rad-dataset-index-"):
        current_schema = getattr(rad_dataset, "INDEX_SCHEMA_VERSION", None)
        if schema_version != current_schema:
            raise RadEvalError(
                "unsupported RAD dataset index schema: "
                f"expected {current_schema or 'the installed dataset adapter'}, got {schema_version}"
            )
        try:
            payload = rad_dataset.load_index(path)
        except Exception as error:
            raise RadEvalError(f"RAD dataset index seal or schema validation failed: {error}") from error
    audit_payload = None
    audit_sha256 = None
    if audit_path is not None:
        try:
            audit_raw = audit_path.read_bytes()
            audit_payload = json.loads(audit_raw)
            audit_sha256 = sha256_bytes(audit_raw)
        except (OSError, json.JSONDecodeError) as error:
            raise RadEvalError(f"cannot load RAD audit manifest: {error}") from error
    adapted = _index_to_manifest(
        payload, raw, audit_payload, require_official_release=require_official_release
    )
    metadata, events = validate_manifest(adapted, dataset_root)
    if audit_sha256:
        metadata = {**metadata, "audit_manifest_sha256": audit_sha256}
    return raw, metadata, events


def load_contract() -> Contract:
    try:
        native = private_release_gate.load_production_contract()
    except private_release_gate.GateError as error:
        raise RadEvalError(str(error)) from error
    source_path = Path(production_eval.__file__).resolve()
    prompt = production_eval.prompts().get("baseline")
    if (
        native["model"] != EXACT_MODEL
        or native["detail"] != EXACT_DETAIL
        or production_eval.DRIVE_DEFAULT_MODEL != EXACT_MODEL
        or production_eval.DRIVE_DEFAULT_DETAIL != EXACT_DETAIL
        or prompt != native["prompt"]
        or production_eval.SCHEMA != native["schema"]
        or production_eval.PROMPT_VERSION != native["prompt_version"]
        or production_eval.SCHEMA_VERSION != native["schema_version"]
        or production_eval.NATIVE_DRIVE_MAX_OUTPUT_TOKENS != native["max_output_tokens"]
    ):
        raise RadEvalError("RAD evaluator does not match the exact native Drive contract")
    return Contract(
        prompt=prompt,
        schema=native["schema"],
        model=EXACT_MODEL,
        detail=EXACT_DETAIL,
        prompt_version=native["prompt_version"],
        schema_version=native["schema_version"],
        max_output_tokens=native["max_output_tokens"],
        prompt_sha256=sha256_bytes(prompt.encode()),
        schema_sha256=sha256_bytes(canonical_json(native["schema"]).encode()),
        production_eval_sha256=sha256_file(source_path),
    )


def build_production_request(event: Event, contract: Contract) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    entry = {
        "path": str(event.frames[event.primary_index].path),
        "frames": [str(frame.path) for frame in event.frames],
        "primary_index": event.primary_index,
        "mode": "drive",
    }
    # This is the sole image-preparation path: the same downscale-only, complete-frame
    # transform used by the production evaluator and Android Drive request mirror.
    views, transforms, layout_note = production_eval.prepare_event(entry, Path("/"), "drive")
    prompt = production_eval.effective_prompt(contract.prompt, "drive", layout_note)
    body = production_eval.build_request(
        views, prompt, contract.model, contract.detail, mode="drive"
    )
    image_inputs = [
        item for item in body.get("input", [{}])[0].get("content", [])
        if item.get("type") == "input_image"
    ]
    if (
        body.get("model") != EXACT_MODEL
        or any(item.get("detail") != contract.detail for item in image_inputs)
        or body.get("reasoning") != {"effort": "low"}
        or body.get("store") is not False
        or body.get("max_output_tokens") != contract.max_output_tokens
        or body.get("text", {}).get("format", {}).get("schema") != contract.schema
        or len(image_inputs) != 4
        or len(transforms) != 4
        or any(transform.get("full_frame") is not True for transform in transforms)
        or [item.get("role") for item in transforms[1:]]
            != ["chronological_full_frame"] * 3
        or [item.get("frame_index") for item in transforms[1:]] != [0, 1, 2]
        or "No image is cropped, tiled, masked, or limited to a region of interest."
            not in layout_note
    ):
        raise RadEvalError(f"event {event.event_id} request drifted from production Drive")
    return body, transforms


def validate_assessment(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    try:
        return private_release_gate.validate_assessment(value, schema)
    except private_release_gate.GateError as error:
        raise RadEvalError(str(error)) from error


def parse_api_payload(payload: Any, schema: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    if not isinstance(payload, dict) or payload.get("error"):
        raise RadEvalError("API returned an error payload")
    try:
        messages = [item for item in payload["output"] if item.get("type") == "message"]
        texts = [
            content["text"]
            for message in messages
            for content in message["content"]
            if content.get("type") == "output_text"
        ]
        if len(texts) != 1:
            raise RadEvalError("API response must contain exactly one structured output")
        assessment = json.loads(texts[0])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RadEvalError("API response is not a valid structured assessment") from error
    return validate_assessment(assessment, schema), payload.get("id")


def retry_after_seconds(value: str | None, now: float | None = None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            current = time.time() if now is None else now
            return max(0.0, when.timestamp() - current)
        except (TypeError, ValueError, OverflowError):
            return None


def _backoff_seconds(attempt: int, retry_after: str | None) -> float:
    server_delay = retry_after_seconds(retry_after)
    if server_delay is not None:
        return min(120.0, server_delay)
    # Jitter avoids a synchronized retry wave when several bounded workers get 429s.
    return min(30.0, (2 ** attempt) + random.random())


def call_api(
    api_key: str,
    body: dict[str, Any],
    schema: dict[str, Any],
    timeout: int,
    max_retries: int,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], str | None, int]:
    request = urllib.request.Request(API_URL, data=canonical_json(body).encode(), headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    for attempt in range(max_retries + 1):
        try:
            with opener(request, timeout=timeout) as response:
                payload = json.loads(response.read())
            assessment, response_id = parse_api_payload(payload, schema)
            return assessment, response_id, attempt
        except urllib.error.HTTPError as error:
            if error.code not in RETRYABLE_HTTP_STATUS or attempt >= max_retries:
                raise RadEvalError(f"OpenAI HTTP {error.code}") from error
            sleeper(_backoff_seconds(attempt, error.headers.get("Retry-After")))
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt >= max_retries:
                raise RadEvalError("OpenAI request failed after retries") from error
            sleeper(_backoff_seconds(attempt, None))
        except json.JSONDecodeError as error:
            raise RadEvalError("OpenAI response body is not JSON") from error
    raise RadEvalError("OpenAI request exhausted retries")


def event_fingerprint(event: Event, contract: Contract) -> str:
    value = {
        "event_id": event.event_id,
        "source_video_id": event.source_video_id,
        "frame_sha256": [frame.sha256 for frame in event.frames],
        "frame_sequence": [frame.sequence for frame in event.frames],
        "primary_index": event.primary_index,
        "model": contract.model,
        "detail": contract.detail,
        "prompt_sha256": contract.prompt_sha256,
        "schema_sha256": contract.schema_sha256,
        "production_eval_sha256": contract.production_eval_sha256,
    }
    return sha256_bytes(canonical_json(value).encode())


def make_jobs(events: Iterable[Event], contract: Contract) -> list[Job]:
    jobs = []
    for event in events:
        fingerprint = event_fingerprint(event, contract)
        jobs.append(Job(event=event, job_id=fingerprint, cache_key=fingerprint))
    return jobs


def _prepared_request(
    job: Job, contract: Contract
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    body, transforms = build_production_request(job.event, contract)
    request_sha256 = sha256_bytes(canonical_json(body).encode())
    return body, transforms, request_sha256


def load_success_cache(
    path: Path, job: Job, contract: Contract,
    expected_request_sha256: str | None = None,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        if expected_request_sha256 is None:
            _body, _transforms, expected_request_sha256 = _prepared_request(job, contract)
        payload = json.loads(path.read_text())
        if (
            payload.get("job_id") != job.job_id
            or payload.get("request_sha256") != expected_request_sha256
        ):
            return None
        assessment = validate_assessment(payload["assessment"], contract.schema)
        retry_count = payload.get("retry_count", 0)
        response_id = payload.get("response_id")
        if type(retry_count) is not int or retry_count < 0:
            return None
        if response_id is not None and not isinstance(response_id, str):
            return None
        return {
            "assessment": assessment,
            "response_id": response_id,
            "request_sha256": expected_request_sha256,
            "retry_count": retry_count,
        }
    except (OSError, KeyError, json.JSONDecodeError, RadEvalError):
        return None


def _atomic_write_json(path: Path, payload: Any) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _base_row(job: Job) -> dict[str, Any]:
    event = job.event
    return {
        "job_id": job.job_id,
        "event_id": event.event_id,
        "split": event.split,
        "source_video_id": event.source_video_id,
        "label": event.label,
        "audit_status": event.audit_status,
        "labelled_by": event.labelled_by,
        "source_label": event.source_label,
        "capture_provenance": event.capture_provenance,
        "ambiguous_source": event.ambiguous_source,
        "frame_sequence": [frame.sequence for frame in event.frames],
        "frame_timestamp_ms": [frame.timestamp_ms for frame in event.frames],
        "frame_sha256": [frame.sha256 for frame in event.frames],
    }


def evaluate_job(
    job: Job,
    contract: Contract,
    cache_dir: Path,
    api_key: str,
    timeout: int,
    max_retries: int,
) -> dict[str, Any]:
    row = _base_row(job)
    cache_path = cache_dir / f"{job.cache_key}.json"
    try:
        body, transforms, request_sha256 = _prepared_request(job, contract)
        cached = load_success_cache(
            cache_path, job, contract, expected_request_sha256=request_sha256
        )
        if cached is None:
            assessment, response_id, retry_count = call_api(
                api_key, body, contract.schema, timeout, max_retries
            )
            _atomic_write_json(cache_path, {
                "job_id": job.job_id,
                "request_sha256": request_sha256,
                "response_id": response_id,
                "retry_count": retry_count,
                "assessment": assessment,
            })
            cache_hit = False
        else:
            assessment = cached["assessment"]
            response_id = cached["response_id"]
            request_sha256 = cached["request_sha256"]
            retry_count = cached["retry_count"]
            cache_hit = True
        actual = production_eval.decision(assessment, "drive", source_view_count=3)
        row.update({
            "status": "ok",
            "decision": actual,
            "cache_hit": cache_hit,
            "request_sha256": request_sha256,
            "response_id": response_id,
            "retry_count": retry_count,
            "transforms": transforms,
            "assessment": assessment,
        })
    except Exception as error:  # Error rows are deliberately retained and scored fail-closed.
        row.update({
            "status": "error",
            "decision": "error",
            "cache_hit": False,
            "error_type": type(error).__name__,
            "error": str(error)[:500],
        })
    return row


def bounded_parallel_map(
    jobs: Iterable[Job], worker: Callable[[Job], dict[str, Any]], concurrency: int
) -> Iterator[dict[str, Any]]:
    """Submit at most ``concurrency`` image-heavy jobs at any point."""
    iterator = iter(jobs)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        active = set()
        for _ in range(concurrency):
            try:
                active.add(pool.submit(worker, next(iterator)))
            except StopIteration:
                break
        while active:
            completed, active = wait(active, return_when=FIRST_COMPLETED)
            for future in completed:
                yield future.result()
                try:
                    active.add(pool.submit(worker, next(iterator)))
                except StopIteration:
                    pass


def validate_result_rows(
    rows: list[dict[str, Any]], jobs: Iterable[Job], contract: Contract,
    *, require_complete: bool = False,
) -> set[str]:
    """Bind persisted rows back to exact jobs and recompute every derived success field."""
    job_list = list(jobs)
    expected = {job.job_id: job for job in job_list}
    if len(expected) != len(job_list):
        raise RadEvalError("selected RAD jobs contain duplicate IDs")
    completed: set[str] = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise RadEvalError(f"raw.jsonl row {index} is not an object")
        job_id = row.get("job_id")
        if not isinstance(job_id, str) or job_id not in expected:
            raise RadEvalError(f"raw.jsonl row {index} has an unknown job ID")
        if job_id in completed:
            raise RadEvalError(f"raw.jsonl contains duplicate job ID {job_id}")
        job = expected[job_id]
        for field, value in _base_row(job).items():
            if row.get(field) != value:
                raise RadEvalError(
                    f"raw.jsonl row {index} changed immutable job field {field}"
                )
        status = row.get("status")
        if status == "ok":
            body, transforms, request_sha256 = _prepared_request(job, contract)
            del body
            if row.get("request_sha256") != request_sha256:
                raise RadEvalError(f"raw.jsonl row {index} request hash does not match production")
            if row.get("transforms") != transforms:
                raise RadEvalError(f"raw.jsonl row {index} transforms do not match production")
            assessment = validate_assessment(row.get("assessment"), contract.schema)
            decision = production_eval.decision(
                assessment, "drive", source_view_count=3
            )
            if row.get("decision") != decision:
                raise RadEvalError(f"raw.jsonl row {index} decision does not match production")
            if type(row.get("cache_hit")) is not bool:
                raise RadEvalError(f"raw.jsonl row {index} cache_hit must be boolean")
            response_id = row.get("response_id")
            if response_id is not None and not isinstance(response_id, str):
                raise RadEvalError(f"raw.jsonl row {index} response_id must be a string")
            retry_count = row.get("retry_count")
            if type(retry_count) is not int or retry_count < 0:
                raise RadEvalError(f"raw.jsonl row {index} retry_count must be non-negative")
        elif status == "error":
            if row.get("decision") != "error":
                raise RadEvalError(f"raw.jsonl row {index} error decision was altered")
            if any(field in row for field in ("assessment", "request_sha256", "transforms")):
                raise RadEvalError(f"raw.jsonl row {index} mixes error and success evidence")
        else:
            raise RadEvalError(f"raw.jsonl row {index} has invalid status")
        completed.add(job_id)
    if require_complete and completed != set(expected):
        missing = len(set(expected) - completed)
        raise RadEvalError(f"raw.jsonl is missing {missing} selected job(s)")
    return completed


def load_completed_job_ids(
    raw_path: Path, jobs: Iterable[Job], contract: Contract
) -> set[str]:
    if not raw_path.is_file():
        return set()
    return validate_result_rows(_read_rows(raw_path), jobs, contract)


def select_events(
    events: list[Event], splits: set[str], labels: set[str], event_ids: set[str]
) -> list[Event]:
    ambiguous_requested = {
        event.event_id for event in events
        if event.ambiguous_source and (not event_ids or event.event_id in event_ids)
        and (not splits or event.split in splits) and (not labels or event.label in labels)
    }
    if event_ids & ambiguous_requested:
        raise RadEvalError(
            "ambiguous_source event(s) cannot be treated as chronological Drive evidence: "
            + ", ".join(sorted(event_ids & ambiguous_requested))
        )
    selected = [
        event for event in events
        if not event.ambiguous_source
        and (not splits or event.split in splits)
        and (not labels or event.label in labels)
        and (not event_ids or event.event_id in event_ids)
    ]
    missing = event_ids - {event.event_id for event in selected}
    if missing:
        raise RadEvalError(f"selected event ID(s) unavailable after filters: {sorted(missing)}")
    if not selected:
        raise RadEvalError("filters selected no RAD events")
    return selected


def summarise(rows: list[dict[str, Any]], selected_events: list[Event]) -> dict[str, Any]:
    by_job: dict[str, dict[str, Any]] = {}
    duplicate_jobs: set[str] = set()
    for row in rows:
        job_id = row.get("job_id")
        if job_id in by_job:
            duplicate_jobs.add(str(job_id))
        by_job[str(job_id)] = row
    selected_ids = {event.event_id for event in selected_events}
    selected_rows = [row for row in by_job.values() if row.get("event_id") in selected_ids]

    def group(label: str, locked_only: bool = False) -> dict[str, Any]:
        values = [row for row in selected_rows if row.get("label") == label
                  and (not locked_only or row.get("audit_status") == "locked")]
        accepted = sum(row.get("status") == "ok" and row.get("decision") == "accept"
                       for row in values)
        rejected = sum(row.get("status") == "ok" and row.get("decision") == "reject"
                       for row in values)
        errors = sum(row.get("status") != "ok" for row in values)
        total = len(values)
        return {
            "total": total,
            "accepted": accepted,
            "rejected": rejected,
            "errors": errors,
            "accept_rate_including_errors": accepted / total if total else None,
        }

    pothole = group("pothole")
    locked_pothole = group("pothole", locked_only=True)
    not_pothole = group("not_pothole")
    speed_breaker = group("speed_breaker")
    broad = group(BROAD_LABEL)
    errors = sum(row.get("status") != "ok" for row in selected_rows)
    return {
        "selected_events": len(selected_events),
        "completed_unique_events": len(selected_rows),
        "complete": len(selected_rows) == len(selected_events) and not duplicate_jobs,
        "duplicate_job_ids": sorted(duplicate_jobs),
        "errors": errors,
        "audited": {
            "pothole": {**pothole, "recall_including_errors": pothole["accept_rate_including_errors"]},
            "locked_pothole": {
                **locked_pothole,
                "recall_including_errors": locked_pothole["accept_rate_including_errors"],
            },
            "not_pothole": {
                **not_pothole,
                "false_accept_rate_including_errors": not_pothole["accept_rate_including_errors"],
            },
            "speed_breaker": {
                **speed_breaker,
                "false_accept_rate_including_errors": speed_breaker["accept_rate_including_errors"],
            },
        },
        "broad_unreviewed_anomaly": {
            **broad,
            "accuracy_note": (
                "RAD RoadDamages is a broad review class, not binary pothole ground truth; "
                "its accept rate is descriptive only."
            ),
        },
    }


def evaluate_gate(
    summary: dict[str, Any], required_recall: float,
    max_speed_breaker_accepts: int, max_errors: int,
    max_not_pothole_accepts: int = 0,
) -> list[str]:
    failures: list[str] = []
    if not summary["complete"]:
        failures.append("selected event set is incomplete or duplicated")
    locked = summary["audited"]["locked_pothole"]
    if locked["total"] == 0:
        failures.append("no locked audited pothole events were selected")
    elif locked["recall_including_errors"] < required_recall:
        failures.append(
            f"locked pothole recall {locked['recall_including_errors']:.6f} "
            f"is below {required_recall:.6f}"
        )
    breakers = summary["audited"]["speed_breaker"]
    if breakers["total"] == 0:
        failures.append("no audited speed-breaker events were selected")
    elif breakers["accepted"] > max_speed_breaker_accepts:
        failures.append(
            f"accepted {breakers['accepted']} speed breakers; maximum is "
            f"{max_speed_breaker_accepts}"
        )
    non_potholes = summary["audited"]["not_pothole"]
    if non_potholes["total"] == 0:
        failures.append("no locked audited non-pothole events were selected")
    elif non_potholes["accepted"] > max_not_pothole_accepts:
        failures.append(
            f"accepted {non_potholes['accepted']} audited non-potholes; maximum is "
            f"{max_not_pothole_accepts}"
        )
    if summary["errors"] > max_errors:
        failures.append(f"evaluation has {summary['errors']} errors; maximum is {max_errors}")
    return failures


def parse_set(value: str, *, normalise_labels: bool = False) -> set[str]:
    items = {item.strip() for item in value.split(",") if item.strip()}
    if normalise_labels:
        items = {_normalise_label(item) for item in items}
        unknown = items - ALL_LABELS
        if unknown:
            raise RadEvalError(f"unknown label filter(s): {sorted(unknown)}")
    return items


def load_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        env = ROOT / ".env"
        if env.is_file():
            for line in env.read_text().splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    if not key:
        raise RadEvalError("OPENAI_API_KEY is required for a paid execution")
    return key


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--audit-manifest", type=Path,
                        help="optional named audit overlay locked to the dataset index")
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--split", default="", help="comma-separated split names; blank means all")
    parser.add_argument("--label", default="", help="comma-separated audited/broad labels")
    parser.add_argument("--events", default="", help="comma-separated exact event IDs")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--max-calls", type=int, default=0,
                        help="hard ceiling on uncached paid calls")
    parser.add_argument("--validate-only", action="store_true",
                        help="verify manifest, images, labels and production contract only")
    parser.add_argument("--dry-run", action="store_true",
                        help="also construct every exact request, but make no API calls")
    parser.add_argument("--paid-run", action="store_true",
                        help="explicitly authorize paid OpenAI API execution")
    parser.add_argument("--resume", action="store_true",
                        help="continue only jobs absent from the existing raw.jsonl")
    parser.add_argument("--gate", action="store_true",
                        help="enforce the configured locked-audit release thresholds")
    parser.add_argument("--require-locked-pothole-recall", type=float, default=1.0)
    parser.add_argument("--max-speed-breaker-accepts", type=int, default=0)
    parser.add_argument("--max-not-pothole-accepts", type=int, default=0)
    parser.add_argument("--max-errors", type=int, default=0)
    return parser


def provenance(
    manifest_bytes: bytes, metadata: dict[str, Any], contract: Contract,
    selected: list[Event], args: argparse.Namespace
) -> dict[str, Any]:
    selection = {
        "splits": sorted(parse_set(args.split)),
        "labels": sorted(parse_set(args.label, normalise_labels=True)),
        "events": sorted(parse_set(args.events)),
        "selected_event_ids_sha256": sha256_bytes(
            canonical_json([event.event_id for event in selected]).encode()
        ),
    }
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(),
        "dataset": metadata,
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "selection": selection,
        "selected_events": len(selected),
        "model": contract.model,
        "image_detail": contract.detail,
        "prompt_version": contract.prompt_version,
        "prompt_sha256": contract.prompt_sha256,
        "schema_version": contract.schema_version,
        "schema_sha256": contract.schema_sha256,
        "production_eval_sha256": contract.production_eval_sha256,
        "rad_eval_sha256": sha256_file(Path(__file__).resolve()),
        "rad_dataset_sha256": (
            sha256_file(Path(rad_dataset.__file__).resolve()) if rad_dataset is not None else None
        ),
        "decision": "eval/run_eval.py:decision(mode=drive, source_view_count=3)",
        "full_frame_only": True,
        "chronological_source_frames_per_event": 3,
        "excluded_ambiguous_source_events": sorted(
            event.event_id for event in selected if event.ambiguous_source
        ),
    }


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise RadEvalError(f"raw.jsonl line {line_number} is invalid JSON") from error
        if not isinstance(value, dict):
            raise RadEvalError(f"raw.jsonl line {line_number} is not an object")
        rows.append(value)
    return rows


def _validate_args(args: argparse.Namespace) -> None:
    if args.concurrency < 1 or args.concurrency > MAX_CONCURRENCY:
        raise RadEvalError(f"--concurrency must be between 1 and {MAX_CONCURRENCY}")
    if args.timeout < 1:
        raise RadEvalError("--timeout must be positive")
    if args.max_retries < 0 or args.max_retries > 10:
        raise RadEvalError("--max-retries must be between 0 and 10")
    if not 0.0 <= args.require_locked_pothole_recall <= 1.0:
        raise RadEvalError("--require-locked-pothole-recall must be between 0 and 1")
    if (args.max_speed_breaker_accepts < 0 or args.max_not_pothole_accepts < 0
            or args.max_errors < 0):
        raise RadEvalError("gate error/false-positive maxima must be non-negative")
    modes = sum((args.validate_only, args.dry_run, args.paid_run))
    if modes != 1:
        raise RadEvalError("choose exactly one of --validate-only, --dry-run or --paid-run")
    if args.paid_run and args.max_calls < 1:
        raise RadEvalError("paid execution requires an explicit positive --max-calls")
    if args.gate and not args.paid_run:
        raise RadEvalError("--gate requires --paid-run so thresholds are actually evaluated")
    if args.gate and args.audit_manifest is None:
        raise RadEvalError("--gate requires an audit manifest pinned to the official RAD v3 index")


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    _validate_args(args)
    dataset_root = args.dataset_root.expanduser().resolve()
    manifest_bytes, metadata, all_events = load_manifest(
        args.manifest,
        dataset_root,
        args.audit_manifest,
        require_official_release=args.gate,
    )
    contract = load_contract()
    selected = select_events(
        all_events,
        parse_set(args.split),
        parse_set(args.label, normalise_labels=True),
        parse_set(args.events),
    )
    jobs = make_jobs(selected, contract)
    run_provenance = provenance(manifest_bytes, metadata, contract, selected, args)
    ambiguous = [event.event_id for event in all_events if event.ambiguous_source]
    ambiguous_sources = metadata.get("excluded_ambiguous_source_videos", [])
    run_provenance["excluded_ambiguous_source_events"] = sorted(ambiguous)
    run_provenance["excluded_ambiguous_source_videos"] = sorted(ambiguous_sources)
    print(
        f"RAD validated: {len(all_events)} manifest events; {len(selected)} selected; "
        f"model={contract.model}; prompt={contract.prompt_version}"
    )
    if ambiguous or ambiguous_sources:
        print(
            f"RAD excluded {len(ambiguous_sources)} ambiguous source-video ID(s) and "
            f"{len(ambiguous)} residual event(s) from Drive accuracy"
        )
    if args.validate_only:
        print("RAD EVAL VALIDATION PASS (NO REQUESTS, NO API CALLS)")
        return 0

    if args.dry_run:
        for index, job in enumerate(jobs, 1):
            body, transforms = build_production_request(job.event, contract)
            del body, transforms
            if index % 250 == 0:
                print(f"  request parity {index}/{len(jobs)}")
        print(f"RAD EVAL DRY RUN PASS ({len(jobs)} EXACT REQUESTS, NO API CALLS)")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    raw_path = args.out / "raw.jsonl"
    run_path = args.out / "run.json"
    if raw_path.exists() and not args.resume:
        raise RadEvalError("output already has raw.jsonl; use --resume or choose a new --out")
    if run_path.exists():
        try:
            previous = json.loads(run_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise RadEvalError("existing run.json is unreadable") from error
        comparable = {key: value for key, value in run_provenance.items() if key != "created_at"}
        old_comparable = {key: value for key, value in previous.items() if key != "created_at"}
        if comparable != old_comparable:
            raise RadEvalError("--resume provenance does not match the existing run")
    else:
        _atomic_write_json(run_path, run_provenance)

    existing_ids = load_completed_job_ids(raw_path, jobs, contract) if args.resume else set()
    pending = [job for job in jobs if job.job_id not in existing_ids]
    cache_dir = args.out / "cache"
    cache_dir.mkdir(exist_ok=True)
    uncached = sum(
        load_success_cache(cache_dir / f"{job.cache_key}.json", job, contract) is None
        for job in pending
    )
    if uncached > args.max_calls:
        raise RadEvalError(
            f"paid run needs {uncached} uncached calls, above --max-calls={args.max_calls}; "
            "nothing was sent"
        )
    api_key = load_api_key() if uncached else ""
    worker = lambda job: evaluate_job(
        job, contract, cache_dir, api_key, args.timeout, args.max_retries
    )
    with raw_path.open("a", encoding="utf-8") as output:
        for index, row in enumerate(bounded_parallel_map(pending, worker, args.concurrency), 1):
            output.write(canonical_json(row) + "\n")
            output.flush()
            status = row["status"].upper()
            print(f"{status} {row['event_id']} decision={row['decision']}")
            if index % 25 == 0:
                print(f"  completed {index}/{len(pending)} pending events")

    rows = _read_rows(raw_path)
    validate_result_rows(rows, jobs, contract)
    summary = summarise(rows, selected)
    summary["provenance"] = run_provenance
    summary["paid_call_budget"] = args.max_calls
    summary["uncached_calls_planned"] = uncached
    summary["broad_label_warning"] = (
        "No original RAD RoadDamages label is counted as pothole ground truth."
    )
    failures = evaluate_gate(
        summary,
        args.require_locked_pothole_recall,
        args.max_speed_breaker_accepts,
        args.max_errors,
        args.max_not_pothole_accepts,
    ) if args.gate else []
    summary["gate"] = {
        "enabled": args.gate,
        "passed": not failures if args.gate else None,
        "required_locked_pothole_recall": args.require_locked_pothole_recall,
        "max_speed_breaker_accepts": args.max_speed_breaker_accepts,
        "max_not_pothole_accepts": args.max_not_pothole_accepts,
        "max_errors": args.max_errors,
        "failures": failures,
    }
    _atomic_write_json(args.out / "summary.json", summary)
    print(json.dumps({
        "complete": summary["complete"],
        "errors": summary["errors"],
        "locked_pothole": summary["audited"]["locked_pothole"],
        "speed_breaker": summary["audited"]["speed_breaker"],
        "gate": summary["gate"],
    }, indent=2))
    if failures:
        raise RadEvalError("RAD release gate failed: " + "; ".join(failures))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RadEvalError as error:
        print(f"RAD EVAL FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
