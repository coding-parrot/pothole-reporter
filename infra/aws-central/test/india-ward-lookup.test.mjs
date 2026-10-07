import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { createCachedGeolocator } from "../service/geo-cache.mjs";
import { createGeolocator } from "../service/geolocation.mjs";
import { INDIA_WARD_RUNTIME_FORMAT, createIndiaWards } from "../service/india-wards.mjs";
import {
  MINIMUM_PAIRS, MOST_UNDECIDED, RUNTIME_HANDREAD_FORMAT, RUNTIME_HANDREAD_PATH, RUNTIME_PATH, buildRuntime, keptOff,
  readingOf, runtimeEntry, samplePoints, wardsFor,
} from "../tools/india-ward-runtime.mjs";
import { INDEX_PATH, WARDS_DIR, root } from "../tools/snapshot-india-wards.mjs";

// The ward of a point outside Karnataka, from the ward snapshots that are switched on
// (service/india-wards.mjs, data/wards/runtime.json). The points are real: each was taken
// inside the ward it is asserted to be in, from the committed polygons.

const quiet = { error() {}, log() {} };
// U+2013 and U+2014, written by code point so that this file holds neither.
const LONG_DASH = new RegExp(`[${String.fromCharCode(0x2013, 0x2014)}]`);
const index = JSON.parse(readFileSync(INDEX_PATH));
const runtime = JSON.parse(readFileSync(RUNTIME_PATH));
const handread = JSON.parse(readFileSync(RUNTIME_HANDREAD_PATH));
const LAMBHA = { lat: 22.95558, lng: 72.53967 };
const SHAHIBAG = { lat: 23.05129, lng: 72.60568 };
const BHOPAL_47 = { lat: 23.21383, lng: 77.42127 };
// Connaught Place and Jaipur's walled city: inside the box of a committed snapshot that
// is not switched on. Ghaziabad: no snapshot at all.
const DELHI = { lat: 28.6315, lng: 77.2167 };
const JAIPUR = { lat: 26.9239, lng: 75.8267 };
const GHAZIABAD = { lat: 28.6692, lng: 77.4538 };

test("the runtime list is the one the tool builds from the index and the pairs a person read", () => {
  assert.equal(runtime.format, INDIA_WARD_RUNTIME_FORMAT);
  assert.deepEqual(runtime, JSON.parse(JSON.stringify(buildRuntime())), "rebuild: node infra/aws-central/tools/india-ward-runtime.mjs --write");
  assert.equal(runtime.snapshots.length + runtime.off.length, index.snapshots.length, "every committed snapshot is on or says why it is off");
});

test("a snapshot is on only if a person read its pairs: none wrong, at most one in ten undecided", () => {
  assert.equal(handread.format, RUNTIME_HANDREAD_FORMAT);
  assert.ok(runtime.snapshots.length >= 2, "Ahmedabad and Bhopal passed on 7 Oct 2026");
  for (const entry of runtime.snapshots) {
    const indexEntry = index.snapshots.find((item) => item.id === entry.id);
    assert.ok(["name", "number"].includes(indexEntry.use.by), entry.id);
    if (indexEntry.use.by === "number") assert.equal(indexEntry.use.numbers, "current", `${entry.id}: matched by number, so its numbering must be the tenders'`);
    assert.equal(entry.sha256, indexEntry.sha256, `${entry.id}: the list pins the committed file`);
    const reading = readingOf(handread, entry.id);
    assert.deepEqual(entry.read, reading);
    assert.equal(reading.wrong, 0, entry.id);
    assert.ok(reading.read >= MINIMUM_PAIRS, `${entry.id}: ${reading.read} pairs read`);
    assert.ok(reading.cannot_tell <= MOST_UNDECIDED * reading.read, entry.id);
    assert.equal(reading.right + reading.cannot_tell, reading.read, entry.id);
  }
  assert.deepEqual(runtime.snapshots.map((entry) => entry.id).filter((id) => ["GJ/ahmedabad", "MP/bhopal"].includes(id)), ["MP/bhopal", "GJ/ahmedabad"]);
});

