#!/usr/bin/env python3
"""Build and verify the client-downloadable, content-addressed state packs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"
STATIC_MANIFEST = PROJECT_ROOT / "static" / "pack-manifest.json"
ANDROID_MANIFEST = PROJECT_ROOT / "android-app" / "www" / "pack-manifest.json"
AUTHORITIES_SOURCE = PROJECT_ROOT / "data" / "state-authorities.json"
PUBLIC_BASE_URL = "https://coding-parrot.github.io/pothole-reporter/"
PACK_FORMAT = "pothole-pack-manifest"
PACK_SCHEMA_VERSION = 1
CATALOG_VERSION = 1
PACK_VERSION = 1
REVIEW_AFTER = "2026-11-21"
MAX_PACK_BYTES = 16 * 1024 * 1024
REQUIRED_RESOURCE_IDS = {
    "in-dl-routing",
    "in-ka-routing",
    "in-ka-tenders",
    "in-mh-routing",
    "in-wb-routing",
}
MANIFEST_KEYS = {"format", "schema_version", "catalog_version", "cache", "resources"}
CACHE_POLICY = {
    "max_bytes": 67_108_864,
    "routing_max_unused_days": 90,
    "tender_max_unused_days": 30,
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class ResourceSpec:
    pack_id: str
    state_code: str
    kind: str
    adapter: str
    coverage_scope: str
    statewide: bool
    licenses: tuple[str, ...]
    source_path: str


SPECS = {
    "in-dl-routing": ResourceSpec(
        "in-dl-routing",
        "DL",
        "routing",
        "delhi-nct-v1",
        "Delhi NCT",
        True,
        ("OpenStreetMap data: ODbL 1.0",),
        "static/delhi-coverage.json",
    ),
    "in-mh-routing": ResourceSpec(
        "in-mh-routing",
        "MH",
        "routing",
        "maharashtra-mmr-pmc-v1",
        "Mumbai Metropolitan Region and Pune Municipal Corporation",
        False,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Maharashtra GIS and public-authority sources: respective source terms",
        ),
        "static/maharashtra-coverage.json",
    ),
    "in-wb-routing": ResourceSpec(
        "in-wb-routing",
        "WB",
        "routing",
        "kolkata-kmc-v1",
        "Kolkata Municipal Corporation",
        False,
        ("Official West Bengal UDMA and KMC sources: respective source terms",),
        "static/kolkata-coverage.json",
    ),
    "in-ka-routing": ResourceSpec(
        "in-ka-routing",
        "KA",
        "routing",
        "karnataka-kgis-v1",
        "Karnataka urban local bodies",
        True,
        ("Official Karnataka public-body records: respective source terms",),
        "data/karnataka-bodies.json",
    ),
    "in-ka-tenders": ResourceSpec(
        "in-ka-tenders",
        "KA",
        "tenders",
        "karnataka-locally-indexed-v1",
        "Karnataka municipal procurement records",
        True,
        ("Official Karnataka procurement records: respective source terms",),
        "data/tenders-karnataka.json",
    ),
}


class PackError(RuntimeError):
    """Raised when a pack or manifest violates the release contract."""


def _compact_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _manifest_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackError(f"missing required file: {path.relative_to(PROJECT_ROOT)}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackError(f"invalid UTF-8 JSON: {path.relative_to(PROJECT_ROOT)}: {exc}") from exc


def _write_if_changed(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == content:
        return
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
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


def _is_date(value: Any) -> bool:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _authority_snapshot(state_code: str) -> list[dict[str, Any]]:
    if state_code == "KA":
        return []
    source = _read_json(AUTHORITIES_SOURCE)
    state = source.get(state_code) if isinstance(source, dict) else None
    if not isinstance(state, dict) or not isinstance(state.get("authorities"), list):
        raise PackError(f"state-authorities.json has no authority list for {state_code}")
    authorities = list(state["authorities"])
    if state_code == "MH":
        for key in ("pmc", "fallback"):
            if not isinstance(state.get(key), dict):
                raise PackError(f"state-authorities.json has no MH {key} authority")
            authorities.append(state[key])
    identifiers = [entry.get("id") for entry in authorities if isinstance(entry, dict)]
    if len(identifiers) != len(authorities) or any(not value for value in identifiers):
        raise PackError(f"{state_code} authority snapshot contains an invalid entry")
    if len(identifiers) != len(set(identifiers)):
        raise PackError(f"{state_code} authority snapshot contains duplicate ids")
    return authorities


def _source_date(payload: Any, fallback: str | None = None) -> str:
    if isinstance(payload, dict):
        candidate = payload.get("retrieved_at") or payload.get("generated_at") or payload.get("generated")
        if _is_date(candidate):
            return candidate
    if _is_date(fallback):
        return fallback
    raise PackError("source payload has no deterministic YYYY-MM-DD retrieval/generated date")


def _validate_raw_payload(spec: ResourceSpec, payload: Any) -> None:
    if spec.kind == "tenders":
        if not isinstance(payload, list) or not payload or len(payload) > 100_000:
            raise PackError(f"{spec.pack_id} must contain a non-empty tender list")
        fields = {"tn", "t", "loc", "c", "d", "b"}
        seen: set[tuple[str, str]] = set()
        for index, row in enumerate(payload):
            if not isinstance(row, dict) or set(row) != fields:
                raise PackError(f"{spec.pack_id} tender {index} has unexpected fields")
            if (
                not isinstance(row["tn"], str) or not row["tn"] or len(row["tn"]) > 100
                or not isinstance(row["t"], str) or not row["t"] or len(row["t"]) > 500
                or not isinstance(row["loc"], str) or len(row["loc"]) > 200
                or not isinstance(row["c"], str) or len(row["c"]) > 200
                or not isinstance(row["d"], str) or re.fullmatch(r"\d{2}-\d{2}-\d{4}", row["d"]) is None
                or not isinstance(row["b"], str) or re.fullmatch(r"(?:BLR|\d{3,12})", row["b"]) is None
            ):
                raise PackError(f"{spec.pack_id} tender {index} is invalid")
            identity = (row["tn"], row["b"])
            if identity in seen:
                raise PackError(f"{spec.pack_id} contains duplicate tender/body record {identity!r}")
            seen.add(identity)
        return
    if not isinstance(payload, dict):
        raise PackError(f"{spec.pack_id} routing payload must be an object")
    if spec.pack_id == "in-ka-routing":
        if not isinstance(payload.get("bodies"), dict) or not payload["bodies"]:
            raise PackError("in-ka-routing payload has no bodies")
    elif spec.pack_id == "in-mh-routing":
        if not isinstance(payload.get("regions"), dict) or not payload["regions"]:
            raise PackError("in-mh-routing payload has no regions")
    elif not isinstance(payload.get("region"), dict):
        raise PackError(f"{spec.pack_id} payload has no region")


def _pack_envelope(spec: ResourceSpec, payload: Any, generated_at: str) -> dict[str, Any]:
    _validate_raw_payload(spec, payload)
    common = {
        "format": "pothole-routing-pack" if spec.kind == "routing" else "pothole-tender-pack",
        "schema_version": PACK_SCHEMA_VERSION,
        "pack_id": spec.pack_id,
        "pack_version": PACK_VERSION,
        "state_code": spec.state_code,
        "adapter": spec.adapter,
        "generated_at": generated_at,
    }
    if spec.kind == "routing":
        common["authorities"] = _authority_snapshot(spec.state_code)
        common["payload"] = payload
    else:
        common["tenders"] = payload
    return common


def _base_manifest() -> dict[str, Any]:
    return {
        "format": PACK_FORMAT,
        "schema_version": PACK_SCHEMA_VERSION,
        "catalog_version": CATALOG_VERSION,
        "cache": dict(CACHE_POLICY),
        "resources": {},
    }


def _manifest_for_update() -> dict[str, Any]:
    if not STATIC_MANIFEST.exists():
        raise PackError("no pack manifest exists; run tools/build-state-packs.py first")
    manifest = _read_json(STATIC_MANIFEST)
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise PackError("existing pack manifest top-level fields differ from the contract")
    if (
        manifest.get("format") != PACK_FORMAT
        or manifest.get("schema_version") != PACK_SCHEMA_VERSION
        or manifest.get("catalog_version") != CATALOG_VERSION
        or manifest.get("cache") != CACHE_POLICY
    ):
        raise PackError("existing pack manifest metadata differs from the contract")
    resources = manifest.get("resources")
    if not isinstance(resources, dict) or set(resources) != REQUIRED_RESOURCE_IDS:
        raise PackError("existing pack manifest must contain all five resource ids")
    return manifest


def _resource_entry(spec: ResourceSpec, pack_bytes: bytes, source_date: str) -> tuple[dict[str, Any], Path]:
    if not pack_bytes or len(pack_bytes) > MAX_PACK_BYTES:
        raise PackError(f"{spec.pack_id} exceeds the {MAX_PACK_BYTES}-byte runtime limit")
    digest = hashlib.sha256(pack_bytes).hexdigest()
    path = f"packs/v1/states/{spec.state_code.lower()}/{spec.kind}-{digest}.json"
    entry: dict[str, Any] = {
        "pack_id": spec.pack_id,
        "state_code": spec.state_code,
        "kind": spec.kind,
        "pack_version": PACK_VERSION,
        "schema_version": PACK_SCHEMA_VERSION,
        "adapter": spec.adapter,
        "path": path,
        "url": PUBLIC_BASE_URL + path,
        "bytes": len(pack_bytes),
        "sha256": digest,
        "coverage_scope": spec.coverage_scope,
        "statewide": spec.statewide,
        "source_retrieved_at": source_date,
        "review_after": REVIEW_AFTER,
        "licenses": list(spec.licenses),
    }
    if spec.kind == "tenders":
        entry["records"] = len(json.loads(pack_bytes)["tenders"])
    return entry, DOCS_ROOT / path


def publish_resource(
    resource_id: str,
    payload: Any,
    *,
    source_retrieved_at: str | None = None,
    manifest: dict[str, Any] | None = None,
    write_manifest: bool = True,
) -> tuple[dict[str, Any], Path]:
    """Publish one immutable pack and update both bundled manifest mirrors."""
    if resource_id not in SPECS:
        raise PackError(f"unknown resource id: {resource_id}")
    spec = SPECS[resource_id]
    source_date = _source_date(payload, source_retrieved_at)
    pack_bytes = _compact_json(_pack_envelope(spec, payload, source_date))
    entry, output = _resource_entry(spec, pack_bytes, source_date)
    if output.exists() and output.read_bytes() != pack_bytes:
        raise PackError(f"content-address collision: {output.relative_to(PROJECT_ROOT)}")
    _write_if_changed(output, pack_bytes)

    if manifest is None:
        manifest = _manifest_for_update()
    resources = manifest.get("resources") if isinstance(manifest, dict) else None
    if not isinstance(resources, dict):
        raise PackError("pack manifest resources must be an object")
    resources[resource_id] = entry
    if write_manifest:
        serialized = _manifest_json(manifest)
        _write_if_changed(STATIC_MANIFEST, serialized)
        _write_if_changed(ANDROID_MANIFEST, serialized)
        verify_all()
    return manifest, output


def _active_payload(previous_manifest: Any, resource_id: str) -> Any:
    """Recover a routing payload from the active envelope after legacy inputs are removed."""
    resources = previous_manifest.get("resources") if isinstance(previous_manifest, dict) else None
    resource = resources.get(resource_id) if isinstance(resources, dict) else None
    relative_path = resource.get("path") if isinstance(resource, dict) else None
    if not isinstance(relative_path, str):
        raise PackError(f"no canonical source or active pack is available for {resource_id}")
    spec = SPECS[resource_id]
    expected = re.compile(
        rf"^packs/v1/states/{spec.state_code.lower()}/{spec.kind}-[0-9a-f]{{64}}\.json$"
    )
    if expected.fullmatch(relative_path) is None:
        raise PackError(f"active pack path is invalid for {resource_id}")
    envelope = _read_json(DOCS_ROOT / relative_path)
    if not isinstance(envelope, dict) or "payload" not in envelope:
        raise PackError(f"active pack for {resource_id} has no routing payload")
    return envelope["payload"]


def build_all() -> list[Path]:
    """Build all five packs from the reviewed canonical source snapshots."""
    previous_manifest = _read_json(STATIC_MANIFEST) if STATIC_MANIFEST.exists() else None
    manifest = _base_manifest()
    outputs: list[Path] = []
    for resource_id in sorted(SPECS):
        spec = SPECS[resource_id]
        source = PROJECT_ROOT / spec.source_path
        payload = _read_json(source) if source.exists() else _active_payload(previous_manifest, resource_id)
        previous_resources = previous_manifest.get("resources") if isinstance(previous_manifest, dict) else None
        previous_resource = (
            previous_resources.get(resource_id, {}) if isinstance(previous_resources, dict) else {}
        )
        fallback = (
            previous_resource.get("source_retrieved_at", "2026-08-21")
            if spec.kind == "tenders"
            else None
        )
        if spec.kind == "tenders":
            payload = [row for row in payload if isinstance(row, dict) and row.get("b")]
        manifest, output = publish_resource(
            resource_id,
            payload,
            source_retrieved_at=fallback,
            manifest=manifest,
            write_manifest=False,
        )
        outputs.append(output)
    serialized = _manifest_json(manifest)
    _write_if_changed(STATIC_MANIFEST, serialized)
    _write_if_changed(ANDROID_MANIFEST, serialized)
    verify_all()
    return outputs


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise PackError(message)


def _validate_resource(resource_id: str, resource: Any) -> None:
    spec = SPECS[resource_id]
    required = {
        "pack_id", "state_code", "kind", "pack_version", "schema_version", "adapter",
        "path", "url", "bytes", "sha256", "coverage_scope", "statewide",
        "source_retrieved_at", "review_after", "licenses",
    }
    if spec.kind == "tenders":
        required.add("records")
    _expect(isinstance(resource, dict), f"{resource_id}: manifest resource is not an object")
    _expect(set(resource) == required, f"{resource_id}: manifest fields differ from the contract")
    expected_scalars = {
        "pack_id": spec.pack_id,
        "state_code": spec.state_code,
        "kind": spec.kind,
        "pack_version": PACK_VERSION,
        "schema_version": PACK_SCHEMA_VERSION,
        "adapter": spec.adapter,
        "coverage_scope": spec.coverage_scope,
        "statewide": spec.statewide,
        "licenses": list(spec.licenses),
    }
    for key, expected in expected_scalars.items():
        _expect(resource.get(key) == expected, f"{resource_id}: unexpected {key}")
    digest = resource.get("sha256")
    _expect(isinstance(digest, str) and SHA256_RE.fullmatch(digest) is not None,
            f"{resource_id}: sha256 must be 64 lowercase hex characters")
    expected_path = f"packs/v1/states/{spec.state_code.lower()}/{spec.kind}-{digest}.json"
    _expect(resource.get("path") == expected_path, f"{resource_id}: path is not content-addressed")
    _expect(resource.get("url") == PUBLIC_BASE_URL + expected_path,
            f"{resource_id}: URL is not the exact production GitHub Pages URL")
    _expect(type(resource.get("bytes")) is int and resource["bytes"] > 0,
            f"{resource_id}: bytes must be a positive integer")
    _expect(resource["bytes"] <= MAX_PACK_BYTES, f"{resource_id}: pack exceeds the runtime size limit")
    source_date = resource.get("source_retrieved_at")
    _expect(_is_date(source_date), f"{resource_id}: invalid source_retrieved_at")
    _expect(resource.get("review_after") == REVIEW_AFTER,
            f"{resource_id}: unexpected review_after")

    pack_path = DOCS_ROOT / expected_path
    try:
        resolved = pack_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PackError(f"{resource_id}: hosted pack is missing: {expected_path}") from exc
    states_root = (DOCS_ROOT / "packs" / "v1" / "states").resolve()
    _expect(states_root in resolved.parents, f"{resource_id}: hosted path escapes the pack directory")
    pack_bytes = pack_path.read_bytes()
    _expect(resource.get("bytes") == len(pack_bytes), f"{resource_id}: byte length does not match")
    _expect(hashlib.sha256(pack_bytes).hexdigest() == digest, f"{resource_id}: SHA-256 does not match")
    try:
        envelope = json.loads(pack_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackError(f"{resource_id}: hosted pack is not valid UTF-8 JSON: {exc}") from exc
    expected_envelope_keys = {
        "format", "schema_version", "pack_id", "pack_version", "state_code", "adapter", "generated_at",
        "authorities", "payload",
    } if spec.kind == "routing" else {
        "format", "schema_version", "pack_id", "pack_version", "state_code", "adapter", "generated_at",
        "tenders",
    }
    _expect(isinstance(envelope, dict) and set(envelope) == expected_envelope_keys,
            f"{resource_id}: pack envelope fields differ from the contract")
    expected_format = "pothole-routing-pack" if spec.kind == "routing" else "pothole-tender-pack"
    for key, expected in {
        "format": expected_format,
        "schema_version": PACK_SCHEMA_VERSION,
        "pack_id": spec.pack_id,
        "pack_version": PACK_VERSION,
        "state_code": spec.state_code,
        "adapter": spec.adapter,
        "generated_at": source_date,
    }.items():
        _expect(envelope.get(key) == expected, f"{resource_id}: pack has unexpected {key}")
    if spec.kind == "routing":
        _expect(envelope.get("authorities") == _authority_snapshot(spec.state_code),
                f"{resource_id}: authority snapshot differs from data/state-authorities.json")
        _validate_raw_payload(spec, envelope.get("payload"))
    else:
        tenders = envelope.get("tenders")
        _validate_raw_payload(spec, tenders)
        _expect(type(resource.get("records")) is int and resource["records"] > 0,
                f"{resource_id}: records must be a positive integer")
        _expect(resource.get("records") == len(tenders), f"{resource_id}: records count does not match")


def verify_all() -> None:
    """Fail unless the manifests and all referenced hosted packs match exactly."""
    manifest_bytes = STATIC_MANIFEST.read_bytes() if STATIC_MANIFEST.exists() else b""
    if not manifest_bytes:
        raise PackError("missing bundled manifest: static/pack-manifest.json")
    _expect(ANDROID_MANIFEST.exists(), "missing Android manifest mirror: android-app/www/pack-manifest.json")
    _expect(ANDROID_MANIFEST.read_bytes() == manifest_bytes, "static and Android pack manifests differ")
    manifest = _read_json(STATIC_MANIFEST)
    _expect(isinstance(manifest, dict) and set(manifest) == MANIFEST_KEYS,
            "pack manifest top-level fields differ from the contract")
    _expect(manifest.get("format") == PACK_FORMAT, "unexpected pack manifest format")
    _expect(manifest.get("schema_version") == PACK_SCHEMA_VERSION, "unexpected manifest schema_version")
    _expect(manifest.get("catalog_version") == CATALOG_VERSION, "unexpected manifest catalog_version")
    _expect(manifest.get("cache") == CACHE_POLICY, "unexpected manifest cache policy")
    resources = manifest.get("resources")
    _expect(isinstance(resources, dict) and set(resources) == REQUIRED_RESOURCE_IDS,
            "manifest must contain exactly the five reviewed resource ids")
    for resource_id in sorted(REQUIRED_RESOURCE_IDS):
        _validate_resource(resource_id, resources[resource_id])
