#!/usr/bin/env python3
"""Fail-closed release gate for the project's exact private drive footage.

The videos and extracted frames never belong in git.  The committed manifest contains
only fingerprints, stream metadata, reviewed labels, timestamps and expected JPEG
fingerprints.  A real gate run must provide the matching private source files, rebuild
all five sampling phases with ffmpeg, and obtain a fresh correct model decision for
every phase and trial.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import run_eval as production_eval


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "eval" / "private_release_gate.json"
DEFAULT_WORK_ROOT = ROOT / "eval" / ".private-release-gate"
API_URL = "https://api.openai.com/v1/responses"
SOURCE_SUFFIXES = {".mp4", ".m4v", ".mov"}
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class GateError(RuntimeError):
    """Expected, user-actionable release-gate failure."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_keys(value: dict[str, Any], expected: set[str], where: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise GateError(f"{where} keys mismatch (missing={missing}, extra={extra})")


def validate_manifest(manifest: Any) -> dict[str, Any]:
    """Validate the compact metadata without needing private media or network access."""
    if not isinstance(manifest, dict):
        raise GateError("manifest must be a JSON object")
    _require_keys(manifest, {"version", "sources", "events"}, "manifest")
    if manifest["version"] != 2:
        raise GateError("unsupported private release gate manifest version")
    sources = manifest["sources"]
    events = manifest["events"]
    if not isinstance(sources, dict) or not sources:
        raise GateError("sources must be a non-empty object")
    if not isinstance(events, list) or not events:
        raise GateError("events must be a non-empty array")

    for source_id, source in sources.items():
        if not isinstance(source_id, str) or not source_id or not isinstance(source, dict):
            raise GateError("each source must have a non-empty string ID and object metadata")
        _require_keys(source, {"sha256", "codec", "width", "height", "frames", "duration_seconds"},
                      f"source {source_id}")
        if not HEX_64.fullmatch(str(source["sha256"])):
            raise GateError(f"source {source_id} has an invalid SHA-256")
        if source["codec"] != "h264":
            raise GateError(f"source {source_id} must be the audited H.264 stream")
        if any(type(source[field]) is not int or source[field] <= 0
               for field in ("width", "height", "frames")):
            raise GateError(f"source {source_id} has invalid dimensions or frame count")
        if type(source["duration_seconds"]) not in {int, float} or source["duration_seconds"] <= 0:
            raise GateError(f"source {source_id} has an invalid duration")

    seen_ids: set[str] = set()
    label_counts = {"pothole": 0, "not_pothole": 0}
    for event_index, event in enumerate(events):
        where = f"event {event_index}"
        if not isinstance(event, dict):
            raise GateError(f"{where} must be an object")
        _require_keys(event, {"id", "source", "label", "evidence_tier",
                              "label_provenance", "capture_provenance",
                              "raw_camerax_accuracy_eligible", "phases"}, where)
        event_id = event["id"]
        if not isinstance(event_id, str) or not event_id or event_id in seen_ids:
            raise GateError(f"{where} has a missing or duplicate ID")
        seen_ids.add(event_id)
        if event["source"] not in sources:
            raise GateError(f"event {event_id} refers to an unknown source")
        if event["label"] not in label_counts:
            raise GateError(f"event {event_id} has an unsupported binary label")
        label_counts[event["label"]] += 1
        evidence_tier = event["evidence_tier"]
        label_provenance = event["label_provenance"]
        if evidence_tier == "owner_ground_truth":
            if label_provenance != "owner_confirmed":
                raise GateError(f"event {event_id} has incorrect owner label provenance")
            expected_capture = (
                "native_mediarecorder_reconstruction" if event["label"] == "pothole"
                else "external_recording_of_test_device"
            )
            if event["capture_provenance"] != expected_capture:
                raise GateError(f"event {event_id} has incorrect capture provenance")
        elif evidence_tier == "independent_assistant_regression":
            if (event["label"] != "not_pothole"
                    or label_provenance != "independent_assistant_full_video_review"
                    or event["capture_provenance"] not in {
                        "native_mediarecorder_reconstruction",
                        "external_recording_of_test_device",
                    }):
                raise GateError(f"event {event_id} has invalid independent regression provenance")
        else:
            raise GateError(f"event {event_id} has an unsupported evidence tier")
        if event["raw_camerax_accuracy_eligible"] is not False:
            raise GateError(f"event {event_id} must not claim raw CameraX accuracy eligibility")
        phases = event["phases"]
        if not isinstance(phases, list) or len(phases) != 5:
            raise GateError(f"event {event_id} must contain exactly five sampling phases")
        source_duration = float(sources[event["source"]]["duration_seconds"])
        for phase_index, phase in enumerate(phases):
            phase_where = f"event {event_id} phase {phase_index}"
            if not isinstance(phase, dict):
                raise GateError(f"{phase_where} must be an object")
            _require_keys(phase, {"timestamps_seconds", "fixture_sha256"}, phase_where)
            timestamps = phase["timestamps_seconds"]
            fingerprints = phase["fixture_sha256"]
            if not isinstance(timestamps, list) or len(timestamps) != 3:
                raise GateError(f"{phase_where} must contain exactly three timestamps")
            if (any(type(value) not in {int, float} for value in timestamps)
                    or timestamps != sorted(timestamps)
                    or len(set(timestamps)) != 3
                    or timestamps[0] < 0
                    or timestamps[-1] >= source_duration):
                raise GateError(f"{phase_where} timestamps are invalid")
            if (not isinstance(fingerprints, list) or len(fingerprints) != 3
                    or any(not HEX_64.fullmatch(str(value)) for value in fingerprints)):
                raise GateError(f"{phase_where} must contain three valid JPEG SHA-256 values")
    if label_counts["pothole"] == 0 or label_counts["not_pothole"] == 0:
        raise GateError("manifest must contain both positive and negative events")
    return manifest


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        return validate_manifest(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError) as error:
        raise GateError(f"cannot load release-gate manifest: {error}") from error