test("the gate itself: what keeps a snapshot off", () => {
  const entry = (use) => ({ id: "XX/town", use: { evidence: "e", ...use } });
  const read = (right, wrong = 0, undecided = 0) => ({ read: right + wrong + undecided, right, wrong, cannot_tell: undecided });
  assert.equal(keptOff(entry({ by: "name", numbers: "untested" }), read(30)), null);
  assert.equal(keptOff(entry({ by: "number", numbers: "current" }), read(27, 0, 3)), null);
  assert.match(keptOff(entry({ by: "nothing", numbers: "wrong" }), read(30)), /match by nothing/);
  assert.match(keptOff(entry({ by: "number", numbers: "untested" }), read(30)), /numbering is untested/);
  assert.match(keptOff(entry({ by: "number", numbers: "wrong" }), read(30)), /numbering is wrong/);
  assert.match(keptOff(entry({ by: "name", numbers: "wrong" }), read(0)), /no pair/);
  assert.match(keptOff(entry({ by: "name", numbers: "wrong" }), read(29, 1)), /1 of 30 pairs read were wrong/);
  assert.match(keptOff(entry({ by: "name", numbers: "wrong" }), read(26, 0, 4)), /could not be told/);
  assert.match(keptOff(entry({ by: "name", numbers: "wrong" }), read(MINIMUM_PAIRS - 1)), /only 9 pairs/);
});

test("the record of pairs read is whole: every pair has a verdict, a reason and a ward in its snapshot", () => {
  assert.deepEqual(Object.keys(handread.verdicts).sort(), ["cannot tell", "right", "wrong"]);
  for (const [id, body] of Object.entries(handread.bodies)) {
    const indexEntry = index.snapshots.find((item) => item.id === id);
    assert.ok(indexEntry, `${id} is not a committed snapshot`);
    const codes = new Set(JSON.parse(readFileSync(path.join(root, indexEntry.path))).wards.map((ward) => ward.code));
    assert.ok(body.points >= 30, `${id}: ${body.points} points sampled`);
    assert.equal(body.pairs.length, body.pairs_returned, `${id}: every returned pair was read`);
    for (const pair of body.pairs) {
      assert.ok(handread.verdicts[pair.verdict], `${id} pair ${pair.n}: verdict ${pair.verdict}`);
      assert.ok(pair.reason.length > 10 && pair.title.length > 10 && pair.tender_number, `${id} pair ${pair.n}`);
      assert.ok(["name", "number"].includes(pair.basis), `${id} pair ${pair.n}`);
      assert.ok(codes.has(pair.ward_code), `${id} pair ${pair.n}: ${pair.ward_code} is not in the snapshot`);
      assert.ok(!LONG_DASH.test(`${pair.title}${pair.reason}`), `${id} pair ${pair.n}: a long dash`);
    }
  }
});

test("a real Ahmedabad point is in its ward by name, and a Bhopal point by number", async () => {
  const wards = createIndiaWards({ logger: quiet });
  const lambha = await wards.locate(LAMBHA.lat, LAMBHA.lng);
  assert.equal(lambha.status, "resolved");
  assert.equal(lambha.snapshot.id, "GJ/ahmedabad");
  assert.deepEqual([lambha.ward.code, lambha.ward.no, lambha.ward.name], ["GJ-ahmedabad-46", "46", "LAMBHA"]);
  const shahibag = await wards.locate(SHAHIBAG.lat, SHAHIBAG.lng);
  assert.equal(shahibag.ward.name, "SHAHIBAG");
  const bhopal = await wards.locate(BHOPAL_47.lat, BHOPAL_47.lng);
  assert.equal(bhopal.snapshot.id, "MP/bhopal");
  assert.equal(bhopal.ward.no, "47");
  assert.equal(bhopal.snapshot.by, "number");
  assert.equal(bhopal.snapshot.numbers, "current");
  assert.deepEqual(bhopal.snapshot.markers.letters, ["w"]);
});

