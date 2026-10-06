import assert from "node:assert/strict";
import test from "node:test";
import { gunzipSync } from "node:zlib";

import { createCachedGeolocator } from "../service/geo-cache.mjs";
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