def _kotlin_trim_indent(value: str) -> str:
    return textwrap.dedent(value).strip("\n")


def load_production_contract() -> dict[str, Any]:
    """Fail if the evaluator mirror is not the current native production contract."""
    contract_path = (ROOT / "android-app" / "android" / "app" / "src" / "main" / "java" /
                     "dev" / "aiengg" / "potholereporter" / "drive" /
                     "NativeDetectionContract.kt")
    engine_path = contract_path.with_name("NativeInferenceEngine.kt")
    request_path = contract_path.with_name("NativeInferenceRequest.kt")
    retry_path = contract_path.with_name("NativeDetectionRetryPolicy.kt")
    try:
        contract_source = contract_path.read_text()
        engine_source = engine_path.read_text()
        request_source = request_path.read_text()
        retry_source = retry_path.read_text()
    except OSError as error:
        raise GateError("native production detection contract is unavailable") from error

    def integer_constant(name: str) -> int:
        match = re.search(rf"const val {re.escape(name)} = ([0-9_]+)", contract_source)
        if not match:
            raise GateError(f"native production constant {name} is unavailable")
        return int(match.group(1).replace("_", ""))

    prompt_match = re.search(
        r'val DETECT_PROMPT\s*=\s*"""(.*?)"""\.trimIndent\(\)', contract_source, re.S)
    schema_match = re.search(
        r'val SCHEMA_JSON\s*=\s*"""(.*?)"""\.trimIndent\(\)', contract_source, re.S)
    version_match = re.search(r'const val PROMPT_VERSION = "([^"]+)"', contract_source)
    model_match = re.search(r'private val model: String = "([^"]+)"', engine_source)
    detail_match = re.search(r'private val detail: String = "([^"]+)"', engine_source)
    if not all((prompt_match, schema_match, version_match, model_match, detail_match)):
        raise GateError(
            "native production prompt, schema, version, model or image detail could not be read"
        )
    try:
        native_schema = json.loads(_kotlin_trim_indent(schema_match.group(1)))
    except json.JSONDecodeError as error:
        raise GateError("native production schema is invalid JSON") from error
    native_prompt = _kotlin_trim_indent(prompt_match.group(1))
    native_model = model_match.group(1)
    native_detail = detail_match.group(1)
    native_version = version_match.group(1)
    native_schema_version = integer_constant("SCHEMA_VERSION")
    native_max_tokens = integer_constant("MAX_OUTPUT_TOKENS")
    retry_match = re.search(r"const val MAX_ATTEMPTS = ([0-9_]+)", retry_source)
    if not retry_match:
        raise GateError("native production retry limit is unavailable")
    retry_max_attempts = int(retry_match.group(1).replace("_", ""))

    live_prompt = production_eval.prompts().get("baseline")
    parity = (
        live_prompt == native_prompt
        and production_eval.SCHEMA == native_schema
        and production_eval.DRIVE_DEFAULT_MODEL == native_model
        and production_eval.DRIVE_DEFAULT_DETAIL == native_detail
        and production_eval.client_string_constant("DRIVE_DETECTION_DETAIL") == native_detail
        and production_eval.PROMPT_VERSION == native_version
        and production_eval.SCHEMA_VERSION == native_schema_version
        and production_eval.NATIVE_DRIVE_MAX_OUTPUT_TOKENS == native_max_tokens
        and production_eval.TEMPORARY_SURFACE_MAX_ATTEMPTS == retry_max_attempts
        and 'put("stream", true)' in request_source
        and "allowEarlyReject = allowEarlyReject" in engine_source
        and 'assessment.surfaceType == "temporary_drivable_surface"' in retry_source
        and 'assessment.temporalConsistency == "consistent"' in retry_source
        and "NativeDetectionRetryPolicy.shouldRetry(attempts)" in retry_source
        and "NativeDetectionRetryPolicy.acceptedByMajority(attempts)" in retry_source
    )
    if not parity:
        raise GateError("native and evaluator production contracts have drifted; release blocked")
    return {
        "prompt": native_prompt,
        "schema": native_schema,
        "model": native_model,
        "detail": native_detail,
        "prompt_version": native_version,
        "schema_version": native_schema_version,
        "max_output_tokens": native_max_tokens,
        "retry_max_attempts": retry_max_attempts,
    }


