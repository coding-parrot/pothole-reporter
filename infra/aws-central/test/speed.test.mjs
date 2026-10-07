import assert from "node:assert/strict";
import test from "node:test";
import { gunzipSync } from "node:zlib";

import { createCachedGeolocator } from "../service/geo-cache.mjs";
import { createGeolocator } from "../service/geolocation.mjs";
import { harness, memoryRepository } from "./support.mjs";

// The public map is 52.8 KB of JSON and went over the air uncompressed: the HTTP API
// does not compress, so the function has to. On a weak mobile network that is the map.

const mapRepository = (count = 300) => {
  const repository = memoryRepository();
  let reads = 0;
  repository.listPotholes = async () => {
    reads += 1;
    return Array.from({ length: count }, (_, id) => ({ id, lat: 12.9 + id / 1e4, lng: 77.6,
      damage_type: "pothole_cavity", size: "medium", first_seen_at: 1, last_seen_at: 1,
      complaint_count: 1, observation_count: 1, seen_count: 1, town: "GBA - Central", lgd: "305851" }));
  };
  repository.reads = () => reads;
  return repository;
};
const get = (h, path, headers = {}) => h.handle({ rawPath: path, rawQueryString: "",
  requestContext: { http: { method: "GET" } }, headers });

test("a large response is gzipped for a client that accepts it, and still parses", async () => {
  const h = await harness({ repository: mapRepository() });
  const plain = await get(h, "/v1/map");
  assert.equal(plain.isBase64Encoded, false);
  assert.equal(plain.headers["content-encoding"], undefined);
  const zipped = await get(h, "/v1/map", { "Accept-Encoding": "br, gzip" });
  assert.equal(zipped.statusCode, 200);
  assert.equal(zipped.isBase64Encoded, true);
  assert.equal(zipped.headers["content-encoding"], "gzip");
  assert.match(zipped.headers.vary, /accept-encoding/i);
  const body = JSON.parse(gunzipSync(Buffer.from(zipped.body, "base64")).toString("utf8"));
  assert.equal(body.features.length, 300);
  assert.ok(Buffer.from(zipped.body, "base64").length * 4 < Buffer.byteLength(plain.body),
    "compressed map is under a quarter of the plain one");
});

test("a small response is left alone", async () => {
  const h = await harness({ repository: mapRepository(1) });
  const result = await get(h, "/v1/health", { "accept-encoding": "gzip" });
  assert.equal(result.isBase64Encoded, false);
  assert.equal(result.headers["content-encoding"], undefined);
});

// The map and impact routes already tell clients their answer is good for 30 and 60
// seconds, and the app polls them. Reading the tables again inside that window bought
// nothing; each instance now keeps the rows for exactly as long as it says they are fresh.
test("the map rows are read once inside the freshness the route advertises", async () => {
  const repository = mapRepository();
  const h = await harness({ repository });
  const first = await get(h, "/v1/map");
  const second = await get(h, "/v1/map");
  assert.equal(repository.reads(), 1);
  assert.notEqual(JSON.parse(first.body).request_id, JSON.parse(second.body).request_id,
    "each answer keeps its own request id");
  const other = await h.handle({ rawPath: "/v1/map", rawQueryString: "limit=5",
    queryStringParameters: { limit: "5" }, requestContext: { http: { method: "GET" } } });
  assert.equal(other.statusCode, 200);
  assert.equal(repository.reads(), 2, "a different query is a different read");
});

// A location's town, road class and street do not change from one request to the next,
// but each lookup asked the state GIS (which stalls for 20 s at a time) and a public
// geocoder (which allows one request a second) again. Live answers are kept for a week.
const liveAnswer = (lat, lng) => ({ lat, lng, address: "MM Road, Cox Town, Bengaluru", lgd: "305851",
  town: "GBA - Central", source: "kgis", address_source: "operator_geocoder",
  road_ownership: "municipal", lookup: { kgis: "available", geocoder: "available" } });

function cellStore() {
  const cells = new Map();
  return { cells,
    async getGeoCell(cell) { return cells.get(cell) || null; },
    async putGeoCell(cell, value, expiresAt) { cells.set(cell, { value, expiresAt }); } };
}

