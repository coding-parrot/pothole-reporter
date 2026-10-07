import assert from "node:assert/strict";
import test from "node:test";

import { createCachedGeolocator } from "../service/geo-cache.mjs";
import { createGeolocator } from "../service/geolocation.mjs";
import { matchWardTenders } from "../service/ward-tenders.mjs";
import { harness, memoryRepository } from "./support.mjs";

// /v1/tenders/resolve for points outside Karnataka: the ward from a switched-on snapshot,
// ward_tenders from the road notices of that ward's own body, and the body the point is
// in. (/v1/potholes/report answers with the same routing object; report.test.mjs holds
// it to that.) The points are real (inside the
// ward named, from the committed polygons) and so is every title, from the notice packs
// of 5 Oct 2026; the packs themselves are not read, they change every week.

const quiet = { error() {}, log() {} };
const CLOCK = Date.parse("2026-10-07T06:00:00Z");
const LAMBHA = { lat: 22.95558, lng: 72.53967 };
const MANINAGAR = { lat: 23.00028, lng: 72.59613 };
const BHOPAL_47 = { lat: 23.21383, lng: 77.42127 };
const BHOPAL_6 = { lat: 23.27173, lng: 77.36398 };
const GHAZIABAD = { lat: 28.6692, lng: 77.4538 };
const ADDRESSES = new Map([
  [LAMBHA, { road: "Narol Road", suburb: "Lambha", city: "Ahmedabad", state: "Gujarat", "ISO3166-2-lvl4": "IN-GJ", country_code: "in" }],
  [MANINAGAR, { road: "Bhairavnath Road", suburb: "Maninagar", city: "Ahmedabad", state: "Gujarat", country_code: "in" }],
  [BHOPAL_47, { road: "Link Road Number 3", suburb: "Panchsheel Nagar", city: "Bhopal", state: "Madhya Pradesh", country_code: "in" }],
  [BHOPAL_6, { road: "Airport Road", suburb: "Lalghati", city: "Bhopal", state: "Madhya Pradesh", country_code: "in" }],
  [GHAZIABAD, { road: "GT Road", suburb: "Navyug Market", city: "Ghaziabad", state: "Uttar Pradesh", country_code: "in" }],
].map(([point, address]) => [`${point.lat},${point.lng}`, address]));

let serial = 0;
const notice = (chain, title, patch = {}) => {
  serial += 1;
  return {
    award_verified: false, closing_at: "2026-10-16T18:00:00+05:30", dlp_verified: false, lifecycle: "procurement_notice",
    opening_at: null, organisation_chain: chain, published_at: null, record_id: `r:${serial}`, scope: "road_surface",
    segment_verified: false, source_id: "portal", source_url: "https://portal.test/view", tender_id: String(350_000 + serial),
    tender_reference: `ref ${serial}`, title, ...patch,
  };
};
const AMC = "AMC-Engineering Department - SZ - Danilimda Ward";
const BMC = "Directorate Urban Administration and Development||Muncipal Corporations - UAD||Muncipal Corporation-Bhopal - UAD||Civil - MC Bhopal- UAD";
const GNN = "Directorate of Local Bodies UP||Ghaziabad Municipal Corporation";
const TITLES = {
  lambha: [
    "Road resurfacing and milling work at various locations in the South Zone Lambha Ward.",
    "Regarding the work of supplying Coldmix for pothole repair at kamodgam, gyaspurgam and other places as per requirement in Lambha ward of South Zone.",
  ],
  vatva: "Work of supplying supervisors, laborers, and tractor-trolleys for pothole filling, protection, and maintenance activities under monsoon preparation during the rainy season in Gamtal as well as different areas as required and on roads in Vatva Ward of the South Zone.",
  zone: "Construction of New Road, Regrade and Resurface work at different wards of South West Zone of AMC (ARC) (Package-1).",
  ward47: "CONSTRUCTION OF CC ROAD FROM ROHIT KIRANA TO SHOP OF GANGARAM KUSHWAHA AT PANCHSHEEL NAGAR WARD 47 ZONE 06",
  ward6: "CONSTRUCTION OF CC ROAD AT JAIN NAGAR VALLABH NAGAR VIJAY NAGAR VITTHAL NAGAR W-06 Z-20",
};
const PACKS = {
  GJ: [
    ...TITLES.lambha.map((title) => notice(AMC, title)),
    notice(AMC, TITLES.vatva),
    notice("AMC-Bridge Projects", TITLES.zone),
    notice(AMC, "Resurfacing of internal roads in Lambha Ward", { closing_at: "2026-10-06T18:00:00+05:30" }),
    notice("NAGARPALIKA-DHARAMPUR", "Resurfacing of road in Lambha Ward"),
  ],
  MP: [notice(BMC, TITLES.ward47), notice(BMC, TITLES.ward6)],
  UP: [
    notice(GNN, "Road improvement work in Ward 47 Rajnagar"),
    notice(GNN, "Road improvement work in Ward 12 Kavi Nagar", { closing_at: "2026-10-06T18:00:00+05:30" }),
    notice("Chief Engineer Agra Zone PWD Agra UP||AGRA CIRCLE PWD AGRA", "Strengthening of Agra Road"),
  ],
};

