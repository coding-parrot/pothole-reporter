import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { createGeolocator } from "../service/geolocation.mjs";
import {
  INDIA_WARD_CANARY, INDIA_WARD_QUERY, ROAD_LAYER_QUERY, WARD_TENDER_QUERY, judgeIndiaWardCanary, judgeIndiaWardSnapshots,
  judgeRoadLayers, judgeWardSnapshot, judgeWardTenders,
} from "../service/health/rules.mjs";
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

// ----------------------------------------------------------------------------------------
// Outside Karnataka a ward comes from the snapshots data/wards/runtime.json switches on,
// and deploy.sh stages that list and those files (tools/stage-india-wards.mjs). A package
// without one of them cannot name a ward there, and without a rule nobody would know: the
// lookup still answers, with no ward and no ward tenders.

test("a switched-on ward snapshot missing from the package is said out loud, by name", () => {
  const missing = judgeIndiaWardSnapshots([
    { n: "12", ward_lookup: "unavailable", ward_snapshot: "GJ/ahmedabad" },
    { n: "3", ward_lookup: "resolved", ward_snapshot: "MP/bhopal" },
    { n: "40", ward_lookup: "out_of_scope" },
  ]);
  assert.equal(missing.broken, true);
  assert.equal(missing.unavailable, 12);
  assert.match(missing.detail, /12 lookups outside Karnataka/);
  assert.match(missing.detail, /GJ\/ahmedabad \(12\)/);
  // No snapshot named: the list itself could not be read.
  const noList = judgeIndiaWardSnapshots([{ n: "55", ward_lookup: "unavailable" }]);
  assert.equal(noList.broken, true);
  assert.match(noList.detail, /data\/wards\/runtime\.json \(55\)/);
  const whole = judgeIndiaWardSnapshots([
    { n: "9", ward_lookup: "resolved", ward_snapshot: "GJ/ahmedabad" }, { n: "2", ward_lookup: "resolved_unnamed", ward_snapshot: "MP/bhopal" },
    { n: "1", ward_lookup: "between_wards", ward_snapshot: "GJ/ahmedabad" }, { n: "300", ward_lookup: "out_of_scope" },
    // Lines from before this release carry no ward_snapshot and say out_of_scope.
    { n: "500" },
  ]);
  assert.equal(whole.broken, false);
  assert.match(whole.detail, /11 placed in a ward \(GJ\/ahmedabad 9, MP\/bhopal 2\)/);
  assert.equal(judgeIndiaWardSnapshots([]).broken, false);
  for (const field of ["ward_lookup", "ward_snapshot", "road_ownership", "outside_state", "route"]) assert.ok(INDIA_WARD_QUERY.includes(field), field);
});

test("a service packaged without a switched-on snapshot, or without the list, logs what the rule reads", async () => {
  const runtime = JSON.parse(readFileSync(new URL("../../../data/wards/runtime.json", import.meta.url)));
  const withoutAhmedabad = mkdtempSync(path.join(os.tmpdir(), "india-wards-"));
  writeFileSync(path.join(withoutAhmedabad, "runtime.json"), JSON.stringify(runtime));
  for (const [dir, snapshot] of [[withoutAhmedabad, "GJ/ahmedabad"], [mkdtempSync(path.join(os.tmpdir(), "india-wards-")), null]]) {
    const geolocator = createGeolocator({ indiaWardsDir: dir, logger: { error() {}, log() {} },
      fetchImpl: async () => { throw new Error("no network in this test"); } });
    const h = await harness({ geolocator });
    const result = await h.post("/v1/tenders/resolve", { lat: INDIA_WARD_CANARY.lat, lng: INDIA_WARD_CANARY.lng, address_hint: INDIA_WARD_CANARY.hint });
    assert.equal(result.statusCode, 200, result.body);
    const body = JSON.parse(result.body);
    assert.equal(body.jurisdiction.ward_name, null);
    assert.deepEqual(body.ward_tenders, []);
    const logged = JSON.parse(h.lines.log.findLast((line) => line.includes('"http_request"')));
    assert.equal(logged.road_ownership, "outside_state");
    assert.equal(logged.ward_lookup, "unavailable");
    assert.equal(logged.ward_snapshot, snapshot);
    const verdict = judgeIndiaWardSnapshots([{ n: "1", ward_lookup: logged.ward_lookup, ward_snapshot: logged.ward_snapshot || undefined }]);
    assert.equal(verdict.broken, true);
    // And the canary says the same about the answer itself.
    assert.equal(judgeIndiaWardCanary({ status: 200, body }).state, "fail");
  }
});

