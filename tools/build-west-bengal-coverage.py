#!/usr/bin/env python3
"""Build the reviewed West Bengal routing source with exact KMC and state bounds."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "static" / "pack-manifest-v1.34.json"
OUTPUT = ROOT / "data" / "metro-coverage" / "wb.json"
RELATION_ID = 1_960_177
RETRIEVED_AT = "2026-08-24"
EXPECTED_GEOMETRY_SHA256 = (
    "aa4ab13c3064be2e168889f6eb02e87c59e01bc709d36b66bece534dfea23015"
)
LOOKUP_URL = (
    "https://nominatim.openstreetmap.org/lookup?osm_ids=R1960177&format=jsonv2"
    "&polygon_geojson=1&polygon_threshold=0.00001"
)


class BuildError(RuntimeError):
    """Raised when a source cannot safely produce the statewide coverage file."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read {path}: {exc}") from exc


def active_kmc_region() -> dict[str, Any]:
    manifest = read_json(MANIFEST)
    resource = manifest.get("resources", {}).get("in-wb-routing", {})
    relative_path = resource.get("path")
    if not isinstance(relative_path, str):
        raise BuildError("the active West Bengal pack is missing from the manifest")
    envelope = read_json(ROOT / "docs" / relative_path)
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    if not isinstance(payload, dict):
        raise BuildError("the active West Bengal pack has no payload")
    if payload.get("version") == 1 and isinstance(payload.get("region"), dict):
        region = copy.deepcopy(payload["region"])
        region["retrieved_at"] = payload.get("retrieved_at")
        return region
    regions = payload.get("regions")
    if payload.get("version") == 2 and isinstance(regions, dict) \
            and isinstance(regions.get("kmc"), dict):
        return copy.deepcopy(regions["kmc"])
    raise BuildError("the active West Bengal pack has no reviewed KMC region")


def download_lookup() -> Any:
    request = urllib.request.Request(
        LOOKUP_URL,
        headers={"User-Agent": "PotholeReporter/1.24 contact@aiengg.dev"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot download the West Bengal boundary: {exc}") from exc


def geometry_digest(geometry: dict[str, Any]) -> str:
    compact = json.dumps(
        geometry, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(compact).hexdigest()


def validated_feature(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise BuildError("Nominatim did not return exactly one West Bengal feature")
    feature = raw[0]
    if (
        feature.get("osm_type") != "relation"
        or feature.get("osm_id") != RELATION_ID
        or feature.get("category") != "boundary"
        or feature.get("type") != "administrative"
        or feature.get("display_name") != "West Bengal, India"
        or "OpenStreetMap" not in str(feature.get("licence", ""))
    ):
        raise BuildError("the downloaded feature is not the pinned West Bengal relation")
    geometry = feature.get("geojson")
    if (
        not isinstance(geometry, dict)
        or set(geometry) != {"type", "coordinates"}
        or geometry.get("type") != "MultiPolygon"
        or not isinstance(geometry.get("coordinates"), list)
        or not geometry["coordinates"]
    ):
        raise BuildError("the West Bengal relation has no usable MultiPolygon")
    digest = geometry_digest(geometry)
    if digest != EXPECTED_GEOMETRY_SHA256:
        raise BuildError(
            "the West Bengal geometry changed since review; inspect and update the pin"
        )
    bbox = feature.get("boundingbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise BuildError("the West Bengal relation has no valid bounding box")
    try:
        south, north, west, east = (float(value) for value in bbox)
    except (TypeError, ValueError) as exc:
        raise BuildError("the West Bengal bounding box is not numeric") from exc
    expected = (
        21.4 < south < 21.7,
        27.0 < north < 27.4,
        85.6 < west < 86.0,
        89.7 < east < 90.1,
    )
    if not all(expected):
        raise BuildError("the West Bengal bounding box differs materially from the review")
    return {
        "geometry": geometry,
        "bbox": {
            "min_lng": west,
            "min_lat": south,
            "max_lng": east,
            "max_lat": north,
        },
    }


def build(raw: Any) -> dict[str, Any]:
    source = validated_feature(raw)
    geometry = source["geometry"]
    return {
        "version": 2,
        "retrieved_at": RETRIEVED_AT,
        "regions": {
            "west_bengal": {
                "name": "West Bengal",
                "scope": "Full State of West Bengal",
                "authority_id": "wb-statewide-unverified",
                "source": "https://www.openstreetmap.org/relation/1960177",
                "source_lookup": LOOKUP_URL,
                "source_relation_id": RELATION_ID,
                "retrieved_at": RETRIEVED_AT,
                "licence": "OpenStreetMap contributors, ODbL 1.0",
                "coordinate_precision": 7,
                "bbox": source["bbox"],
                "geometry_sha256": geometry_digest(geometry),
                "routing_note": (
                    "State containment enables a neutral West Bengal grievance handoff "
                    "after the exact KMC check; it does not identify a road owner or ULB."
                ),
                "limitations": [
                    "The OpenStreetMap boundary is a routing aid, not a legal boundary record.",
                    "The state grievance portal requires the user to select and verify the responsible department.",
                    "Municipal, PWD, panchayat, NHAI and other ownership is not inferred.",
                ],
                "geometry": geometry,
            },
            "kmc": active_kmc_region(),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        help="saved Nominatim JSON response; omit to download the pinned relation",
    )
    args = parser.parse_args()
    raw = read_json(args.input) if args.input else download_lookup()
    payload = build(raw)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    OUTPUT.write_bytes(encoded)
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(encoded)} bytes)")
    print(payload["regions"]["west_bengal"]["geometry_sha256"])


if __name__ == "__main__":
    try:
        main()
    except BuildError as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
