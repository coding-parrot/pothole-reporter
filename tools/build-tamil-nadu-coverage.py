#!/usr/bin/env python3
"""Build the pinned full-State-of-Tamil-Nadu routing boundary.

The OpenStreetMap geometry is only a containment aid for a neutral Tamil Nadu
CM Helpline handoff. It does not identify a municipality, road owner, or
complaint category. Source identity, geometry, boundary fixtures, and the
Puducherry exclusions are pinned so upstream changes fail closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "metro-coverage" / "tn-state.json"
RELATION_ID = 96_905
RETRIEVED_AT = "2026-08-24"
LOOKUP_URL = (
    "https://nominatim.openstreetmap.org/lookup?osm_ids=R96905&format=jsonv2"
    "&polygon_geojson=1&polygon_threshold=0.00001"
)
EXPECTED_GEOMETRY_SHA256 = (
    "b3034527326b1120366adaf4b7c3df4bd0b8c7aab4d82b28e3dde189b39c313e"
)
EXPECTED_BBOX = {
    "min_lng": 76.2329467,
    "min_lat": 8.0768938,
    "max_lng": 80.3592971,
    "max_lat": 13.5639111,
}
EXPECTED_SOURCE_BBOX = [8.0768938, 13.5639111, 76.2329467, 80.3592991]

# These points exercise the state extremities, islands, neighbouring states,
# and the two Puducherry enclaves surrounded by Tamil Nadu. They prove only
# boundary containment, never administrative or road ownership.
INSIDE_FIXTURES = {
    "chennai": (13.0827, 80.2707),
    "coimbatore": (11.0168, 76.9558),
    "madurai": (9.9252, 78.1198),
    "tiruchirappalli": (10.7905, 78.7047),
    "salem": (11.6643, 78.1460),
    "tirunelveli": (8.7139, 77.7567),
    "kanniyakumari": (8.0883, 77.5385),
    "ooty": (11.4064, 76.6932),
    "hosur": (12.7409, 77.8253),
    "rameswaram": (9.2876, 79.3129),
}
OUTSIDE_FIXTURES = {
    "puducherry_city": (11.9416, 79.8083),
    "ozhukarai": (11.9414045, 79.8064577),
    "kalapet": (12.0308440, 79.8648402),
    "ariyankuppam": (11.8948712, 79.8075867),
    "nettapakkam": (11.8646584, 79.6339075),
    "mannadipet": (11.9841410, 79.6229991),
    "karaikal": (10.9254, 79.8380),
    "mahe": (11.7010, 75.5360),
    "yanam": (16.7333, 82.2167),
    "bengaluru": (12.9716, 77.5946),
    "tirupati": (13.6288, 79.4192),
    "palakkad": (10.7867, 76.6548),
    "thiruvananthapuram": (8.5241, 76.9366),
}


class BuildError(RuntimeError):
    """Raised when the reviewed Tamil Nadu source contract is not satisfied."""


def compact_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read {path}: {exc}") from exc


def download_lookup() -> Any:
    request = urllib.request.Request(
        LOOKUP_URL,
        headers={
            "User-Agent": (
                "PotholeReporter-coverage-builder/1.0 "
                "(https://github.com/coding-parrot/pothole-reporter; "
                "contact@aiengg.dev)"
            ),
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot download the Tamil Nadu boundary: {exc}") from exc


def rounded_geometry(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"type", "coordinates"}:
        raise BuildError("the Tamil Nadu relation has an invalid GeoJSON envelope")
    if raw.get("type") not in {"Polygon", "MultiPolygon"}:
        raise BuildError("the Tamil Nadu relation is not a Polygon or MultiPolygon")

    def visit(value: Any) -> Any:
        if isinstance(value, list) and value and isinstance(value[0], (int, float)):
            if len(value) < 2 or not all(math.isfinite(float(item)) for item in value[:2]):
                raise BuildError("the Tamil Nadu geometry contains an invalid coordinate")
            return [round(float(value[0]), 7), round(float(value[1]), 7)]
        if isinstance(value, list):
            return [visit(child) for child in value]
        raise BuildError("the Tamil Nadu geometry has an invalid coordinate structure")

    geometry = {"type": raw["type"], "coordinates": visit(raw["coordinates"])}
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" \
        else [geometry["coordinates"]]
    if not polygons:
        raise BuildError("the Tamil Nadu geometry contains no polygons")
    for polygon in polygons:
        if not polygon:
            raise BuildError("the Tamil Nadu geometry contains an empty polygon")
        for ring in polygon:
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise BuildError("the Tamil Nadu geometry contains an open or undersized ring")
    return geometry


def positions(geometry: dict[str, Any]):
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" \
        else [geometry["coordinates"]]
    for polygon in polygons:
        for ring in polygon:
            yield from ring


def geometry_bbox(geometry: dict[str, Any]) -> dict[str, float]:
    points = list(positions(geometry))
    if not points:
        raise BuildError("the Tamil Nadu geometry contains no positions")
    return {
        "min_lng": min(point[0] for point in points),
        "min_lat": min(point[1] for point in points),
        "max_lng": max(point[0] for point in points),
        "max_lat": max(point[1] for point in points),
    }


def point_in_ring(lng: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    previous = len(ring) - 1
    for index, (current_lng, current_lat) in enumerate(ring):
        previous_lng, previous_lat = ring[previous]
        crosses = (current_lat > lat) != (previous_lat > lat)
        if crosses and lng < (
            (previous_lng - current_lng) * (lat - current_lat)
            / (previous_lat - current_lat) + current_lng
        ):
            inside = not inside
        previous = index
    return inside


def point_in_geometry(lng: float, lat: float, geometry: dict[str, Any]) -> bool:
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" \
        else [geometry["coordinates"]]
    return any(
        point_in_ring(lng, lat, polygon[0])
        and not any(point_in_ring(lng, lat, hole) for hole in polygon[1:])
        for polygon in polygons
    )


def validate_fixtures(geometry: dict[str, Any]) -> None:
    for name, (lat, lng) in INSIDE_FIXTURES.items():
        if not point_in_geometry(lng, lat, geometry):
            raise BuildError(f"reviewed inside fixture moved outside Tamil Nadu: {name}")
    for name, (lat, lng) in OUTSIDE_FIXTURES.items():
        if point_in_geometry(lng, lat, geometry):
            raise BuildError(f"reviewed outside fixture moved inside Tamil Nadu: {name}")


def validated_feature(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise BuildError("Nominatim did not return exactly one Tamil Nadu feature")
    feature = raw[0]
    if (
        feature.get("osm_type") != "relation"
        or int(feature.get("osm_id", 0)) != RELATION_ID
        or feature.get("category") != "boundary"
        or feature.get("type") != "administrative"
        or feature.get("display_name") != "Tamil Nadu, India"
        or "OpenStreetMap" not in str(feature.get("licence", ""))
        or "ODbL" not in str(feature.get("licence", ""))
    ):
        raise BuildError("the downloaded feature is not the pinned Tamil Nadu relation")

    geometry = rounded_geometry(feature.get("geojson"))
    digest = hashlib.sha256(compact_json(geometry)).hexdigest()
    if digest != EXPECTED_GEOMETRY_SHA256:
        raise BuildError(
            "the Tamil Nadu geometry changed since review; inspect it and update the pin deliberately"
        )
    bbox = geometry_bbox(geometry)
    if bbox != EXPECTED_BBOX:
        raise BuildError(f"the Tamil Nadu geometry bounding box changed since review: {bbox}")

    source_bbox = feature.get("boundingbox")
    if not isinstance(source_bbox, list) or len(source_bbox) != 4:
        raise BuildError("the Tamil Nadu relation has no valid source bounding box")
    try:
        normalized_source_bbox = [round(float(value), 7) for value in source_bbox]
    except (TypeError, ValueError) as exc:
        raise BuildError("the Tamil Nadu source bounding box is not numeric") from exc
    if normalized_source_bbox != EXPECTED_SOURCE_BBOX:
        raise BuildError("the Tamil Nadu source bounding box changed since review")

    validate_fixtures(geometry)
    return geometry


def build(raw: Any) -> dict[str, Any]:
    geometry = validated_feature(raw)
    return {
        "version": 1,
        "retrieved_at": RETRIEVED_AT,
        "region": {
            "id": "tamil-nadu-state",
            "authority_id": "tn-statewide-unverified",
            "name": "Tamil Nadu",
            "scope": "Full State of Tamil Nadu; excludes Puducherry Union Territory enclaves",
            "osm_relation_id": RELATION_ID,
            "source_name": "OpenStreetMap contributors",
            "source_home_url": f"https://www.openstreetmap.org/relation/{RELATION_ID}",
            "source_url": LOOKUP_URL,
            "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
            "attribution": "© OpenStreetMap contributors",
            "routing_note": (
                "State containment enables a neutral Tamil Nadu CM Helpline grievance "
                "handoff after the exact Greater Chennai Corporation check; it does not "
                "identify a local body, road owner, or complaint category."
            ),
            "limitations": [
                "The OpenStreetMap boundary is a routing aid, not a legal boundary record.",
                "Puducherry Union Territory enclaves, including Puducherry and Karaikal, are outside this route.",
                "The user must select and verify the responsible department or urban local body.",
                "Municipal, PWD, panchayat, NHAI and other ownership is not inferred.",
            ],
            "coordinate_precision": 7,
            "bbox": dict(EXPECTED_BBOX),
            "geometry_sha256": EXPECTED_GEOMETRY_SHA256,
            "geometry": geometry,
        },
    }


def encoded_payload(payload: dict[str, Any]) -> bytes:
    return compact_json(payload) + b"\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        help="saved Nominatim jsonv2 response; omit to download the pinned relation",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that the canonical output already matches without writing it",
    )
    args = parser.parse_args()

    raw = read_json(args.input) if args.input else download_lookup()
    encoded = encoded_payload(build(raw))
    if args.check:
        try:
            current = OUTPUT.read_bytes()
        except OSError as exc:
            raise BuildError(f"cannot read canonical output {OUTPUT}: {exc}") from exc
        if current != encoded:
            raise BuildError("canonical Tamil Nadu output is stale; rerun the builder")
        print(f"OK {OUTPUT.relative_to(ROOT)} ({len(encoded)} bytes)")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(encoded)
        print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(encoded)} bytes)")
    print(f"geometry_sha256={EXPECTED_GEOMETRY_SHA256}")


if __name__ == "__main__":
    try:
        main()
    except BuildError as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