// The service as the handler builds it: the real geolocator (packaged polygons, a fixture
// geocoder) behind the week-long location store, and a catalogue that hands over the
// State's road notice pack and matches no street.
async function service({ street = null, load = true } = {}) {
  const geolocator = createGeolocator({
    geocoderUrl: "https://geocoder.test/reverse", logger: quiet,
    fetchImpl: async (input) => {
      const url = new URL(input);
      const address = ADDRESSES.get(`${url.searchParams.get("lat")},${url.searchParams.get("lon")}`);
      return new Response(JSON.stringify(address ? { address } : { error: "Unable to geocode" }));
    },
  });
  const cells = new Map();
  const repository = memoryRepository();
  repository.getGeoCell = async (cell) => cells.get(cell) || null;
  repository.putGeoCell = async (cell, value, expiresAt) => { cells.set(cell, { value, expiresAt }); };
  const loads = [];
  const catalogue = {
    async match() { return street ? { tender: street, reason: null, catalogue: "road_notice" } : { tender: null, reason: "no_location_match", catalogue: null }; },
    ...(load ? { async load(kind, stateCode) {
      loads.push(`${kind}:${stateCode}`);
      return PACKS[stateCode] ? { pack: { notices: PACKS[stateCode], sources: [{ source_id: "portal", source_name: "State portal", source_url: "https://portal.test/" }] }, resource: {} } : null;
    } } : {}),
  };
  const h = await harness({ geolocator: createCachedGeolocator({ geolocator, repository }), repository, catalogue, now: () => CLOCK });
  return { ...h, loads };
}
const resolve = async (h, point) => {
  // The service's clock is fixed at CLOCK, so the request is signed as sent then.
  const result = await h.post("/v1/tenders/resolve", point, { sentAt: CLOCK });
  assert.equal(result.statusCode, 200, result.body);
  return JSON.parse(result.body);
};
const lastRequest = (h) => JSON.parse(h.lines.log.findLast((line) => line.includes('"http_request"')));
const titles = (found) => found.map((tender) => tender.title);