test("a live location answer is stored and the next lookup in that cell asks nobody", async () => {
  let asked = 0;
  const repository = cellStore();
  const geolocator = createCachedGeolocator({ repository,
    geolocator: { kgisTimeoutMs: 3000, resolve: async ({ lat, lng }) => { asked += 1; return liveAnswer(lat, lng); } } });
  const first = await geolocator.resolve({ lat: 12.99657, lng: 77.62034 });
  assert.equal(first.lookup.cache, "miss");
  const second = await geolocator.resolve({ lat: 12.99658, lng: 77.62031 });
  assert.equal(asked, 1);
  assert.equal(second.lookup.cache, "hit");
  assert.equal(second.lgd, "305851");
  assert.equal(second.lat, 12.99658, "the caller keeps its own coordinates");
  assert.equal(second.lng, 77.62031);
  assert.equal(geolocator.kgisTimeoutMs, 3000);
});

// Since 7 Oct 2026 the road class is read from the packaged copy of the state GIS layers
// (highways and panchayats included), so that answer is complete and is stored like a
// live one. Without this every Karnataka lookup asked the geocoder again.
test("an answer from the packaged state GIS layers is stored", async () => {
  let asked = 0;
  const repository = cellStore();
  const packaged = (lat, lng) => ({ ...liveAnswer(lat, lng), source: "kgis_snapshot",
    lookup: { kgis: "snapshot", local: "municipal_polygon", geocoder: "available" } });
  const geolocator = createCachedGeolocator({ repository,
    geolocator: { resolve: async ({ lat, lng }) => { asked += 1; return packaged(lat, lng); } } });
  await geolocator.resolve({ lat: 12.99657, lng: 77.62034 });
  const second = await geolocator.resolve({ lat: 12.99658, lng: 77.62031 });
  assert.equal(asked, 1);
  assert.equal(second.lookup.cache, "hit");
  assert.equal(second.lookup.kgis, "snapshot");
});

test("an answer that came from a caller's hint, a fallback or an outage is never stored", async () => {
  for (const patch of [
    { address_source: "client_hint" },
    { road_ownership: "unknown" },
    { source: "kgis_snapshot", lookup: { kgis: "unavailable", local: "municipal_polygon", geocoder: "available" } },
    { address: null, address_source: "unresolved", lookup: { kgis: "available", geocoder: "unavailable" } },
  ]) {
    const repository = cellStore();
    const geolocator = createCachedGeolocator({ repository,
      geolocator: { resolve: async ({ lat, lng }) => ({ ...liveAnswer(lat, lng), ...patch }) } });
    await geolocator.resolve({ lat: 12.9, lng: 77.6, addressHint: "Somebody's Lane" });
    assert.equal(repository.cells.size, 0, JSON.stringify(patch));
  }
});

test("a stored answer past its week is asked again, and a broken store never fails a lookup", async () => {
  let asked = 0;
  let now = 1_000_000;
  const repository = cellStore();
  const inner = { resolve: async ({ lat, lng }) => { asked += 1; return liveAnswer(lat, lng); } };
  const geolocator = createCachedGeolocator({ repository, geolocator: inner, now: () => now });
  await geolocator.resolve({ lat: 12.9, lng: 77.6 });
  now += 8 * 86_400_000;
  await geolocator.resolve({ lat: 12.9, lng: 77.6 });
  assert.equal(asked, 2);
  const broken = createCachedGeolocator({ geolocator: inner, repository: {
    async getGeoCell() { throw new Error("throttled"); }, async putGeoCell() { throw new Error("throttled"); } } });
  const result = await broken.resolve({ lat: 13.0, lng: 77.5 });
  assert.equal(result.lgd, "305851");
});

// A tender lookup for a place the service already knows took 90 to 145 ms, and almost
// none of it was the lookup: it was seven database round trips of bookkeeping (read the
// install, claim an idempotency key, claim the signature, touch last-seen, complete the
// key, count the request) around a millisecond of matching.

