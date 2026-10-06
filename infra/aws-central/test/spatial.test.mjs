import assert from "node:assert/strict";
import test from "node:test";

import {
  REPORT_MAX_ABS_LAT, decodeRun, metresBetween, metresToPolyline, metresToSegment,
  nearbyCells, pointInRings, spatialCell, withinBox,
} from "../service/spatial.mjs";

// A report locks and searches every cell a duplicate could sit in. Too few cells and a
// pothole within the dedupe radius is reported twice; too many and nearby reports
// contend for the same locks. One DynamoDB transaction holds at most 100 of them.

const RADIUS = 30;

// Deterministic, so a miss reproduces.
function random(seed) {
  let state = seed;
  return () => {
    state = (state * 1_103_515_245 + 12_345) % 2_147_483_648;
    return state / 2_147_483_648;
  };
}

function misses(lat, lng, samples = 4_000) {
  const next = random(Math.abs(Math.round(lat * 1_000)) + 7);
  let missed = 0;
  let largest = 0;
  for (let index = 0; index < samples; index += 1) {
    const originLat = lat + (next() - 0.5) * 0.001;
    const originLng = lng + (next() - 0.5) * 0.001;
    const cells = new Set(nearbyCells(originLat, originLng, RADIUS));
    largest = Math.max(largest, cells.size);
    const bearing = next() * 2 * Math.PI;
    const distance = RADIUS * Math.sqrt(next());
    const other = {
      lat: originLat + distance * Math.cos(bearing) / 111_195,
      lng: originLng + distance * Math.sin(bearing)
        / (111_195 * Math.cos(originLat * Math.PI / 180)),
    };
    if (metresBetween(originLat, originLng, other.lat, other.lng) > RADIUS) continue;
    if (!cells.has(spatialCell(other.lat, other.lng))) missed += 1;
  }
  return { missed, largest };
}

for (const lat of [0, 12.97, 45, 70, REPORT_MAX_ABS_LAT, -REPORT_MAX_ABS_LAT]) {
  test(`every point within the radius is searched at latitude ${lat}`, () => {
    const { missed, largest } = misses(lat, 77.59);
    assert.equal(missed, 0);
    assert.ok(largest <= 100, `${largest} cells will not fit one transaction`);
  });
}

test("a report in Karnataka locks 25 cells, not 49", () => {
  assert.equal(nearbyCells(12.9716, 77.5946, RADIUS).length, 25);
  assert.equal(nearbyCells(15.3647, 75.124, RADIUS).length, 25);
});

// The Karnataka fallback geometry is stored as scaled integers with per-vertex deltas,
// the app's highway tile encoding, and decided with an even-odd ray cast so holes and
// multi-part towns need no orientation bookkeeping.

// A 10 by 10 square at the origin with a 2 by 2 hole in its middle, plus a detached
// 1 by 1 square at (20, 20).
const SQUARE_WITH_HOLE = [
  [0, 0, 10, 0, 0, 10, -10, 0, 0, -10],
  [4, 4, 2, 0, 0, 2, -2, 0, 0, -2],
  [20, 20, 1, 0, 0, 1, -1, 0, 0, -1],
];

test("decodeRun turns deltas back into absolute vertices", () => {
  assert.deepEqual(decodeRun([5, 7, 1, -1, -3, 2]), [[5, 7], [6, 6], [3, 8]]);
});

test("a point is inside a ring, outside its hole, and inside a detached part", () => {
  assert.equal(pointInRings(2, 2, SQUARE_WITH_HOLE), true);
  assert.equal(pointInRings(5, 5, SQUARE_WITH_HOLE), false, "inside the hole");
  assert.equal(pointInRings(20.5, 20.5, SQUARE_WITH_HOLE), true, "second part");
  assert.equal(pointInRings(15, 15, SQUARE_WITH_HOLE), false);
  assert.equal(pointInRings(-0.1, 5, SQUARE_WITH_HOLE), false);
});

test("withinBox pads each axis on its own", () => {
  assert.equal(withinBox(11, 5, [0, 0, 10, 10]), false);
  assert.equal(withinBox(11, 5, [0, 0, 10, 10], 2, 0), true);
  assert.equal(withinBox(5, 11, [0, 0, 10, 10], 2, 0), false);
});

test("metres to a segment is zero on it and the perpendicular offset beside it", () => {
  // A west-east segment through Bengaluru's latitude, 100 m of longitude long.
  const lat = 12.9716;
  const lngPerMetre = 1 / (111_320 * Math.cos(lat * Math.PI / 180));
  const a = [77.5946, lat];
  const b = [77.5946 + 100 * lngPerMetre, lat];
  assert.ok(metresToSegment(a[0] + 50 * lngPerMetre, lat, ...a, ...b) < 0.01);
  const offset = metresToSegment(a[0] + 50 * lngPerMetre, lat + 20 / 110_540, ...a, ...b);
  assert.ok(Math.abs(offset - 20) < 0.1, `${offset} m`);
  // Past the end the distance is to the end point, not the infinite line.
  const beyond = metresToSegment(b[0] + 30 * lngPerMetre, lat, ...a, ...b);
  assert.ok(Math.abs(beyond - 30) < 0.1, `${beyond} m`);
});

test("metres to a polyline is the nearest of its segments", () => {
  const scale = 100_000;
  const lat = 12.9716;
  const lng = 77.5946;
  // Three vertices: a corner 20 m north of the point, then east, then north again.
  const dLat = Math.round(20 / 110_540 * scale);
  const dLng = Math.round(100 / (111_320 * Math.cos(lat * Math.PI / 180)) * scale);
  const encoded = [Math.round(lng * scale) - dLng, Math.round(lat * scale) + dLat, 2 * dLng, 0, 0, 10 * dLat];
  const distance = metresToPolyline(lng, lat, encoded, scale);
  assert.ok(Math.abs(distance - 20) < 0.5, `${distance} m`);
});