// The canary asks the live service about one real point, 760 m inside Shahibag ward of
// Ahmedabad. The ward must be answered by name. Its tenders are another matter: notices
// close every week, so an empty list is a failure only where the caller knows the
// catalogue it deployed has an open notice for that ward.
test("the Ahmedabad canary point is deep inside the ward it names, in the committed file", async () => {
  const geolocator = createGeolocator({ logger: { error() {}, log() {} }, fetchImpl: async () => { throw new Error("no network in this test"); } });
  const answer = await geolocator.resolve({ lat: INDIA_WARD_CANARY.lat, lng: INDIA_WARD_CANARY.lng });
  assert.equal(answer.ward_name, INDIA_WARD_CANARY.ward);
  assert.equal(answer.lookup.ward_snapshot, INDIA_WARD_CANARY.snapshot);
  // Half a kilometre in every direction is still the ward: a phone's fix cannot leave it.
  for (const [dLat, dLng] of [[0.0045, 0], [-0.0045, 0], [0, 0.0049], [0, -0.0049]]) {
    const near = await geolocator.resolve({ lat: INDIA_WARD_CANARY.lat + dLat, lng: INDIA_WARD_CANARY.lng + dLng });
    assert.equal(near.ward_name, INDIA_WARD_CANARY.ward, `${dLat},${dLng}`);
  }
});

test("the canary: the ward by name is required, its tenders only where they are known to exist", () => {
  const jurisdiction = (patch = {}) => ({
    road_ownership: "outside_state", ward_name: "SHAHIBAG", ward_no: "16",
    lookup: { ward: "resolved", ward_snapshot: "GJ/ahmedabad" },
    urban_body: { name: "Ahmedabad Municipal Corporation", road_notices: 46, road_notices_open: 46 }, ...patch,
  });
  const title = "In Shahibaug Ward of Central Zone, Regarding the Road Departments work arrangements are being made for the supply of Wetmix to fill potholes and carry out patchwork repairs.(ARC Tender)";
  const answered = judgeIndiaWardCanary({ status: 200, body: { jurisdiction: jurisdiction(), ward_tenders: [{ tender_number: "T 149 [346558]", title }] } });
  assert.equal(answered.state, "ok");
  assert.match(answered.detail, /SHAHIBAG/);
  assert.match(answered.detail, /In Shahibaug Ward of Central Zone/, "the first title is printed");
  // A title that does not say the ward is exactly the wrong tender the rule exists for.
  const wrong = judgeIndiaWardCanary({ status: 200, body: { jurisdiction: jurisdiction(), ward_tenders: [{ tender_number: "T", title: "Resurfacing of roads in Vatva Ward of the South Zone" }] } });
  assert.equal(wrong.state, "fail");
  assert.match(wrong.detail, /does not say/);
  // No ward, another ward, another snapshot, no answer: all failures.
  for (const body of [
    { jurisdiction: jurisdiction({ ward_name: null, lookup: { ward: "out_of_scope" } }), ward_tenders: [] },
    { jurisdiction: jurisdiction({ ward_name: "SHAHPUR" }), ward_tenders: [] },
    { jurisdiction: jurisdiction({ lookup: { ward: "unavailable", ward_snapshot: "GJ/ahmedabad" }, ward_name: null }), ward_tenders: [] },
    { jurisdiction: jurisdiction({ road_ownership: "unknown" }), ward_tenders: [] },
  ]) assert.equal(judgeIndiaWardCanary({ status: 200, body }).state, "fail", JSON.stringify(body.jurisdiction.lookup));
  assert.equal(judgeIndiaWardCanary({ status: 503, body: { error: "x" } }).state, "fail");
  // A service from before this release says nothing of an urban body: not judged.
  const before = { jurisdiction: { road_ownership: "outside_state", ward_name: null, lookup: { ward: "out_of_scope" } }, ward_tenders: [] };
  assert.equal(judgeIndiaWardCanary({ status: 200, body: before }).state, "skip");
  assert.match(judgeIndiaWardCanary({ status: 200, body: before }).detail, /before/);
  // The ward answered and no tender: skipped with the reason, unless the deployed
  // catalogue is this checkout's and holds open notices for the ward.
  const empty = { jurisdiction: jurisdiction(), ward_tenders: [] };
  const unknown = judgeIndiaWardCanary({ status: 200, body: empty });
  assert.equal(unknown.state, "skip");
  assert.match(unknown.detail, /SHAHIBAG.*notices close every week/);
  const none = judgeIndiaWardCanary({ status: 200, body: empty, expectedOpen: 0 });
  assert.equal(none.state, "skip");
  assert.match(none.detail, /no open notice for SHAHIBAG/);
  const expired = judgeIndiaWardCanary({ status: 200, body: { jurisdiction: jurisdiction({ urban_body: { name: "Ahmedabad Municipal Corporation", road_notices: 0, road_notices_open: 0 } }), ward_tenders: [] }, expectedOpen: 10 });
  assert.equal(expired.state, "skip");
  assert.match(expired.detail, /holds no open road notice of Ahmedabad Municipal Corporation/);
  const lost = judgeIndiaWardCanary({ status: 200, body: empty, expectedOpen: 10 });
  assert.equal(lost.state, "fail");
  assert.match(lost.detail, /10 open notices/);
});

