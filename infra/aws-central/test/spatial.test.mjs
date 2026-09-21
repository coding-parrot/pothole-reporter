import assert from "node:assert/strict";
import test from "node:test";

import {
  REPORT_MAX_ABS_LAT, metresBetween, nearbyCells, spatialCell,
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