def parse_source_mappings(values: list[str]) -> dict[str, Path]:
    mappings: dict[str, Path] = {}
    for value in values:
        source_id, separator, raw_path = value.partition("=")
        if not separator or not source_id or not raw_path:
            raise GateError("--source must be SOURCE_ID=/absolute/or/relative/path")
        if source_id in mappings:
            raise GateError(f"source {source_id} was supplied more than once")
        mappings[source_id] = Path(raw_path).expanduser()
    return mappings


def discover_sources(directory: Path, sources: dict[str, Any], mapped: dict[str, Path]) -> None:
    """Find private media by content fingerprint, never by a committed private filename."""
    if not directory.is_dir():
        raise GateError("--source-dir is not a directory")
    wanted = {metadata["sha256"]: source_id for source_id, metadata in sources.items()
              if source_id not in mapped}
    if not wanted:
        return
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        fingerprint = sha256_file(path)
        source_id = wanted.pop(fingerprint, None)
        if source_id:
            mapped[source_id] = path
        if not wanted:
            return


def probe_video(path: Path) -> dict[str, Any]:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,nb_frames:format=duration",
        "-of", "json", str(path),
    ]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        payload = json.loads(completed.stdout)
        stream = payload["streams"][0]
        return {
            "codec": stream["codec_name"],
            "width": int(stream["width"]),
            "height": int(stream["height"]),
            "frames": int(stream["nb_frames"]),
            "duration_seconds": float(payload["format"]["duration"]),
        }
    except (FileNotFoundError, subprocess.CalledProcessError, KeyError, IndexError,
            TypeError, ValueError, json.JSONDecodeError) as error:
        raise GateError("ffprobe could not verify a private source video") from error