function counted(repository) {
  const counts = {};
  for (const name of ["getInstallation", "touchInstallation", "claimIdempotency", "claimReplay",
    "completeIdempotency", "queryTenders"]) {
    const original = repository[name] || (async () => []);
    counts[name] = 0;
    repository[name] = async (...args) => { counts[name] += 1; return original.apply(repository, args); };
  }
  return counts;
}
const municipal = { async resolve({ lat, lng }) {
  return { lat, lng, road_ownership: "municipal", source: "kgis", lgd: "1", town: "T",
    address: "Rose Road, T", address_source: "operator_geocoder", lookup: { kgis: "available" } };
} };

test("a tender lookup is a read: no idempotency claim, no replay claim, and it can be repeated", async () => {
  const repository = memoryRepository();
  const counts = counted(repository);
  const h = await harness({ repository, geolocator: municipal });
  const first = await h.post("/v1/tenders/resolve", { lat: 12.9, lng: 77.6 }, { key: "same-key" });
  const second = await h.post("/v1/tenders/resolve", { lat: 12.9, lng: 77.6 }, { key: "same-key" });
  assert.equal(first.statusCode, 200, first.body);
  assert.equal(second.statusCode, 200, second.body);
  assert.equal(JSON.parse(second.body).idempotent_replay, undefined);
  assert.equal(counts.claimIdempotency, 0);
  assert.equal(counts.claimReplay, 0);
  assert.equal(counts.completeIdempotency, 0);
});

test("a write still claims its idempotency key and its signature", async () => {
  const repository = memoryRepository();
  const counts = counted(repository);
  const h = await harness({ repository, geolocator: municipal });
  await h.post("/v1/feedback", { rating: 5, text: "ok", test_mode: "walk" });
  assert.equal(counts.claimIdempotency, 1);
  assert.equal(counts.claimReplay, 1);
});

test("an install's key is read once and its last-seen written once, not on every request", async () => {
  const repository = memoryRepository();
  const counts = counted(repository);
  const h = await harness({ repository, geolocator: municipal });
  for (let i = 0; i < 5; i += 1) await h.post("/v1/tenders/resolve", { lat: 12.9, lng: 77.6 + i / 100 });
  assert.equal(counts.getInstallation, 1);
  assert.equal(counts.touchInstallation, 1);
  assert.equal(counts.queryTenders, 1, "one body's tenders are read once");
});

test("an unknown install is not remembered as unknown", async () => {
  const repository = memoryRepository();
  repository.queryTenders = async () => [];
  const h = await harness({ repository, geolocator: municipal });
  const real = repository.getInstallation.bind(repository);
  let hidden = true;
  repository.getInstallation = async (id) => (hidden ? null : real(id));
  const refused = await h.post("/v1/tenders/resolve", { lat: 12.9, lng: 77.6 });
  assert.equal(refused.statusCode, 401);
  hidden = false;
  const accepted = await h.post("/v1/tenders/resolve", { lat: 12.9, lng: 77.6 });
  assert.equal(accepted.statusCode, 200, accepted.body);
});

test("a revoked install is refused once its remembered key expires", async () => {
  const repository = memoryRepository();
  repository.queryTenders = async () => [];
  let now = 1_800_000_000_000;
  const h = await harness({ repository, geolocator: municipal, now: () => now });
  const ok = await h.post("/v1/tenders/resolve", { lat: 12.9, lng: 77.6 }, { sentAt: now });
  assert.equal(ok.statusCode, 200, ok.body);
  const stored = await repository.getInstallation(h.installId);
  await repository.registerInstallation({ ...stored, revoked_at: now });
  now += 6 * 60_000;
  const refused = await h.post("/v1/tenders/resolve", { lat: 12.9, lng: 77.6 }, { sentAt: now });
  assert.equal(refused.statusCode, 401);
});

