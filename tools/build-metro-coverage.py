#!/usr/bin/env python3
"""Build reviewed Chennai, Hyderabad and Ahmedabad municipal-city source snapshots.

Chennai and Hyderabad use ODbL OpenStreetMap polygons. Ahmedabad deliberately does
not bundle AMC's unlicensed, stale GIS outline: it accepts only an exact structured
Nominatim city/municipality match inside the OSM place node's relevance envelope.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from state_pack_tools import PROJECT_ROOT, _compact_json, _write_if_changed, build_all


RETRIEVED_AT = "2026-08-21"
NOMINATIM_URL = "https://nominatim.openstreetmap.org"
SOURCE_ROOT = PROJECT_ROOT / "data" / "metro-coverage"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def relation_feature(path: Path, relation_id: int, label: str) -> dict[str, Any]:
    value = load_json(path)
    feature = value[0] if isinstance(value, list) and len(value) == 1 else None
    if (
        not isinstance(feature, dict)
        or feature.get("osm_type") != "relation"
        or int(feature.get("osm_id", 0)) != relation_id
    ):
        raise RuntimeError(f"{label} input is not OSM relation {relation_id}.")
    return feature


def place_feature(path: Path, node_id: int, label: str) -> dict[str, Any]:
    value = load_json(path)
    features = value if isinstance(value, list) else []
    matches = [
        item for item in features
        if isinstance(item, dict)
        and item.get("osm_type") == "node"
        and int(item.get("osm_id", 0)) == node_id
    ]
    if len(matches) != 1:
        raise RuntimeError(f"{label} input does not contain OSM node {node_id}.")
    return matches[0]


def rounded_geometry(raw: Any, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("type") not in {"Polygon", "MultiPolygon"}:
        raise RuntimeError(f"{label} is not a Polygon or MultiPolygon.")

    def visit(value: Any) -> Any:
        if isinstance(value, list) and value and isinstance(value[0], (int, float)):
            if len(value) < 2 or not all(math.isfinite(float(part)) for part in value[:2]):
                raise RuntimeError(f"{label} has an invalid coordinate.")
            return [round(float(value[0]), 7), round(float(value[1]), 7)]
        if isinstance(value, list):
            return [visit(child) for child in value]
        raise RuntimeError(f"{label} has an invalid coordinate structure.")

    geometry = {"type": raw["type"], "coordinates": visit(raw.get("coordinates"))}
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    if not polygons:
        raise RuntimeError(f"{label} has no polygons.")
    for polygon in polygons:
        if not polygon:
            raise RuntimeError(f"{label} has an empty polygon.")
        for ring in polygon:
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise RuntimeError(f"{label} has an open or undersized ring.")
    return geometry


def assert_simple_geometry(geometry: dict[str, Any], label: str) -> None:
    """Reject self-crossing rings before a future source update can be published."""
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
                            f"{label} ring self-intersects at segments {first} and {second}."
                        )


def positions(geometry: dict[str, Any]):
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    for polygon in polygons:
        for ring in polygon:
            yield from ring


def bbox(geometry: dict[str, Any]) -> dict[str, float]:
    points = list(positions(geometry))
    return {
        "min_lng": min(point[0] for point in points),
        "min_lat": min(point[1] for point in points),
        "max_lng": max(point[0] for point in points),
        "max_lat": max(point[1] for point in points),
    }


def ring_area_km2(ring: list[list[float]]) -> float:
    radius_km = 6371.0088
    mean_lat = math.radians(sum(point[1] for point in ring) / len(ring))
    projected = [
        (radius_km * math.radians(lng) * math.cos(mean_lat), radius_km * math.radians(lat))
        for lng, lat in ring
    ]
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(projected, projected[1:]))) / 2


def geometry_area_km2(geometry: dict[str, Any]) -> float:
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    return sum(
        ring_area_km2(polygon[0]) - sum(ring_area_km2(ring) for ring in polygon[1:])
        for polygon in polygons
    )


def point_in_ring(lng: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i, (xi, yi) in enumerate(ring):
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_geometry(lng: float, lat: float, geometry: dict[str, Any]) -> bool:
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    for polygon in polygons:
        if point_in_ring(lng, lat, polygon[0]) and not any(
            point_in_ring(lng, lat, hole) for hole in polygon[1:]
        ):
            return True
    return False


def assert_fixtures(
    geometry: dict[str, Any], label: str, inside: list[tuple[float, float]], outside: list[tuple[float, float]]
) -> None:
    for lat, lng in inside:
        if not point_in_geometry(lng, lat, geometry):
            raise RuntimeError(f"{label} reviewed inside fixture is outside: {lat},{lng}")
    for lat, lng in outside:
        if point_in_geometry(lng, lat, geometry):
            raise RuntimeError(f"{label} reviewed outside fixture is inside: {lat},{lng}")


def geometry_digest(geometry: dict[str, Any]) -> str:
    compact = json.dumps(geometry, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()


def boundary_region(
    *, region_id: str, authority_id: str, name: str, scope: str, relation_id: int,
    routing_source: str, match_value: str, state_aliases: list[str], place_aliases: list[str],
    envelope: dict[str, float], geometry: dict[str, Any], official_scope_reference: str,
    routing_note: str, limitations: list[str], exclusions: list[dict[str, Any]],
) -> dict[str, Any]:
    bounds = bbox(geometry)
    if any(
        (key.startswith("min_") and bounds[key] < envelope[key])
        or (key.startswith("max_") and bounds[key] > envelope[key])
        for key in bounds
    ):
        raise RuntimeError(f"{name} geometry is outside its relevance envelope: {bounds}")
    return {
        "id": region_id,
        "authority_id": authority_id,
        "name": name,
        "scope": scope,
        "routing_mode": "boundary",
        "routing_source": routing_source,
        "match_value": match_value,
        "state_aliases": state_aliases,
        "place_aliases": place_aliases,
        "envelope": envelope,
        "source_name": "OpenStreetMap contributors",
        "source_home_url": f"https://www.openstreetmap.org/relation/{relation_id}",
        "source_url": (
            f"{NOMINATIM_URL}/lookup?osm_ids=R{relation_id}&format=jsonv2"
            "&polygon_geojson=1&polygon_threshold=0.00001"
        ),
        "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors",
        "official_scope_reference": official_scope_reference,
        "routing_note": routing_note,
        "limitations": limitations,
        "exclusions": exclusions,
        "source_object_id": f"osm:relation:{relation_id}",
        "coordinate_precision": 7,
        "area_km2": round(geometry_area_km2(geometry), 3),
        "bbox": bounds,
        "geometry_sha256": geometry_digest(geometry),
        "geometry": geometry,
    }


def write_source(state_code: str, regions: list[dict[str, Any]]) -> None:
    document = {"version": 1, "retrieved_at": RETRIEVED_AT, "regions": regions}
    _write_if_changed(SOURCE_ROOT / f"{state_code.lower()}.json", _compact_json(document))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chennai", type=Path, required=True, help="Saved Nominatim relation response")
    parser.add_argument("--hyderabad", type=Path, required=True, help="Saved Nominatim relation response")
    parser.add_argument("--ahmedabad", type=Path, required=True, help="Saved Nominatim city search response")
    args = parser.parse_args()

    chennai = rounded_geometry(relation_feature(args.chennai, 1766358, "Chennai").get("geojson"), "Chennai")
    assert_simple_geometry(chennai, "Chennai")
    assert_fixtures(
        chennai,
        "Chennai",
        [(13.0827, 80.2707), (13.1600, 80.3009), (13.1143, 80.1548), (12.8996, 80.2279)],
        [(12.9249, 80.1275), (13.1187, 80.1003), (13.0473, 80.0945)],
    )
    write_source("TN", [boundary_region(
        region_id="chennai-gcc",
        authority_id="tn-gcc",
        name="Greater Chennai Corporation",
        scope="Greater Chennai Corporation only; not the wider Chennai Metropolitan Area",
        relation_id=1766358,
        routing_source="osm_gcc_boundary",
        match_value="OpenStreetMap relation 1766358",
        state_aliases=["tamil nadu", "tamilnadu", "தமிழ்நாடு"],
        place_aliases=["chennai", "madras", "சென்னை"],
        envelope={"min_lng": 80.05, "min_lat": 12.75, "max_lng": 80.40, "max_lat": 13.30},
        geometry=chennai,
        official_scope_reference="https://gisgcc.chennaicorporation.gov.in/server/rest/services/GCCDepts/EDPMobile2025/FeatureServer/1",
        routing_note="ODbL coverage boundary validated against official GCC zone fixtures; it does not prove road ownership.",
        limitations=[
            "The official GCC GIS has no affirmative reuse licence and is not redistributed.",
            "Ports, airports, highways, institutional and other roads may have a different maintainer.",
        ],
        exclusions=[],
    )])

    hyderabad = rounded_geometry(
        relation_feature(args.hyderabad, 7868535, "Hyderabad").get("geojson"), "Hyderabad"
    )
    assert_simple_geometry(hyderabad, "Hyderabad")
    assert_fixtures(
        hyderabad,
        "Hyderabad",
        [(17.3616, 78.4747), (17.4095, 78.4800), (17.4486, 78.3908), (17.4018, 78.5602)],
        [(17.6500, 78.5000), (17.2500, 78.4800)],
    )
    write_source("TG", [boundary_region(
        region_id="hyderabad-cure-core",
        authority_id="tg-cure-shared",
        name="Hyderabad CURE core coverage",
        scope="Partial Hyderabad core only; shared My Cure intake, without per-corporation attribution",
        relation_id=7868535,
        routing_source="osm_hyderabad_core_boundary",
        match_value="OpenStreetMap relation 7868535",
        state_aliases=["telangana", "తెలంగాణ"],
        place_aliases=["hyderabad", "secunderabad", "హైదరాబాద్", "హైదరాబాదు"],
        envelope={"min_lng": 78.15, "min_lat": 17.20, "max_lng": 78.70, "max_lat": 17.65},
        geometry=hyderabad,
        official_scope_reference="https://ipass.telangana.gov.in/Downloads.aspx",
        routing_note="Coverage only. My Cure categorizes complaints across the 2026 GHMC, CMC and MMC structure; the app does not select one corporation.",
        limitations=[
            "No authoritative reusable 2026 three-corporation vector boundaries were publicly available.",
            "Coverage is partial and must not be read as the current GHMC, CMC or MMC boundary.",
            "The full published Secunderabad Cantonment layer extent is conservatively refused, so some neighbouring civic points are also excluded.",
            "NHAI, TG R&B, HMDA, airport, private and other roads can have a different maintainer.",
        ],
        exclusions=[{
            "id": "secunderabad-cantonment-extent",
            "name": "Secunderabad Cantonment conservative exclusion",
            "mode": "bbox",
            "bbox": {
                "min_lng": 78.459155005,
                "min_lat": 17.443033296,
                "max_lng": 78.539634302,
                "max_lat": 17.540382430,
            },
            "source_name": "Telangana Remote Sensing Applications Centre (TGRAC)",
            "source_url": "https://tgrac.telangana.gov.in/arcgis/rest/services/Hydra_Folder/Administrative_Layer/MapServer/1",
            "routing_note": "The complete official layer extent is refused; no unlicensed Cantonment polygon is redistributed.",
        }],
    )])

    ahmedabad = place_feature(args.ahmedabad, 245711197, "Ahmedabad")
    raw_bounds = ahmedabad.get("boundingbox")
    if not isinstance(raw_bounds, list) or len(raw_bounds) != 4:
        raise RuntimeError("Ahmedabad Nominatim result has no four-value bounding box.")
    south, north, west, east = [float(value) for value in raw_bounds]
    if not (22.8 < south < 23.0 < north < 23.3 and 72.3 < west < 72.6 < east < 72.9):
        raise RuntimeError(f"Ahmedabad relevance envelope changed unexpectedly: {raw_bounds}")
    write_source("GJ", [{
        "id": "ahmedabad-structured",
        "authority_id": "gj-amc",
        "name": "Ahmedabad structured city coverage",
        "scope": "Exact Ahmedabad/Amdavad structured address matches inside a reviewed relevance envelope; not a municipal-boundary claim",
        "routing_mode": "structured_geocode",
        "routing_source": "nominatim_structured_city",
        "match_value": "Nominatim structured city/municipality Ahmedabad",
        "state_aliases": ["gujarat", "ગુજરાત"],
        "place_aliases": ["ahmedabad", "amdavad", "અમદાવાદ", "अहमदाबाद"],
        "envelope": {"min_lng": west, "min_lat": south, "max_lng": east, "max_lat": north},
        "source_name": "OpenStreetMap contributors via Nominatim",
        "source_home_url": "https://www.openstreetmap.org/node/245711197",
        "source_url": (
            f"{NOMINATIM_URL}/search?city=Ahmedabad&state=Gujarat&country=India"
            "&format=jsonv2&polygon_geojson=1&addressdetails=1&limit=10"
        ),
        "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors",
        "official_scope_reference": "https://www.amccrs.com/AMCPortal/View/AMCDetail.aspx",
        "routing_note": "No current reusable AMC polygon was found. Exact structured fields gate an editable CCRS handoff and never assert road ownership.",
        "limitations": [
            "This is not point-in-polygon municipal containment.",
            "A missing or conflicting structured city/state field fails closed.",
            "AMC's public GIS is stale, lacks a reuse licence and is not bundled.",
        ],
        "exclusions": [],
        "source_object_id": "osm:node:245711197",
    }])

    for output in build_all():
        print(output.relative_to(PROJECT_ROOT))
    print("static/pack-manifest.json")
    print("android-app/www/pack-manifest.json")


if __name__ == "__main__":
    main()
