import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

import { KINDS, LOCALITIES_FORMAT, LOCALITIES_PATH, SCALE, latinName, placesNear } from "../tools/build-india-localities.mjs";
import { INDEX_PATH, STATE_BOX_PAD, stateBox } from "../tools/snapshot-india-wards.mjs";

// data/wards/india-localities.json is the fallback where no ward polygons exist: named
// places from OpenStreetMap as points, by State (tools/build-india-localities.mjs).

const bytes = readFileSync(LOCALITIES_PATH);
const bundle = JSON.parse(bytes);
const index = JSON.parse(readFileSync(INDEX_PATH));

test("the gazetteer is the file the index hashed, is OpenStreetMap's, and is under 40 MB", () => {
  assert.equal(bundle.format, LOCALITIES_FORMAT);
  assert.equal(bundle.coordinate_scale, SCALE);
  assert.equal(index.localities.sha256, createHash("sha256").update(bytes).digest("hex"), "rebuild: node infra/aws-central/tools/snapshot-india-wards.mjs --index");
  assert.equal(index.localities.bytes, bytes.length);
  assert.equal(index.localities.count, bundle.count);
  assert.ok(bytes.length < 40_000_000, `${bytes.length} bytes`);
  assert.match(bundle.provenance.licence, /ODbL/);
  assert.match(bundle.provenance.attribution, /OpenStreetMap contributors/);
  assert.match(bundle.provenance.source_url, /^https:\/\/download\.geofabrik\.de\/asia\/india-latest\.osm\.pbf$/);
  assert.match(bundle.provenance.raw_sha256, /^[0-9a-f]{64}$/);
  assert.match(bundle.provenance.osm_data_as_of, /^\d{4}-\d{2}-\d{2}T/);
});

test("every State's four lists agree, are sorted, and lie in the State", () => {
  const letters = new Set(Object.values(KINDS));
  assert.deepEqual(Object.keys(bundle.kinds).sort(), [...letters].sort());
  let total = 0;
  const kinds = Object.fromEntries([...letters].map((letter) => [letter, 0]));
  for (const [code, state] of Object.entries(bundle.states)) {
    assert.equal(state.n.length, state.count, code);
    assert.equal(state.k.length, state.count, code);
    assert.equal(state.x.length, state.count, code);
    assert.equal(state.y.length, state.count, code);
    total += state.count;
    const [west, south, east, north] = stateBox(code).map((value) => value * SCALE);
    const pad = STATE_BOX_PAD * SCALE;
    for (let at = 0; at < state.count; at += 1) {
      kinds[state.k[at]] += 1;
      assert.ok(letters.has(state.k[at]), `${code}: kind ${state.k[at]}`);
      assert.ok(Number.isInteger(state.x[at]) && Number.isInteger(state.y[at]), `${code}: ${state.n[at]}`);
      assert.ok(state.x[at] >= west - pad && state.x[at] <= east + pad && state.y[at] >= south - pad && state.y[at] <= north + pad,
        `${code}: ${state.n[at]} lies outside the State`);
      if (at) {
        assert.ok(state.y[at] > state.y[at - 1] || (state.y[at] === state.y[at - 1] && state.x[at] >= state.x[at - 1]),
          `${code}: not sorted at ${state.n[at]}`);
      }
      const name = state.n[at];
      assert.ok(typeof name === "string" && name.length >= 2 && name.length <= 80 && name === name.trim(), `${code}: name ${JSON.stringify(name)}`);
      assert.ok(/\p{Script=Latin}/u.test(name) && !/[\u2013\u2014]/.test(name), `${code}: ${name}`);
    }
  }
  assert.equal(total, bundle.count);
  assert.deepEqual(kinds, bundle.kind_counts);
  assert.equal(bundle.provenance.counts.kept, bundle.count);
  assert.ok(bundle.count > 250_000, `${bundle.count} places`);
});

test("a name is taken in Latin letters or not at all", () => {
  assert.equal(latinName({ name: "इंदिरापुरम", "name:en": "Indirapuram" }), "Indirapuram");
  assert.equal(latinName({ name: "Vaishali" }), "Vaishali");
  assert.equal(latinName({ name: "इंदिरापुरम" }), null);
  assert.equal(latinName({ name: "Kalas \u2013 Dhanori" }), "Kalas - Dhanori");
  assert.equal(latinName({ name: "5" }), null);
});

test("the places near a point come back nearest first", () => {
  // Ghaziabad has the most road notices of any body and no ward polygons anyone may copy.
  // The point is in Indirapuram, which OpenStreetMap does not hold as a place: what comes
  // back is housing societies, a village and the next suburb. That is the fallback's limit.
  const near = placesNear(bundle.states.UP, 28.6460, 77.3695, 2_500);
  assert.deepEqual(near.slice(0, 2).map((place) => place.name), ["Sun Tower", "Windsor Park, Indirapuram"]);
  assert.equal(near.some((place) => place.name === "Indirapuram"), false);
  assert.deepEqual(near.find((place) => place.kind === "s"), { name: "Vasundhara", kind: "s", metres: 1804 });
  assert.ok(near.every((place, at) => !at || place.metres >= near[at - 1].metres));
  assert.ok(near.every((place) => place.metres <= 2_500));
  assert.deepEqual(placesNear(bundle.states.UP, 12.9716, 77.5946, 5_000), [], "Bengaluru is near nothing in Uttar Pradesh");
  assert.deepEqual(placesNear(undefined, 12.9716, 77.5946, 5_000), []);
});
