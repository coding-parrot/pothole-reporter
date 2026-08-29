#!/usr/bin/env python3
"""A tile must carry the neighbouring geometry a phone near its edge can still match."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "android-app/www/highway-manifest.json").read_text())
POLICY = MANIFEST["match"]
TILE_SIZE = POLICY["tile_size_degrees"]
MATCH_DISTANCE = POLICY["max_match_distance_m"]
TILE_BUFFER = POLICY["tile_buffer_m"]
LAT_METRES_PER_DEGREE = 110_540
LNG_METRES_PER_DEGREE = 111_320

failures: list[str] = []

if TILE_BUFFER <= MATCH_DISTANCE:
    failures.append(
        f"tile_buffer_m {TILE_BUFFER} must exceed max_match_distance_m {MATCH_DISTANCE}")

packs = {}
for path in sorted((ROOT / "docs/packs/v1/highways").glob("*.json")):
    pack = json.loads(path.read_text())
    packs[pack["tile_id"]] = pack


def tile_origin(tile_id: str) -> tuple[int, int]:
    match = re.fullmatch(r"e(\d{3})n(\d{2})", tile_id)
    return int(match.group(1)), int(match.group(2))


def segments(pack: dict) -> list[tuple[float, float, float, float]]:
    scale = pack["coordinate_scale"]
    out = []
    for _ref, _bbox, encoded in pack["features"]:
        x, y = encoded[0], encoded[1]
        for index in range(2, len(encoded), 2):
            next_x, next_y = x + encoded[index], y + encoded[index + 1]
            out.append((x / scale, y / scale, next_x / scale, next_y / scale))
            x, y = next_x, next_y
    return out


def rounded(segment: tuple[float, float, float, float]) -> tuple[float, ...]:
    return tuple(round(value, 5) for value in segment)


geometry = {tile_id: segments(pack) for tile_id, pack in packs.items()}
present = {tile_id: {rounded(s) for s in value} for tile_id, value in geometry.items()}

# The northern edge of the country is where a degree of longitude is shortest, so the
# east-west buffer has to be measured there rather than at the equator.
worst_lat = max(tile_origin(tile_id)[1] + TILE_SIZE for tile_id in packs)
east_west_buffer = TILE_BUFFER / LAT_METRES_PER_DEGREE * (
    LNG_METRES_PER_DEGREE * math.cos(math.radians(worst_lat)))

for tile_id, own in geometry.items():
    west, south = tile_origin(tile_id)
    neighbours = {
        "west": (f"e{west - TILE_SIZE:03d}n{south:02d}", 0, west),
        "east": (f"e{west + TILE_SIZE:03d}n{south:02d}", 0, west + TILE_SIZE),
        "south": (f"e{west:03d}n{south - TILE_SIZE:02d}", 1, south),
        "north": (f"e{west:03d}n{south + TILE_SIZE:02d}", 1, south + TILE_SIZE),
    }
    for side, (neighbour_id, axis, edge) in neighbours.items():
        neighbour = geometry.get(neighbour_id)
        if neighbour is None:
            continue
        for segment in neighbour:
            values = (segment[axis], segment[axis + 2])
            degrees = min(abs(values[0] - edge), abs(values[1] - edge))
            metres_per_degree = LAT_METRES_PER_DEGREE if axis else (
                LNG_METRES_PER_DEGREE * math.cos(math.radians(south + TILE_SIZE / 2)))
            if degrees * metres_per_degree > MATCH_DISTANCE:
                continue
            if rounded(segment) not in present[tile_id]:
                failures.append(
                    f"{tile_id} is missing a {neighbour_id} segment "
                    f"{round(degrees * metres_per_degree, 1)} m past its {side} edge; a "
                    f"phone inside {tile_id} would not match that highway")
                break

if east_west_buffer <= MATCH_DISTANCE:
    failures.append(
        f"the east-west tile buffer is {east_west_buffer:.1f} m at latitude {worst_lat}, "
        f"which does not cover max_match_distance_m {MATCH_DISTANCE}")

if failures:
    print("FAIL")
    for failure in failures[:20]:
        print(f"  {failure}")
    raise SystemExit(1)
print(f"tiles {len(packs)}, east-west buffer {east_west_buffer:.1f} m at latitude {worst_lat}")
print("NATIONAL HIGHWAY TILE BUFFER TEST PASS")
