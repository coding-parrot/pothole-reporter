#!/usr/bin/env python3
"""Build the reviewed Maharashtra routing source with a statewide OSM boundary."""

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
OUTPUT = ROOT / "data" / "metro-coverage" / "mh.json"
RELATION_ID = 1_950_884
RETRIEVED_AT = "2026-08-23"
LOOKUP_URL = (
    "https://nominatim.openstreetmap.org/lookup?osm_ids=R1950884&format=jsonv2"
    "&polygon_geojson=1&polygon_threshold=0.00001"
)


class BuildError(RuntimeError):
    """Raised when a source cannot safely produce the statewide coverage file."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read {path}: {exc}") from exc


def active_payload() -> dict[str, Any]:
    manifest = read_json(MANIFEST)
    resource = manifest.get("resources", {}).get("in-mh-routing", {})
    relative_path = resource.get("path")
    if not isinstance(relative_path, str):
        raise BuildError("the active Maharashtra pack is missing from the manifest")
    envelope = read_json(ROOT / "docs" / relative_path)
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    if not isinstance(payload, dict):
        raise BuildError("the active Maharashtra pack has no payload")
    return payload


def download_lookup() -> Any:
    request = urllib.request.Request(
        LOOKUP_URL,
        headers={"User-Agent": "PotholeReporter/1.23 contact@aiengg.dev"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot download the Maharashtra boundary: {exc}") from exc


def geometry_digest(geometry: dict[str, Any]) -> str:
    compact = json.dumps(
        geometry, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(compact).hexdigest()


def validated_feature(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise BuildError("Nominatim did not return exactly one Maharashtra feature")
    feature = raw[0]
    if (
        feature.get("osm_type") != "relation"
        or feature.get("osm_id") != RELATION_ID
        or feature.get("category") != "boundary"
        or feature.get("type") != "administrative"
        or feature.get("display_name") != "Maharashtra, India"
        or "OpenStreetMap" not in str(feature.get("licence", ""))
    ):
        raise BuildError("the downloaded feature is not the pinned Maharashtra relation")
    geometry = feature.get("geojson")
    if (
        not isinstance(geometry, dict)
        or set(geometry) != {"type", "coordinates"}
        or geometry.get("type") != "MultiPolygon"
        or not isinstance(geometry.get("coordinates"), list)
        or not geometry["coordinates"]
    ):
        raise BuildError("the Maharashtra relation has no usable MultiPolygon")
    bbox = feature.get("boundingbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise BuildError("the Maharashtra relation has no valid bounding box")
    try:
        south, north, west, east = (float(value) for value in bbox)
    except (TypeError, ValueError) as exc:
        raise BuildError("the Maharashtra bounding box is not numeric") from exc
    expected = (15.5 < south < 15.8, 21.9 < north < 22.2,
                72.5 < west < 72.8, 80.7 < east < 81.1)
    if not all(expected):
        raise BuildError("the Maharashtra bounding box differs materially from the review")
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
    payload = copy.deepcopy(active_payload())
    regions = payload.get("regions")
    if (
        payload.get("version") not in {1, 2}
        or not isinstance(regions, dict)
        or not isinstance(regions.get("mmr"), dict)
        or not isinstance(regions.get("pmc"), dict)
    ):
        raise BuildError("the active Maharashtra payload is not the reviewed MMR/PMC source")
    geometry = source["geometry"]
    payload["version"] = 2
    payload["retrieved_at"] = RETRIEVED_AT
    regions["maharashtra"] = {
        "name": "Maharashtra",
        "scope": "Full State of Maharashtra",
        "authority_id": "mh-statewide-unverified",
        "source": "https://www.openstreetmap.org/relation/1950884",
        "source_lookup": LOOKUP_URL,
        "source_relation_id": RELATION_ID,
        "retrieved_at": RETRIEVED_AT,
        "licence": "OpenStreetMap contributors, ODbL 1.0",
        "coordinate_precision": 7,
        "bbox": source["bbox"],
        "geometry_sha256": geometry_digest(geometry),
        "routing_note": (
            "State containment enables the neutral Aaple Sarkar handoff after the "
            "more specific MMR and PMC checks; it does not identify a road owner."
        ),
        "limitations": [
            "The OpenStreetMap boundary is a routing aid, not a legal boundary record.",
            "Aaple Sarkar requires the user to select and verify the responsible department.",
            "Municipal, PWD, panchayat, NHAI, MSRDC and other ownership is not inferred.",
        ],
        "geometry": geometry,
    }
    return payload


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
    print(payload["regions"]["maharashtra"]["geometry_sha256"])


if __name__ == "__main__":
    try:
        main()
    except BuildError as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
