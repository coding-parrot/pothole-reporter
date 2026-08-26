#!/usr/bin/env python3
"""Build or verify content-addressed State/UT highway-contract packs.

The normalized source snapshot is intentionally retained outside the runtime packs. In
particular, ``bid_due_at`` helps refresh tooling distinguish open and expired notices but
is not part of the strict browser record contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_RELATIVE_PATH = Path("data/tenders-national-highways.json")
PACK_ROOT_RELATIVE_PATH = Path("docs/packs/v1/contracts")
MANIFEST_RELATIVE_PATHS = (
    Path("static/contract-manifest-v1.36.json"),
    Path("docs/contract-manifest-v1.36.json"),
    Path("android-app/www/contract-manifest-v1.36.json"),
)

PACK_FORMAT = "pothole-highway-contract-pack"
MANIFEST_FORMAT = "pothole-contract-manifest"
ADAPTER = "nhai-nhidcl-public-projects-v1"
PUBLIC_BASE_URL = "https://coding-parrot.github.io/pothole-reporter/"
SCHEMA_VERSION = 1
PACK_VERSION = 1
CATALOG_VERSION = 1
MAX_PACK_BYTES = 8 * 1024 * 1024
MAX_RECORDS_PER_PACK = 10_000
CACHE_POLICY = {"max_bytes": 64 * 1024 * 1024, "max_unused_days": 30}
REVIEW_DAYS = 30
LICENSES = [
    "Official Government of India public information; respective source terms apply"
]

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STATE_RE = re.compile(r"^[A-Z]{2}$")
HIGHWAY_REF_RE = re.compile(
    r"^N[HE]-[0-9]{1,4}[A-Z]{0,3}(?: / N[HE]-[0-9]{1,4}[A-Z]{0,3})*$"
)

TOP_LEVEL_FIELDS = {"schema_version", "generated_at", "sources", "contracts"}
SOURCE_FIELDS = {
    "source_name",
    "source_url",
    "retrieved_at",
    "input_mode",
    "sha256",
    "records_seen",
    "records_kept",
}
INPUT_RECORD_FIELDS = {
    "record_id",
    "reference_label",
    "reference_value",
    "state_code",
    "agency",
    "lifecycle",
    "lifecycle_status",
    "title",
    "highway_refs",
    "chainages",
    "contractor",
    "published_at",
    "start_date",
    "likely_completion_date",
    "bid_due_at",
    "division",
    "source_name",
    "source_url",
    "retrieved_at",
    "scope_verified",
    "segment_verified",
    "award_verified",
    "dlp_verified",
}
OUTPUT_RECORD_FIELDS = INPUT_RECORD_FIELDS - {"bid_due_at"}
CHAINAGE_FIELDS = {"start_km", "end_km"}
REFERENCE_LABELS = {
    "UPC": "UPC",
    "Tender ID": "Tender ID",
    "Official notice fingerprint": "NHIDCL notice",
    "NHIDCL notice": "NHIDCL notice",
}


class BuildError(RuntimeError):
    """Raised when source data or generated artifacts violate the runtime contract."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise BuildError(message)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BuildError(f"missing normalized input: {path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"invalid UTF-8 JSON in {path}: {exc}") from exc


