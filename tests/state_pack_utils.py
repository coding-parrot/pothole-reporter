"""Shared assertions for the bundled state-pack catalog and hosted pack files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "android-app" / "www" / "pack-manifest-v1.29.json"
PACK_ROOT = ROOT / "docs" / "packs" / "v1"


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def resource_for(pack_id: str) -> dict:
    resources = load_manifest().get("resources")
    if not isinstance(resources, dict) or pack_id not in resources:
        raise AssertionError(f"pack manifest has no {pack_id!r} resource")
    resource = resources[pack_id]
    if not isinstance(resource, dict):
        raise AssertionError(f"pack manifest resource {pack_id!r} is not an object")
    return resource


def resource_relative_path(pack_id: str) -> str:
    resource = resource_for(pack_id)
    raw_path = resource.get("path")
    if not raw_path:
        raw_path = urlsplit(str(resource.get("url") or "")).path
    raw_path = str(raw_path).replace("\\", "/")
    marker = "packs/v1/"
    offset = raw_path.find(marker)
    if offset >= 0:
        relative = raw_path[offset + len(marker) :].lstrip("/")
    else:
        # The bundled catalog stores paths relative to the public /packs/ root.
        normalized = raw_path.lstrip("/")
        if not normalized.startswith("v1/"):
            raise AssertionError(f"{pack_id!r} has no path under packs/v1: {raw_path!r}")
        relative = normalized[len("v1/") :]
    if not relative or ".." in relative.split("/"):
        raise AssertionError(f"unsafe or empty pack path for {pack_id!r}: {raw_path!r}")
    return relative


def pack_path(pack_id: str) -> Path:
    path = (PACK_ROOT / resource_relative_path(pack_id)).resolve()
    path.relative_to(PACK_ROOT.resolve())
    return path


def route_pattern(pack_id: str) -> str:
    return f"**/packs/v1/{resource_relative_path(pack_id)}"


def read_pack(pack_id: str) -> tuple[dict, bytes]:
    raw = pack_path(pack_id).read_bytes()
    resource = resource_for(pack_id)
    expected_bytes = resource.get("bytes")
    if expected_bytes != len(raw):
        raise AssertionError(
            f"{pack_id!r} size mismatch: catalog {expected_bytes!r}, file {len(raw)}"
        )
    digest = hashlib.sha256(raw).hexdigest()
    if resource.get("sha256") != digest:
        raise AssertionError(
            f"{pack_id!r} digest mismatch: catalog {resource.get('sha256')!r}, file {digest}"
        )
    return json.loads(raw.decode("utf-8")), raw


def read_payload(pack_id: str) -> dict | list:
    envelope, _ = read_pack(pack_id)
    payload = envelope.get("payload")
    if not isinstance(payload, (dict, list)):
        raise AssertionError(f"{pack_id!r} pack has no object/array payload")
    return payload
