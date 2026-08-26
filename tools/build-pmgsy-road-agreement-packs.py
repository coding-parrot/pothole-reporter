#!/usr/bin/env python3
"""Build or verify content-addressed State/UT PMGSY road-agreement packs.

The source snapshots contain only rows whose official PMGSY ``WORK_STATUS`` was
``In Progress`` when retrieved.  A road name/package/agreement can be a useful
candidate, but it does not prove that the captured GPS segment belongs to that project.
Contractor, maintenance and DLP responsibility are therefore never inferred.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = Path("data/pmgsy-road-agreements/sources")
PACK_ROOT = Path("docs/packs/v1/road-agreements")
MANIFEST_PATHS = (
    Path("static/road-agreement-manifest-v1.36.json"),
    Path("docs/road-agreement-manifest-v1.36.json"),
    Path("android-app/www/road-agreement-manifest-v1.36.json"),
)

SOURCE_FORMAT = "pmgsy-current-road-agreement-source"
PACK_FORMAT = "pothole-pmgsy-road-agreement-pack"
MANIFEST_FORMAT = "pothole-road-agreement-manifest"
ADAPTER = "pmgsy-ommas-in-progress-v1"
KIND = "road_current_agreements"
LIFECYCLE = "current_project"
SOURCE_STATUS = "In Progress"
PUBLIC_BASE_URL = "https://coding-parrot.github.io/pothole-reporter/"
SCHEMA_VERSION = 1
PACK_VERSION = 1
CATALOG_VERSION = 1
REVIEW_DAYS = 30
MAX_PACK_BYTES = 8 * 1024 * 1024
MAX_RECORDS_PER_PACK = 100_000
CACHE_POLICY = {"max_bytes": 16 * 1024 * 1024, "max_unused_days": 30}
LICENSES = [
    "Official Government of India PMGSY dashboard information; source terms apply"
]
INFERENCE_POLICY = {
    "candidate_only": True,
    "lifecycle": LIFECYCLE,
    "source_status": SOURCE_STATUS,
    "freshness_window_years": 5,
    "scope_verified": True,
    "agreement_verified": True,
    "award_verified": False,
    "segment_verified": False,
    "contractor_assignment_verified": False,
    "dlp_verified": False,
}
SOURCE_LIMITATIONS = [
    "WORK_STATUS is source-reported and not independently freshness-verified",
    "catalog excludes missing, future, or more-than-five-year-old agreement dates",
    "road names are not road geometry and do not verify responsibility for a captured segment",
    "endpoint does not provide contractor, completion, maintenance, or DLP fields",
    "agreement number/date verifies those agreement fields, not an award or contractor assignment",
]

# The PMGSY dashboard still publishes two legacy feeds for the merged DH Union Territory.
# Both source snapshots are required; their records are merged into one DH runtime pack.
EXPECTED_STATE_CODE_BY_SOURCE_ID = {
    1: "AN", 2: "AP", 3: "AR", 4: "AS", 5: "BR", 6: "CH", 7: "CG",
    8: "DH", 9: "DH", 10: "DL", 11: "GA", 12: "GJ", 13: "HR",
    14: "HP", 15: "JK", 16: "JH", 17: "KA", 18: "KL", 19: "LD",
    20: "MP", 21: "MH", 22: "MN", 23: "ML", 24: "MZ", 25: "NL",
    26: "OD", 27: "PY", 28: "PB", 29: "RJ", 30: "SK", 31: "TN",
    32: "TR", 33: "UP", 34: "UK", 35: "WB", 36: "TG", 37: "LA",
}

SOURCE_FIELDS = {
    "format", "schema_version", "source_id", "source_name", "source_url",
    "endpoint", "retrieved_at", "source_state_id", "source_state_name",
    "state_code", "source_status_filter", "rows_scanned",
    "freshness_window_years", "rows_excluded_by_status",
    "rows_excluded_by_freshness", "rows_excluded_invalid",
    "records_kept", "limitations", "agreements",
}
SOURCE_AGREEMENT_FIELDS = {
    "record_id", "reference_label", "reference_value", "state_code", "agency",
    "lifecycle", "lifecycle_status", "lifecycle_basis", "title", "road_id",
    "package_number", "state_name", "district_id", "district_name", "road_from",
    "road_to", "scheme", "scheme_id", "project_year", "project_batch",
    "pavement_length_km", "sanctioned_date", "sanctioned_cost",
    "agreement_number", "agreement_date", "agreement_amount",
    "days_sanction_to_agreement", "contractor", "completion_date",
    "maintenance_start_date", "maintenance_end_date", "scope_verified",
    "segment_verified", "agreement_verified", "award_verified",
    "contractor_assignment_verified", "dlp_verified", "source_name", "source_url",
    "retrieved_at",
}
RUNTIME_AGREEMENT_FIELDS = (
    "record_id",
    "reference_value",
    "title",
    "road_id",
    "district_name",
    "road_from",
    "road_to",
    "agreement_number",
    "agreement_date",
)
PACK_SOURCE_FIELDS = {
    "source_id", "source_name", "source_url", "endpoint", "source_state_id",
    "source_state_name", "retrieved_at", "rows_scanned",
    "freshness_window_years", "rows_excluded_by_status",
    "rows_excluded_by_freshness", "rows_excluded_invalid", "records_kept",
}
RESOURCE_FIELDS = {
    "pack_id", "state_code", "kind", "pack_version", "schema_version", "adapter",
    "path", "url", "bytes", "sha256", "records", "sources", "rows_scanned",
    "rows_excluded_by_status", "rows_excluded_invalid", "lifecycle",
    "rows_excluded_by_freshness", "candidate_only", "source_retrieved_at",
    "review_after", "licenses",
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STATE_RE = re.compile(r"^[A-Z]{2}$")
SOURCE_ID_RE = re.compile(r"^in-[a-z]{2}-pmgsy-\d{2}$")


class BuildError(RuntimeError):
    """A source snapshot or generated runtime artifact violated its contract."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise BuildError(message)


