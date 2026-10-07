import assert from "node:assert/strict";
import test from "node:test";

import { createGeolocator } from "../service/geolocation.mjs";
import { ROAD_LAYER_QUERY, WARD_TENDER_QUERY, judgeRoadLayers, judgeWardSnapshot, judgeWardTenders } from "../tools/health-rules.mjs";
import { loadBodyTenders } from "../tools/ward-tender-vocabulary.mjs";
import { harness, memoryRepository } from "./support.mjs";
import { CASES } from "./ward-tender-cases.mjs";

// The production health rule for ward tenders: among Karnataka municipal lookups whose
// ward was resolved, at least one in five must come back with a ward tender or a street
// tender, once there are 30 to judge. The rule reads the request log, so these tests feed
// it rows made from the service's own log lines, grouped the way the query groups them.

const rows = (...groups) => groups.map(([n, wardTenderCount, catalogue, wardLookup = "resolved"]) => ({
  n: String(n), ward_lookup: wardLookup || undefined,
  ward_tender_count: wardTenderCount === null ? undefined : String(wardTenderCount),
  tender_catalogue: catalogue || undefined,
}));

test("fewer than 30 resolved wards is too few to judge", () => {
  const verdict = judgeWardTenders(rows([29, 0]));
  assert.equal(verdict.broken, false);
  assert.match(verdict.detail, /29 .*too few/);
  assert.equal(judgeWardTenders([]).broken, false);
});

test("30 resolved wards and under a fifth answered breaks the rule", () => {
  const verdict = judgeWardTenders(rows([25, 0], [5, 3]));
  assert.equal(verdict.broken, true, "5 of 30 is 16.7%");
  assert.match(verdict.detail, /5 of 30/);
  assert.equal(judgeWardTenders(rows([30, 0])).broken, true);
  // A lookup whose routing failed logs no count at all. It answered nothing.
  assert.equal(judgeWardTenders(rows([30, null])).broken, true);
});

test("a fifth answered holds the rule, and a street tender counts", () => {
  assert.equal(judgeWardTenders(rows([24, 0], [6, 2])).broken, false, "6 of 30 is 20%");
  assert.equal(judgeWardTenders(rows([24, 0], [3, 1], [3, 0, "ka_index"])).broken, false);
  assert.equal(judgeWardTenders(rows([24, 0], [6, 0, "ka_index"])).broken, false);
  assert.equal(judgeWardTenders(rows([100, 5])).broken, false);
});

test("only lookups whose ward was resolved are judged", () => {
  // 40 municipal lookups in a town KGIS draws no wards for, 10 in Bengaluru.
  const verdict = judgeWardTenders(rows([40, 0, null, "no_ward"], [10, 0]));
  assert.equal(verdict.resolved, 10);
  assert.equal(verdict.broken, false);
  assert.equal(judgeWardTenders(rows([40, 0, null, "no_ward"], [30, 0])).broken, true);
  // Lines from before the ward release have no ward_lookup and no count.
  assert.equal(judgeWardTenders(rows([500, null, null, null])).resolved, 0);
});

test("a package without the ward snapshot is said out loud", () => {
  const missing = judgeWardSnapshot(rows([12, 0, null, "unavailable"], [3, 0, "ka_index", "unavailable"]));
  assert.equal(missing.broken, true);
  assert.match(missing.detail, /15 municipal lookups/);
  // The tender rule alone would have stayed quiet: nothing was resolved.
  assert.equal(judgeWardTenders(rows([15, 0, null, "unavailable"])).broken, false);
  assert.equal(judgeWardSnapshot(rows([40, 0], [9, 2], [7, 0, null, "no_ward"], [500, null, null, null])).broken, false);
});