def verify_sources(manifest: dict[str, Any], mappings: dict[str, Path],
                   source_dir: Path | None = None) -> dict[str, Path]:
    unknown = sorted(set(mappings) - set(manifest["sources"]))
    if unknown:
        raise GateError(f"unknown source mapping(s): {', '.join(unknown)}")
    if source_dir is not None:
        discover_sources(source_dir, manifest["sources"], mappings)
    missing = sorted(set(manifest["sources"]) - set(mappings))
    if missing:
        raise GateError("missing private source mapping(s): " + ", ".join(missing))

    verified: dict[str, Path] = {}
    for source_id, expected in manifest["sources"].items():
        path = mappings[source_id]
        if not path.is_file():
            raise GateError(f"private source {source_id} is not a readable file")
        if sha256_file(path) != expected["sha256"]:
            raise GateError(f"private source {source_id} failed its SHA-256 check")
        actual = probe_video(path)
        exact_fields = ("codec", "width", "height", "frames")
        if any(actual[field] != expected[field] for field in exact_fields):
            raise GateError(f"private source {source_id} failed its stream metadata check")
        if abs(actual["duration_seconds"] - float(expected["duration_seconds"])) > 0.000001:
            raise GateError(f"private source {source_id} failed its duration check")
        verified[source_id] = path
    return verified


def extract_fixtures(manifest: dict[str, Any], sources: dict[str, Path],
                     run_dir: Path) -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = []
    for event in manifest["events"]:
        for phase_index, phase in enumerate(event["phases"]):
            phase_dir = run_dir / event["id"] / f"phase-{phase_index}"
            phase_dir.mkdir(parents=True, exist_ok=False)
            frame_paths: list[Path] = []
            for frame_index, (timestamp, expected_hash) in enumerate(zip(
                    phase["timestamps_seconds"], phase["fixture_sha256"])):
                output = phase_dir / f"f{frame_index}.jpg"
                command = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-i", str(sources[event["source"]]), "-ss", f"{timestamp:.6f}",
                    "-map", "0:v:0", "-frames:v", "1", "-q:v", "2", "-n", str(output),
                ]
                try:
                    subprocess.run(command, check=True, capture_output=True)
                except (FileNotFoundError, subprocess.CalledProcessError) as error:
                    raise GateError(
                        f"ffmpeg could not extract {event['id']} phase {phase_index} frame {frame_index}"
                    ) from error
                if not output.is_file() or sha256_file(output) != expected_hash:
                    raise GateError(
                        f"extracted JPEG fingerprint mismatch for {event['id']} "
                        f"phase {phase_index} frame {frame_index}"
                    )
                frame_paths.append(output)
            fixtures.append({
                "event": event,
                "phase_index": phase_index,
                "frame_paths": frame_paths,
            })
    return fixtures