test("a point no switched-on snapshot covers is out of scope, whether or not a file is committed for it", async () => {
  const wards = createIndiaWards({ logger: quiet });
  for (const point of [DELHI, JAIPUR, GHAZIABAD, { lat: 12.9716, lng: 77.5946 }]) {
    assert.deepEqual(await wards.locate(point.lat, point.lng), { status: "out_of_scope", snapshot: null, ward: null, passed_over: [] });
  }
});

// A copy of the committed list in a directory of its own, with the snapshot files given.
function packageWith(files, patch = (value) => value) {
  const dir = mkdtempSync(path.join(os.tmpdir(), "india-wards-"));
  writeFileSync(path.join(dir, "runtime.json"), JSON.stringify(patch(structuredClone(runtime))));
  for (const file of files) {
    mkdirSync(path.dirname(path.join(dir, file)), { recursive: true });
    cpSync(path.join(WARDS_DIR, file), path.join(dir, file));
  }
  return dir;
}

test("a State with no snapshot costs nothing, and a switched-on file missing from the package says so once", async () => {
  const errors = [];
  const wards = createIndiaWards({ dir: packageWith(["MP/bhopal.json"]), logger: { error: (line) => errors.push(JSON.parse(line)) } });
  // No file is asked for: Delhi and Karnataka are in no box, and Bhopal's file is there.
  assert.equal((await wards.locate(DELHI.lat, DELHI.lng)).status, "out_of_scope");
  assert.equal((await wards.locate(BHOPAL_47.lat, BHOPAL_47.lng)).status, "resolved");
  assert.deepEqual(errors, []);
  for (let n = 0; n < 3; n += 1) {
    const found = await wards.locate(LAMBHA.lat, LAMBHA.lng);
    assert.equal(found.status, "unavailable");
    assert.equal(found.snapshot.id, "GJ/ahmedabad");
    assert.equal(found.ward, null);
  }
  assert.equal(errors.length, 1, "logged once, not per lookup");
  assert.equal(errors[0].event, "india_ward_snapshot_unavailable");
  assert.equal(errors[0].snapshot, "GJ/ahmedabad");
});

test("a file that is not the one the list pinned is not used", async () => {
  const errors = [];
  const dir = packageWith(["GJ/ahmedabad.json"]);
  const edited = JSON.parse(readFileSync(path.join(dir, "GJ/ahmedabad.json")));
  edited.wards[45].name = "SOMEWHERE ELSE";
  writeFileSync(path.join(dir, "GJ/ahmedabad.json"), JSON.stringify(edited));
  const wards = createIndiaWards({ dir, logger: { error: (line) => errors.push(JSON.parse(line)) } });
  assert.equal((await wards.locate(LAMBHA.lat, LAMBHA.lng)).status, "unavailable");
  assert.match(errors[0].error_message, /do not match/);
});

test("without the runtime list every lookup outside Karnataka says unavailable, once in the log", async () => {
  const errors = [];
  const wards = createIndiaWards({ dir: mkdtempSync(path.join(os.tmpdir(), "india-wards-")), logger: { error: (line) => errors.push(JSON.parse(line)) } });
  assert.equal((await wards.locate(LAMBHA.lat, LAMBHA.lng)).status, "unavailable");
  assert.equal((await wards.locate(DELHI.lat, DELHI.lng)).status, "unavailable");
  assert.equal(errors.length, 1);
  assert.equal(errors[0].event, "india_ward_runtime_unavailable");
});

