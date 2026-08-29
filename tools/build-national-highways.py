#!/usr/bin/env python3
"""Build and verify content-addressed National Highway geometry tiles.

The large OSM extract is an input, never an app asset. A release checks in only the
small immutable tiles, their pinned manifest, and an auditable source receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = (
    ROOT / "static" / "highway-manifest.json",
    ROOT / "android-app" / "www" / "highway-manifest.json",
    ROOT / "docs" / "highway-manifest.json",
)
SOURCE_RECEIPT = ROOT / "data" / "national-highways-source.json"
PAGES_ROOT = ROOT / "docs"
PUBLIC_ROOT = "https://coding-parrot.github.io/pothole-reporter/"
FORMAT = "pothole-highway-pack-manifest"
TILE_FORMAT = "pothole-national-highway-tile"
SCHEMA_VERSION = 1
CATALOG_VERSION = 1
PACK_VERSION = 1
TILE_SIZE = 2
COORDINATE_SCALE = 100_000
TILE_BUFFER_METERS = 60
MAX_TILE_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
SOURCE_URL = "https://download.geofabrik.de/asia/india-260820.osm.pbf"
SOURCE_HOME_URL = "https://download.geofabrik.de/asia/india.html"
SOURCE_DATE = "2026-08-20"
SOURCE_MD5 = "c5e0a62a1cb00c80d8c5948bf18370d7"
SOURCE_FILTER = "ways with ref=NH*, ref=NE*, or network=IN:NH; operational drivable classes"
ALLOWED_HIGHWAYS = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "tertiary", "tertiary_link", "corridor",
}
REF_RE = re.compile(
    r"(?i)(?<![A-Z0-9])N([HE])\s*[- ]?\s*([0-9]{1,4}[A-Z]{0,3})(?![A-Z0-9])"
)
NUMERIC_REF_RE = re.compile(r"^[0-9]{1,4}[A-Z]{0,3}$", re.I)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TILE_ID_RE = re.compile(r"^e[0-9]{3}n[0-9]{2}$")
AUTHORITY = {
    "id": "in-national-highway",
    "name": "National Highway grievance coordination (verify maintaining agency)",
    "handoff_name": "Rajmargyatra",
    "handoff_url": "https://play.google.com/store/apps/details?id=com.nhai.rajmargyatra",
    "handoff_package": "com.nhai.rajmargyatra",
    "alternate_handoff_name": "CPGRAMS",
    "alternate_handoff_url": "https://pgportal.gov.in/",
    "helpline": "1033",
}


class HighwayPackError(RuntimeError):
    pass


def compact(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def write_if_changed(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise HighwayPackError(message)


def canonical_refs(properties: dict[str, Any]) -> tuple[str, ...]:
    raw = str(properties.get("ref") or "")
    refs: set[str] = set()
    for kind, number in REF_RE.findall(raw):
        match = re.fullmatch(r"([0-9]+)([A-Z]*)", number.upper())
        assert match
        refs.add(f"N{kind.upper()}-{int(match.group(1))}{match.group(2)}")
    network = str(properties.get("network") or "").upper()
    if not refs and network == "IN:NH" and NUMERIC_REF_RE.fullmatch(raw.strip()):
        match = re.fullmatch(r"([0-9]+)([A-Z]*)", raw.strip().upper())
        assert match
        refs.add(f"NH-{int(match.group(1))}{match.group(2)}")

    def key(value: str) -> tuple[int, int, str]:
        match = re.fullmatch(r"N([HE])-([0-9]+)([A-Z]*)", value)
        assert match
        return (0 if match.group(1) == "H" else 1, int(match.group(2)), match.group(3))

    return tuple(sorted(refs, key=key))


def metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    return math.hypot((b[0] - a[0]) * 111_320 * math.cos(lat), (b[1] - a[1]) * 110_540)


def perpendicular_distance(point: tuple[float, float], start: tuple[float, float],
                           end: tuple[float, float]) -> float:
    lat = math.radians(point[1])
    sx = (start[0] - point[0]) * 111_320 * math.cos(lat)
    sy = (start[1] - point[1]) * 110_540
    ex = (end[0] - point[0]) * 111_320 * math.cos(lat)
    ey = (end[1] - point[1]) * 110_540
    dx, dy = ex - sx, ey - sy
    denom = dx * dx + dy * dy
    turn = max(0.0, min(1.0, -(sx * dx + sy * dy) / denom)) if denom else 0.0
    return math.hypot(sx + turn * dx, sy + turn * dy)


def simplify(points: list[tuple[float, float]], tolerance_m: float = 2.0) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        left, right = stack.pop()
        best_index, best_distance = -1, -1.0
        for index in range(left + 1, right):
            distance = perpendicular_distance(points[index], points[left], points[right])
            if distance > best_distance:
                best_index, best_distance = index, distance
        if best_index >= 0 and best_distance > tolerance_m:
            keep.add(best_index)
            stack.extend(((left, best_index), (best_index, right)))
    return [points[index] for index in sorted(keep)]


def chunks(points: list[tuple[float, float]], max_m: float = 25_000,
           max_points: int = 400) -> Iterable[list[tuple[float, float]]]:
    current = [points[0]]
    distance = 0.0
    for point in points[1:]:
        step = metres(current[-1], point)
        if len(current) >= max_points or (distance + step > max_m and len(current) >= 2):
            yield current
            current = [current[-1]]
            distance = 0.0
        current.append(point)
        distance += step
    if len(current) >= 2:
        yield current


def tile_id(lng: int, lat: int) -> str:
    expect(lng >= 0 and lat >= 0, "India highway tile coordinates must be positive")
    return f"e{lng:03d}n{lat:02d}"


def tiles_for(points: list[tuple[float, float]]) -> Iterable[tuple[str, list[float]]]:
    lat_buffer_deg = TILE_BUFFER_METERS / 110_540
    # A degree of longitude spans 111_320 * cos(latitude) metres, so the east-west buffer
    # is sized at the highest latitude the way reaches and holds along the whole way.
    worst_lat = max(abs(point[1]) for point in points)
    lng_buffer_deg = TILE_BUFFER_METERS / max(
        1.0, 111_320 * math.cos(math.radians(worst_lat)))
    min_lng = min(point[0] for point in points) - lng_buffer_deg
    min_lat = min(point[1] for point in points) - lat_buffer_deg
    max_lng = max(point[0] for point in points) + lng_buffer_deg
    max_lat = max(point[1] for point in points) + lat_buffer_deg
    lng_start = math.floor(min_lng / TILE_SIZE) * TILE_SIZE
    lat_start = math.floor(min_lat / TILE_SIZE) * TILE_SIZE
    lng_end = math.floor(max_lng / TILE_SIZE) * TILE_SIZE
    lat_end = math.floor(max_lat / TILE_SIZE) * TILE_SIZE
    for lng in range(lng_start, lng_end + 1, TILE_SIZE):
        for lat in range(lat_start, lat_end + 1, TILE_SIZE):
            yield tile_id(lng, lat), [lng, lat, lng + TILE_SIZE, lat + TILE_SIZE]


def encode_feature(refs: tuple[str, ...], points: list[tuple[float, float]]) -> list[Any] | None:
    integers = [(round(lng * COORDINATE_SCALE), round(lat * COORDINATE_SCALE))
                for lng, lat in points]
    integers = [point for index, point in enumerate(integers)
                if index == 0 or point != integers[index - 1]]
    if len(integers) < 2:
        return None
    flat = [integers[0][0], integers[0][1]]
    for previous, current in zip(integers, integers[1:]):
        flat.extend((current[0] - previous[0], current[1] - previous[1]))
    bbox = [min(point[0] for point in integers), min(point[1] for point in integers),
            max(point[0] for point in integers), max(point[1] for point in integers)]
    return [" / ".join(refs), bbox, flat]


def parse_source(path: Path) -> tuple[dict[str, list[list[Any]]], dict[str, Any]]:
    per_tile: dict[str, list[list[Any]]] = defaultdict(list)
    tile_bounds: dict[str, list[float]] = {}
    ref_counts: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    source_features = accepted_features = output_features = source_points = output_points = 0
    carriageway_m = 0.0
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.lstrip("\x1e").strip()
            if not line:
                continue
            source_features += 1
            feature = json.loads(line)
            properties = feature.get("properties") or {}
            highway = str(properties.get("highway") or "")
            refs = canonical_refs(properties)
            geometry = feature.get("geometry") or {}
            coordinates = geometry.get("coordinates")
            if highway not in ALLOWED_HIGHWAYS or not refs or geometry.get("type") != "LineString":
                continue
            if not isinstance(coordinates, list) or len(coordinates) < 2:
                continue
            points: list[tuple[float, float]] = []
            for coordinate in coordinates:
                if (not isinstance(coordinate, list) or len(coordinate) < 2
                        or not all(isinstance(item, (int, float)) and math.isfinite(item)
                                   for item in coordinate[:2])):
                    points = []
                    break
                point = (float(coordinate[0]), float(coordinate[1]))
                if not (67 <= point[0] <= 98 and 5 <= point[1] <= 36):
                    points = []
                    break
                if not points or point != points[-1]:
                    points.append(point)
            if len(points) < 2:
                continue
            accepted_features += 1
            source_points += len(points)
            class_counts[highway] += 1
            for ref in refs:
                ref_counts[ref] += 1
            carriageway_m += sum(metres(left, right) for left, right in zip(points, points[1:]))
            for part in chunks(simplify(points)):
                encoded = encode_feature(refs, part)
                if encoded is None:
                    continue
                output_features += 1
                output_points += len(encoded[2]) // 2
                for identifier, bounds in tiles_for(part):
                    per_tile[identifier].append(encoded)
                    tile_bounds[identifier] = bounds
    expect(accepted_features > 100_000, "source has too few operational NH features")
    expect(len(ref_counts) > 500, "source has too few distinct NH/NE references")
    expect(per_tile, "source produced no highway tiles")
    for features in per_tile.values():
        features.sort(key=lambda item: (item[0], item[1], len(item[2]), item[2][:2]))
    stats = {
        "source_features": source_features,
        "accepted_features": accepted_features,
        "output_features": output_features,
        "source_points": source_points,
        "output_points": output_points,
        "distinct_refs": len(ref_counts),
        "mapped_carriageway_km": round(carriageway_m / 1000, 1),
        "tile_count": len(per_tile),
        "highway_classes": dict(sorted(class_counts.items())),
        "refs": sorted(ref_counts),
        "tile_bounds": tile_bounds,
    }
    return per_tile, stats


def source_receipt(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "pothole-national-highway-source-receipt",
        "schema_version": 1,
        "source_name": "OpenStreetMap India extract by Geofabrik",
        "source_home_url": SOURCE_HOME_URL,
        "source_url": SOURCE_URL,
        "source_retrieved_at": SOURCE_DATE,
        "source_md5": SOURCE_MD5,
        "source_filter": SOURCE_FILTER,
        "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors; extract by Geofabrik",
        "limitations": [
            "Coverage follows operational NH/NE references mapped in OpenStreetMap and is not a legal road-ownership register.",
            "The maintaining agency is deliberately not inferred from geometry; the user must verify it in the official service.",
            "Proposed, construction-only, service, residential, path, track and other non-carriageway classes are excluded.",
        ],
        **{key: value for key, value in stats.items() if key != "tile_bounds"},
    }


def build(source: Path) -> None:
    expect(source.is_file(), f"missing GeoJSONSeq source: {source}")
    per_tile, stats = parse_source(source)
    tiles: dict[str, Any] = {}
    for identifier in sorted(per_tile):
        features = per_tile[identifier]
        pack = {
            "coordinate_scale": COORDINATE_SCALE,
            "features": features,
            "format": TILE_FORMAT,
            "generated_at": SOURCE_DATE,
            "pack_id": f"in-nh-{identifier}",
            "pack_version": PACK_VERSION,
            "schema_version": SCHEMA_VERSION,
            "tile_id": identifier,
        }
        data = compact(pack)
        expect(len(data) <= MAX_TILE_BYTES, f"{identifier} exceeds the tile size limit")
        digest = hashlib.sha256(data).hexdigest()
        path = f"packs/v1/highways/{identifier}-{digest}.json"
        write_if_changed(PAGES_ROOT / path, data)
        refs = {feature[0] for feature in features}
        tiles[identifier] = {
            "bbox": stats["tile_bounds"][identifier],
            "bytes": len(data),
            "feature_count": len(features),
            "kind": "highways",
            "pack_id": f"in-nh-{identifier}",
            "pack_version": PACK_VERSION,
            "path": path,
            "ref_count": len(refs),
            "schema_version": SCHEMA_VERSION,
            "sha256": digest,
            "state_code": "IN",
            "tile_id": identifier,
            "url": PUBLIC_ROOT + path,
        }
    receipt = source_receipt(stats)
    manifest = {
        "authority": AUTHORITY,
        "cache": {"max_bytes": 67_108_864, "max_unused_days": 90},
        "catalog_version": CATALOG_VERSION,
        "format": FORMAT,
        "match": {
            "max_gps_accuracy_m": 30,
            "max_match_distance_m": 45,
            "minimum_match_distance_m": 15,
            "tile_buffer_m": TILE_BUFFER_METERS,
            "tile_size_degrees": TILE_SIZE,
        },
        "schema_version": SCHEMA_VERSION,
        "source": {key: value for key, value in receipt.items()
                   if key not in {"format", "schema_version", "refs", "highway_classes"}},
        "tiles": tiles,
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"
    expect(len(manifest_bytes) <= MAX_MANIFEST_BYTES, "highway manifest exceeds runtime limit")
    for path in MANIFESTS:
        write_if_changed(path, manifest_bytes)
    write_if_changed(SOURCE_RECEIPT, json.dumps(
        receipt, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n")
    verify()
    print(json.dumps({
        "tiles": len(tiles), "refs": stats["distinct_refs"],
        "accepted_features": stats["accepted_features"],
        "mapped_carriageway_km": stats["mapped_carriageway_km"],
        "manifest_bytes": len(manifest_bytes),
        "tile_bytes": sum(item["bytes"] for item in tiles.values()),
    }, sort_keys=True))


def validate_tile_entry(identifier: str, entry: Any) -> None:
    keys = {"bbox", "bytes", "feature_count", "kind", "pack_id", "pack_version", "path",
            "ref_count", "schema_version", "sha256", "state_code", "tile_id", "url"}
    expect(isinstance(entry, dict) and set(entry) == keys, f"{identifier}: invalid manifest fields")
    expect(TILE_ID_RE.fullmatch(identifier) is not None and entry["tile_id"] == identifier,
           f"{identifier}: invalid tile identity")
    expect(entry["pack_id"] == f"in-nh-{identifier}" and entry["kind"] == "highways"
           and entry["state_code"] == "IN" and entry["pack_version"] == PACK_VERSION
           and entry["schema_version"] == SCHEMA_VERSION, f"{identifier}: invalid pack identity")
    expect(isinstance(entry["bytes"], int) and 0 < entry["bytes"] <= MAX_TILE_BYTES,
           f"{identifier}: invalid size")
    expect(isinstance(entry["feature_count"], int) and entry["feature_count"] > 0,
           f"{identifier}: invalid feature count")
    expect(isinstance(entry["ref_count"], int) and entry["ref_count"] > 0,
           f"{identifier}: invalid ref count")
    expect(isinstance(entry["sha256"], str) and SHA256_RE.fullmatch(entry["sha256"]) is not None,
           f"{identifier}: invalid checksum")
    expected_path = f"packs/v1/highways/{identifier}-{entry['sha256']}.json"
    expect(entry["path"] == expected_path and entry["url"] == PUBLIC_ROOT + expected_path,
           f"{identifier}: invalid URL binding")
    bounds = entry["bbox"]
    expect(isinstance(bounds, list) and len(bounds) == 4
           and all(isinstance(value, int) for value in bounds)
           and bounds[2] - bounds[0] == TILE_SIZE and bounds[3] - bounds[1] == TILE_SIZE,
           f"{identifier}: invalid bounds")


def validate_pack(identifier: str, entry: dict[str, Any], data: bytes) -> None:
    expect(len(data) == entry["bytes"], f"{identifier}: byte length mismatch")
    expect(hashlib.sha256(data).hexdigest() == entry["sha256"], f"{identifier}: checksum mismatch")
    pack = json.loads(data)
    expect(isinstance(pack, dict) and set(pack) == {
        "coordinate_scale", "features", "format", "generated_at", "pack_id", "pack_version",
        "schema_version", "tile_id",
    }, f"{identifier}: invalid pack fields")
    expect(pack["format"] == TILE_FORMAT and pack["schema_version"] == SCHEMA_VERSION
           and pack["pack_version"] == PACK_VERSION and pack["tile_id"] == identifier
           and pack["pack_id"] == entry["pack_id"] and pack["generated_at"] == SOURCE_DATE
           and pack["coordinate_scale"] == COORDINATE_SCALE, f"{identifier}: invalid pack envelope")
    features = pack["features"]
    expect(isinstance(features, list) and len(features) == entry["feature_count"],
           f"{identifier}: feature count mismatch")
    refs: set[str] = set()
    for feature in features:
        expect(isinstance(feature, list) and len(feature) == 3,
               f"{identifier}: malformed feature")
        ref, bbox, coordinates = feature
        expect(isinstance(ref, str) and ref and len(ref) <= 100
               and all(REF_RE.fullmatch(item.strip()) for item in ref.split("/")),
               f"{identifier}: malformed reference")
        refs.add(ref)
        expect(isinstance(bbox, list) and len(bbox) == 4
               and all(isinstance(value, int) for value in bbox)
               and bbox[0] <= bbox[2] and bbox[1] <= bbox[3],
               f"{identifier}: malformed feature bounds")
        expect(isinstance(coordinates, list) and len(coordinates) >= 4
               and len(coordinates) % 2 == 0
               and all(isinstance(value, int) and abs(value) <= 10_000_000 for value in coordinates),
               f"{identifier}: malformed delta coordinates")
    expect(len(refs) == entry["ref_count"], f"{identifier}: reference count mismatch")


def verify() -> None:
    expect(all(path.is_file() for path in MANIFESTS), "a highway manifest mirror is missing")
    first = MANIFESTS[0].read_bytes()
    expect(0 < len(first) <= MAX_MANIFEST_BYTES, "highway manifest size is invalid")
    expect(all(path.read_bytes() == first for path in MANIFESTS[1:]),
           "highway manifest mirrors differ")
    manifest = json.loads(first)
    expect(isinstance(manifest, dict) and set(manifest) == {
        "authority", "cache", "catalog_version", "format", "match", "schema_version",
        "source", "tiles",
    }, "highway manifest fields differ from the contract")
    expect(manifest["format"] == FORMAT and manifest["schema_version"] == SCHEMA_VERSION
           and manifest["catalog_version"] == CATALOG_VERSION,
           "highway manifest identity is invalid")
    expect(manifest["authority"] == AUTHORITY, "highway authority differs from the reviewed pin")
    expect(manifest["match"] == {
        "max_gps_accuracy_m": 30, "max_match_distance_m": 45,
        "minimum_match_distance_m": 15, "tile_buffer_m": TILE_BUFFER_METERS,
        "tile_size_degrees": TILE_SIZE,
    }, "highway matching thresholds differ from the reviewed pin")
    expect(manifest["cache"] == {"max_bytes": 67_108_864, "max_unused_days": 90},
           "highway cache policy differs from the reviewed pin")
    source = manifest["source"]
    expect(isinstance(source, dict) and source.get("source_url") == SOURCE_URL
           and source.get("source_md5") == SOURCE_MD5
           and source.get("source_retrieved_at") == SOURCE_DATE
           and source.get("source_license") == "Open Data Commons Open Database License (ODbL) 1.0",
           "highway source receipt is invalid")
    tiles = manifest["tiles"]
    expect(isinstance(tiles, dict) and 50 <= len(tiles) <= 300,
           "highway tile catalog has an implausible size")
    expected_paths = {PAGES_ROOT / entry["path"] for entry in tiles.values()}
    actual_paths = set((PAGES_ROOT / "packs" / "v1" / "highways").glob("*.json"))
    expect(actual_paths == expected_paths, "highway tile directory has missing or stale files")
    for identifier, entry in tiles.items():
        validate_tile_entry(identifier, entry)
        path = PAGES_ROOT / entry["path"]
        expect(path.is_file(), f"{identifier}: immutable pack is missing")
        validate_pack(identifier, entry, path.read_bytes())
    expect(SOURCE_RECEIPT.is_file(), "highway source receipt is missing")
    receipt = json.loads(SOURCE_RECEIPT.read_text())
    expect(receipt.get("source_md5") == SOURCE_MD5
           and receipt.get("tile_count") == len(tiles)
           and receipt.get("distinct_refs", 0) > 500
           and receipt.get("accepted_features", 0) > 100_000,
           "highway source receipt does not prove nationwide source coverage")
    print(f"national highway packs OK: {len(tiles)} tiles, {receipt['distinct_refs']} refs")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or verify National Highway geometry tiles")
    parser.add_argument("--source", type=Path, help="filtered OSM GeoJSONSeq input")
    parser.add_argument("--check", action="store_true", help="verify committed outputs without writing")
    args = parser.parse_args()
    if args.check:
        verify()
    else:
        expect(args.source is not None, "--source is required when building")
        build(args.source)


if __name__ == "__main__":
    try:
        main()
    except HighwayPackError as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