def _read_json(path: Path) -> Any:
    try:
        content = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
        return json.loads(content.decode("utf-8"))
    except FileNotFoundError as error:
        raise BuildError(f"missing normalized PMGSY source: {path}") from error
    except (UnicodeDecodeError, json.JSONDecodeError, gzip.BadGzipFile) as error:
        raise BuildError(f"invalid UTF-8 JSON in {path}: {error}") from error


def _compact_json(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _manifest_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _write_if_changed(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == content:
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _text(value: Any, field: str, maximum: int) -> str:
    _expect(
        isinstance(value, str) and bool(value) and value == value.strip(),
        f"{field} must be non-empty text without surrounding whitespace",
    )
    _expect(len(value) <= maximum, f"{field} exceeds {maximum} characters")
    return value


def _nullable_text(value: Any, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, field, maximum)


def _https_url(value: Any, field: str) -> str:
    value = _text(value, field, 500)
    parsed = urlparse(value)
    _expect(parsed.scheme == "https" and bool(parsed.netloc), f"{field} must use HTTPS")
    return value


def _timestamp(value: Any, field: str) -> str:
    value = _text(value, field, 40)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise BuildError(f"{field} must be an ISO timestamp") from error
    _expect(
        parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0),
        f"{field} must use UTC",
    )
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _date(value: Any, field: str) -> str:
    value = _text(value, field, 10)
    _expect(DATE_RE.fullmatch(value) is not None, f"{field} must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise BuildError(f"{field} is not a calendar date") from error
    return value


def _nullable_date(value: Any, field: str) -> str | None:
    return None if value is None else _date(value, field)


def _identifier(value: Any, field: str, *, nullable: bool = False) -> int | str | None:
    if value is None and nullable:
        return None
    _expect(
        not isinstance(value, bool) and isinstance(value, (int, str)),
        f"{field} must be an integer or text identifier",
    )
    if isinstance(value, str):
        return _text(value, field, 200)
    return value


def _number(
    value: Any, field: str, *, nullable: bool = True, maximum: float = 1e15
) -> int | float | None:
    if value is None and nullable:
        return None
    _expect(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value),
        f"{field} must be a finite number",
    )
    _expect(abs(float(value)) <= maximum, f"{field} is outside the allowed range")
    return value


def _agreement(value: Any, index: int, source: dict[str, Any]) -> list[Any]:
    field = f"{source['source_id']}.agreements[{index}]"
    _expect(
        isinstance(value, dict) and set(value) == SOURCE_AGREEMENT_FIELDS,
        f"{field} fields differ from the normalized agreement contract",
    )
    _expect(value["state_code"] == source["state_code"], f"{field} state_code mismatch")
    _expect(value["reference_label"] == "PMGSY package", f"{field} reference label drifted")
    _expect(value["agency"] == "NRIDA / OMMAS", f"{field} agency drifted")
    _expect(value["lifecycle"] == LIFECYCLE, f"{field} lifecycle is not current_project")
    _expect(value["lifecycle_status"] == SOURCE_STATUS, f"{field} status is not In Progress")
    _expect(value["contractor"] is None, f"{field} invents a contractor")
    _expect(value["completion_date"] is None, f"{field} invents completion")
    _expect(value["maintenance_start_date"] is None, f"{field} invents maintenance")
    _expect(value["maintenance_end_date"] is None, f"{field} invents maintenance")
    _expect(value["scope_verified"] is True, f"{field} scope must be road work")
    _expect(value["segment_verified"] is False, f"{field} cannot verify the GPS segment")
    _expect(
        value["contractor_assignment_verified"] is False,
        f"{field} cannot verify contractor assignment",
    )
    _expect(value["dlp_verified"] is False, f"{field} cannot verify DLP")
    _expect(type(value["agreement_verified"]) is bool, f"{field} agreement flag invalid")
    _expect(type(value["award_verified"]) is bool, f"{field} award flag invalid")
    expected_agreement = bool(value["agreement_number"] and value["agreement_date"])
    _expect(
        value["agreement_verified"] == expected_agreement,
        f"{field} agreement evidence flag is inconsistent",
    )
    _expect(
        value["award_verified"] is False,
        f"{field} cannot verify an award from agreement number/date alone",
    )
    _expect(value["source_name"] == source["source_name"], f"{field} source name mismatch")
    _expect(value["source_url"] == source["endpoint"], f"{field} source URL mismatch")
    _expect(
        value["retrieved_at"] == source["retrieved_at"][:10],
        f"{field} retrieval date mismatch",
    )

    _text(value["record_id"], f"{field}.record_id", 300)
    _text(value["reference_value"], f"{field}.reference_value", 300)
    _text(value["lifecycle_basis"], f"{field}.lifecycle_basis", 300)
    _text(value["title"], f"{field}.title", 1_200)
    _identifier(value["road_id"], f"{field}.road_id")
    _text(value["package_number"], f"{field}.package_number", 300)
    _text(value["state_name"], f"{field}.state_name", 200)
    _identifier(value["district_id"], f"{field}.district_id", nullable=True)
    for key in ("district_name", "road_from", "road_to", "scheme"):
        _nullable_text(value[key], f"{field}.{key}", 600)
    for key in ("scheme_id", "project_year", "project_batch"):
        _identifier(value[key], f"{field}.{key}", nullable=True)
    _number(value["pavement_length_km"], f"{field}.pavement_length_km", maximum=100_000)
    _nullable_date(value["sanctioned_date"], f"{field}.sanctioned_date")
    _number(value["sanctioned_cost"], f"{field}.sanctioned_cost")
    _nullable_text(value["agreement_number"], f"{field}.agreement_number", 500)
    _nullable_date(value["agreement_date"], f"{field}.agreement_date")
    _expect(expected_agreement, f"{field} lacks a verified agreement number/date")
    retrieved_date = date.fromisoformat(source["retrieved_at"][:10])
    try:
        cutoff = retrieved_date.replace(
            year=retrieved_date.year - source["freshness_window_years"]
        )
    except ValueError:
        cutoff = retrieved_date.replace(
            year=retrieved_date.year - source["freshness_window_years"], day=28
        )
    agreement_date = date.fromisoformat(value["agreement_date"])
    _expect(
        cutoff <= agreement_date <= retrieved_date,
        f"{field} is outside the five-year agreement-date window",
    )
    _number(value["agreement_amount"], f"{field}.agreement_amount")
    _number(
        value["days_sanction_to_agreement"],
        f"{field}.days_sanction_to_agreement",
        maximum=1_000_000,
    )
    _date(value["retrieved_at"], f"{field}.retrieved_at")
    _https_url(value["source_url"], f"{field}.source_url")
    return [value[key] for key in RUNTIME_AGREEMENT_FIELDS]


def _source_snapshot(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    value = _read_json(path)
    field = path.name
    _expect(
        isinstance(value, dict) and set(value) == SOURCE_FIELDS,
        f"{field} fields differ from the PMGSY source contract",
    )
    _expect(value["format"] == SOURCE_FORMAT, f"{field} has unsupported format")
    _expect(value["schema_version"] == SCHEMA_VERSION, f"{field} schema_version must be 1")
    source_state_id = value["source_state_id"]
    _expect(
        type(source_state_id) is int and source_state_id in EXPECTED_STATE_CODE_BY_SOURCE_ID,
        f"{field}.source_state_id is invalid",
    )
    state_code = value["state_code"]
    _expect(
        state_code == EXPECTED_STATE_CODE_BY_SOURCE_ID[source_state_id],
        f"{field}.state_code does not match its official source selector",
    )
    expected_source_id = f"in-{state_code.lower()}-pmgsy-{source_state_id:02d}"
    _expect(value["source_id"] == expected_source_id, f"{field}.source_id is invalid")
    _expect(
        path.name == f"{expected_source_id}.json.gz",
        f"{field} filename must equal source_id",
    )
    _expect(SOURCE_ID_RE.fullmatch(expected_source_id) is not None, f"{field} source ID invalid")
    _expect(STATE_RE.fullmatch(state_code) is not None, f"{field} state code invalid")
    _expect(value["source_status_filter"] == SOURCE_STATUS, f"{field} status filter drifted")
    _expect(value["limitations"] == SOURCE_LIMITATIONS, f"{field} limitations drifted")
    _text(value["source_name"], f"{field}.source_name", 300)
    _https_url(value["source_url"], f"{field}.source_url")
    _https_url(value["endpoint"], f"{field}.endpoint")
    _timestamp(value["retrieved_at"], f"{field}.retrieved_at")
    _text(value["source_state_name"], f"{field}.source_state_name", 200)
    _expect(
        value["freshness_window_years"] == INFERENCE_POLICY["freshness_window_years"],
        f"{field} freshness window drifted",
    )
    for key in (
        "rows_scanned", "rows_excluded_by_status", "rows_excluded_by_freshness",
        "rows_excluded_invalid", "records_kept",
    ):
        _expect(type(value[key]) is int and value[key] >= 0, f"{field}.{key} is invalid")
    agreements = value["agreements"]
    _expect(isinstance(agreements, list), f"{field}.agreements must be an array")
    _expect(value["records_kept"] == len(agreements), f"{field} kept count mismatch")
    _expect(
        value["rows_excluded_by_status"]
        + value["rows_excluded_by_freshness"]
        + value["rows_excluded_invalid"]
        + value["records_kept"]
        == value["rows_scanned"],
        f"{field} source row accounting is inconsistent",
    )
    normalized = [_agreement(item, index, value) for index, item in enumerate(agreements)]
    receipt = {key: value[key] for key in PACK_SOURCE_FIELDS}
    return receipt, normalized


def _source_paths(project_root: Path) -> dict[int, Path]:
    directory = project_root / SOURCE_DIRECTORY
    paths = sorted(directory.glob("*.json.gz"), key=lambda path: path.name)
    by_id: dict[int, Path] = {}
    for path in paths:
        match = re.search(r"-(\d{2})\.json\.gz$", path.name)
        _expect(match is not None, f"unexpected PMGSY source filename: {path.name}")
        source_id = int(match.group(1))
        _expect(source_id not in by_id, f"duplicate PMGSY selector snapshot: {source_id}")
        by_id[source_id] = path
    expected = set(EXPECTED_STATE_CODE_BY_SOURCE_ID)
    _expect(set(by_id) == expected, "PMGSY source directory must contain all 37 selector snapshots")
    return by_id


def _pack_envelope(
    state_code: str,
    generated_at: str,
    sources: list[dict[str, Any]],
    agreements: list[list[Any]],
) -> dict[str, Any]:
    return {
        "format": PACK_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "pack_id": f"in-road-agreements-{state_code.lower()}",
        "pack_version": PACK_VERSION,
        "state_code": state_code,
        "adapter": ADAPTER,
        "generated_at": generated_at,
        "inference_policy": dict(INFERENCE_POLICY),
        "sources": sources,
        "agreement_fields": list(RUNTIME_AGREEMENT_FIELDS),
        "agreements": agreements,
    }


def plan_build(project_root: Path = PROJECT_ROOT) -> tuple[dict[Path, bytes], bytes]:
    """Validate one state at a time and return expected packs plus the manifest."""
    project_root = Path(project_root)
    by_id = _source_paths(project_root)
    grouped: dict[str, list[Path]] = defaultdict(list)
    for source_state_id, path in by_id.items():
        grouped[EXPECTED_STATE_CODE_BY_SOURCE_ID[source_state_id]].append(path)

    packs: dict[Path, bytes] = {}
    resources: dict[str, dict[str, Any]] = {}
    manifest_dates: list[str] = []
    all_record_ids: set[str] = set()
    for state_code in sorted(grouped):
        receipts: list[dict[str, Any]] = []
        agreements: list[list[Any]] = []
        for path in sorted(grouped[state_code], key=lambda item: item.name):
            receipt, source_agreements = _source_snapshot(path)
            receipts.append(receipt)
            for agreement in source_agreements:
                record_id = agreement[0]
                _expect(record_id not in all_record_ids, f"duplicate PMGSY record_id: {record_id}")
                all_record_ids.add(record_id)
                agreements.append(agreement)
        agreements.sort(
            key=lambda item: (
                item[4] or "",
                item[1] or "",
                str(item[3] or ""),
            )
        )
        _expect(
            len(agreements) <= MAX_RECORDS_PER_PACK,
            f"{state_code} exceeds {MAX_RECORDS_PER_PACK} PMGSY agreements",
        )
        retrieved_dates = [receipt["retrieved_at"][:10] for receipt in receipts]
        generated_at = max(retrieved_dates)
        manifest_dates.extend(retrieved_dates)
        pack_id = f"in-road-agreements-{state_code.lower()}"
        content = _compact_json(
            _pack_envelope(state_code, generated_at, receipts, agreements)
        )
        _expect(0 < len(content) <= MAX_PACK_BYTES, f"{pack_id} exceeds runtime size limit")
        digest = hashlib.sha256(content).hexdigest()
        relative_path = PACK_ROOT / state_code.lower() / f"agreements-{digest}.json"
        public_path = relative_path.relative_to("docs").as_posix()
        packs[relative_path] = content
        rows_scanned = sum(receipt["rows_scanned"] for receipt in receipts)
        excluded_status = sum(
            receipt["rows_excluded_by_status"] for receipt in receipts
        )
        excluded_freshness = sum(
            receipt["rows_excluded_by_freshness"] for receipt in receipts
        )
        excluded_invalid = sum(receipt["rows_excluded_invalid"] for receipt in receipts)
        resource = {
            "pack_id": pack_id,
            "state_code": state_code,
            "kind": KIND,
            "pack_version": PACK_VERSION,
            "schema_version": SCHEMA_VERSION,
            "adapter": ADAPTER,
            "path": public_path,
            "url": PUBLIC_BASE_URL + public_path,
            "bytes": len(content),
            "sha256": digest,
            "records": len(agreements),
            "sources": len(receipts),
            "rows_scanned": rows_scanned,
            "rows_excluded_by_status": excluded_status,
            "rows_excluded_by_freshness": excluded_freshness,
            "rows_excluded_invalid": excluded_invalid,
            "lifecycle": LIFECYCLE,
            "candidate_only": True,
            "source_retrieved_at": generated_at,
            "review_after": (
                date.fromisoformat(generated_at) + timedelta(days=REVIEW_DAYS)
            ).isoformat(),
            "licenses": list(LICENSES),
        }
        _expect(set(resource) == RESOURCE_FIELDS, f"{pack_id} resource fields drifted")
        resources[pack_id] = resource

    manifest = {
        "format": MANIFEST_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "catalog_version": CATALOG_VERSION,
        "generated_at": max(manifest_dates),
        "cache": dict(CACHE_POLICY),
        "inference_policy": dict(INFERENCE_POLICY),
        "resources": resources,
    }
    return packs, _manifest_json(manifest)


def build_all(project_root: Path = PROJECT_ROOT) -> list[Path]:
    project_root = Path(project_root)
    packs, manifest = plan_build(project_root)
    outputs: list[Path] = []
    for relative_path, content in sorted(packs.items(), key=lambda item: str(item[0])):
        output = project_root / relative_path
        if output.exists() and output.read_bytes() != content:
            raise BuildError(f"content-address collision: {relative_path}")
        _write_if_changed(output, content)
        outputs.append(output)
    for relative_path in MANIFEST_PATHS:
        output = project_root / relative_path
        _write_if_changed(output, manifest)
        outputs.append(output)
    verify_all(project_root)
    return outputs


def verify_all(project_root: Path = PROJECT_ROOT) -> None:
    project_root = Path(project_root)
    packs, manifest = plan_build(project_root)
    for relative_path, expected in packs.items():
        path = project_root / relative_path
        try:
            actual = path.read_bytes()
        except FileNotFoundError as error:
            raise BuildError(f"missing PMGSY runtime pack: {relative_path}") from error
        _expect(actual == expected, f"PMGSY runtime pack differs: {relative_path}")
        digest = relative_path.stem.removeprefix("agreements-")
        _expect(SHA256_RE.fullmatch(digest) is not None, f"pack path is not content-addressed")
    for relative_path in MANIFEST_PATHS:
        path = project_root / relative_path
        try:
            actual = path.read_bytes()
        except FileNotFoundError as error:
            raise BuildError(f"missing PMGSY manifest mirror: {relative_path}") from error
        _expect(actual == manifest, f"PMGSY manifest differs: {relative_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    args = parser.parse_args()
    try:
        if args.check:
            verify_all()
            print("PMGSY road-agreement packs OK")
        else:
            for output in build_all():
                print(output.relative_to(PROJECT_ROOT))
            print("PMGSY road-agreement packs and manifest mirrors updated")
    except BuildError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