test("the query reads the fields the service logs, and the rule counts them rightly", async () => {
  for (const field of ["ward_lookup", "ward_tender_count", "tender_catalogue", "road_ownership", "route"]) {
    assert.ok(WARD_TENDER_QUERY.includes(field), field);
  }
  const tenders = loadBodyTenders("BLR").rows;
  const agaram = { lat: 12.97298, lng: 77.62247, address: { road: "Artillery Road", neighbourhood: "Gowthamapura", suburb: "Agaram", city: "Bengaluru", state: "Karnataka" } };
  const pune = { lat: 18.52, lng: 73.86, address: { road: "Shivneri Nagar DP Road", city: "Pune", state: "Maharashtra" } };
  const points = { ...CASES, agaram, pune };
  const byPoint = new Map(Object.values(points).map((point) => [`${point.lat},${point.lng}`, point.address]));
  const geolocator = createGeolocator({
    geocoderUrl: "https://geocoder.test/reverse",
    fetchImpl: async (input) => {
      const url = new URL(input);
      if (url.hostname !== "geocoder.test") throw new Error("KGIS is down");
      return new Response(JSON.stringify({ address: byPoint.get(`${url.searchParams.get("lat")},${url.searchParams.get("lon")}`) }));
    },
    logger: { error() {}, log() {} },
  });
  const repository = memoryRepository();
  repository.queryTenders = async () => tenders;
  const h = await harness({ geolocator, repository });
  for (const point of Object.values(points)) {
    const result = await h.post("/v1/tenders/resolve", { lat: point.lat, lng: point.lng });
    assert.equal(result.statusCode, 200, result.body);
  }
  // What the query does: keep municipal lookups on the two routes, group by the three
  // fields the rules read.
  const lines = h.lines.log.map((line) => JSON.parse(line)).filter((line) => line.event === "http_request"
    && line.road_ownership === "municipal"
    && ["/v1/tenders/resolve", "/v1/potholes/report"].includes(line.route));
  assert.equal(lines.length, 4, "three wards with tenders and Agaram; Pune is not a Karnataka town");
  const grouped = new Map();
  for (const line of lines) {
    const key = `${line.ward_lookup}|${line.ward_tender_count}|${line.tender_catalogue}`;
    const group = grouped.get(key) || { n: 0, ward_lookup: line.ward_lookup, ward_tender_count: String(line.ward_tender_count), tender_catalogue: line.tender_catalogue || undefined };
    group.n += 1;
    grouped.set(key, group);
  }
  const verdict = judgeWardTenders([...grouped.values()].map((group) => ({ ...group, n: String(group.n) })), { minimum: 4 });
  assert.equal(verdict.resolved, 4);
  assert.equal(verdict.answered, 3);
  assert.equal(verdict.broken, false);
  assert.equal(judgeWardSnapshot([...grouped.values()]).broken, false);
});

test("a service packaged without the ward bundle logs ward_lookup unavailable", async () => {
  const geolocator = createGeolocator({
    fetchImpl: async () => { throw new Error("KGIS is down"); },
    wardGeometryPath: "/nonexistent/karnataka-ward-geometry.json",
    logger: { error() {}, log() {} },
  });
  const repository = memoryRepository();
  repository.queryTenders = async () => [];
  const h = await harness({ geolocator, repository });
  const result = await h.post("/v1/tenders/resolve", { lat: 12.99657, lng: 77.62034, address_hint: "Thambhuchetty Road" });
  assert.equal(result.statusCode, 200, result.body);
  const logged = JSON.parse(h.lines.log.findLast((line) => line.includes('"http_request"')));
  assert.equal(logged.ward_lookup, "unavailable");
  assert.equal(judgeWardSnapshot([{ n: "1", ward_lookup: logged.ward_lookup }]).broken, true);
});

// Since 7 Oct 2026 the road class comes from data/karnataka-ownership.bin and the state
// GIS is never asked. A package without that file answers every Karnataka point
// "unknown", which the app shows as a report nobody can be sent.
test("a package without the road ownership layers is said out loud", () => {
  const missing = judgeRoadLayers([{ n: "7", local_lookup: "unavailable" }, { n: "40", local_lookup: "municipal_polygon" }]);
  assert.equal(missing.broken, true);
  assert.match(missing.detail, /7 lookups/);
  const whole = judgeRoadLayers([{ n: "40", local_lookup: "municipal_polygon" }, { n: "3", local_lookup: "out_of_scope" },
    { n: "9" }]);
  assert.equal(whole.broken, false);
  assert.match(ROAD_LAYER_QUERY, /local_lookup/);
});

test("a service packaged without the ownership layers logs local_lookup unavailable", async () => {
  const geolocator = createGeolocator({ localGeometryPath: "/nonexistent/karnataka-ownership.bin",
    fetchImpl: async () => { throw new Error("no network in this test"); } });
  const answer = await geolocator.resolve({ lat: 12.99657, lng: 77.62034 });
  assert.equal(answer.road_ownership, "unknown");
  assert.equal(answer.lookup.local, "unavailable");
});