test("Ahmedabad: the ward by name, its own notices in ward_tenders, and the body it is in", async () => {
  const h = await service();
  const body = await resolve(h, LAMBHA);
  assert.equal(body.jurisdiction.road_ownership, "outside_state");
  assert.deepEqual([body.jurisdiction.ward_name, body.jurisdiction.ward_no, body.jurisdiction.ward_code], ["LAMBHA", "46", "GJ-ahmedabad-46"]);
  assert.equal(body.jurisdiction.lookup.ward, "resolved");
  assert.equal(body.jurisdiction.lookup.ward_snapshot, "GJ/ahmedabad");
  assert.equal(body.jurisdiction.lookup.ward_snapshot_dated, "2016-08-12");
  assert.equal(body.tender, null);
  assert.equal(body.reason, "no_location_match", "the street answer is the national matcher's, untouched");
  assert.deepEqual(titles(body.ward_tenders).sort(), [...TITLES.lambha].sort());
  assert.ok(body.ward_tenders.every((entry) => entry.match_basis === "ward name LAMBHA" && entry.scope === "ward"));
  // The app reads these with the reader it uses for a Karnataka ward tender.
  const karnataka = matchWardTenders({ wardName: "Hoodi", tenders: [{ tender_number: "T/1", title: "Improvements to roads in Hoodi ward no.54", location: "Mahadevapura" }] });
  assert.deepEqual(Object.keys(body.ward_tenders[0]), Object.keys(karnataka[0]));
  assert.ok(body.ward_tenders.every((entry) => typeof entry.tender_number === "string" && typeof entry.title === "string"));
  assert.deepEqual(body.jurisdiction.urban_body, {
    name: "Ahmedabad Municipal Corporation", kind: "Municipal Corporation", city: "Ahmedabad", state_code: "GJ",
    basis: "ward_snapshot", road_notices: 5, road_notices_open: 4,
  });
  assert.deepEqual(h.loads, ["road_notice:GJ"]);
  const logged = lastRequest(h);
  assert.equal(logged.road_ownership, "outside_state");
  assert.equal(logged.ward_lookup, "resolved");
  assert.equal(logged.ward_snapshot, "GJ/ahmedabad");
  assert.equal(logged.ward_tender_count, 2);
  assert.equal(logged.urban_body, "Ahmedabad Municipal Corporation");
  assert.equal(logged.urban_body_notices, 5);
  assert.equal(logged.tender_catalogue, null);
});

test("Ahmedabad: a ward none of whose notices is open answers an empty list, never the zone's or the body's", async () => {
  const h = await service();
  const body = await resolve(h, MANINAGAR);
  assert.equal(body.jurisdiction.ward_name, "MANINAGAR");
  assert.deepEqual(body.ward_tenders, []);
  assert.equal(body.jurisdiction.urban_body.road_notices, 5, "the body's notices are counted, and none is offered as the ward's");
  assert.equal(lastRequest(h).ward_tender_count, 0);
});

test("Bhopal: the ward by number, and a zone number is nobody's ward", async () => {
  const h = await service();
  const in47 = await resolve(h, BHOPAL_47);
  assert.equal(in47.jurisdiction.ward_no, "47");
  assert.equal(in47.jurisdiction.ward_numbering, "snapshot_current");
  assert.equal(in47.jurisdiction.lookup.ward_snapshot, "MP/bhopal");
  assert.deepEqual(titles(in47.ward_tenders), [TITLES.ward47]);
  assert.equal(in47.ward_tenders[0].match_basis, "ward number 47");
  // Ward 6: before 7 Oct 2026 the Karnataka reader gave it WARD 47 ZONE 06.
  const in6 = await resolve(h, BHOPAL_6);
  assert.equal(in6.jurisdiction.ward_no, "6");
  assert.deepEqual(titles(in6.ward_tenders), [TITLES.ward6]);
  assert.equal(in6.jurisdiction.urban_body.name, "Bhopal Municipal Corporation");
  assert.equal(lastRequest(h).ward_snapshot, "MP/bhopal");
});

test("a city with no snapshot: no ward, no ward tenders, and the body by the address's city", async () => {
  const h = await service();
  const body = await resolve(h, GHAZIABAD);
  assert.deepEqual([body.jurisdiction.ward_name, body.jurisdiction.ward_code], [null, null]);
  assert.equal(body.jurisdiction.lookup.ward, "out_of_scope");
  assert.equal(body.jurisdiction.lookup.ward_snapshot, undefined);
  assert.deepEqual(body.ward_tenders, [], "Ghaziabad's notices say Ward 47 and nothing says which ward the point is in");
  assert.deepEqual(body.jurisdiction.urban_body, {
    name: "Ghaziabad Municipal Corporation", kind: "Municipal Corporation", city: "Ghaziabad", state_code: "UP",
    basis: "address_city", road_notices: 2, road_notices_open: 1,
  });
  const logged = lastRequest(h);
  assert.equal(logged.ward_lookup, "out_of_scope");
  assert.equal(logged.ward_snapshot, null);
  assert.equal(logged.urban_body, "Ghaziabad Municipal Corporation");
  assert.equal(logged.urban_body_notices, 2);
});