def _compact_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _manifest_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_if_changed(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == content:
        return
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _is_https_url(value: Any) -> bool:
    if not isinstance(value, str) or value != value.strip():
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _date(value: Any, field: str) -> str:
    _expect(isinstance(value, str) and DATE_RE.fullmatch(value) is not None,
            f"{field} must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise BuildError(f"{field} is not a calendar date") from exc
    return value


def _generated_date(value: Any) -> str:
    _expect(isinstance(value, str) and value == value.strip(),
            "generated_at must be an ISO UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BuildError("generated_at must be an ISO UTC timestamp") from exc
    _expect(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0),
            "generated_at must include the UTC timezone")
    return parsed.astimezone(timezone.utc).date().isoformat()


def _text(value: Any, field: str, maximum: int) -> str:
    _expect(isinstance(value, str) and bool(value) and value == value.strip(),
            f"{field} must be non-empty text without surrounding whitespace")
    _expect(len(value) <= maximum, f"{field} exceeds {maximum} characters")
    return value


def _nullable_text(value: Any, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, field, maximum)


def _nullable_contract_date(value: Any, field: str) -> str | None:
    if value is None:
        return None
    value = _text(value, field, 40)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BuildError(f"{field} must be an ISO date or timestamp") from exc
    return value


def _source_entry(value: Any, index: int) -> dict[str, Any]:
    field = f"sources[{index}]"
    _expect(isinstance(value, dict) and set(value) == SOURCE_FIELDS,
            f"{field} fields differ from the normalized-source contract")
    seen = value["records_seen"]
    kept = value["records_kept"]
    _expect(type(seen) is int and seen >= 0, f"{field}.records_seen must be non-negative")
    _expect(type(kept) is int and 0 <= kept <= seen,
            f"{field}.records_kept must be between zero and records_seen")
    _expect(_is_https_url(value["source_url"]), f"{field}.source_url must use HTTPS")
    _expect(isinstance(value["sha256"], str) and SHA256_RE.fullmatch(value["sha256"]),
            f"{field}.sha256 must be lowercase SHA-256")
    return {
        "source_name": _text(value["source_name"], f"{field}.source_name", 300),
        "source_url": value["source_url"],
        "retrieved_at": _date(value["retrieved_at"], f"{field}.retrieved_at"),
        "input_mode": _text(value["input_mode"], f"{field}.input_mode", 100),
        "sha256": value["sha256"],
        "records_seen": seen,
        "records_kept": kept,
    }


def _number(value: Any, field: str) -> int | float:
    _expect(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value),
        f"{field} must be a finite number",
    )
    number = float(value)
    _expect(0 <= number <= 10_000, f"{field} is outside the runtime range")
    return int(number) if number.is_integer() else number


def _chainages(value: Any, field: str) -> list[dict[str, int | float]]:
    _expect(isinstance(value, list) and len(value) <= 16,
            f"{field} must contain at most 16 ranges")
    ranges: set[tuple[int | float, int | float]] = set()
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        _expect(isinstance(item, dict) and set(item) == CHAINAGE_FIELDS,
                f"{item_field} fields differ from the runtime contract")
        start = _number(item["start_km"], f"{item_field}.start_km")
        end = _number(item["end_km"], f"{item_field}.end_km")
        _expect(end >= start, f"{item_field} end_km precedes start_km")
        ranges.add((start, end))
    return [
        {"start_km": start, "end_km": end}
        for start, end in sorted(ranges)
    ]


def _highway_refs(value: Any, field: str) -> list[str]:
    # The normalized research inventory deliberately retains official rows whose title
    # has no parseable NH/NE reference. They remain auditable source records, but cannot
    # enter a GPS-to-highway runtime index and are filtered in plan_build().
    _expect(isinstance(value, list) and len(value) <= 12,
            f"{field} must contain at most 12 highway references")
    refs: set[str] = set()
    for index, ref in enumerate(value):
        _expect(isinstance(ref, str) and HIGHWAY_REF_RE.fullmatch(ref) is not None,
                f"{field}[{index}] is not a canonical NH/NE reference")
        refs.add(ref)
    return sorted(refs)


def _normalized_record(value: Any, index: int) -> dict[str, Any]:
    field = f"contracts[{index}]"
    _expect(isinstance(value, dict) and set(value) == INPUT_RECORD_FIELDS,
            f"{field} fields differ from the normalized-source contract")

    state_code = value["state_code"]
    _expect(isinstance(state_code, str) and STATE_RE.fullmatch(state_code) is not None,
            f"{field}.state_code must be two uppercase letters")
    reference_label = REFERENCE_LABELS.get(value["reference_label"])
    _expect(reference_label is not None, f"{field}.reference_label is unsupported")
    if value["reference_label"] == "Official notice fingerprint":
        _expect(value["agency"] == "NHIDCL",
                f"{field} official notice fingerprints must belong to NHIDCL")

    agency = value["agency"]
    lifecycle = value["lifecycle"]
    _expect(agency in {"NHAI", "MoRTH", "NHIDCL"}, f"{field}.agency is unsupported")
    _expect(lifecycle in {"current_project", "procurement_notice"},
            f"{field}.lifecycle is unsupported")
    lifecycle_status = _text(
        value["lifecycle_status"], f"{field}.lifecycle_status", 160
    )
    status_text = lifecycle_status.casefold()
    ongoing_after_cc = "o&m" in status_text or "operation and maintenance" in status_text
    completed_status = (
        "archived" in status_text
        or "completed" in status_text
        or (("cc issued" in status_text or "pcc issued" in status_text)
            and not ongoing_after_cc)
    )
    _expect(
        not (lifecycle == "current_project" and completed_status),
        f"{field} completed/archived source status cannot be a current project",
    )
    contractor = _nullable_text(value["contractor"], f"{field}.contractor", 300)
    _expect(type(value["award_verified"]) is bool,
            f"{field}.award_verified must be boolean")
    if lifecycle == "procurement_notice":
        _expect(contractor is None and value["award_verified"] is False,
                f"{field} procurement notices cannot claim a contractor or award")

    _expect(value["scope_verified"] is True,
            f"{field}.scope_verified must be true")
    _expect(value["segment_verified"] is False,
            f"{field}.segment_verified must remain false")
    _expect(value["dlp_verified"] is False,
            f"{field}.dlp_verified must remain false")
    _expect(_is_https_url(value["source_url"]), f"{field}.source_url must use HTTPS")
    if value["bid_due_at"] is not None:
        _nullable_contract_date(value["bid_due_at"], f"{field}.bid_due_at")

    record = {
        "record_id": _text(value["record_id"], f"{field}.record_id", 160),
        "reference_label": reference_label,
        "reference_value": _text(
            value["reference_value"], f"{field}.reference_value", 200
        ),
        "state_code": state_code,
        "agency": agency,
        "lifecycle": lifecycle,
        "lifecycle_status": lifecycle_status,
        "title": _text(value["title"], f"{field}.title", 1200),
        "highway_refs": _highway_refs(value["highway_refs"], f"{field}.highway_refs"),
        "chainages": _chainages(value["chainages"], f"{field}.chainages"),
        "contractor": contractor,
        "published_at": _nullable_contract_date(
            value["published_at"], f"{field}.published_at"
        ),
        "start_date": _nullable_contract_date(
            value["start_date"], f"{field}.start_date"
        ),
        "likely_completion_date": _nullable_contract_date(
            value["likely_completion_date"], f"{field}.likely_completion_date"
        ),
        "division": _nullable_text(value["division"], f"{field}.division", 300),
        "source_name": _text(value["source_name"], f"{field}.source_name", 300),
        "source_url": value["source_url"],
        "retrieved_at": _date(value["retrieved_at"], f"{field}.retrieved_at"),
        "scope_verified": True,
        "segment_verified": False,
        "award_verified": value["award_verified"],
        "dlp_verified": False,
    }
    _expect(set(record) == OUTPUT_RECORD_FIELDS,
            f"{field} internal runtime projection has incorrect fields")
    return record


def _normalized_input(project_root: Path) -> tuple[str, list[dict[str, Any]]]:
    path = project_root / INPUT_RELATIVE_PATH
    payload = _read_json(path)
    _expect(isinstance(payload, dict) and set(payload) == TOP_LEVEL_FIELDS,
            "normalized input top-level fields differ from the contract")
    _expect(payload["schema_version"] == SCHEMA_VERSION,
            "normalized input schema_version must be 1")
    generated_at = _generated_date(payload["generated_at"])
    sources = payload["sources"]
    _expect(isinstance(sources, list) and bool(sources),
            "normalized input sources must be a non-empty array")
    normalized_sources = [_source_entry(item, index) for index, item in enumerate(sources)]
    _expect(len({(item["source_name"], item["source_url"]) for item in normalized_sources})
            == len(normalized_sources), "normalized input contains duplicate source receipts")

    contracts = payload["contracts"]
    _expect(isinstance(contracts, list) and bool(contracts),
            "normalized input contracts must be a non-empty array")
    normalized = [_normalized_record(item, index) for index, item in enumerate(contracts)]
    record_ids = [item["record_id"] for item in normalized]
    _expect(len(set(record_ids)) == len(record_ids),
            "normalized input contains duplicate record_id values")
    return generated_at, normalized


def _pack_envelope(
    state_code: str, generated_at: str, contracts: list[dict[str, Any]]
) -> dict[str, Any]:
    pack_id = f"in-nh-contracts-{state_code.lower()}"
    return {
        "format": PACK_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "pack_id": pack_id,
        "pack_version": PACK_VERSION,
        "state_code": state_code,
        "adapter": ADAPTER,
        "generated_at": generated_at,
        "contracts": contracts,
    }


def plan_build(project_root: Path = PROJECT_ROOT) -> tuple[dict[Path, bytes], bytes]:
    """Validate the normalized source and return every expected pack plus manifest bytes."""
    project_root = Path(project_root)
    generated_at, contracts = _normalized_input(project_root)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in contracts:
        if not record["highway_refs"]:
            continue
        grouped[record["state_code"]].append(record)
    _expect(bool(grouped), "normalized input has no contracts with a mapped NH/NE reference")

    resources: dict[str, dict[str, Any]] = {}
    packs: dict[Path, bytes] = {}
    review_after = (date.fromisoformat(generated_at) + timedelta(days=REVIEW_DAYS)).isoformat()
    for state_code in sorted(grouped):
        rows = sorted(grouped[state_code], key=lambda item: item["record_id"])
        _expect(len(rows) <= MAX_RECORDS_PER_PACK,
                f"{state_code} exceeds {MAX_RECORDS_PER_PACK} runtime records")
        pack_id = f"in-nh-contracts-{state_code.lower()}"
        pack_bytes = _compact_json(_pack_envelope(state_code, generated_at, rows))
        _expect(0 < len(pack_bytes) <= MAX_PACK_BYTES,
                f"{pack_id} exceeds the {MAX_PACK_BYTES}-byte runtime limit")
        digest = hashlib.sha256(pack_bytes).hexdigest()
        relative_path = Path(
            f"docs/packs/v1/contracts/{state_code.lower()}/highways-{digest}.json"
        )
        public_path = relative_path.relative_to("docs").as_posix()
        packs[relative_path] = pack_bytes
        resources[pack_id] = {
            "pack_id": pack_id,
            "state_code": state_code,
            "kind": "highway_contracts",
            "pack_version": PACK_VERSION,
            "schema_version": SCHEMA_VERSION,
            "adapter": ADAPTER,
            "path": public_path,
            "url": PUBLIC_BASE_URL + public_path,
            "bytes": len(pack_bytes),
            "sha256": digest,
            "records": len(rows),
            "coverage_scope": (
                "Official NHAI, MoRTH and NHIDCL public project and procurement "
                f"records assigned to {state_code}"
            ),
            "source_retrieved_at": generated_at,
            "review_after": review_after,
            "licenses": list(LICENSES),
        }

    manifest = {
        "format": MANIFEST_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "catalog_version": CATALOG_VERSION,
        "generated_at": generated_at,
        "cache": dict(CACHE_POLICY),
        "resources": resources,
    }
    return packs, _manifest_json(manifest)


def build_all(project_root: Path = PROJECT_ROOT) -> list[Path]:
    """Write immutable packs and identical manifest mirrors after full validation."""
    project_root = Path(project_root)
    packs, manifest_bytes = plan_build(project_root)
    outputs: list[Path] = []
    for relative_path, pack_bytes in sorted(packs.items(), key=lambda item: str(item[0])):
        output = project_root / relative_path
        if output.exists() and output.read_bytes() != pack_bytes:
            raise BuildError(f"content-address collision: {relative_path}")
        _write_if_changed(output, pack_bytes)
        outputs.append(output)
    for relative_path in MANIFEST_RELATIVE_PATHS:
        output = project_root / relative_path
        _write_if_changed(output, manifest_bytes)
        outputs.append(output)
    verify_all(project_root)
    return outputs


def verify_all(project_root: Path = PROJECT_ROOT) -> None:
    """Verify expected artifacts without creating, deleting, or repairing any file."""
    project_root = Path(project_root)
    packs, manifest_bytes = plan_build(project_root)
    for relative_path, expected in packs.items():
        path = project_root / relative_path
        try:
            actual = path.read_bytes()
        except FileNotFoundError as exc:
            raise BuildError(f"missing generated contract pack: {relative_path}") from exc
        _expect(actual == expected, f"generated contract pack differs: {relative_path}")
    for relative_path in MANIFEST_RELATIVE_PATHS:
        path = project_root / relative_path
        try:
            actual = path.read_bytes()
        except FileNotFoundError as exc:
            raise BuildError(f"missing contract manifest mirror: {relative_path}") from exc
        _expect(actual == manifest_bytes, f"contract manifest differs: {relative_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build immutable State/UT highway-contract packs and manifest mirrors."
    )
    parser.add_argument(
        "--check", action="store_true", help="verify only; never mutate the checkout"
    )
    args = parser.parse_args()
    try:
        if args.check:
            verify_all()
            print("highway contract packs OK")
        else:
            for output in build_all():
                print(output.relative_to(PROJECT_ROOT))
            print("highway contract packs and manifest mirrors updated")
    except BuildError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
