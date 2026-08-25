#!/usr/bin/env python3
"""Build reviewed Chennai, Hyderabad and Ahmedabad municipal-city source snapshots.

Chennai uses an ODbL OpenStreetMap polygon. Hyderabad stores only pinned metadata
for live point queries against TGRAC's official 2,053 km² CURE and Secunderabad
Cantonment layers; no unlicensed government polygon is copied into the app.
Ahmedabad uses the ODbL OpenCity 48-ward snapshot published by Bharatlas. The
Ahmedabad download currently stores positions as ``[latitude, longitude]`` despite
being GeoJSON; the builder validates that reviewed source quirk, swaps the axes, and
dissolves the ward edges before publishing one AMC coverage polygon.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from state_pack_tools import PROJECT_ROOT, _compact_json, _write_if_changed, build_all


RETRIEVED_AT = "2026-08-21"
HYDERABAD_RETRIEVED_AT = "2026-08-23"
AHMEDABAD_RETRIEVED_AT = "2026-08-23"
NOMINATIM_URL = "https://nominatim.openstreetmap.org"
SOURCE_ROOT = PROJECT_ROOT / "data" / "metro-coverage"
AHMEDABAD_SOURCE_URL = (
    "https://pub-0429b8e3b5a946e69ea007df844a6f1c.r2.dev/"
    "admin/wards-ahmedabad/wards_ahmedabad.geojson"
)
AHMEDABAD_SOURCE_SHA256 = "c5015c0cd147118e34ddf60fccce4f4c93d72118b21ae5d5dc36d1723c17043a"


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


def ahmedabad_ward_union(path: Path) -> dict[str, Any]:
    """Validate, axis-correct, and dissolve the reviewed 48-ward source."""
    source_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if source_digest != AHMEDABAD_SOURCE_SHA256:
        raise RuntimeError(
            "Ahmedabad ward input differs from the reviewed source snapshot: "
            f"{source_digest}"
        )
    value = load_json(path)
    features = value.get("features") if isinstance(value, dict) else None
    if not isinstance(value, dict) or value.get("type") != "FeatureCollection" \
            or not isinstance(features, list):
        raise RuntimeError("Ahmedabad ward input is not a GeoJSON FeatureCollection.")
    if len(features) != 48:
        raise RuntimeError(f"Ahmedabad ward input has {len(features)} features; expected 48.")

    expected_wards = {str(number) for number in range(1, 49)}
    found_wards: set[str] = set()
    corrected_features: list[dict[str, Any]] = []

    def swap_positions(raw: Any, label: str) -> Any:
        if isinstance(raw, list) and raw and isinstance(raw[0], (int, float)):
            if len(raw) < 2:
                raise RuntimeError(f"{label} has an undersized position.")
            latitude, longitude = float(raw[0]), float(raw[1])
            # This exact source snapshot is known to publish reversed GeoJSON axes.
            # Fail closed if that changes so a future standard-order file is not
            # accidentally swapped a second time.
            if not (22.8 <= latitude <= 23.3 and 72.3 <= longitude <= 72.9):
                raise RuntimeError(
                    f"{label} no longer has the reviewed latitude/longitude source order: {raw[:2]}"
                )
            return [round(longitude, 7), round(latitude, 7)]
        if isinstance(raw, list):
            return [swap_positions(child, label) for child in raw]
        raise RuntimeError(f"{label} has an invalid coordinate structure.")

    for index, feature in enumerate(features):
        label = f"Ahmedabad ward feature {index}"
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise RuntimeError(f"{label} is not a GeoJSON Feature.")
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            raise RuntimeError(f"{label} is missing properties or geometry.")
        ward_code = str(properties.get("sourcewardcode", "")).strip()
        if ward_code in found_wards or ward_code not in expected_wards:
            raise RuntimeError(f"{label} has an invalid or duplicate ward code: {ward_code!r}")
        if properties.get("townname") not in {"Ahmadabad", "Ahmedabad"}:
            raise RuntimeError(f"{label} is not identified as Ahmedabad.")
        if properties.get("state") != "Gujarat":
            raise RuntimeError(f"{label} is not identified as Gujarat.")
        if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            raise RuntimeError(f"{label} does not contain a polygon.")
        found_wards.add(ward_code)
        corrected_features.append({
            "type": "Feature",
            "properties": {"sourcewardcode": ward_code},
            "geometry": {
                "type": geometry["type"],
                "coordinates": swap_positions(geometry.get("coordinates"), label),
            },
        })
    if found_wards != expected_wards:
        raise RuntimeError("Ahmedabad ward input does not contain the reviewed ward codes 1-48.")

    ogr2ogr = shutil.which("ogr2ogr")
    if not ogr2ogr:
        raise RuntimeError("ogr2ogr is required to dissolve Ahmedabad's reviewed ward polygons.")
    with tempfile.TemporaryDirectory(prefix="pothole-amc-") as directory:
        corrected_path = Path(directory) / "ahmedabad_wards.geojson"
        dissolved_path = Path(directory) / "ahmedabad_boundary.geojson"
        corrected_path.write_bytes(_compact_json({
            "type": "FeatureCollection",
            "name": "ahmedabad_wards",
            "features": corrected_features,
        }))
        command = [
            ogr2ogr, "-f", "GeoJSON", str(dissolved_path), str(corrected_path),
            "-dialect", "sqlite",
            "-sql", "SELECT ST_Union(geometry) AS geometry FROM ahmedabad_wards",
            "-lco", "COORDINATE_PRECISION=7",
        ]
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown ogr2ogr error"
            raise RuntimeError(f"Ahmedabad ward dissolve failed: {detail}")
        dissolved = load_json(dissolved_path)

    dissolved_features = dissolved.get("features") if isinstance(dissolved, dict) else None
    if not isinstance(dissolved_features, list) or len(dissolved_features) != 1:
        raise RuntimeError("Ahmedabad ward dissolve did not produce exactly one feature.")
    geometry = rounded_geometry(dissolved_features[0].get("geometry"), "Ahmedabad")
    assert_simple_geometry(geometry, "Ahmedabad")
    return geometry


def boundary_region(
    *, region_id: str, authority_id: str, name: str, scope: str, relation_id: int,
    routing_source: str, match_value: str, state_aliases: list[str], place_aliases: list[str],
    envelope: dict[str, float], geometry: dict[str, Any], official_scope_reference: str,
    routing_note: str, limitations: list[str], exclusions: list[dict[str, Any]],
    source_name: str = "OpenStreetMap contributors", source_home_url: str | None = None,
    source_url: str | None = None,
    source_license: str = "Open Data Commons Open Database License (ODbL) 1.0",
    attribution: str = "© OpenStreetMap contributors", source_object_id: str | None = None,
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
        "source_name": source_name,
        "source_home_url": source_home_url or f"https://www.openstreetmap.org/relation/{relation_id}",
        "source_url": source_url or (
            f"{NOMINATIM_URL}/lookup?osm_ids=R{relation_id}&format=jsonv2"
            "&polygon_geojson=1&polygon_threshold=0.00001"
        ),
        "source_license": source_license,
        "attribution": attribution,
        "official_scope_reference": official_scope_reference,
        "routing_note": routing_note,
        "limitations": limitations,
        "exclusions": exclusions,
        "source_object_id": source_object_id or f"osm:relation:{relation_id}",
        "coordinate_precision": 7,
        "area_km2": round(geometry_area_km2(geometry), 3),
        "bbox": bounds,
        "geometry_sha256": geometry_digest(geometry),
        "geometry": geometry,
    }


def write_source(
    state_code: str, regions: list[dict[str, Any]], *, retrieved_at: str = RETRIEVED_AT
) -> None:
    document = {"version": 1, "retrieved_at": retrieved_at, "regions": regions}
    _write_if_changed(SOURCE_ROOT / f"{state_code.lower()}.json", _compact_json(document))


def official_hyderabad_region() -> dict[str, Any]:
    """Return the reviewed service contract without copying either source polygon."""
    return {
        "id": "hyderabad-cure-2053",
        "authority_id": "tg-cure-shared",
        "name": "Hyderabad Core Urban Region official service coverage",
        "scope": (
            "Official 2,053 km² CURE point-query coverage; shared My Cure intake "
            "without per-corporation attribution"
        ),
        "routing_mode": "official_point_query",
        "routing_source": "tgrac_cure_2053_point_query",
        "match_value": (
            "TGRAC CURE layer 22; GPS-accuracy envelope within the official "
            "2,053 km² coverage"
        ),
        "state_aliases": ["telangana", "తెలంగాణ"],
        "place_aliases": ["hyderabad", "secunderabad", "హైదరాబాద్", "హైదరాబాదు"],
        "envelope": {
            "min_lng": 78.15, "min_lat": 17.10, "max_lng": 78.82, "max_lat": 17.72,
        },
        "source_name": "Telangana Remote Sensing Applications Centre (TGRAC)",
        "source_home_url": (
            "https://tgrac.telangana.gov.in/arcgis/rest/services/TCUR_Folder/"
            "TCUR_Telangana_Core_Urban_Region_V2/MapServer"
        ),
        "source_url": (
            "https://tgrac.telangana.gov.in/arcgis/rest/services/TCUR_Folder/"
            "TCUR_Telangana_Core_Urban_Region_V2/MapServer/22"
        ),
        "source_license": (
            "Official public query service; no boundary geometry is redistributed"
        ),
        "attribution": (
            "Telangana Remote Sensing Applications Centre (TGRAC), Government of Telangana"
        ),
        "official_scope_reference": (
            "https://tg-bn-website-assets.flowwlabs.tech/GOs-and-ACTs/"
            "GO.Ms.No.55_11-02-2026.pdf"
        ),
        "routing_note": (
            "The Android app asks the official TGRAC service whether the complete "
            "GPS-accuracy envelope is within CURE layer 22. G.O.Ms.No.292 reorganised "
            "the expanded area into 12 zones and 60 circles; G.O.Ms.No.55 later "
            "constituted three corporations. The app deliberately uses the shared My "
            "Cure intake instead of guessing one corporation."
        ),
        "limitations": [
            "A live response from the official TGRAC service is required; browser/PWA use and service failures fail closed.",
            "The shared My Cure handoff does not identify which of Greater Hyderabad, Cyberabad or Malkajgiri Municipal Corporation owns the issue.",
            "The exact official Secunderabad Cantonment layer is queried and any intersecting accuracy envelope is refused.",
            "NHAI, TG R&B, HMDA, airport, railway, defence, private and other roads may have a different maintainer.",
        ],
        "exclusions": [{
            "id": "secunderabad-cantonment",
            "name": "Secunderabad Cantonment official boundary",
            "mode": "official_point_query",
            "bbox": {
                "min_lng": 78.459155005,
                "min_lat": 17.443033296,
                "max_lng": 78.539634302,
                "max_lat": 17.540382430,
            },
            "source_name": "Telangana Remote Sensing Applications Centre (TGRAC)",
            "source_url": (
                "https://tgrac.telangana.gov.in/arcgis/rest/services/"
                "Hydra_Folder/Administrative_Layer/MapServer/1"
            ),
            "routing_note": (
                "The Android app refuses any GPS-accuracy envelope intersecting the "
                "exact official layer; no Cantonment polygon is redistributed."
            ),
            "query_url": (
                "https://tgrac.telangana.gov.in/arcgis/rest/services/"
                "Hydra_Folder/Administrative_Layer/MapServer/1/query"
            ),
            "query_where": "1=1",
            "query_geometry_type": "esriGeometryEnvelope",
            "query_in_sr": 4326,
            "query_spatial_rel": "esriSpatialRelIntersects",
            "source_object_id": "tgrac:Hydra_Folder:Administrative_Layer:MapServer:1",
        }],
        "source_object_id": (
            "tgrac:TCUR_Folder:TCUR_Telangana_Core_Urban_Region_V2:MapServer:22"
        ),
        "query_url": (
            "https://tgrac.telangana.gov.in/arcgis/rest/services/TCUR_Folder/"
            "TCUR_Telangana_Core_Urban_Region_V2/MapServer/22/query"
        ),
        "query_where": "1=1",
        "query_geometry_type": "esriGeometryEnvelope",
        "query_in_sr": 4326,
        "query_spatial_rel": "esriSpatialRelWithin",
        "official_area_km2": 2053,
        "legal_references": [
            {
                "title": (
                    "G.O.Ms.No.292, MA&UD (GHMC-1): reorganisation into 12 zones "
                    "and 60 circles"
                ),
                "date": "2025-12-24",
                "url": "https://goir.telangana.gov.in/",
            },
            {
                "title": (
                    "G.O.Ms.No.55, MA&UD (GHMC-1): constitution of three municipal "
                    "corporations"
                ),
                "date": "2026-02-11",
                "url": (
                    "https://tg-bn-website-assets.flowwlabs.tech/GOs-and-ACTs/"
                    "GO.Ms.No.55_11-02-2026.pdf"
                ),
            },
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chennai", type=Path, required=True, help="Saved Nominatim relation response")
    parser.add_argument(
        "--ahmedabad-wards", type=Path, required=True,
        help="Saved Bharatlas/OpenCity 48-ward GeoJSON response",
    )
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

    write_source("TG", [official_hyderabad_region()], retrieved_at=HYDERABAD_RETRIEVED_AT)

    ahmedabad = ahmedabad_ward_union(args.ahmedabad_wards)
    assert_fixtures(
        ahmedabad,
        "Ahmedabad",
        [(23.0225, 72.5714), (23.1050, 72.5750), (22.9400, 72.6800)],
        [(23.2156, 72.6369), (22.9910, 72.3810), (22.7500, 72.6800)],
    )
    write_source("GJ", [boundary_region(
        region_id="ahmedabad-amc",
        authority_id="gj-amc",
        name="Ahmedabad Municipal Corporation 48-ward coverage",
        scope="Reviewed Ahmedabad 48-ward coverage footprint; not the wider AUDA area",
        relation_id=0,
        routing_source="opencity_amc_wards_union",
        match_value="OpenCity AMC 48-ward union, snapshot 2026-05-26",
        state_aliases=["gujarat", "ગુજરાત"],
        place_aliases=["ahmedabad", "amdavad", "અમદાવાદ", "अहमदाबाद"],
        envelope={"min_lng": 72.40, "min_lat": 22.85, "max_lng": 72.75, "max_lat": 23.20},
        geometry=ahmedabad,
        official_scope_reference="https://ahmedabadcity.gov.in/Home/AboutTheCorporation",
        routing_note=(
            "The 48 reviewed ward polygons are dissolved into one coverage boundary. "
            "Containment supports AMC complaint intake and does not prove road ownership."
        ),
        limitations=[
            "The ward snapshot is an ODbL secondary-source copy, checked against AMC's current 48-ward inventory.",
            "The 439.397 km² union is not proven to include every current outer AMC expansion; AMC materials publish larger total areas.",
            "AUDA and neighbouring municipal areas outside AMC are not covered.",
            "NHAI, state, railway, airport, private and other roads may have a different maintainer.",
        ],
        exclusions=[],
        source_name="OpenCity / Oorvani Foundation via Bharatlas",
        source_home_url="https://bharatlas.com/view/wards_ahmedabad",
        source_url=AHMEDABAD_SOURCE_URL,
        attribution="© OpenCity / Oorvani Foundation contributors",
        source_object_id="opencity:wards-ahmedabad:2026-05-26",
    )], retrieved_at=AHMEDABAD_RETRIEVED_AT)

    for output in build_all():
        print(output.relative_to(PROJECT_ROOT))
    print("static/pack-manifest-v1.35.json")
    print("android-app/www/pack-manifest-v1.35.json")


if __name__ == "__main__":
    main()
