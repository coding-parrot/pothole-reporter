#!/usr/bin/env python3
"""Build the pinned full-State-of-Punjab routing boundary.

The OpenStreetMap geometry is a containment aid for a neutral Connect Punjab
handoff. It does not identify a municipal body, road owner, or complaint
category. Any upstream identity, geometry, or reviewed-fixture change fails
closed and requires a deliberate code-and-data review.
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
OUTPUT = ROOT / "data" / "metro-coverage" / "pb.json"
RELATION_ID = 1_942_686
RETRIEVED_AT = "2026-08-24"
LOOKUP_URL = (
    "https://nominatim.openstreetmap.org/lookup?osm_ids=R1942686&format=jsonv2"
    "&polygon_geojson=1&polygon_threshold=0.00001"
)
EXPECTED_GEOMETRY_SHA256 = (
    "e113eb774f4f353d3c7a9c98830f4b665f9bd4d166ed3b84e90855bdf38f5782"
)
EXPECTED_BBOX = {
    "min_lng": 73.8798336,
    "min_lat": 29.5429378,
    "max_lng": 76.9390583,
    "max_lat": 32.5111793,
}

# Coordinates are deliberately spread across the state and around the Chandigarh
# hole/eastern border. They are routing regression fixtures, not representative
# points for population or administrative ownership.
INSIDE_FIXTURES = {
    "amritsar": (31.6340, 74.8723),
    "jalandhar": (31.3260, 75.5762),
    "ludhiana": (30.9010, 75.8573),
    "mohali": (30.7046, 76.7179),
    "patiala": (30.3398, 76.3869),
    "bathinda": (30.2110, 74.9455),
}
OUTSIDE_FIXTURES = {
    "chandigarh_union_territory": (30.7333, 76.7794),
    "panchkula_haryana": (30.6942, 76.8606),
    "ambala_haryana": (30.3782, 76.7767),
    "sri_ganganagar_rajasthan": (29.9038, 73.8772),
    "jammu": (32.7266, 74.8570),
    "shimla_himachal_pradesh": (31.1048, 77.1734),
}


class BuildError(RuntimeError):
    """Raised when the reviewed Punjab source contract is not satisfied."""


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
                "(https://github.com/coding-parrot/pothole-reporter; contact@aiengg.dev)"
            ),
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot download the Punjab boundary: {exc}") from exc


def rounded_geometry(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"type", "coordinates"}:
        raise BuildError("the Punjab relation has an invalid GeoJSON envelope")
    if raw.get("type") not in {"Polygon", "MultiPolygon"}:
        raise BuildError("the Punjab relation is not a Polygon or MultiPolygon")

    def visit(value: Any) -> Any:
        if isinstance(value, list) and value and isinstance(value[0], (int, float)):
            if len(value) < 2 or not all(math.isfinite(float(item)) for item in value[:2]):
                raise BuildError("the Punjab geometry contains an invalid coordinate")
            return [round(float(value[0]), 7), round(float(value[1]), 7)]
        if isinstance(value, list):
            return [visit(child) for child in value]
        raise BuildError("the Punjab geometry contains an invalid coordinate structure")

    geometry = {"type": raw["type"], "coordinates": visit(raw["coordinates"])}
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" \
        else [geometry["coordinates"]]
    if not polygons:
        raise BuildError("the Punjab geometry contains no polygons")
    for polygon in polygons:
        if not polygon:
            raise BuildError("the Punjab geometry contains an empty polygon")
        for ring in polygon:
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise BuildError("the Punjab geometry contains an open or undersized ring")
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
        raise BuildError("the Punjab geometry contains no positions")
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
            raise BuildError(f"reviewed inside fixture moved outside Punjab: {name}")
    for name, (lat, lng) in OUTSIDE_FIXTURES.items():
        if point_in_geometry(lng, lat, geometry):
            raise BuildError(f"reviewed outside fixture moved inside Punjab: {name}")


def validated_feature(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise BuildError("Nominatim did not return exactly one Punjab feature")
    feature = raw[0]
    if (
        feature.get("osm_type") != "relation"
        or int(feature.get("osm_id", 0)) != RELATION_ID
        or feature.get("category") != "boundary"
        or feature.get("type") != "administrative"
        or feature.get("display_name") != "Punjab, India"
        or "OpenStreetMap" not in str(feature.get("licence", ""))
        or "ODbL" not in str(feature.get("licence", ""))
    ):
        raise BuildError("the downloaded feature is not the pinned Punjab relation")

    geometry = rounded_geometry(feature.get("geojson"))
    digest = hashlib.sha256(compact_json(geometry)).hexdigest()
    if digest != EXPECTED_GEOMETRY_SHA256:
        raise BuildError(
            "the Punjab geometry changed since review; inspect it and update the pin deliberately"
        )
    bbox = geometry_bbox(geometry)
    if bbox != EXPECTED_BBOX:
        raise BuildError(f"the Punjab geometry bounding box changed since review: {bbox}")

    source_bbox = feature.get("boundingbox")
    if not isinstance(source_bbox, list) or len(source_bbox) != 4:
        raise BuildError("the Punjab relation has no valid source bounding box")
    try:
        south, north, west, east = (round(float(value), 7) for value in source_bbox)
    except (TypeError, ValueError) as exc:
        raise BuildError("the Punjab source bounding box is not numeric") from exc
    if [west, south, east, north] != [
        EXPECTED_BBOX["min_lng"], EXPECTED_BBOX["min_lat"],
        EXPECTED_BBOX["max_lng"], EXPECTED_BBOX["max_lat"],
    ]:
        raise BuildError("the Punjab source bounding box changed since review")
    validate_fixtures(geometry)
    return geometry


def build(raw: Any) -> dict[str, Any]:
    geometry = validated_feature(raw)
    return {
        "version": 1,
        "retrieved_at": RETRIEVED_AT,
        "region": {
            "id": "punjab-state",
            "authority_id": "pb-statewide-unverified",
            "name": "Punjab",
            "scope": "Full State of Punjab; excludes Chandigarh Union Territory",
            "osm_relation_id": RELATION_ID,
            "source_name": "OpenStreetMap contributors",
            "source_home_url": f"https://www.openstreetmap.org/relation/{RELATION_ID}",
            "source_url": LOOKUP_URL,
            "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
            "attribution": "© OpenStreetMap contributors",
            "routing_note": (
                "State containment enables a neutral Connect Punjab grievance handoff; "
                "it does not identify a local body, road owner, or complaint category."
            ),
            "limitations": [
                "The OpenStreetMap boundary is a routing aid, not a legal boundary record.",
                "Chandigarh Union Territory and neighbouring states are outside this route.",
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
    payload = build(raw)
    encoded = encoded_payload(payload)
    if args.check:
        try:
            current = OUTPUT.read_bytes()
        except OSError as exc:
            raise BuildError(f"cannot read canonical output {OUTPUT}: {exc}") from exc
        if current != encoded:
            raise BuildError("canonical Punjab output is stale; rerun the builder")
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
