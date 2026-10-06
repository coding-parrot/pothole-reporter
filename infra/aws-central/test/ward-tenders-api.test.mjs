import assert from "node:assert/strict";
import test from "node:test";

import { DETECT_PROMPT_VERSION, DETECT_SCHEMA_VERSION } from "../../../llm/generated/contract.mjs";
import { createGeolocator } from "../service/geolocation.mjs";
import { matchTender } from "../service/tenders.mjs";
import { loadBodyTenders } from "../tools/ward-tender-vocabulary.mjs";
import { harness, memoryRepository } from "./support.mjs";
import { CASES, GANDHI_NAGAR } from "./ward-tender-cases.mjs";

// /v1/tenders/resolve and the routing object of /v1/potholes/report carry ward_tenders
// beside `tender`: the same three real points as ward-tenders.test.mjs, through the whole
// service, with KGIS down so the town and the ward both come from the packaged snapshots.

const BENGALURU = loadBodyTenders("BLR").rows;
const quiet = { error() {}, log() {} };
const titles = (found) => found.map((tender) => tender.title);

function bengaluruService({ addresses = CASES } = {}) {
  const byPoint = new Map(Object.values(addresses).map((point) => [`${point.lat},${point.lng}`, point.address]));
  const geolocator = createGeolocator({
    geocoderUrl: "https://geocoder.test/reverse",
    // KGIS down: the town and the ward both come from the packaged snapshots.
    fetchImpl: async (input) => {
      const url = new URL(input);
      if (url.hostname !== "geocoder.test") throw new Error("KGIS is down");
      const address = byPoint.get(`${url.searchParams.get("lat")},${url.searchParams.get("lon")}`);
      return new Response(JSON.stringify(address ? { address } : { error: "Unable to geocode" }));
    },
    logger: quiet,
  });
  const repository = memoryRepository();
  const asked = [];
  repository.queryTenders = async (lgd) => { asked.push(lgd); return /^30585[0-4]$/.test(lgd) ? BENGALURU : []; };
  return harness({ geolocator, repository }).then((h) => ({ ...h, asked }));
}
const lastRequest = (h) => JSON.parse(h.lines.log.findLast((line) => line.includes('"http_request"')));

for (const [name, point] of Object.entries(CASES)) {
  test(`POST /v1/tenders/resolve, ${name}: ward_tenders carries the real tenders and tender stays null`, async () => {
    const h = await bengaluruService();
    const result = await h.post("/v1/tenders/resolve", { lat: point.lat, lng: point.lng });
    assert.equal(result.statusCode, 200, result.body);
    const body = JSON.parse(result.body);
    assert.equal(body.jurisdiction.ward_name, point.ward);
    assert.equal(body.jurisdiction.ward_no, point.ward_no);
    assert.equal(body.jurisdiction.ward_numbering, "kgis_current");
    assert.equal(body.jurisdiction.address, point.street);
    assert.equal(body.tender, null);
    assert.equal(body.reason, matchTender(point.street, BENGALURU).reason, "the street matcher's own reason");
    assert.equal(body.catalogue, null);
    assert.ok(Array.isArray(body.ward_tenders));
    for (const title of point.expected) assert.ok(titles(body.ward_tenders).includes(title), title);
    assert.ok(body.ward_tenders.every((entry) => entry.scope === "ward"));
    const logged = lastRequest(h);
    assert.equal(logged.outcome, body.reason);
    assert.equal(logged.ward_lookup, "resolved");
    assert.equal(logged.ward_tender_count, body.ward_tenders.length);
    assert.equal(logged.tender_catalogue, null);
  });
}

test("POST /v1/tenders/resolve, Gandhi Nagar in Munnekolala: only Munnekolala's tenders are answered", async () => {
  const h = await bengaluruService({ addresses: { gandhiNagar: GANDHI_NAGAR } });
  const result = await h.post("/v1/tenders/resolve", { lat: GANDHI_NAGAR.lat, lng: GANDHI_NAGAR.lng });
  assert.equal(result.statusCode, 200, result.body);
  const body = JSON.parse(result.body);
  assert.equal(body.jurisdiction.address, GANDHI_NAGAR.street);
  assert.equal(body.jurisdiction.ward_name, "Munnenkolalu");
  assert.deepEqual(body.jurisdiction.address_parts.localities, ["Gandhi Nagar", "Munnenkolalu"]);
  assert.deepEqual(titles(body.ward_tenders).sort(), [...GANDHI_NAGAR.expected].sort());
  assert.ok(!body.ward_tenders.some((entry) => /gandhi/i.test(entry.title)));
  assert.equal(lastRequest(h).ward_tender_count, 2);
});