// The budgets of the brief: the first lookup in a State reads and checks its files in
// under 50 ms, and a lookup after that takes under 1 ms.
test("the first lookup in a State is under 50 ms and a warm one under 1 ms", async () => {
  const wards = createIndiaWards({ logger: quiet });
  await wards.locate(DELHI.lat, DELHI.lng);
  for (const point of [LAMBHA, BHOPAL_47]) {
    const started = performance.now();
    const found = await wards.locate(point.lat, point.lng);
    const took = performance.now() - started;
    assert.equal(found.status, "resolved");
    assert.ok(took < 50, `first lookup in ${found.snapshot.state_code} took ${took.toFixed(1)} ms`);
  }
  for (const entry of runtime.snapshots) {
    const snapshot = JSON.parse(readFileSync(path.join(WARDS_DIR, entry.file)));
    const points = samplePoints(snapshot, 3);
    const times = [];
    for (const point of points) {
      const started = performance.now();
      const found = await wards.locate(point.lat, point.lng);
      times.push(performance.now() - started);
      assert.ok(found.ward || found.status === "between_wards", `${entry.id} ${point.ward}: ${found.status}`);
    }
    times.sort((left, right) => left - right);
    const mean = times.reduce((sum, value) => sum + value, 0) / times.length;
    assert.ok(mean < 1, `${entry.id}: a warm lookup took ${mean.toFixed(3)} ms on average over ${times.length} points`);
    assert.ok(times[Math.floor(times.length * 0.99)] < 1, `${entry.id}: the slowest in a hundred took ${times[Math.floor(times.length * 0.99)].toFixed(3)} ms`);
  }
  const started = performance.now();
  for (let n = 0; n < 1_000; n += 1) await wards.locate(GHAZIABAD.lat + n / 1e5, GHAZIABAD.lng);
  assert.ok((performance.now() - started) / 1_000 < 0.1, "a point in no snapshot's box is told so in well under a tenth of a millisecond");
});

test("where two wards of a snapshot both hold a point, neither is said", async () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "india-wards-"));
  const ward = (code, x) => ({ code, no: code.slice(-1), name: `Ward ${code}`, bbox: [x, 0, x + 1000, 1000], rings: [[x, 0, 1000, 0, 0, 1000, -1000, 0, 0, -1000]] });
  mkdirSync(path.join(dir, "ZZ"));
  writeFileSync(path.join(dir, "ZZ/town.json"), JSON.stringify({
    format: "pothole-india-ward-polygons", coordinate_scale: 100_000, wards: [ward("ZZ-town-1", 0), ward("ZZ-town-2", 600)],
  }));
  writeFileSync(path.join(dir, "runtime.json"), JSON.stringify({
    format: INDIA_WARD_RUNTIME_FORMAT, coordinate_scale: 100_000,
    snapshots: [{ id: "ZZ/town", file: "ZZ/town.json", state_code: "ZZ", by: "name", numbers: "untested", bbox: [0, 0, 1600, 1000] }],
  }));
  const wards = createIndiaWards({ dir, logger: quiet });
  assert.equal((await wards.locate(0.005, 0.003)).ward.code, "ZZ-town-1");
  assert.equal((await wards.locate(0.005, 0.013)).ward.code, "ZZ-town-2");
  const between = await wards.locate(0.005, 0.008);
  assert.equal(between.status, "between_wards");
  assert.equal(between.ward, null);
  assert.equal((await wards.locate(0.02, 0.008)).status, "out_of_scope");
});