// The map said its answer was good for 30 seconds and then made the next caller wait
// 60 ms for a fresh read. It now answers from memory at once and refreshes behind.
test("an expired map answers from memory at once and refreshes behind the answer", async () => {
  let now = 1_800_000_000_000;
  let reads = 0;
  let release;
  const repository = memoryRepository();
  repository.listPotholes = async () => {
    reads += 1;
    if (reads > 1) await new Promise((resolve) => { release = resolve; });
    return [{ id: reads, lat: 12.9, lng: 77.6, damage_type: "pothole_cavity", size: "medium",
      first_seen_at: 1, last_seen_at: 1, complaint_count: 1, observation_count: 1, seen_count: 1 }];
  };
  const h = await harness({ repository, now: () => now });
  const first = JSON.parse((await get(h, "/v1/map")).body);
  assert.equal(first.features[0].properties.id, 1);
  now += 31_000;
  const stale = JSON.parse((await get(h, "/v1/map")).body);
  assert.equal(stale.features[0].properties.id, 1, "answered without waiting for the refresh");
  assert.equal(reads, 2, "and the refresh was started");
  release();
  await new Promise((resolve) => setTimeout(resolve, 5));
  const fresh = JSON.parse((await get(h, "/v1/map")).body);
  assert.equal(fresh.features[0].properties.id, 2);
  now += 10 * 60_000;
  release = () => {};
  const blocked = get(h, "/v1/map");
  await new Promise((resolve) => setTimeout(resolve, 5));
  release();
  assert.equal(JSON.parse((await blocked).body).features[0].properties.id, 3,
    "rows far past their freshness are not served");
});

test("a location answered once is remembered in the function, without asking the store again", async () => {
  let gets = 0;
  const repository = cellStore();
  const getGeoCell = repository.getGeoCell;
  repository.getGeoCell = async (cell) => { gets += 1; return getGeoCell(cell); };
  const geolocator = createCachedGeolocator({ repository,
    geolocator: { resolve: async ({ lat, lng }) => liveAnswer(lat, lng) } });
  await geolocator.resolve({ lat: 12.99657, lng: 77.62034 });
  const second = await geolocator.resolve({ lat: 12.99657, lng: 77.62034 });
  const third = await geolocator.resolve({ lat: 12.99657, lng: 77.62034 });
  assert.equal(gets, 1);
  assert.equal(second.lookup.cache, "hit");
  assert.equal(third.lgd, "305851");
});

// Matching a location's tenders is the same work every time for the same place. The
// finished answer is kept for ten minutes per 11 m cell, so a repeat lookup is one map
// read: no tender scan, no catalogue match, no ward match.
test("the finished answer for a place is computed once and repeated from memory", async () => {
  const repository = memoryRepository();
  repository.queryTenders = async () => [{ tender_number: "T/1", title: "Resurfacing of Rose Road in T", location: "T" }];
  let matches = 0;
  const catalogue = { async match() { matches += 1; return { tender: null, reason: "no_location_match", catalogue: null }; } };
  const where = { async resolve({ lat, lng }) {
    return { lat, lng, road_ownership: "municipal", source: "kgis", lgd: "1", town: "T", state_code: "KA",
      address: "Lily Lane, T", address_source: "operator_geocoder", lookup: { kgis: "available" } };
  } };
  const h = await harness({ repository, geolocator: where, catalogue });
  const first = JSON.parse((await h.post("/v1/tenders/resolve", { lat: 12.90001, lng: 77.60001 })).body);
  const second = JSON.parse((await h.post("/v1/tenders/resolve", { lat: 12.90002, lng: 77.60002 })).body);
  assert.equal(matches, 1);
  assert.equal(second.reason, first.reason);
  assert.equal(second.jurisdiction.lat, 12.90002, "the caller keeps its own coordinates");
  const elsewhere = JSON.parse((await h.post("/v1/tenders/resolve", { lat: 12.95, lng: 77.65 })).body);
  assert.equal(matches, 2, "another place is matched afresh");
  assert.equal(elsewhere.jurisdiction.lat, 12.95);
  const lines = h.lines.log.map((entry) => JSON.parse(entry)).filter((entry) => entry.route === "/v1/tenders/resolve");
  assert.equal(lines[1].answer_cache, "hit");
  assert.equal(lines[0].answer_cache, "miss");
});

