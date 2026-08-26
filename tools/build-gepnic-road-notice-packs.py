#!/usr/bin/env python3
"""Build or verify content-addressed official State/UT road-notice packs.

These packs contain procurement-notice candidates, not awarded or active contracts.
They preserve the official notice identity, work title, organisation chain, dates and
source URL while explicitly keeping segment, award and DLP verification false.  No
contractor field exists in the runtime schema.  The canonical GePNIC crawl and the
strictly validated snapshots from other official State portals share one runtime
format; portal-private fields are deliberately not shipped to clients.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterable
from urllib.parse import urlparse

from tender_scope import is_road_surface_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = Path("data/tender-sources-india.json")
SOURCE_DIRECTORY = Path("data/gepnic-road-notices/sources")
CUSTOM_SOURCE_DIRECTORY = Path("data/custom-road-tenders")
CRAWL_REPORT_PATH = Path("data/gepnic-road-notices/crawl-report.json")
PACK_ROOT = Path("docs/packs/v1/road-notices")
MANIFEST_PATHS = (
    Path("static/road-notice-manifest-v1.36.json"),
    Path("docs/road-notice-manifest-v1.36.json"),
    Path("android-app/www/road-notice-manifest-v1.36.json"),
)

PACK_FORMAT = "pothole-official-road-notice-pack"
MANIFEST_FORMAT = "pothole-road-notice-manifest"
SOURCE_FORMAT = "gepnic-road-surface-procurement-notices"
CUSTOM_SOURCE_FORMAT = "official-road-surface-procurement-notices"
ADAPTER = "official-road-notices-v2"
LIFECYCLE = "procurement_notice"
SCOPE = "road_surface"
PUBLIC_BASE_URL = "https://coding-parrot.github.io/pothole-reporter/"
SCHEMA_VERSION = 1
PACK_VERSION = 1
CATALOG_VERSION = 1
REVIEW_DAYS = 7
MAX_PACK_BYTES = 8 * 1024 * 1024
MAX_RECORDS_PER_PACK = 20_000
CACHE_POLICY = {"max_bytes": 16 * 1024 * 1024, "max_unused_days": 14}
LICENSES = [
    "Official Indian government procurement information; respective portal terms apply"
]
INFERENCE_POLICY = {
    "candidate_only": True,
    "lifecycle": LIFECYCLE,
    "scope": SCOPE,
    "segment_verified": False,
    "award_verified": False,
    "dlp_verified": False,
}

STATE_RE = re.compile(r"^[A-Z]{2}$")
SOURCE_ID_RE = re.compile(r"^in-[a-z]{2}-[a-z0-9][a-z0-9-]*$")
GEPNIC_SOURCE_ID_RE = re.compile(r"^in-[a-z]{2}-gepnic(?:-[a-z0-9-]+)?$")
TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
JURISDICTION_RE = re.compile(r"^IN-([A-Z]{2})$")
STATE_CODE_OVERRIDES = {"CT": "CG", "UT": "UK"}

REPORT_FIELDS = {
    "failures", "format", "retrieved_at", "schema_version",
    "source_count_failed", "source_count_requested", "source_count_succeeded",
    "states",
}

SOURCE_FIELDS = {
    "format", "schema_version", "source_id", "source_name", "source_url",
    "retrieved_at", "state_code", "lifecycle", "organisations", "rows_scanned",
    "rows_excluded_by_scope", "notices",
}
NOTICE_FIELDS = {
    "closing_at", "detail_url", "lifecycle", "listing_url", "opening_at",
    "organisation_chain", "organisation_path", "published_at", "retrieved_at",
    "scope", "source_name", "source_url", "state_code", "tender_id",
    "tender_reference", "title",
}
RUNTIME_NOTICE_FIELDS = {
    "record_id", "tender_id", "tender_reference", "title", "organisation_chain",
    "published_at", "closing_at", "opening_at", "source_id", "source_url",
    "lifecycle", "scope", "segment_verified", "award_verified", "dlp_verified",
}
FORBIDDEN_INFERENCE_FIELDS = {
    "contractor", "contractor_name", "winning_bidder", "award", "award_date",
    "award_status", "road_segment", "chainage", "chainages", "dlp", "warranty",
    "active_contract",
}


class BuildError(RuntimeError):
    """Source data or generated artifacts violated the notice-only contract."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise BuildError(message)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise BuildError(f"missing JSON source: {path}") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BuildError(f"invalid UTF-8 JSON in {path}: {error}") from error