// Pune has two committed files, the 41 prabhags of 2025 (numbers) and the 58 of 2022
// (names). Neither is on. If both were, the list would put the one whose numbers the
// tenders use first, and the service would answer with it and name the other.
test("of two snapshots of one body the first in the list answers and the other is named", async () => {
  const entries = ["MH/pune-2022", "MH/pune"].map((id) => runtimeEntry(index.snapshots.find((item) => item.id === id)));
  const built = buildRuntime({
    index: { ...index, snapshots: index.snapshots.map((item) => (item.id === "MH/pune" ? { ...item, use: { ...item.use, numbers: "current" } } : item)) },
    handread: { bodies: Object.fromEntries(["MH/pune", "MH/pune-2022"].map((id) => [id, { pairs: Array.from({ length: MINIMUM_PAIRS }, () => ({ verdict: "right" })) }])) },
  });
  assert.deepEqual(built.snapshots.map((entry) => entry.id).filter((id) => id.startsWith("MH/pune")), ["MH/pune", "MH/pune-2022"],
    "the file whose numbers are the tenders' comes first, whatever the index's order");
  const wards = await wardsFor([{ ...entries[1], numbers: "current" }, entries[0]]);
  const found = await wards.locate(18.47699, 73.86474);
  assert.equal(found.snapshot.id, "MH/pune");
  assert.equal(found.ward.code, "MH-pune-20");
  assert.deepEqual(found.passed_over, ["MH/pune-2022"]);
  const placed = await wards.place({ lat: 18.47699, lng: 73.86474, road_ownership: "outside_state", lookup: { ward: "out_of_scope" } });
  assert.equal(placed.lookup.ward_snapshot, "MH/pune");
  assert.deepEqual(placed.lookup.ward_snapshot_over, ["MH/pune-2022"]);
  assert.equal(placed.ward_name, null);
  assert.equal(placed.lookup.ward, "resolved_unnamed");
  assert.equal(placed.ward_no, "20");
});

test("only a point outside Karnataka is placed, and a ward kept from an earlier answer is not trusted", async () => {
  const wards = createIndiaWards({ logger: quiet });
  const karnataka = { lat: 12.99657, lng: 77.62034, road_ownership: "municipal", ward_name: "Cox Town", lookup: { ward: "resolved" } };
  assert.equal(await wards.place(karnataka), karnataka, "the same object, untouched");
  const stale = {
    lat: DELHI.lat, lng: DELHI.lng, road_ownership: "outside_state", ward_name: "CONNAUGHT PLACE", ward_no: "5", ward_code: "DL-delhi-5",
    ward_numbering: "snapshot_wrong",
    lookup: { kgis: "out_of_scope", ward: "resolved", ward_snapshot: "DL/delhi-2017", ward_snapshot_dated: "2018-01-03", ward_vintage: "x", geocoder: "available" },
  };
  const placed = await wards.place(stale);
  assert.deepEqual([placed.ward_name, placed.ward_no, placed.ward_code, placed.ward_numbering], [null, null, null, null]);
  assert.deepEqual(placed.lookup, { kgis: "out_of_scope", ward: "out_of_scope", geocoder: "available" });
});

// ----------------------------------------------------------------------------------------
// Through the geolocator.

const geocoded = (address) => async () => new Response(JSON.stringify({ address }));
const AHMEDABAD_ADDRESS = { road: "Narol Road", suburb: "Lambha", city: "Ahmedabad", state: "Gujarat", "ISO3166-2-lvl4": "IN-GJ", country_code: "in", postcode: "382405" };

test("the geolocator answers a point outside Karnataka with its ward, in the fields a Karnataka ward has", async () => {
  const geolocator = createGeolocator({ geocoderUrl: "https://geocoder.test/reverse", fetchImpl: geocoded(AHMEDABAD_ADDRESS), logger: quiet });
  const value = await geolocator.resolve(LAMBHA);
  assert.equal(value.road_ownership, "outside_state");
  assert.equal(value.state_code, "GJ");
  assert.deepEqual([value.ward_name, value.ward_no, value.ward_code], ["LAMBHA", "46", "GJ-ahmedabad-46"]);
  // Ahmedabad's titles carry no ward numbers, so nothing says whose numbering the file's is.
  assert.equal(value.ward_numbering, "snapshot_untested");
  assert.equal(value.lookup.ward, "resolved");
  assert.equal(value.lookup.ward_snapshot, "GJ/ahmedabad");
  assert.equal(value.lookup.ward_snapshot_dated, "2016-08-12");
  assert.match(value.lookup.ward_vintage, /48 wards/);
  assert.equal(value.lookup.kgis, "out_of_scope");
  assert.equal(value.lgd, null);
  assert.equal(value.town, null);
  assert.equal((await geolocator.wardSnapshot("GJ/ahmedabad")).wards.length, 48);
  assert.equal(await geolocator.wardSnapshot("DL/delhi-2017"), null, "a snapshot that is not switched on is not handed out");
  // The geolocator's own five-minute memory gives the same answer.
  assert.deepEqual(await geolocator.resolve(LAMBHA), value);
  const bhopal = await geolocator.resolve(BHOPAL_47);
  assert.equal(bhopal.ward_no, "47");
  assert.equal(bhopal.ward_numbering, "snapshot_current");
  assert.equal(bhopal.lookup.ward_snapshot, "MP/bhopal");
});