def build_production_request(fixture: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    frame_paths = fixture["frame_paths"]
    entry = {
        "path": str(frame_paths[1]),
        "frames": [str(path) for path in frame_paths],
        "mode": "drive",
    }
    views, _transforms, layout_note = production_eval.prepare_event(entry, Path("/"), "drive")
    prompt = production_eval.effective_prompt(contract["prompt"], "drive", layout_note)
    body = production_eval.build_request(
        views, prompt, contract["model"], contract["detail"], mode="drive")
    body_schema = body.get("text", {}).get("format", {}).get("schema")
    if (body.get("model") != contract["model"]
            or body.get("reasoning") != {"effort": "low"}
            or body.get("store") is not False
            or body.get("stream") is not True
            or body.get("max_output_tokens") != contract["max_output_tokens"]
            or body_schema != contract["schema"]):
        raise GateError("generated request is not the current production detection contract")
    return body


def validate_assessment(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GateError("API assessment is not a JSON object")
    required = schema["required"]
    if set(value) != set(required):
        raise GateError("API assessment does not exactly match the strict schema fields")
    properties = schema["properties"]
    for field in required:
        item = value[field]
        spec = properties[field]
        allowed_types = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        valid_type = any(
            (kind == "boolean" and type(item) is bool)
            or (kind == "string" and isinstance(item, str))
            or (kind == "null" and item is None)
            for kind in allowed_types
        )
        if not valid_type:
            raise GateError(f"API assessment field {field} has the wrong type")
        if "enum" in spec and item not in spec["enum"]:
            raise GateError(f"API assessment field {field} is outside its enum")
    return value


def fresh_api_assessment(api_key: str, body: dict[str, Any], timeout: int,
                         schema: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    request = urllib.request.Request(API_URL, data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            assessment, response_id = production_eval.parse_api_response(
                response.read(), body.get("stream") is True)
    except (urllib.error.URLError, TimeoutError, ValueError, TypeError,
            StopIteration, json.JSONDecodeError) as error:
        raise GateError("fresh OpenAI request failed or returned invalid JSON") from error
    return validate_assessment(assessment, schema), response_id


def production_decision_with_retries(get_assessment) -> tuple[str, int]:
    """Execute the native bounded temporary-surface vote on complete gate responses."""
    outcome = production_eval.run_bounded_detection_policy(
        get_assessment, mode="drive", source_view_count=3)
    return outcome.decision, outcome.attempts_started


def load_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        env_path = ROOT / ".env"
        if env_path.is_file():
            for line in env_path.read_text().splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    if not key:
        raise GateError("OPENAI_API_KEY is required for a fresh release-gate evaluation")
    return key


def expected_decision(label: str) -> str:
    return "accept" if label == "pothole" else "reject"


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source", action="append", default=[], metavar="ID=PATH",
                        help="map a manifest source ID to an exact private video")
    parser.add_argument("--source-dir", type=Path,
                        help="find the exact private videos in this directory by SHA-256")
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--api-timeout", type=int, default=30)
    parser.add_argument("--check-manifest", action="store_true",
                        help="offline metadata and production-contract check only")
    parser.add_argument("--validate-only", action="store_true",
                        help="verify and extract private media without making API calls")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if args.trials < 1 or args.trials > 20:
        raise GateError("--trials must be between 1 and 20")
    if args.api_timeout < 1:
        raise GateError("--api-timeout must be positive")
    manifest = load_manifest(args.manifest)
    contract = load_production_contract()
    if args.check_manifest:
        print("PRIVATE RELEASE GATE MANIFEST PASS")
        return 0

    mappings = parse_source_mappings(args.source)
    verified_sources = verify_sources(manifest, mappings, args.source_dir)
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.work_root / f"run-{run_stamp}-{uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    fixtures = extract_fixtures(manifest, verified_sources, run_dir)
    print(f"Verified {len(verified_sources)} exact private videos and {len(fixtures) * 3} JPEGs")
    if args.validate_only:
        print("PRIVATE RELEASE GATE MEDIA PASS (API NOT RUN)")
        return 0

    api_key = load_api_key()
    failures: list[str] = []
    completed = 0
    api_calls = 0
    for fixture in fixtures:
        event = fixture["event"]
        expected = expected_decision(event["label"])
        body = build_production_request(fixture, contract)
        for trial in range(args.trials):
            def get_assessment():
                nonlocal api_calls
                api_calls += 1
                assessment, _response_id = fresh_api_assessment(
                    api_key, body, args.api_timeout, contract["schema"])
                return assessment

            actual, attempts = production_decision_with_retries(get_assessment)
            completed += 1
            status = "PASS" if actual == expected else "FAIL"
            print(f"{status} {event['id']} phase={fixture['phase_index']} trial={trial + 1} "
                  f"attempts={attempts} expected={expected} actual={actual}")
            if actual != expected:
                failures.append(
                    f"{event['id']} phase {fixture['phase_index']} trial {trial + 1}"
                )
    if failures:
        raise GateError(
            f"release blocked: {len(failures)} of {completed} strict decisions failed; "
            + ", ".join(failures)
        )
    print(f"PRIVATE RELEASE GATE PASS ({completed} production decisions, "
          f"{api_calls} fresh API calls)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GateError as error:
        print(f"PRIVATE RELEASE GATE FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
