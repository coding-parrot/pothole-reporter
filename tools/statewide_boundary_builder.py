#!/usr/bin/env python3
"""Shared fail-closed builder for pinned statewide routing boundaries."""

from __future__ import annotations

import hashlib
import json
import math
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


class BuildError(RuntimeError):
    """Raised when a reviewed state-boundary source contract is not satisfied."""


@dataclass(frozen=True)
class StateBoundaryConfig:
    label: str
    relation_id: int
    retrieved_at: str
    output: Path
    region_id: str
    authority_id: str
    scope: str
    geometry_sha256: str
    bbox: dict[str, float]
    source_bbox: list[float]
    routing_note: str
    limitations: tuple[str, ...]
    inside_fixtures: dict[str, tuple[float, float]]
    outside_fixtures: dict[str, tuple[float, float]]

    @property
    def lookup_url(self) -> str:
        return (
            "https://nominatim.openstreetmap.org/lookup?"
            f"osm_ids=R{self.relation_id}&format=jsonv2&polygon_geojson=1"
            "&polygon_threshold=0.00001"
        )


def compact_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read {path}: {exc}") from exc


def download_lookup(config: StateBoundaryConfig) -> Any:
    request = urllib.request.Request(
        config.lookup_url,
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
        raise BuildError(f"cannot download the {config.label} boundary: {exc}") from exc


def rounded_geometry(raw: Any, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"type", "coordinates"}:
        raise BuildError(f"the {label} relation has an invalid GeoJSON envelope")
    if raw.get("type") not in {"Polygon", "MultiPolygon"}:
        raise BuildError(f"the {label} relation is not a Polygon or MultiPolygon")

    def visit(value: Any) -> Any:
        if isinstance(value, list) and value and isinstance(value[0], (int, float)):
            if len(value) < 2 or not all(math.isfinite(float(item)) for item in value[:2]):
                raise BuildError(f"the {label} geometry contains an invalid coordinate")
            # JSON.parse/JSON.stringify normalises an integral JSON number such as
            # ``79.0`` to ``79``. Coerce those coordinates here so the build-time
            # geometry digest is identical to the browser's runtime digest.
            rounded = [round(float(value[0]), 7), round(float(value[1]), 7)]
            return [int(item) if item.is_integer() else item for item in rounded]
        if isinstance(value, list):
            return [visit(child) for child in value]
        raise BuildError(f"the {label} geometry has an invalid coordinate structure")

    geometry = {"type": raw["type"], "coordinates": visit(raw["coordinates"])}
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" \
        else [geometry["coordinates"]]
    if not polygons:
        raise BuildError(f"the {label} geometry contains no polygons")
    for polygon in polygons:
        if not polygon:
            raise BuildError(f"the {label} geometry contains an empty polygon")
        for ring in polygon:
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise BuildError(f"the {label} geometry contains an open or undersized ring")
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
        raise BuildError("state geometry contains no positions")
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


def validate_fixtures(config: StateBoundaryConfig, geometry: dict[str, Any]) -> None:
    for name, (lat, lng) in config.inside_fixtures.items():
        if not point_in_geometry(lng, lat, geometry):
            raise BuildError(f"reviewed inside fixture moved outside {config.label}: {name}")
    for name, (lat, lng) in config.outside_fixtures.items():
        if point_in_geometry(lng, lat, geometry):
            raise BuildError(f"reviewed outside fixture moved inside {config.label}: {name}")


def validated_geometry(config: StateBoundaryConfig, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise BuildError(f"Nominatim did not return exactly one {config.label} feature")
    feature = raw[0]
    if (
        feature.get("osm_type") != "relation"
        or int(feature.get("osm_id", 0)) != config.relation_id
        or feature.get("category") != "boundary"
        or feature.get("type") != "administrative"
        or feature.get("display_name") != f"{config.label}, India"
        or "OpenStreetMap" not in str(feature.get("licence", ""))
        or "ODbL" not in str(feature.get("licence", ""))
    ):
        raise BuildError(f"the downloaded feature is not the pinned {config.label} relation")

    geometry = rounded_geometry(feature.get("geojson"), config.label)
    digest = hashlib.sha256(compact_json(geometry)).hexdigest()
    if digest != config.geometry_sha256:
        raise BuildError(
            f"the {config.label} geometry changed since review; update the pin deliberately"
        )
    if geometry_bbox(geometry) != config.bbox:
        raise BuildError(f"the {config.label} geometry bounding box changed")
    source_bbox = feature.get("boundingbox")
    if not isinstance(source_bbox, list) or len(source_bbox) != 4:
        raise BuildError(f"the {config.label} relation has no valid source bounding box")
    try:
        normalized = [round(float(value), 7) for value in source_bbox]
    except (TypeError, ValueError) as exc:
        raise BuildError(f"the {config.label} source bounding box is not numeric") from exc
    if normalized != config.source_bbox:
        raise BuildError(f"the {config.label} source bounding box changed")
    validate_fixtures(config, geometry)
    return geometry


def build(config: StateBoundaryConfig, raw: Any) -> dict[str, Any]:
    geometry = validated_geometry(config, raw)
    return {
        "version": 1,
        "retrieved_at": config.retrieved_at,
        "region": {
            "id": config.region_id,
            "authority_id": config.authority_id,
            "name": config.label,
            "scope": config.scope,
            "osm_relation_id": config.relation_id,
            "source_name": "OpenStreetMap contributors",
            "source_home_url": f"https://www.openstreetmap.org/relation/{config.relation_id}",
            "source_url": config.lookup_url,
            "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
            "attribution": "© OpenStreetMap contributors",
            "routing_note": config.routing_note,
            "limitations": list(config.limitations),
            "coordinate_precision": 7,
            "bbox": dict(config.bbox),
            "geometry_sha256": config.geometry_sha256,
            "geometry": geometry,
        },
    }


def run(config: StateBoundaryConfig, input_path: Path | None, check: bool) -> None:
    raw = read_json(input_path) if input_path else download_lookup(config)
    encoded = compact_json(build(config, raw)) + b"\n"
    if check:
        try:
            current = config.output.read_bytes()
        except OSError as exc:
            raise BuildError(f"cannot read canonical output {config.output}: {exc}") from exc
        if current != encoded:
            raise BuildError(f"canonical {config.label} output is stale; rerun the builder")
        print(f"OK {config.output.relative_to(ROOT)} ({len(encoded)} bytes)")
    else:
        config.output.parent.mkdir(parents=True, exist_ok=True)
        config.output.write_bytes(encoded)
        print(f"wrote {config.output.relative_to(ROOT)} ({len(encoded)} bytes)")
    print(f"geometry_sha256={config.geometry_sha256}")