test("a point outside Karnataka that no snapshot covers is answered exactly as before", async () => {
  const geolocator = createGeolocator({ geocoderUrl: "https://geocoder.test/reverse", logger: quiet,
    fetchImpl: geocoded({ road: "GT Road", city: "Ghaziabad", state: "Uttar Pradesh", country_code: "in" }) });
  const value = await geolocator.resolve(GHAZIABAD);
  assert.deepEqual([value.ward_name, value.ward_no, value.ward_code, value.ward_numbering], [null, null, null, null]);
  assert.deepEqual(value.lookup, { kgis: "out_of_scope", kgis_town: "out_of_scope", kgis_highway: "out_of_scope",
    kgis_gp: "not_needed_or_unavailable", local: "out_of_scope", ward: "out_of_scope", geocoder: "available" });
});

test("a stored location is given its ward afresh: a snapshot switched off stops answering at once", async () => {
  const cells = new Map();
  const repository = {
    async getGeoCell(cell) { return cells.get(cell) || null; },
    async putGeoCell(cell, value, expiresAt) { cells.set(cell, { value, expiresAt }); },
  };
  const inner = createGeolocator({ geocoderUrl: "https://geocoder.test/reverse", fetchImpl: geocoded(AHMEDABAD_ADDRESS), logger: quiet });
  const first = await createCachedGeolocator({ geolocator: inner, repository }).resolve(LAMBHA);
  assert.equal(first.lookup.cache, "miss");
  assert.equal(first.ward_name, "LAMBHA");
  assert.equal([...cells.values()][0].value.ward_name, "LAMBHA", "the stored answer carries the ward of the day it was stored");
  // The next deploy ships a list without Ahmedabad. A new function instance reads the
  // stored cell and must not repeat its ward.
  const without = createGeolocator({ geocoderUrl: "https://geocoder.test/reverse", fetchImpl: async () => { throw new Error("not asked"); }, logger: quiet,
    indiaWardsDir: packageWith(["MP/bhopal.json"], (value) => ({ ...value, snapshots: value.snapshots.filter((entry) => entry.id !== "GJ/ahmedabad") })) });
  const later = await createCachedGeolocator({ geolocator: without, repository }).resolve(LAMBHA);
  assert.equal(later.lookup.cache, "hit");
  assert.deepEqual([later.ward_name, later.ward_no, later.ward_code, later.lookup.ward, later.lookup.ward_snapshot], [null, null, null, "out_of_scope", undefined]);
  // And the other way: a cell stored before the ward release gets its ward on a hit.
  const before = new Map([...cells].map(([cell, stored]) => [cell, { ...stored, value: { ...stored.value, ward_name: null, ward_no: null, ward_code: null, ward_numbering: null, lookup: { ...stored.value.lookup, ward: "out_of_scope", ward_snapshot: undefined } } }]));
  const old = { async getGeoCell(cell) { return before.get(cell) || null; }, async putGeoCell() {} };
  const hit = await createCachedGeolocator({ geolocator: inner, repository: old }).resolve(LAMBHA);
  assert.equal(hit.lookup.cache, "hit");
  assert.deepEqual([hit.ward_name, hit.lookup.ward, hit.lookup.ward_snapshot], ["LAMBHA", "resolved", "GJ/ahmedabad"]);
});