def _compact_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _manifest_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _report_json(value: Any) -> bytes:
    return _manifest_json(value)


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


def _text(value: Any, field: str, maximum: int) -> str:
    _expect(
        isinstance(value, str) and bool(value) and value == value.strip(),
        f"{field} must be non-empty text without surrounding whitespace",
    )
    _expect(len(value) <= maximum, f"{field} exceeds {maximum} characters")
    return value


def _https_url(value: Any, field: str, maximum: int = 500) -> str:
    value = _text(value, field, maximum)
    parsed = urlparse(value)
    _expect(parsed.scheme == "https" and bool(parsed.netloc), f"{field} must use HTTPS")
    return value


def _timestamp(value: Any, field: str, *, require_utc: bool = False) -> str:
    value = _text(value, field, 40)
    _expect(TIMESTAMP_RE.fullmatch(value) is not None, f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise BuildError(f"{field} must be an ISO timestamp") from error
    _expect(parsed.tzinfo is not None, f"{field} must include a timezone")
    if require_utc:
        _expect(parsed.utcoffset() == timedelta(0), f"{field} must use UTC")
    return value


def _same_origin(first: str, second: str) -> bool:
    a, b = urlparse(first), urlparse(second)
    return (a.scheme.lower(), a.netloc.lower()) == (b.scheme.lower(), b.netloc.lower())


@dataclass(frozen=True)
class SourceReceipt:
    source_id: str
    source_name: str
    source_url: str
    retrieved_at: str
    state_code: str
    rows_scanned: int
    rows_excluded_by_scope: int

    def runtime_value(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
            "rows_scanned": self.rows_scanned,
            "rows_excluded_by_scope": self.rows_excluded_by_scope,
        }


@dataclass(frozen=True)
class SourceSnapshot:
    receipt: SourceReceipt
    notices: tuple[dict[str, Any], ...]


def _normalise_notice(
    value: Any, index: int, receipt: SourceReceipt
) -> dict[str, Any] | None:
    field = f"{receipt.source_id}.notices[{index}]"
    _expect(
        isinstance(value, dict) and set(value) == NOTICE_FIELDS,
        f"{field} fields differ from the normalized notice contract",
    )
    _expect(value["lifecycle"] == LIFECYCLE, f"{field} must be a procurement notice")
    _expect(value["scope"] == SCOPE, f"{field} must have road_surface scope")
    _expect(value["state_code"] == receipt.state_code, f"{field} state_code mismatch")
    _expect(value["source_name"] == receipt.source_name, f"{field} source_name mismatch")
    _expect(value["retrieved_at"] == receipt.retrieved_at, f"{field} retrieved_at mismatch")

    title = _text(value["title"], f"{field}.title", 1_200)
    tender_reference = _text(
        value["tender_reference"], f"{field}.tender_reference", 300
    )
    runtime_scope_eligible = is_road_surface_contract(title, tender_reference)
    tender_id = _text(value["tender_id"], f"{field}.tender_id", 160)
    organisation_chain = _text(
        value["organisation_chain"], f"{field}.organisation_chain", 800
    )
    expected_path = [part.strip() for part in organisation_chain.split("||") if part.strip()]
    _expect(
        isinstance(value["organisation_path"], list)
        and value["organisation_path"] == expected_path,
        f"{field}.organisation_path does not match organisation_chain",
    )

    source_url = _https_url(value["source_url"], f"{field}.source_url")
    detail_url = _https_url(value["detail_url"], f"{field}.detail_url")
    listing_url = _https_url(value["listing_url"], f"{field}.listing_url")
    _expect(source_url == detail_url, f"{field} source_url must equal detail_url")
    _expect(
        _same_origin(receipt.source_url, source_url)
        and _same_origin(receipt.source_url, listing_url),
        f"{field} source links must stay on the source portal",
    )

    record = {
        "record_id": f"{receipt.source_id}:{tender_id}",
        "tender_id": tender_id,
        "tender_reference": tender_reference,
        "title": title,
        "organisation_chain": organisation_chain,
        "published_at": _timestamp(value["published_at"], f"{field}.published_at"),
        "closing_at": _timestamp(value["closing_at"], f"{field}.closing_at"),
        "opening_at": _timestamp(value["opening_at"], f"{field}.opening_at"),
        "source_id": receipt.source_id,
        "source_url": source_url,
        "lifecycle": LIFECYCLE,
        "scope": SCOPE,
        "segment_verified": False,
        "award_verified": False,
        "dlp_verified": False,
    }
    _expect(set(record) == RUNTIME_NOTICE_FIELDS, f"{field} runtime projection drifted")
    _expect(
        not (set(record) & FORBIDDEN_INFERENCE_FIELDS),
        f"{field} contains a forbidden contract inference",
    )
    return record if runtime_scope_eligible else None


def _gepnic_source_snapshot(path: Path) -> SourceSnapshot:
    value = _read_json(path)
    field = path.name
    _expect(
        isinstance(value, dict) and set(value) == SOURCE_FIELDS,
        f"{field} fields differ from the normalized source contract",
    )
    _expect(value["format"] == SOURCE_FORMAT, f"{field} has unsupported format")
    _expect(value["schema_version"] == SCHEMA_VERSION, f"{field} schema_version must be 1")
    _expect(value["lifecycle"] == LIFECYCLE, f"{field} must contain procurement notices")

    source_id = _text(value["source_id"], f"{field}.source_id", 100)
    _expect(
        GEPNIC_SOURCE_ID_RE.fullmatch(source_id) is not None,
        f"{field}.source_id is invalid",
    )
    _expect(path.stem == source_id, f"{field} filename must equal source_id")
    state_code = _text(value["state_code"], f"{field}.state_code", 2)
    _expect(STATE_RE.fullmatch(state_code) is not None, f"{field}.state_code is invalid")
    receipt = SourceReceipt(
        source_id=source_id,
        source_name=_text(value["source_name"], f"{field}.source_name", 300),
        source_url=_https_url(value["source_url"], f"{field}.source_url"),
        retrieved_at=_timestamp(
            value["retrieved_at"], f"{field}.retrieved_at", require_utc=True
        ),
        state_code=state_code,
        rows_scanned=value["rows_scanned"],
        rows_excluded_by_scope=value["rows_excluded_by_scope"],
    )
    _expect(
        type(receipt.rows_scanned) is int and receipt.rows_scanned >= 0,
        f"{field}.rows_scanned must be a non-negative integer",
    )
    _expect(
        type(receipt.rows_excluded_by_scope) is int
        and 0 <= receipt.rows_excluded_by_scope <= receipt.rows_scanned,
        f"{field}.rows_excluded_by_scope is invalid",
    )
    organisations = value["organisations"]
    _expect(
        isinstance(organisations, list)
        and all(isinstance(item, str) and item == item.strip() and item for item in organisations),
        f"{field}.organisations must be an array of non-empty strings",
    )
    _expect(
        len(organisations) == len(set(organisations)),
        f"{field}.organisations contains duplicates",
    )
    raw_notices = value["notices"]
    _expect(isinstance(raw_notices, list), f"{field}.notices must be an array")
    _expect(
        receipt.rows_excluded_by_scope + len(raw_notices) == receipt.rows_scanned,
        f"{field} row accounting is inconsistent",
    )
    normalised = tuple(
        _normalise_notice(notice, index, receipt)
        for index, notice in enumerate(raw_notices)
    )
    notices = tuple(notice for notice in normalised if notice is not None)
    return SourceSnapshot(receipt=receipt, notices=notices)


def _optional_text(value: Any, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, field, maximum)


def _optional_timestamp(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _timestamp(value, field)


def _custom_organisation(value: dict[str, Any], field: str) -> str:
    chain = _optional_text(value.get("organisation_chain"), f"{field}.organisation_chain", 800)
    if chain:
        return chain
    path = value.get("organisation_path")
    if isinstance(path, list) and path:
        parts = [_text(item, f"{field}.organisation_path", 300) for item in path]
        return "||".join(parts)
    identifiers: list[str] = []
    for key, label in (("organisation_id", "Organisation ID"), ("department_id", "Department ID")):
        identifier = value.get(key)
        if isinstance(identifier, (str, int)) and not isinstance(identifier, bool):
            rendered = str(identifier).strip()
            if rendered:
                identifiers.append(f"{label} {rendered}")
    if identifiers:
        return "||".join(identifiers)
    return "Organisation not named in public listing"


def _custom_notice(
    value: Any, index: int, receipt: SourceReceipt
) -> dict[str, Any] | None:
    field = f"{receipt.source_id}.notices[{index}]"
    _expect(isinstance(value, dict), f"{field} must be an object")
    _expect(value.get("lifecycle") == LIFECYCLE, f"{field} must be a procurement notice")
    _expect(value.get("scope") == SCOPE, f"{field} must have road_surface scope")
    _expect(value.get("state_code") == receipt.state_code, f"{field} state_code mismatch")
    if value.get("source_name") is not None:
        _expect(value.get("source_name") == receipt.source_name, f"{field} source_name mismatch")
    if value.get("retrieved_at") is not None:
        _expect(value.get("retrieved_at") == receipt.retrieved_at, f"{field} retrieved_at mismatch")

    title = _text(value.get("title"), f"{field}.title", 1_200)
    tender_reference = _text(
        value.get("tender_reference"), f"{field}.tender_reference", 300
    )
    runtime_scope_eligible = is_road_surface_contract(title, tender_reference)
    tender_id = _text(value.get("tender_id"), f"{field}.tender_id", 160)
    organisation_chain = _custom_organisation(value, field)

    listing_url_value = value.get("listing_url") or value.get("source_url")
    listing_url = _https_url(listing_url_value, f"{field}.listing_url")
    detail_url_value = value.get("detail_url") or listing_url
    detail_url = _https_url(detail_url_value, f"{field}.detail_url")
    _expect(
        _same_origin(receipt.source_url, listing_url)
        and _same_origin(receipt.source_url, detail_url),
        f"{field} source links must stay on the source portal",
    )

    record = {
        "record_id": f"{receipt.source_id}:{tender_id}",
        "tender_id": tender_id,
        "tender_reference": tender_reference,
        "title": title,
        "organisation_chain": organisation_chain,
        "published_at": _optional_timestamp(
            value.get("published_at"), f"{field}.published_at"
        ),
        "closing_at": _timestamp(value.get("closing_at"), f"{field}.closing_at"),
        "opening_at": _optional_timestamp(
            value.get("opening_at"), f"{field}.opening_at"
        ),
        "source_id": receipt.source_id,
        "source_url": detail_url,
        "lifecycle": LIFECYCLE,
        "scope": SCOPE,
        "segment_verified": False,
        "award_verified": False,
        "dlp_verified": False,
    }
    _expect(set(record) == RUNTIME_NOTICE_FIELDS, f"{field} runtime projection drifted")
    _expect(
        not (set(record) & FORBIDDEN_INFERENCE_FIELDS),
        f"{field} contains a forbidden contract inference",
    )
    return record if runtime_scope_eligible else None


def _custom_source_snapshot(path: Path) -> SourceSnapshot:
    value = _read_json(path)
    field = path.relative_to(path.parents[2]).as_posix() if len(path.parents) > 2 else path.name
    _expect(isinstance(value, dict), f"{field} must be an object")
    _expect(value.get("format") == CUSTOM_SOURCE_FORMAT, f"{field} has unsupported format")
    _expect(value.get("schema_version") == SCHEMA_VERSION, f"{field} schema_version must be 1")
    _expect(value.get("lifecycle") == LIFECYCLE, f"{field} must contain procurement notices")

    source_id = _text(value.get("source_id"), f"{field}.source_id", 100)
    _expect(SOURCE_ID_RE.fullmatch(source_id) is not None, f"{field}.source_id is invalid")
    _expect(path.stem == source_id, f"{field} filename must equal source_id")
    state_code = _text(value.get("state_code"), f"{field}.state_code", 2)
    _expect(STATE_RE.fullmatch(state_code) is not None, f"{field}.state_code is invalid")
    raw_notices = value.get("notices")
    _expect(isinstance(raw_notices, list), f"{field}.notices must be an array")
    rows_scanned = value.get("rows_scanned")
    rows_excluded = value.get("rows_excluded_by_scope")
    _expect(
        type(rows_scanned) is int and rows_scanned >= 0,
        f"{field}.rows_scanned must be a non-negative integer",
    )
    _expect(
        type(rows_excluded) is int and 0 <= rows_excluded <= rows_scanned,
        f"{field}.rows_excluded_by_scope is invalid",
    )
    _expect(
        rows_excluded + len(raw_notices) == rows_scanned,
        f"{field} row accounting is inconsistent",
    )
    if value.get("records_kept") is not None:
        _expect(
            value.get("records_kept") == len(raw_notices),
            f"{field}.records_kept differs from notices",
        )
    receipt = SourceReceipt(
        source_id=source_id,
        source_name=_text(value.get("source_name"), f"{field}.source_name", 300),
        source_url=_https_url(value.get("source_url"), f"{field}.source_url"),
        retrieved_at=_timestamp(
            value.get("retrieved_at"), f"{field}.retrieved_at", require_utc=True
        ),
        state_code=state_code,
        rows_scanned=rows_scanned,
        rows_excluded_by_scope=rows_excluded,
    )
    normalised = tuple(
        _custom_notice(notice, index, receipt)
        for index, notice in enumerate(raw_notices)
    )
    notices = tuple(notice for notice in normalised if notice is not None)
    return SourceSnapshot(receipt=receipt, notices=notices)


def _registry_sources(project_root: Path) -> dict[str, tuple[str, str]]:
    registry_path = project_root / REGISTRY_PATH
    registry = _read_json(registry_path)
    _expect(
        isinstance(registry, dict) and isinstance(registry.get("jurisdictions"), list),
        f"{REGISTRY_PATH} has no jurisdictions array",
    )
    declared: dict[str, tuple[str, str]] = {}
    for index, jurisdiction in enumerate(registry["jurisdictions"]):
        field = f"{REGISTRY_PATH}.jurisdictions[{index}]"
        _expect(isinstance(jurisdiction, dict), f"{field} must be an object")
        code_match = JURISDICTION_RE.fullmatch(str(jurisdiction.get("code") or ""))
        _expect(code_match is not None, f"{field}.code is invalid")
        source_state_code = code_match.group(1)
        runtime_state_code = STATE_CODE_OVERRIDES.get(
            source_state_code, source_state_code
        )
        sources = jurisdiction.get("sources")
        _expect(isinstance(sources, list), f"{field}.sources must be an array")
        for source_index, source in enumerate(sources):
            source_field = f"{field}.sources[{source_index}]"
            _expect(isinstance(source, dict), f"{source_field} must be an object")
            source_id = _text(source.get("id"), f"{source_field}.id", 100)
            portal_family = _text(
                source.get("portal_family"), f"{source_field}.portal_family", 100
            )
            _expect(
                SOURCE_ID_RE.fullmatch(source_id) is not None,
                f"{source_field}.id is not a valid official source ID",
            )
            _expect(source_id not in declared, f"duplicate registry source ID: {source_id}")
            declared[source_id] = (runtime_state_code, portal_family)
    _expect(bool(declared), f"{REGISTRY_PATH} declares no official tender sources")
    return declared


def _expected_gepnic_sources(project_root: Path) -> dict[str, str]:
    expected = {
        source_id: state_code
        for source_id, (state_code, portal_family) in _registry_sources(project_root).items()
        if portal_family == "nic_gepnic"
    }
    _expect(bool(expected), f"{REGISTRY_PATH} declares no State/UT GePNIC sources")
    return expected


def _source_files(project_root: Path, expected: dict[str, str]) -> list[Path]:
    directory = project_root / SOURCE_DIRECTORY
    try:
        paths = sorted(directory.glob("*.json"), key=lambda path: path.name)
    except OSError as error:
        raise BuildError(f"cannot list GePNIC sources: {directory}: {error}") from error
    actual = {path.stem for path in paths}
    missing = sorted(set(expected) - actual)
    unexpected = sorted(actual - set(expected))
    _expect(
        not missing,
        "missing expected GePNIC source files: " + ", ".join(missing),
    )
    _expect(
        not unexpected,
        "unexpected GePNIC source files not declared by registry: "
        + ", ".join(unexpected),
    )
    return paths


def _custom_source_files(
    project_root: Path, declarations: dict[str, tuple[str, str]]
) -> list[Path]:
    directory = project_root / CUSTOM_SOURCE_DIRECTORY
    if not directory.exists():
        return []
    try:
        paths = sorted(directory.rglob("*.json"), key=lambda path: path.as_posix())
    except OSError as error:
        raise BuildError(f"cannot list custom official sources: {directory}: {error}") from error
    expected_ids = {
        source_id for source_id, (_state_code, portal_family) in declarations.items()
        if portal_family != "nic_gepnic"
    }
    selected: list[Path] = []
    for path in paths:
        if path.stem in expected_ids:
            selected.append(path)
            continue
        value = _read_json(path)
        _expect(
            not (isinstance(value, dict) and value.get("format") == CUSTOM_SOURCE_FORMAT),
            f"unexpected official notice snapshot not declared by registry: {path}",
        )
    return selected


def _snapshot_date(timestamp: str) -> str:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).date().isoformat()


def _pack_envelope(
    state_code: str,
    generated_at: str,
    sources: list[SourceReceipt],
    notices: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "format": PACK_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "pack_id": f"in-road-notices-{state_code.lower()}",
        "pack_version": PACK_VERSION,
        "state_code": state_code,
        "adapter": ADAPTER,
        "generated_at": generated_at,
        "inference_policy": dict(INFERENCE_POLICY),
        "sources": [source.runtime_value() for source in sources],
        "notices": notices,
    }


def _load_sources(
    project_root: Path,
) -> tuple[list[SourceSnapshot], list[SourceSnapshot], dict[str, str]]:
    declarations = _registry_sources(project_root)
    expected = _expected_gepnic_sources(project_root)
    gepnic_snapshots = [
        _gepnic_source_snapshot(path) for path in _source_files(project_root, expected)
    ]
    custom_snapshots = [
        _custom_source_snapshot(path)
        for path in _custom_source_files(project_root, declarations)
    ]
    for snapshot in custom_snapshots:
        source_id = snapshot.receipt.source_id
        _expect(source_id in declarations, f"{source_id} is not declared by {REGISTRY_PATH}")
        expected_state_code, portal_family = declarations[source_id]
        _expect(
            portal_family != "nic_gepnic",
            f"{source_id} must use the canonical GePNIC snapshot directory",
        )
        _expect(
            snapshot.receipt.state_code == expected_state_code,
            f"{source_id} state_code must be {expected_state_code} as declared by the "
            "tender-source registry",
        )
    snapshots = gepnic_snapshots + custom_snapshots
    source_ids = [snapshot.receipt.source_id for snapshot in snapshots]
    _expect(len(source_ids) == len(set(source_ids)), "duplicate official source_id values")
    for snapshot in gepnic_snapshots:
        expected_state_code = expected[snapshot.receipt.source_id]
        _expect(
            snapshot.receipt.state_code == expected_state_code,
            f"{snapshot.receipt.source_id} state_code must be {expected_state_code} "
            "as declared by the tender-source registry",
        )
    record_ids = [
        notice["record_id"] for snapshot in snapshots for notice in snapshot.notices
    ]
    _expect(len(record_ids) == len(set(record_ids)), "duplicate official runtime record_id values")
    return snapshots, gepnic_snapshots, expected


def _validated_crawl_report(
    project_root: Path,
    expected_sources: dict[str, str],
    snapshots: list[SourceSnapshot],
    canonical_retrieved_at: str,
    state_summaries: dict[str, dict[str, int]],
) -> bytes:
    """Validate the crawler-owned report without replacing its failure ledger."""
    path = project_root / CRAWL_REPORT_PATH
    try:
        report_bytes = path.read_bytes()
    except FileNotFoundError as error:
        raise BuildError(f"missing canonical crawl report: {CRAWL_REPORT_PATH}") from error
    try:
        report = json.loads(report_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BuildError(f"invalid UTF-8 JSON in {CRAWL_REPORT_PATH}: {error}") from error
    _expect(
        isinstance(report, dict) and set(report) == REPORT_FIELDS,
        "canonical GePNIC crawl-report fields differ from the crawler contract",
    )
    _expect(
        report["format"] == "india-gepnic-road-surface-crawl-report",
        "canonical GePNIC crawl-report format is unsupported",
    )
    _expect(report["schema_version"] == SCHEMA_VERSION, "crawl-report schema drifted")
    _expect(
        _timestamp(report["retrieved_at"], "crawl-report.retrieved_at", require_utc=True)
        == canonical_retrieved_at,
        "crawl-report retrieved_at differs from its source receipts",
    )
    for key in (
        "source_count_requested", "source_count_succeeded", "source_count_failed"
    ):
        _expect(
            type(report[key]) is int and report[key] >= 0,
            f"crawl-report.{key} must be a non-negative integer",
        )
    failures = report["failures"]
    _expect(isinstance(failures, list), "crawl-report.failures must be an array")
    _expect(
        report["source_count_requested"] == len(expected_sources),
        "crawl-report requested-source count differs from the registry",
    )
    _expect(
        report["source_count_succeeded"] + report["source_count_failed"]
        == report["source_count_requested"],
        "crawl-report source counts do not reconcile",
    )
    _expect(
        report["source_count_failed"] == len(failures),
        "crawl-report failed-source count differs from its failure ledger",
    )
    failure_ids: list[str] = []
    for index, failure in enumerate(failures):
        field = f"crawl-report.failures[{index}]"
        _expect(isinstance(failure, dict), f"{field} must be an object")
        source_id = _text(failure.get("source_id"), f"{field}.source_id", 100)
        _expect(source_id in expected_sources, f"{field} names an unknown source")
        _expect(
            failure.get("state_code") == expected_sources[source_id],
            f"{field}.state_code differs from the registry",
        )
        _text(failure.get("error"), f"{field}.error", 2_000)
        failure_ids.append(source_id)
    _expect(
        len(failure_ids) == len(set(failure_ids)),
        "crawl-report failure ledger contains duplicate source IDs",
    )
    _expect(
        report["source_count_failed"] == 0 and failures == [],
        "production GePNIC build requires zero crawler failures",
    )
    _expect(
        report["source_count_succeeded"] == len(snapshots),
        "crawl-report succeeded-source count differs from source receipts",
    )
    successful_ids = {snapshot.receipt.source_id for snapshot in snapshots}
    _expect(
        successful_ids | set(failure_ids) == set(expected_sources)
        and not (successful_ids & set(failure_ids)),
        "crawl-report success/failure source accounting differs from the registry",
    )
    _expect(
        report["states"] == state_summaries,
        "crawl-report state summaries differ from the validated source receipts",
    )
    return report_bytes


def _state_summaries(snapshots: Iterable[SourceSnapshot]) -> dict[str, dict[str, int]]:
    summaries: dict[str, dict[str, int]] = {}
    for snapshot in snapshots:
        receipt = snapshot.receipt
        summary = summaries.setdefault(
            receipt.state_code,
            {"sources": 0, "rows_scanned": 0, "rows_excluded_by_scope": 0, "notices": 0},
        )
        summary["sources"] += 1
        summary["rows_scanned"] += receipt.rows_scanned
        summary["rows_excluded_by_scope"] += receipt.rows_excluded_by_scope
        # The crawler-owned report records the scope policy used when the immutable
        # source snapshot was acquired. Runtime builds may apply a newer, stricter
        # classifier without rewriting that acquisition receipt.
        summary["notices"] += receipt.rows_scanned - receipt.rows_excluded_by_scope
    return summaries


def _runtime_receipt(snapshot: SourceSnapshot) -> SourceReceipt:
    """Project a source receipt through today's stricter runtime scope gate."""
    receipt = snapshot.receipt
    return SourceReceipt(
        source_id=receipt.source_id,
        source_name=receipt.source_name,
        source_url=receipt.source_url,
        retrieved_at=receipt.retrieved_at,
        state_code=receipt.state_code,
        rows_scanned=receipt.rows_scanned,
        rows_excluded_by_scope=receipt.rows_scanned - len(snapshot.notices),
    )


def plan_build(
    project_root: Path = PROJECT_ROOT,
) -> tuple[dict[Path, bytes], bytes, bytes]:
    """Return expected packs, manifest and canonical all-source crawl report."""
    project_root = Path(project_root)
    snapshots, gepnic_snapshots, expected_sources = _load_sources(project_root)
    grouped: dict[str, list[SourceSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        grouped[snapshot.receipt.state_code].append(snapshot)

    resources: dict[str, dict[str, Any]] = {}
    packs: dict[Path, bytes] = {}
    all_dates = [
        _snapshot_date(snapshot.receipt.retrieved_at) for snapshot in snapshots
    ]
    manifest_date = max(all_dates)
    retrieved_timestamps = {
        snapshot.receipt.retrieved_at for snapshot in gepnic_snapshots
    }
    _expect(
        len(retrieved_timestamps) == 1,
        "GePNIC sources must share one retrieved_at for a canonical crawl snapshot",
    )
    canonical_retrieved_at = next(iter(retrieved_timestamps))
    state_summaries: dict[str, dict[str, int]] = {}

    for state_code in sorted(grouped):
        state_sources = sorted(
            grouped[state_code], key=lambda item: item.receipt.source_id
        )
        receipts = [_runtime_receipt(snapshot) for snapshot in state_sources]
        notices = sorted(
            (notice for snapshot in state_sources for notice in snapshot.notices),
            key=lambda notice: (notice["tender_id"], notice["source_id"]),
        )
        _expect(
            len(notices) <= MAX_RECORDS_PER_PACK,
            f"{state_code} exceeds {MAX_RECORDS_PER_PACK} road notices",
        )
        generated_at = max(_snapshot_date(receipt.retrieved_at) for receipt in receipts)
        pack_id = f"in-road-notices-{state_code.lower()}"
        pack_bytes = _compact_json(
            _pack_envelope(state_code, generated_at, receipts, notices)
        )
        _expect(
            0 < len(pack_bytes) <= MAX_PACK_BYTES,
            f"{pack_id} exceeds the {MAX_PACK_BYTES}-byte runtime limit",
        )
        digest = hashlib.sha256(pack_bytes).hexdigest()
        relative_path = PACK_ROOT / state_code.lower() / f"notices-{digest}.json"
        public_path = relative_path.relative_to("docs").as_posix()
        packs[relative_path] = pack_bytes
        scanned = sum(receipt.rows_scanned for receipt in receipts)
        excluded = sum(receipt.rows_excluded_by_scope for receipt in receipts)
        state_summaries[state_code] = {
            "sources": len(receipts),
            "rows_scanned": scanned,
            "rows_excluded_by_scope": excluded,
            "notices": len(notices),
        }
        resources[pack_id] = {
            "pack_id": pack_id,
            "state_code": state_code,
            "kind": "road_procurement_notices",
            "pack_version": PACK_VERSION,
            "schema_version": SCHEMA_VERSION,
            "adapter": ADAPTER,
            "path": public_path,
            "url": PUBLIC_BASE_URL + public_path,
            "bytes": len(pack_bytes),
            "sha256": digest,
            "records": len(notices),
            "sources": len(receipts),
            "rows_scanned": scanned,
            "rows_excluded_by_scope": excluded,
            "lifecycle": LIFECYCLE,
            "candidate_only": True,
            "source_retrieved_at": generated_at,
            "review_after": (
                date.fromisoformat(generated_at) + timedelta(days=REVIEW_DAYS)
            ).isoformat(),
            "licenses": list(LICENSES),
        }

    manifest = {
        "format": MANIFEST_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "catalog_version": CATALOG_VERSION,
        "generated_at": manifest_date,
        "cache": dict(CACHE_POLICY),
        "inference_policy": dict(INFERENCE_POLICY),
        "resources": resources,
    }
    report_bytes = _validated_crawl_report(
        project_root,
        expected_sources,
        gepnic_snapshots,
        canonical_retrieved_at,
        _state_summaries(gepnic_snapshots),
    )
    return packs, _manifest_json(manifest), report_bytes


def build_all(project_root: Path = PROJECT_ROOT) -> list[Path]:
    """Write immutable packs and three identical manifest mirrors."""
    project_root = Path(project_root)
    packs, manifest_bytes, _report_bytes = plan_build(project_root)
    outputs: list[Path] = []
    for relative_path, content in sorted(packs.items(), key=lambda item: str(item[0])):
        output = project_root / relative_path
        if output.exists() and output.read_bytes() != content:
            raise BuildError(f"content-address collision: {relative_path}")
        _write_if_changed(output, content)
        outputs.append(output)
    for relative_path in MANIFEST_PATHS:
        output = project_root / relative_path
        _write_if_changed(output, manifest_bytes)
        outputs.append(output)
    verify_all(project_root)
    return outputs


def verify_all(project_root: Path = PROJECT_ROOT) -> None:
    """Verify expected artifacts without creating, repairing or deleting files."""
    project_root = Path(project_root)
    packs, manifest_bytes, _report_bytes = plan_build(project_root)
    for relative_path, expected in packs.items():
        path = project_root / relative_path
        try:
            actual = path.read_bytes()
        except FileNotFoundError as error:
            raise BuildError(f"missing generated road-notice pack: {relative_path}") from error
        _expect(actual == expected, f"generated road-notice pack differs: {relative_path}")
        _expect(
            SHA256_RE.fullmatch(relative_path.stem.removeprefix("notices-")) is not None,
            f"road-notice pack path is not content-addressed: {relative_path}",
        )
    for relative_path in MANIFEST_PATHS:
        path = project_root / relative_path
        try:
            actual = path.read_bytes()
        except FileNotFoundError as error:
            raise BuildError(f"missing road-notice manifest mirror: {relative_path}") from error
        _expect(actual == manifest_bytes, f"road-notice manifest differs: {relative_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build immutable official State/UT road-notice packs and manifests."
    )
    parser.add_argument(
        "--check", action="store_true", help="verify only; never mutate the checkout"
    )
    args = parser.parse_args()
    try:
        if args.check:
            verify_all()
            print("Official road-notice packs OK")
        else:
            for output in build_all():
                print(output.relative_to(PROJECT_ROOT))
            print("Official road-notice packs and manifest mirrors updated")
    except BuildError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
