#!/usr/bin/env python3
"""Build the pinned Delhi NCT routing boundary from OpenStreetMap relation 1942586.

This is a coverage aid, not a road-ownership map. The runtime routes every accepted
point to Delhi's cross-agency PWD Sewa workflow and fails closed if this reviewed
geometry is replaced without a matching code release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

from state_pack_tools import publish_resource


RELATION_ID = 1942586
RETRIEVED_AT = "2026-08-21"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/lookup"
RUNTIME_ENVELOPE = {"min_lng": 76.65, "min_lat": 28.10, "max_lng": 77.65, "max_lat": 29.10}
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def fetch_relation() -> dict:
    query = urllib.parse.urlencode(
        {
            "osm_ids": f"R{RELATION_ID}",
            "format": "jsonv2",
            "polygon_geojson": 1,
            "polygon_threshold": "0.00001",
        }
    )
    request = urllib.request.Request(
        f"{NOMINATIM_URL}?{query}",
        headers={
            "User-Agent": "PotholeReporter-coverage-builder/1.0 (https://github.com/coding-parrot/pothole-reporter)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    if not isinstance(result, list) or len(result) != 1:
        raise RuntimeError("Nominatim did not return exactly one Delhi relation.")
    feature = result[0]
    if feature.get("osm_type") != "relation" or int(feature.get("osm_id", 0)) != RELATION_ID:
        raise RuntimeError("Nominatim returned the wrong OpenStreetMap object.")
    return feature


def rounded_geometry(geometry: dict) -> dict:
    if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        raise RuntimeError("Delhi relation is not a Polygon or MultiPolygon.")

    def visit(value):
        if isinstance(value, list) and value and isinstance(value[0], (int, float)):
            if len(value) < 2 or not all(math.isfinite(float(v)) for v in value[:2]):
                raise RuntimeError("Delhi geometry contains an invalid coordinate.")
            return [round(float(value[0]), 7), round(float(value[1]), 7)]
        if isinstance(value, list):
            return [visit(child) for child in value]
        raise RuntimeError("Delhi geometry contains an invalid coordinate structure.")

    result = {"type": geometry["type"], "coordinates": visit(geometry.get("coordinates"))}
    polygons = result["coordinates"] if result["type"] == "MultiPolygon" else [result["coordinates"]]
    if not polygons:
        raise RuntimeError("Delhi geometry has no polygons.")
    for polygon in polygons:
        if not polygon:
            raise RuntimeError("Delhi geometry has an empty polygon.")
        for ring in polygon:
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise RuntimeError("Delhi geometry contains an open or undersized ring.")
    return result


def assert_simple_geometry(geometry: dict) -> None:
    """Reject self-crossing rings before a future boundary can be reviewed and pinned."""
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]

    def orientation(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on_segment(a, b, point):
        epsilon = 1e-12
        return abs(orientation(a, b, point)) <= epsilon and (
            min(a[0], b[0]) - epsilon <= point[0] <= max(a[0], b[0]) + epsilon
            and min(a[1], b[1]) - epsilon <= point[1] <= max(a[1], b[1]) + epsilon
        )

    def intersects(a, b, c, d):
        epsilon = 1e-12
        ab_c, ab_d = orientation(a, b, c), orientation(a, b, d)
        cd_a, cd_b = orientation(c, d, a), orientation(c, d, b)
        if ((ab_c > epsilon and ab_d < -epsilon) or (ab_c < -epsilon and ab_d > epsilon)) and (
            (cd_a > epsilon and cd_b < -epsilon) or (cd_a < -epsilon and cd_b > epsilon)
        ):
            return True
        return (
            (abs(ab_c) <= epsilon and on_segment(a, b, c))
            or (abs(ab_d) <= epsilon and on_segment(a, b, d))
            or (abs(cd_a) <= epsilon and on_segment(c, d, a))
            or (abs(cd_b) <= epsilon and on_segment(c, d, b))
        )

    for polygon in polygons:
        for ring in polygon:
            segment_count = len(ring) - 1
            for first in range(segment_count):
                for second in range(first + 1, segment_count):
                    if second == first + 1 or (first == 0 and second == segment_count - 1):
                        continue
                    if intersects(ring[first], ring[first + 1], ring[second], ring[second + 1]):
                        raise RuntimeError(
                            f"Delhi geometry ring self-intersects at segments {first} and {second}."
                        )


def positions(geometry: dict):
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    for polygon in polygons:
        for ring in polygon:
            yield from ring


def ring_area_km2(ring: list[list[float]]) -> float:
    # A local equal-distance approximation is accurate enough for the sanity check and
    # metadata at Delhi's scale. It is not used for point routing.
    radius_km = 6371.0088
    mean_lat = math.radians(sum(point[1] for point in ring) / len(ring))
    projected = [
        (radius_km * math.radians(lng) * math.cos(mean_lat), radius_km * math.radians(lat))
        for lng, lat in ring
    ]
    return abs(
        sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(projected, projected[1:]))
    ) / 2


def geometry_area_km2(geometry: dict) -> float:
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    return sum(ring_area_km2(polygon[0]) - sum(ring_area_km2(ring) for ring in polygon[1:]) for polygon in polygons)


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, help="Use a saved Nominatim jsonv2 response instead of downloading.")
    args = parser.parse_args()

    if args.input:
        saved = json.loads(args.input.read_text(encoding="utf-8"))
        feature = saved[0] if isinstance(saved, list) else saved
    else:
        feature = fetch_relation()
    if feature.get("osm_type") != "relation" or int(feature.get("osm_id", 0)) != RELATION_ID:
        raise RuntimeError("Input is not OpenStreetMap relation 1942586.")

    geometry = rounded_geometry(feature.get("geojson") or {})
    assert_simple_geometry(geometry)
    coords = list(positions(geometry))
    bbox = {
        "min_lng": min(point[0] for point in coords),
        "min_lat": min(point[1] for point in coords),
        "max_lng": max(point[0] for point in coords),
        "max_lat": max(point[1] for point in coords),
    }
    if (
        bbox["min_lng"] < RUNTIME_ENVELOPE["min_lng"]
        or bbox["min_lat"] < RUNTIME_ENVELOPE["min_lat"]
        or bbox["max_lng"] > RUNTIME_ENVELOPE["max_lng"]
        or bbox["max_lat"] > RUNTIME_ENVELOPE["max_lat"]
    ):
        raise RuntimeError(f"Delhi boundary falls outside the runtime relevance envelope: {bbox}")
    area_km2 = round(geometry_area_km2(geometry), 3)
    if not (1300 <= area_km2 <= 1700):
        raise RuntimeError(f"Delhi area sanity check failed: {area_km2} km²")

    geometry_sha256 = hashlib.sha256(compact_json(geometry).encode("utf-8")).hexdigest()
    source_query = (
        f"{NOMINATIM_URL}?osm_ids=R{RELATION_ID}&format=jsonv2&polygon_geojson=1"
        "&polygon_threshold=0.00001"
    )
    document = {
        "version": 1,
        "retrieved_at": RETRIEVED_AT,
        "region": {
            "id": "delhi-nct",
            "authority_id": "dl-pwd-sewa",
            "name": "National Capital Territory of Delhi",
            "scope": "Delhi NCT only; excludes the wider National Capital Region",
            "osm_relation_id": RELATION_ID,
            "source_name": "OpenStreetMap contributors",
            "source_home_url": f"https://www.openstreetmap.org/relation/{RELATION_ID}",
            "source_url": source_query,
            "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
            "attribution": "© OpenStreetMap contributors",
            "official_scope_reference": "https://stategisportal.nic.in/stategisportal/Home/Map/7",
            "routing_note": "Coverage boundary only; it does not identify a road owner or maintenance agency.",
            "coordinate_precision": 7,
            "area_km2": area_km2,
            "bbox": bbox,
            "geometry_sha256": geometry_sha256,
            "geometry": geometry,
        },
    }
    _, output = publish_resource(
        "in-dl-routing",
        document,
        source_retrieved_at=RETRIEVED_AT,
    )
    print(output.relative_to(PROJECT_ROOT))
    print("static/pack-manifest-v1.28.json")
    print("android-app/www/pack-manifest-v1.28.json")
    print(f"area_km2={area_km2}")
    print(f"geometry_sha256={geometry_sha256}")


if __name__ == "__main__":
    main()