test("a different address in the same cell is not given the first caller's answer", async () => {
  const repository = memoryRepository();
  repository.queryTenders = async () => [];
  let matches = 0;
  const catalogue = { async match() { matches += 1; return { tender: null, reason: "no_location_match", catalogue: null }; } };
  const hinted = { async resolve({ lat, lng, addressHint }) {
    return { lat, lng, road_ownership: "outside_state", source: "unresolved", state_code: "MH",
      address: addressHint, address_source: "client_hint", lookup: { kgis: "out_of_scope" } };
  } };
  const h = await harness({ repository, geolocator: hinted, catalogue });
  await h.post("/v1/tenders/resolve", { lat: 18.52, lng: 73.86, address_hint: "FC Road, Pune" });
  await h.post("/v1/tenders/resolve", { lat: 18.52, lng: 73.86, address_hint: "JM Road, Pune" });
  assert.equal(matches, 2);
});

// Outside Karnataka a lookup now also places the point in a ward and reads the State's
// road notices for that ward's body. Measured on 7 Oct 2026 with Gujarat's real pack (307
// notices): the first Ahmedabad lookup of a process took 33 ms (it reads the ward file and
// files every notice under its body), and each later one in a new place about 0.3 ms more
// than the 7 ms the street matcher already took. The work that could repeat does not:
// the ward file is read once, and a pack's notices are filed under their bodies once.
test("outside Karnataka the ward file and the notices' bodies are worked out once, and a lookup stays cheap", async () => {
  const clock = Date.parse("2026-10-07T06:00:00Z");
  let chainsRead = 0;
  const notices = Array.from({ length: 300 }, (_, n) => ({
    award_verified: false, closing_at: "2026-10-16T18:00:00+05:30", dlp_verified: false, lifecycle: "procurement_notice",
    get organisation_chain() { chainsRead += 1; return n % 2 ? "AMC-Engineering Department - South Zone" : "R&B-Division Office - Ahmedabad"; },
    published_at: null, scope: "road_surface", segment_verified: false, source_id: "portal", tender_id: String(n),
    tender_reference: `ref ${n}`, title: `Road resurfacing and milling work at location ${n} in the South Zone Lambha Ward.`,
  }));
  const pack = { notices, sources: [] };
  let loads = 0;
  const catalogue = {
    async match() { return { tender: null, reason: "no_location_match", catalogue: null }; },
    async load() { loads += 1; return { pack, resource: {} }; },
  };
  const geolocator = createGeolocator({ geocoderUrl: "https://geocoder.test/reverse", logger: { error() {}, log() {} },
    fetchImpl: async () => new Response(JSON.stringify({ address: { road: "Narol Road", city: "Ahmedabad", state: "Gujarat", country_code: "in" } })) });
  const h = await harness({ geolocator, catalogue, now: () => clock });
  // Three real points inside Lambha ward, and others stepped 22 m apart from the first.
  const resolve = async (lat, lng) => {
    const started = performance.now();
    const result = await h.post("/v1/tenders/resolve", { lat, lng }, { sentAt: clock });
    assert.equal(result.statusCode, 200, result.body);
    return { ms: performance.now() - started, body: JSON.parse(result.body) };
  };
  const first = await resolve(22.95558, 72.53967);
  assert.equal(first.body.jurisdiction.ward_name, "LAMBHA");
  assert.equal(first.body.ward_tenders.length, 5);
  assert.equal(first.body.jurisdiction.urban_body.road_notices, 150);
  assert.ok(first.ms < 150, `the first lookup took ${first.ms.toFixed(1)} ms`);
  const filedOnce = chainsRead;
  let total = 0;
  for (let n = 1; n <= 40; n += 1) {
    const next = await resolve(22.95558 + n * 0.0002, 72.53967 + n * 0.0001);
    assert.equal(next.body.jurisdiction.ward_name, "LAMBHA");
    assert.equal(next.body.ward_tenders.length, 5);
    total += next.ms;
  }
  assert.equal(loads, 41, "each new place asks the catalogue for the pack, which the catalogue keeps");
  assert.equal(chainsRead - filedOnce, 40 * 5, "only the five notices answered are read again, for their `location`");
  assert.ok(total / 40 < 5, `a later lookup took ${(total / 40).toFixed(2)} ms on average`);
  const again = await resolve(22.95558, 72.53967);
  assert.equal(loads, 41, "the same place again is answered from memory: no pack, no match");
  assert.deepEqual(again.body.ward_tenders, first.body.ward_tenders);
});