test("the street-level tender is not said twice, and a repeat lookup is answered from memory with the body kept", async () => {
  const street = { tender_number: `ref 1 [${PACKS.GJ[0].tender_id}]`, title: TITLES.lambha[0] };
  const h = await service({ street });
  const first = await resolve(h, LAMBHA);
  assert.equal(first.tender.title, TITLES.lambha[0]);
  assert.deepEqual(titles(first.ward_tenders), [TITLES.lambha[1]]);
  const again = await resolve(h, { lat: LAMBHA.lat + 0.00001, lng: LAMBHA.lng });
  assert.equal(lastRequest(h).answer_cache, "hit");
  assert.deepEqual(again.ward_tenders, first.ward_tenders);
  assert.deepEqual(again.jurisdiction.urban_body, first.jurisdiction.urban_body);
  assert.equal(again.jurisdiction.lat, LAMBHA.lat + 0.00001, "the caller keeps its own coordinates");
  assert.equal(lastRequest(h).urban_body, "Ahmedabad Municipal Corporation");
  assert.equal(lastRequest(h).ward_tender_count, 1);
  assert.deepEqual(h.loads, ["road_notice:GJ"], "the pack was asked for once");
});

test("a catalogue that cannot hand over a pack costs the ward tenders and nothing else", async () => {
  const h = await service({ load: false });
  const body = await resolve(h, LAMBHA);
  assert.equal(body.jurisdiction.ward_name, "LAMBHA");
  assert.deepEqual(body.ward_tenders, []);
  assert.deepEqual(body.jurisdiction.urban_body, {
    name: "Ahmedabad Municipal Corporation", kind: null, city: "Ahmedabad", state_code: "GJ",
    basis: "ward_snapshot", road_notices: 0, road_notices_open: 0,
  });
  assert.deepEqual(h.lines.error, []);
});

test("a failure while matching is logged and the street answer still goes out", async () => {
  const h = await service();
  const real = PACKS.GJ;
  PACKS.GJ = [{ title: "Road in Lambha Ward", get organisation_chain() { throw new Error("boom"); } }];
  try {
    const body = await resolve(h, LAMBHA);
    assert.deepEqual(body.ward_tenders, []);
    assert.equal(body.jurisdiction.urban_body, null);
    assert.equal(body.reason, "no_location_match");
    assert.equal(JSON.parse(h.lines.error.at(-1)).event, "india_ward_tender_match_failed");
  } finally {
    PACKS.GJ = real;
  }
});

test("a Karnataka answer carries no urban_body and its log line says null for the new fields", async () => {
  const repository = memoryRepository();
  repository.queryTenders = async () => [{ tender_number: "T/1", title: "Resurfacing of Rose Road in T", location: "T" }];
  const municipal = { async resolve({ lat, lng }) {
    return { lat, lng, road_ownership: "municipal", source: "kgis_snapshot", lgd: "1", town: "T", state_code: "KA",
      ward_name: "Hoodi", ward_no: "54", ward_code: "20G1054", address: "Lily Lane, T", address_source: "operator_geocoder",
      lookup: { kgis: "snapshot", ward: "resolved" } };
  } };
  const loads = [];
  const catalogue = { async match() { return { tender: null, reason: "no_location_match", catalogue: null }; }, async load(...args) { loads.push(args); return null; } };
  const h = await harness({ repository, geolocator: municipal, catalogue });
  for (const expected of ["miss", "hit"]) {
    const body = JSON.parse((await h.post("/v1/tenders/resolve", { lat: 12.9, lng: 77.6 })).body);
    assert.ok(!Object.hasOwn(body.jurisdiction, "urban_body"));
    const logged = lastRequest(h);
    assert.equal(logged.answer_cache, expected);
    assert.deepEqual([logged.ward_snapshot, logged.urban_body, logged.urban_body_notices], [null, null, null]);
  }
  assert.deepEqual(loads, [], "no pack is read for a Karnataka point");
});
