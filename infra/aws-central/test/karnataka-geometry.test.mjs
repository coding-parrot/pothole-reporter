import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

import { LOCAL_GEOMETRY_FORMAT, LOCAL_GEOMETRY_PATH } from "../service/geolocation.mjs";
import { pointInRings, withinBox } from "../service/spatial.mjs";

// data/karnataka-local-geometry.json is what the service answers Karnataka road
// ownership from when KGIS cannot. It is generated from three sources the repo already
// pins, and this suite is what tells a stale bundle from a fresh one: every hash it
// records has to match the source as it is today.

const root = new URL("../../../", import.meta.url);
const read = (path) => readFileSync(new URL(path, root));
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
const bundle = JSON.parse(read("data/karnataka-local-geometry.json"));

test("the service reads the bundle the deploy packages", () => {
  assert.equal(LOCAL_GEOMETRY_PATH.pathname, new URL("data/karnataka-local-geometry.json", root).pathname);
  assert.equal(bundle.format, LOCAL_GEOMETRY_FORMAT);
  assert.equal(bundle.coordinate_scale, 100_000);
});

test("the town polygons are the snapshot's, and the snapshot is the one on disk", () => {
  const snapshotBytes = read("data/karnataka-town-polygons.json");
  assert.equal(bundle.towns.snapshot_sha256, sha256(snapshotBytes), "rebuild: node infra/aws-central/tools/build-karnataka-geometry.mjs");
  const snapshot = JSON.parse(snapshotBytes);
  assert.equal(snapshot.format, "pothole-kgis-town-polygons");
  assert.equal(snapshot.source, "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/Admin_Dynamic_New/MapServer/1");
  assert.equal(snapshot.spatial_reference, 4326);
  // docs/SOURCES.md: the layer holds exactly 319 polygons, one per urban local body.
  assert.equal(snapshot.count, 319);
  assert.equal(bundle.towns.count, 319);
  assert.equal(bundle.towns.features.length, 319);
  assert.equal(bundle.towns.with_lgd, 318, "every body but ELCITA has an LGD code");
  const codes = bundle.towns.features.map((town) => town.lgd).filter((lgd) => lgd !== null);
  assert.equal(new Set(codes).size, 318, "no LGD code twice");
  for (const town of bundle.towns.features) {
    assert.ok(town.name, "a town has a name");
    assert.equal(town.bbox.length, 4);
    assert.ok(town.rings.length >= 1 && town.rings.every((ring) => ring.length >= 8));
  }
});

test("every town in the roster and every body the app can write to has a polygon", () => {
  const polygons = new Map(bundle.towns.features.map((town) => [String(town.lgd), town]));
  const roster = JSON.parse(read("data/karnataka-towns.json")).towns;
  for (const town of roster) {
    if (town.lgd == null) continue;
    assert.equal(polygons.get(String(town.lgd))?.name, town.name, `LGD ${town.lgd}`);
  }
  const bodies = JSON.parse(read("data/karnataka-bodies.json")).bodies;
  for (const lgd of Object.keys(bodies)) assert.ok(polygons.has(lgd), `body ${lgd} has no polygon`);
});

test("the state boundary is the pinned in-ka-state-routing pack", () => {
  const manifests = readdirSync(new URL("static/", root))
    .map((name) => name.match(/^pack-manifest-v(\d+)\.(\d+)\.json$/)).filter(Boolean)
    .sort((left, right) => Number(left[1]) - Number(right[1]) || Number(left[2]) - Number(right[2]));
  const newest = manifests[manifests.length - 1][0];
  assert.equal(bundle.state.manifest, newest);
  const resource = JSON.parse(read(`static/${newest}`)).resources["in-ka-state-routing"];
  assert.equal(bundle.state.pack_sha256, resource.sha256);
  assert.equal(sha256(read(`docs/${resource.path}`)), resource.sha256);
  assert.equal(bundle.state.osm_relation_id, 2019939);
  assert.ok(bundle.state.rings.length >= 1);
  // Karnataka's extent: lat 11.59 to 18.46, lng 74.04 to 78.59.
  assert.deepEqual(bundle.state.bbox.map((value) => Math.round(value / 1_000)), [7405, 1159, 7859, 1848]);
  for (const town of bundle.towns.features) {
    assert.ok(withinBox(town.bbox[0], town.bbox[1], bundle.state.bbox, 2_000)
      && withinBox(town.bbox[2], town.bbox[3], bundle.state.bbox, 2_000),
    `${town.name} lies outside the state's extent`);
  }
});

test("the highway lines are the app's pinned national highway tiles", () => {
  const manifest = JSON.parse(read("static/highway-manifest.json"));
  assert.deepEqual(bundle.highways.classes, ["national_highway"]);
  assert.equal(bundle.highways.match_metres, manifest.match.minimum_match_distance_m);
  assert.equal(bundle.highways.source_retrieved_at, manifest.source.source_retrieved_at);
  assert.ok(bundle.highways.tiles.length >= 12, "every tile touching Karnataka");
  for (const tile of bundle.highways.tiles) {
    assert.equal(tile.sha256, manifest.tiles[tile.tile_id]?.sha256, tile.tile_id);
  }
  assert.ok(bundle.highways.features.length > 10_000);
  for (const feature of bundle.highways.features) {
    assert.equal(feature.length, 3);
    assert.ok(typeof feature[0] === "string" && feature[1].length === 4 && feature[2].length >= 4);
  }
});

// Answers recorded from KGIS itself, so the snapshot can be checked against the register.
test("the polygons place recorded KGIS answers where KGIS placed them", () => {
  const scale = bundle.coordinate_scale;
  const townAt = (lat, lng) => bundle.towns.features.find((town) => withinBox(lng * scale, lat * scale, town.bbox)
    && pointInRings(lng * scale, lat * scale, town.rings));
  // docs/SOURCES.md, the documented curl: MYSURU, CC, LGD 252045.
  assert.equal(townAt(12.2958, 76.6394)?.lgd, 252045);
  // geolocation.test.mjs recordings of 21 Sep 2026.
  assert.equal(townAt(12.9756, 77.605)?.name, "GBA - Central");
  assert.equal(townAt(13.00271, 77.58406)?.name, "GBA - West");
  assert.equal(townAt(15.3647, 75.124)?.name, "HUBLI DHARWAD");
  assert.equal(townAt(23.181854, 72.652801), undefined, "Gandhinagar");
});