test("the service's own answer for the canary point passes the canary, tenders and all", async () => {
  const clock = Date.parse("2026-10-07T06:00:00Z");
  const titles = [
    "In Shahibaug Ward of Central Zone, Regarding the Road Departments work arrangements are being made for the supply of Wetmix to fill potholes and carry out patchwork repairs.(ARC Tender)",
    "Repairing of Road Potholes using Cold mix Injection potholes patching repairing machine in civil hospital road, mohan cinema road, badiya limdi road, fsl road, ghoda camp road and other different present main road in shahibaug ward of Central Zone of Ahmedabad Municipal Corporation)",
    "Repairing of Road Potholes using Cold mix Injection potholes patching repairing machine in Diff. road in Khadia ward of Central Zone of Ahmedabad Municipal Corporation)",
  ];
  const notices = titles.map((title, n) => ({
    award_verified: false, closing_at: "2026-10-16T18:00:00+05:30", dlp_verified: false, lifecycle: "procurement_notice",
    organisation_chain: "AMC-Central Zone", published_at: null, scope: "road_surface", segment_verified: false,
    source_id: "portal", tender_id: String(346_558 + n), tender_reference: `Tender No.${149 + n}`, title,
  }));
  const catalogue = {
    async match() { return { tender: null, reason: "no_location_match", catalogue: null }; },
    async load() { return { pack: { notices, sources: [] }, resource: {} }; },
  };
  const geolocator = createGeolocator({ logger: { error() {}, log() {} }, fetchImpl: async () => { throw new Error("no network in this test"); } });
  const h = await harness({ geolocator, catalogue, now: () => clock });
  const result = await h.post("/v1/tenders/resolve",
    { lat: INDIA_WARD_CANARY.lat, lng: INDIA_WARD_CANARY.lng, address_hint: INDIA_WARD_CANARY.hint }, { sentAt: clock });
  const body = JSON.parse(result.body);
  assert.equal(body.ward_tenders.length, 2, "Shahibag's two, not Khadia's");
  const verdict = judgeIndiaWardCanary({ status: result.statusCode, body, expectedOpen: 2 });
  assert.equal(verdict.state, "ok", verdict.detail);
  assert.match(verdict.detail, /^SHAHIBAG \(ward 16, GJ\/ahmedabad\); 2 ward tenders, first: /);
});