test("POST /v1/tenders/resolve: a ward with no tender answers an empty ward_tenders", async () => {
  const agaram = { lat: 12.97298, lng: 77.62247, address: { road: "Artillery Road", neighbourhood: "Gowthamapura", suburb: "Agaram", city: "Bengaluru", state: "Karnataka" } };
  const h = await bengaluruService({ addresses: { agaram } });
  const body = JSON.parse((await h.post("/v1/tenders/resolve", { lat: agaram.lat, lng: agaram.lng })).body);
  assert.equal(body.jurisdiction.ward_name, "Agaram");
  assert.deepEqual(body.ward_tenders, []);
  assert.equal(lastRequest(h).ward_lookup, "resolved");
  assert.equal(lastRequest(h).ward_tender_count, 0);
});

test("POST /v1/tenders/resolve: a point outside Karnataka answers an empty ward_tenders", async () => {
  const pune = { lat: 18.52, lng: 73.86, address: { road: "Shivneri Nagar DP Road", suburb: "Kondhwa Khurd", city: "Pune", state: "Maharashtra" } };
  const h = await bengaluruService({ addresses: { pune } });
  const result = await h.post("/v1/tenders/resolve", { lat: pune.lat, lng: pune.lng });
  assert.equal(result.statusCode, 200, result.body);
  const body = JSON.parse(result.body);
  assert.equal(body.jurisdiction.road_ownership, "outside_state");
  assert.equal(body.jurisdiction.ward_name, null);
  assert.deepEqual(body.ward_tenders, []);
  assert.equal(body.tender, null);
  assert.equal(body.reason, "outside_state");
  assert.deepEqual(h.asked, [], "no town index was read");
  assert.equal(lastRequest(h).ward_lookup, "out_of_scope");
  assert.equal(lastRequest(h).ward_tender_count, 0);
});

test("POST /v1/tenders/resolve: the street-level tender is not repeated among the ward tenders", async () => {
  const point = CASES.coxTown;
  const h = await bengaluruService();
  const street = { tender_number: "BBMP/STREET/1", title: "Asphalting of Thambhuchetty Road in Cox Town", location: "BBMP", published: "01-01-2026" };
  h.repository.queryTenders = async () => [street, ...BENGALURU];
  const body = JSON.parse((await h.post("/v1/tenders/resolve", { lat: point.lat, lng: point.lng })).body);
  assert.equal(body.tender?.tender_number, "BBMP/STREET/1");
  assert.equal(body.catalogue, "ka_index");
  assert.equal(body.reason, null);
  assert.ok(body.ward_tenders.length > 0);
  assert.ok(!body.ward_tenders.some((entry) => entry.tender_number === "BBMP/STREET/1"));
  assert.equal(lastRequest(h).outcome, "tender_matched");
  assert.equal(lastRequest(h).ward_tender_count, body.ward_tenders.length);
});

test("POST /v1/potholes/report: routing carries the same ward_tenders", async () => {
  const point = CASES.munnekolala;
  const h = await bengaluruService();
  const stored = [];
  Object.assign(h.repository, {
    async acquireLocationLocks() { return true; },
    async releaseLocationLocks() {},
    async findNearby() { return []; },
    async createPothole(candidate) { stored.push(candidate); return true; },
    async attachObservation() { return { newObserver: true }; },
    async getPothole() { return stored[0]; },
    async recordReport() {},
  });
  const result = await h.post("/v1/potholes/report", {
    lat: point.lat, lng: point.lng, client_observation_id: "ward-tenders-1", observed_at: Date.now(),
    damage_type: "pothole_cavity", size: null, image_hash: "a".repeat(64), capture_source: "manual",
    location_source: "device_gps",
    detector: { provider: "own_key", prompt_version: DETECT_PROMPT_VERSION, schema_version: DETECT_SCHEMA_VERSION },
  });
  assert.ok([200, 201].includes(result.statusCode), result.body);
  const body = JSON.parse(result.body);
  assert.equal(body.routing.tender, null);
  assert.equal(body.routing.jurisdiction.ward_name, "Munnenkolalu");
  for (const title of point.expected) assert.ok(titles(body.routing.ward_tenders).includes(title), title);
  assert.equal(lastRequest(h).ward_lookup, "resolved");
  assert.equal(lastRequest(h).ward_tender_count, body.routing.ward_tenders.length);
});
