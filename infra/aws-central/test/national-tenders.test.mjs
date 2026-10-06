import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { createGeolocator } from "../service/geolocation.mjs";
import {
  createNationalCatalogue, highwayRefsFromAddress, stateCodeFor,
} from "../service/national-tenders.mjs";
import { stageNationalCatalogue } from "../tools/stage-national-tenders.mjs";
import { harness, memoryRepository } from "./support.mjs";

const root = new URL("../../../", import.meta.url);
const read = (name) => readFileSync(new URL(name, root), "utf8");
const NO_LOCAL_GEOMETRY = "/nonexistent/karnataka-local-geometry.json";
// Before the real Pune notice below closes (2026-10-05 IST) and well before any
// fixture's review date.
const CLOCK = Date.parse("2026-10-04T00:00:00Z");

// ------------------------------------------------------------------ parity with the phone
// Every rule the server applies is the phone's own text. The slices are the function and
// constant definitions in static/standalone.js that matchHighwayContract, matchRoadNotice
// and matchRoadAgreement depend on; the server file carries them dedented by one level.
const PHONE_SLICES = [
  ["  function catalogResourceWithinReview(", "  async function fetchCatalogManifestFrom("],
  ["  const HIGHWAY_REF_RE = ", "  let _highwayManifest = "],
  ["  const INDIA_STATE_CODE_BY_NAME = ", "  const stateCodeForGeocode = "],
  ["  function candidateLeadIsUnambiguous(", "  function roadEventMatch("],
  ["  const tenderTokens = ", "  const hasAny = "],
  ["  const TENDER_STOP = ", "  // A publication date says when"],
  ["  function highwayContractCandidates(", "  async function matchRoadAgreement("],
  ["  const HIGHWAY_CONTRACT_LOCATION_STOP = ", "  function roadAgreementCandidates("],
  ["  function roadAgreementCandidates(", "  function savedBoundaryLocationMatches("],
  ["  const ROAD_NOTICE_STOP = ", "  // Restored from the last coherent production file: these were lost in the v1.38 merge\n  // definitions while their call sites stayed, so these paths threw on first use.\n  const ROUTE_RECORD_FIELDS"],
];

test("national matching rules stay byte-identical to the shipped phone logic", () => {
  const phone = read("static/standalone.js");
  const server = read("infra/aws-central/service/national-tenders.mjs");
  const block = server.slice(server.indexOf("// PARITY-START"), server.indexOf("// PARITY-END"));
  const dedent = (text) => text.trimEnd().split("\n")
    .map((line) => (line.startsWith("  ") ? line.slice(2) : line)).join("\n");
  for (const [start, end] of PHONE_SLICES) {
    const from = phone.indexOf(start);
    assert.ok(from >= 0, `phone no longer defines ${start.trim()}`);
    let to = phone.indexOf(end.split("\n")[0], from + start.length);
    if (to < 0) to = phone.indexOf("\n  // Restored from the last coherent", from + start.length);
    assert.ok(to > from, `end marker for ${start.trim()} not found`);
    const slice = dedent(phone.slice(from, to));
    assert.ok(slice.length > 40, `empty slice for ${start.trim()}`);
    assert.ok(block.includes(slice), `server copy of ${start.trim()} differs from the phone`);
  }
});

// ------------------------------------------------------------------ state and highway refs
test("state code comes from the ISO field, then the state name, India only", () => {
  assert.equal(stateCodeFor({ state: "Maharashtra", country_code: "in" }), "MH");
  assert.equal(stateCodeFor({ "ISO3166-2-lvl4": "IN-DL", state: "Delhi" }), "DL");
  assert.equal(stateCodeFor({ "ISO3166-2-lvl4": "IN-CT", state: "Chhattisgarh" }), "CG");
  assert.equal(stateCodeFor({ "ISO3166-2-lvl4": "IN-OR" }), "OD");
  assert.equal(stateCodeFor({ state: "NCT of Delhi" }), "DL");
  assert.equal(stateCodeFor({ state: "National Capital Territory of Delhi" }), "DL");
  assert.equal(stateCodeFor({ state: "Telangana" }), "TG");
  assert.equal(stateCodeFor({ state: "Karnataka", country_code: "in" }), "KA");
  assert.equal(stateCodeFor({ state: "Punjab", country_code: "pk" }), null);
  assert.equal(stateCodeFor({ state: "Narnia" }), null);
  assert.equal(stateCodeFor(null), null);
});

test("national highway refs are read from the road name, its ref and the KGIS name", () => {
  assert.equal(highwayRefsFromAddress("National Highway 48"), "NH-48");
  assert.equal(highwayRefsFromAddress("Delhi - Gurgaon Expressway", "NH 48"), "NH-48");
  assert.equal(highwayRefsFromAddress("NH48"), "NH-48");
  assert.equal(highwayRefsFromAddress(null, null, "BELLARY ROAD NH 7"), "NH-7");
  assert.equal(highwayRefsFromAddress(null, null, "NH-44"), "NH-44");
  assert.equal(highwayRefsFromAddress("Cambridge Road", null, null), null);
  assert.equal(highwayRefsFromAddress("MG Road", "SH 17"), null);
  assert.equal(highwayRefsFromAddress("National Expressway 4"), "NE-4");
});

// ------------------------------------------------------------------ fixture catalogue
// The real Pune notice from docs/packs/v1/road-notices/mh (manifest v1.36, retrieved
// 5 Oct 2026): a title that names a street.
const PUNE_NOTICE = {
  award_verified: false,
  closing_at: "2026-10-05T14:30:00+05:30",
  dlp_verified: false,
  lifecycle: "procurement_notice",
  opening_at: "2026-10-06T15:00:00+05:30",
  organisation_chain: "Pune Municipal Corporation||Road Development Department",
  published_at: "2026-09-19T14:00:00+05:30",
  record_id: "in-mh-gepnic:2026_PMCP_1340019_1",
  scope: "road_surface",
  segment_verified: false,
  source_id: "in-mh-gepnic",
  source_url: "https://mahatenders.gov.in/nicgep/app?component=%24DirectLink&page=FrontEndViewTender&service=direct&session=T&sp=Sth37MO6yBX%2BuXW6D7fJlfw%3D%3D",
  tender_id: "2026_PMCP_1340019_1",
  tender_reference: "PMC/ROAD/2026/272",
  title: "Re-asphalting of Shivneri Nagar DP Road at Kondhwa Khurd",
};
const MH_SOURCE = {
  retrieved_at: "2026-10-05T05:42:16Z",
  rows_excluded_by_scope: 0,
  rows_scanned: 0,
  source_id: "in-mh-gepnic",
  source_name: "Maharashtra e-Procurement Portal",
  source_url: "https://mahatenders.gov.in/nicgep/app?component=clear&page=FrontEndTendersByOrganisation&service=direct",
};
const notice = (id, title, extra = {}) => ({
  ...PUNE_NOTICE, record_id: `in-mh-gepnic:${id}`, tender_id: id, tender_reference: id, title, ...extra,
});

// The real catalogue holds no Delhi contract pack (34 states, DL absent), so the NH-48
// case is a constructed record in the published shape.
const contract = (id, state, refs, title, extra = {}) => ({
  record_id: `morth-upc:${id}`, reference_label: "UPC", reference_value: id, state_code: state,
  agency: "MoRTH", lifecycle: "current_project", lifecycle_status: "Under Construction (AD issued)",
  title, highway_refs: refs, chainages: [{ start_km: 1, end_km: 9 }], contractor: "Example Infra Ltd",
  published_at: null, start_date: "2025-04-01", likely_completion_date: "2027-03-31",
  division: "RO-MoRTH-Delhi", source_name: "MoRTH Data Lake: projects under implementation",
  source_url: "https://datalakem.nhai.gov.in/MoRTH/MISC/DroneVideosReportData?upc=0&state=0&mode=0",
  retrieved_at: "2026-10-05", scope_verified: true, segment_verified: false, award_verified: true,
  dlp_verified: false, ...extra,
});

const AGREEMENT_FIELDS = ["record_id", "reference_value", "title", "road_id", "district_name",
  "road_from", "road_to", "agreement_number", "agreement_date"];
const agreement = (id, title, district, from, to) => [
  `PMGSY:29:${id}`, `KA29P3R0${id}`, title, id, district, from, to, `${id}/PMGSY-III/2024-25`, "2024-11-12",
];

function fixtureCatalogue({ contracts = {}, notices = {}, agreements = {}, corrupt = [], expired = [] } = {}) {
  const dir = mkdtempSync(path.join(tmpdir(), "national-tenders-"));
  const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");
  const write = (kind, prefix, format, manifestFormat, rows, body) => {
    const resources = {};
    for (const [state, records] of Object.entries(rows)) {
      const packId = `${prefix}-${state.toLowerCase()}`;
      const pack = { format, schema_version: 1, pack_id: packId, pack_version: 1,
        state_code: state, adapter: "fixture", generated_at: "2026-10-05", ...body(records) };
      let bytes = Buffer.from(JSON.stringify(pack));
      const relative = `packs/v1/${kind}/${state.toLowerCase()}/pack-${sha(bytes)}.json`;
      const resource = { pack_id: packId, pack_version: 1, state_code: state, adapter: "fixture",
        path: relative, bytes: bytes.length, sha256: sha(bytes), records: records.length,
        review_after: expired.includes(packId) ? "2026-10-01" : "2026-11-04",
        source_retrieved_at: "2026-10-05", sources: 1 };
      if (corrupt.includes(packId)) bytes = Buffer.from(JSON.stringify({ ...pack, adapter: "tampered" }));
      mkdirSync(path.join(dir, path.dirname(relative)), { recursive: true });
      writeFileSync(path.join(dir, relative), bytes);
      resources[packId] = resource;
    }
    writeFileSync(path.join(dir, `${kind === "contracts" ? "contract" : kind.replace(/s$/, "")}-manifest.json`),
      JSON.stringify({ format: manifestFormat, catalog_version: 1, generated_at: "2026-10-05", resources }));
  };
  write("contracts", "in-nh-contracts", "pothole-highway-contract-pack", "pothole-contract-manifest",
    contracts, (records) => ({ contracts: records }));
  write("road-notices", "in-road-notices", "pothole-official-road-notice-pack", "pothole-road-notice-manifest",
    notices, (records) => ({ notices: records, sources: [MH_SOURCE], inference_policy: {} }));
  write("road-agreements", "in-road-agreements", "pothole-pmgsy-road-agreement-pack", "pothole-road-agreement-manifest",
    agreements, (records) => ({ agreements: records, agreement_fields: AGREEMENT_FIELDS, inference_policy: {},
      sources: [{ source_id: "pmgsy", source_name: "PMGSY dashboard road tender/agreement details",
        source_url: "https://pmgsy.dord.gov.in/dbweb" }] }));
  return dir;
}

const DEFAULT_FIXTURE = {
  contracts: {
    DL: [
      contract("RA00048DL001", "DL", ["NH-48"], "Rehabilitation and upgradation of Delhi - Gurgaon Expressway section of NH-48 from Mahipalpur to Rajokri in the NCT of Delhi"),
      contract("RA00044DL002", "DL", ["NH-44"], "Rehabilitation of NH-44 from Mukarba Chowk to Singhu border in Delhi"),
    ],
    KA: [
      contract("RA00007KA001", "KA", ["NH-44", "NH-7"], "Maintenance of Bellary Road NH-7 from Hebbal flyover to Yelahanka in Bengaluru Urban district", { division: "NH Division Bengaluru" }),
    ],
  },
  notices: {
    MH: [
      PUNE_NOTICE,
      notice("2026_PMCP_9000001_1", "Construction of storm water drain and footpath along Shivneri Nagar DP Road at Kondhwa Khurd"),
      notice("2026_PMCP_9000002_1", "Resurfacing of Katraj Kondhwa Road in Kondhwa Budruk"),
      notice("2026_PMCP_9000003_1", "Resurfacing of Katraj Kondhwa Road in Kondhwa Budruk"),
      notice("2026_PMCP_9000004_1", "Asphalting of Paud Road from Chandni Chowk to Kothrud", { closing_at: "2026-10-01T10:00:00+05:30" }),
    ],
    GJ: [notice("2026_AMC_1_1", "Resurfacing of Ashram Road in Navrangpura, Ahmedabad")],
  },
  agreements: {
    KA: [
      agreement(101, "T01-HAROHALLI TO KANAKAPURA ROAD", "Ramanagara", "HAROHALLI", "KANAKAPURA"),
      agreement(102, "L045-DODDABALLAPURA TO GAURIBIDANUR", "Chikkaballapura", "DODDABALLAPURA", "GAURIBIDANUR"),
    ],
  },
};

const catalogueFor = (fixture = DEFAULT_FIXTURE, options = {}) => createNationalCatalogue({
  dir: fixtureCatalogue(fixture), now: () => CLOCK, logger: { error() {} }, ...options,
});

// ------------------------------------------------------------------ matcher unit level
test("Pune street in the MH notices pack matches its re-asphalting notice", async () => {
  const result = await catalogueFor().match({
    stateCode: "MH", address: "Shivneri Nagar DP Road, Kondhwa Khurd, Pune, 411048",
  });
  assert.equal(result.catalogue, "road_notice");
  assert.equal(result.tender.tender_number, "PMC/ROAD/2026/272 [2026_PMCP_1340019_1]");
  assert.equal(result.tender.title, PUNE_NOTICE.title);
  assert.equal(result.tender.contractor, null);
  assert.equal(result.tender.location, PUNE_NOTICE.organisation_chain);
  assert.equal(result.tender.published, PUNE_NOTICE.published_at);
  assert.equal(result.tender.lifecycle, "procurement_notice");
  assert.equal(result.tender.lifecycle_status, "Open procurement notice; bid closing 2026-10-05T14:30:00+05:30");
  assert.equal(result.tender.award_verified, false);
  assert.equal(result.tender.source_name, "Maharashtra e-Procurement Portal");
  assert.equal(result.tender.source_url, MH_SOURCE.source_url);
  assert.equal(result.tender.match_basis, "State/UT MH; title/address kondhwa khurd, shivneri, kondhwa, khurd");
  assert.equal(result.tender.tender_pack_id, "in-road-notices-mh");
  assert.equal(result.tender.tender_pack_state_code, "MH");
  assert.match(result.tender.tender_pack_sha256, /^[0-9a-f]{64}$/);
});

test("a street that matches nothing is no_location_match", async () => {
  const result = await catalogueFor().match({ stateCode: "MH", address: "Fergusson College Road, Shivajinagar, Pune" });
  assert.equal(result.tender, null);
  assert.equal(result.reason, "no_location_match");
});

test("two notices with the same evidence suppress each other (near tie)", async () => {
  const result = await catalogueFor().match({ stateCode: "MH", address: "Katraj Kondhwa Road, Kondhwa Budruk, Pune" });
  assert.equal(result.tender, null);
  assert.equal(result.reason, "no_confident_match");
});

test("a drain and footpath notice on the street is never matched", async () => {
  const fixture = { ...DEFAULT_FIXTURE, notices: { MH: [DEFAULT_FIXTURE.notices.MH[1]] } };
  const result = await catalogueFor(fixture).match({ stateCode: "MH", address: "Shivneri Nagar DP Road, Kondhwa Khurd, Pune" });
  assert.equal(result.tender, null);
  assert.equal(result.reason, "no_location_match");
});

test("a notice whose bid closing has passed is not a candidate", async () => {
  const result = await catalogueFor().match({ stateCode: "MH", address: "Paud Road, Kothrud, Pune" });
  assert.equal(result.tender, null);
  assert.equal(result.reason, "no_location_match");
  const earlier = await catalogueFor(DEFAULT_FIXTURE, { now: () => Date.parse("2026-09-30T00:00:00Z") })
    .match({ stateCode: "MH", address: "Paud Road, Kothrud, Pune" });
  assert.equal(earlier.tender?.tender_number, "2026_PMCP_9000004_1");
});

test("one locality word alone never admits a notice", async () => {
  // "Kondhwa" appears in three fixture titles; a single word is ranking signal only.
  const result = await catalogueFor().match({ stateCode: "MH", address: "Unnamed Road, Kondhwa, Pune" });
  assert.equal(result.tender, null);
  assert.equal(result.reason, "no_location_match");
});

test("Delhi point on NH-48 matches the NH-48 contract, not the NH-44 one", async () => {
  const result = await catalogueFor().match({
    stateCode: "DL", address: "Delhi - Gurgaon Expressway, Mahipalpur, New Delhi, 110037", highwayRef: "NH-48",
  });
  assert.equal(result.catalogue, "nh_contract");
  assert.equal(result.tender.tender_number, "RA00048DL001");
  assert.equal(result.tender.contractor, "Example Infra Ltd");
  assert.equal(result.tender.location, "RO-MoRTH-Delhi");
  assert.equal(result.tender.published, "2025-04-01");
  assert.equal(result.tender.lifecycle, "current_project");
  assert.equal(result.tender.award_verified, true);
  assert.equal(result.tender.match_basis, "State/UT DL; mapped NH-48; title/address gurgaon, expressway, mahipalpur");
});

test("a highway contract needs locality evidence beyond the NH number", async () => {
  const result = await catalogueFor().match({
    stateCode: "DL", address: "Ring Road, Lajpat Nagar, New Delhi", highwayRef: "NH-48",
  });
  assert.equal(result.tender, null);
  assert.equal(result.reason, "no_location_match");
});

test("Karnataka PMGSY agreement matches a rural road by its from and to", async () => {
  const result = await catalogueFor().match({ stateCode: "KA", address: "Kanakapura Road, Harohalli, Ramanagara" });
  assert.equal(result.catalogue, "road_agreement");
  assert.equal(result.tender.tender_number, "KA29P3R0101; agreement 101/PMGSY-III/2024-25 dated 2024-11-12");
  assert.equal(result.tender.contractor, null);
  assert.equal(result.tender.published, null);
  assert.equal(result.tender.location, "Ramanagara district, PMGSY");
  assert.equal(result.tender.road_from, "HAROHALLI");
  assert.equal(result.tender.award_verified, false);
  assert.equal(result.tender.source_url, "https://pmgsy.dord.gov.in/dbweb");
  assert.match(result.tender.match_basis, /^State\/UT KA; title\/from\/to\/district evidence /);
});

test("no address is address_unresolved; no state or no pack is no_tenders_for_jurisdiction", async () => {
  const catalogue = catalogueFor();
  assert.equal((await catalogue.match({ stateCode: "MH", address: "" })).reason, "address_unresolved");
  assert.equal((await catalogue.match({ stateCode: null, address: "Some Road, Town" })).reason, "no_tenders_for_jurisdiction");
  assert.equal((await catalogue.match({ stateCode: "WB", address: "Park Street, Kolkata" })).reason, "no_tenders_for_jurisdiction");
});

test("a pack whose bytes do not match the manifest hash is not used", async () => {
  const errors = [];
  const catalogue = createNationalCatalogue({
    dir: fixtureCatalogue({ ...DEFAULT_FIXTURE, corrupt: ["in-road-notices-gj"] }),
    now: () => CLOCK, logger: { error: (line) => errors.push(JSON.parse(line)) },
  });
  const result = await catalogue.match({ stateCode: "GJ", address: "Ashram Road, Navrangpura, Ahmedabad" });
  assert.equal(result.reason, "no_tenders_for_jurisdiction");
  assert.equal(errors.filter((e) => e.event === "national_catalogue_pack_unavailable").length, 1);
  assert.match(errors[0].error_message, /hash/);
  const intact = await catalogueFor().match({ stateCode: "GJ", address: "Ashram Road, Navrangpura, Ahmedabad" });
  assert.equal(intact.tender?.tender_number, "2026_AMC_1_1");
});

test("a pack past its manifest review date is refused, as the phone refuses it", async () => {
  const errors = [];
  const catalogue = createNationalCatalogue({
    dir: fixtureCatalogue({ ...DEFAULT_FIXTURE, expired: ["in-road-notices-gj"] }),
    now: () => CLOCK, logger: { error: (line) => errors.push(JSON.parse(line)) },
  });
  const result = await catalogue.match({ stateCode: "GJ", address: "Ashram Road, Navrangpura, Ahmedabad" });
  assert.equal(result.reason, "no_tenders_for_jurisdiction");
  assert.equal(errors[0]?.event, "national_catalogue_pack_expired");
});

test("a missing catalogue directory is logged once and matches nothing", async () => {
  const errors = [];
  const catalogue = createNationalCatalogue({
    dir: "/nonexistent/national-tenders", now: () => CLOCK,
    logger: { error: (line) => errors.push(JSON.parse(line)) },
  });
  for (let i = 0; i < 3; i += 1) {
    assert.equal((await catalogue.match({ stateCode: "MH", address: "Paud Road, Kothrud, Pune" })).reason, "no_tenders_for_jurisdiction");
  }
  // Two catalogues were asked (no highway ref, so no contracts): one line each.
  assert.equal(errors.length, 2, "one line per catalogue, not per lookup");
  assert.ok(errors.every((e) => e.event === "national_catalogue_unavailable"));
});

// ------------------------------------------------------------------ service level
// The geocoder stub answers in Nominatim's jsonv2 shape; KGIS answers no features for
// points outside Karnataka (Pune and Delhi are far outside, but Pune is inside the
// envelope the service still asks KGIS for).
function geocoderStub(answers, kgis = () => ({ features: [] })) {
  return async (url) => {
    const target = new URL(url);
    if (target.hostname === "geocoder.test") {
      const key = `${Number(target.searchParams.get("lat")).toFixed(2)},${Number(target.searchParams.get("lon")).toFixed(2)}`;
      const answer = answers[key];
      return new Response(JSON.stringify(answer || { error: "Unable to geocode" }), {
        status: answer ? 200 : 404, headers: { "content-type": "application/json" },
      });
    }
    return new Response(JSON.stringify(kgis(target)), { status: 200, headers: { "content-type": "application/json" } });
  };
}

const GEOCODES = {
  "18.52,73.86": {
    address: { road: "Shivneri Nagar DP Road", suburb: "Kondhwa Khurd", city: "Pune", state: "Maharashtra",
      "ISO3166-2-lvl4": "IN-MH", postcode: "411048", country_code: "in" },
    namedetails: { name: "Shivneri Nagar DP Road" },
  },
  "28.55,77.12": {
    address: { road: "Delhi - Gurgaon Expressway", suburb: "Mahipalpur", city: "New Delhi", state: "Delhi",
      "ISO3166-2-lvl4": "IN-DL", postcode: "110037", country_code: "in" },
    namedetails: { name: "Delhi - Gurgaon Expressway", ref: "NH 48" },
  },
  "18.53,73.84": {
    address: { road: "Fergusson College Road", suburb: "Shivajinagar", city: "Pune", state: "Maharashtra",
      "ISO3166-2-lvl4": "IN-MH", country_code: "in" },
  },
  "13.00,77.58": {
    address: { road: "Bellary Road", suburb: "Hebbal", city: "Bengaluru", state: "Karnataka",
      "ISO3166-2-lvl4": "IN-KA", country_code: "in" },
  },
  "12.68,77.47": {
    address: { road: "Kanakapura Road", village: "Harohalli", county: "Ramanagara", state: "Karnataka",
      "ISO3166-2-lvl4": "IN-KA", country_code: "in" },
  },
  // What Nominatim really answers for 13.0027,77.5840 on 6 Oct 2026: a city road that
  // OpenStreetMap refs as NH44, which KGIS classes as municipal Bengaluru.
  "13.00,77.59": {
    address: { road: "Sankey Road", suburb: "Rajamahal", city: "Bengaluru", state: "Karnataka",
      "ISO3166-2-lvl4": "IN-KA", country_code: "in" },
    namedetails: { name: "Sankey Road", ref: "NH44" },
  },
};

async function service({ kgis, repository = memoryRepository(), catalogue = catalogueFor() } = {}) {
  const geolocator = createGeolocator({
    fetchImpl: geocoderStub(GEOCODES, kgis),
    geocoderUrl: "https://geocoder.test/reverse",
    localGeometryPath: NO_LOCAL_GEOMETRY,
    logger: { error() {} },
  });
  return harness({ geolocator, repository, catalogue, detector: {} });
}

test("POST /v1/tenders/resolve for a Pune street answers the MH notice and logs the catalogue", async () => {
  const { post, lines } = await service();
  const result = await post("/v1/tenders/resolve", { lat: 18.52, lng: 73.86 });
  const body = JSON.parse(result.body);
  assert.equal(result.statusCode, 200);
  assert.equal(body.jurisdiction.road_ownership, "outside_state");
  assert.equal(body.jurisdiction.state_code, "MH");
  assert.equal(body.jurisdiction.address, "Shivneri Nagar DP Road, Kondhwa Khurd, Pune, 411048");
  assert.equal(body.jurisdiction.address_parts.road, "Shivneri Nagar DP Road");
  assert.equal(body.reason, null);
  assert.equal(body.catalogue, "road_notice");
  assert.equal(body.tender.tender_number, "PMC/ROAD/2026/272 [2026_PMCP_1340019_1]");
  assert.equal(body.tender.contractor, null);
  const logged = JSON.parse(lines.log.findLast((line) => line.includes('"http_request"')));
  assert.equal(logged.outcome, "tender_matched");
  assert.equal(logged.tender_catalogue, "road_notice");
  assert.equal(logged.road_ownership, "outside_state");
});

test("POST /v1/tenders/resolve for a Delhi point on NH-48 answers the NH contract", async () => {
  const { post, lines } = await service();
  const result = await post("/v1/tenders/resolve", { lat: 28.55, lng: 77.12 });
  const body = JSON.parse(result.body);
  assert.equal(result.statusCode, 200);
  assert.equal(body.jurisdiction.state_code, "DL");
  assert.equal(body.jurisdiction.address_parts.highway_ref, "NH-48");
  assert.equal(body.catalogue, "nh_contract");
  assert.equal(body.tender.tender_number, "RA00048DL001");
  assert.equal(body.tender.match_basis, "State/UT DL; mapped NH-48; title/address gurgaon, expressway, mahipalpur");
  const logged = JSON.parse(lines.log.findLast((line) => line.includes('"http_request"')));
  assert.equal(logged.outcome, "tender_matched");
  assert.equal(logged.tender_catalogue, "nh_contract");
});

test("POST /v1/tenders/resolve outside Karnataka with no match says no_location_match", async () => {
  const { post, lines } = await service();
  const result = await post("/v1/tenders/resolve", { lat: 18.53, lng: 73.84 });
  const body = JSON.parse(result.body);
  assert.equal(result.statusCode, 200);
  assert.equal(body.tender, null);
  assert.equal(body.reason, "no_location_match");
  assert.equal(body.catalogue, null);
  const logged = JSON.parse(lines.log.findLast((line) => line.includes('"http_request"')));
  assert.equal(logged.outcome, "no_location_match");
  assert.equal(logged.tender_catalogue, null);
});

test("a Karnataka national highway point matches the KA highway contract", async () => {
  const { post } = await service({
    kgis: (url) => (url.pathname.includes("MapServer/289")
      ? { features: [{ attributes: { Name: "BELLARY ROAD NH 7" } }] } : { features: [] }),
  });
  const result = await post("/v1/tenders/resolve", { lat: 13.00271, lng: 77.58406 });
  const body = JSON.parse(result.body);
  assert.equal(body.jurisdiction.road_ownership, "national_highway");
  assert.equal(body.jurisdiction.highway_name, "BELLARY ROAD NH 7");
  assert.equal(body.catalogue, "nh_contract");
  assert.equal(body.tender.tender_number, "RA00007KA001");
  assert.equal(body.tender.match_basis, "State/UT KA; mapped NH-7; title/address bellary, hebbal");
});

test("a Karnataka town whose index has nothing falls through to the national catalogues", async () => {
  const repository = { ...memoryRepository(), async queryTenders() { return []; } };
  const { post, lines } = await service({
    repository,
    kgis: (url) => (url.pathname.includes("MapServer/1/")
      ? { features: [{ attributes: { KGISTownName: "Harohalli", LGD_TownCode: 290000 } }] } : { features: [] }),
  });
  const result = await post("/v1/tenders/resolve", { lat: 12.68, lng: 77.47 });
  const body = JSON.parse(result.body);
  assert.equal(body.jurisdiction.road_ownership, "municipal");
  assert.equal(body.jurisdiction.lgd, "290000");
  assert.equal(body.catalogue, "road_agreement");
  assert.equal(body.tender.tender_number, "KA29P3R0101; agreement 101/PMGSY-III/2024-25 dated 2024-11-12");
  const logged = JSON.parse(lines.log.findLast((line) => line.includes('"http_request"')));
  assert.equal(logged.outcome, "tender_matched");
  assert.equal(logged.tender_catalogue, "road_agreement");
});

test("a road the Karnataka register classed municipal is not a highway because OpenStreetMap refs it", async () => {
  const repository = { ...memoryRepository(), async queryTenders() { return []; } };
  const fixture = { ...DEFAULT_FIXTURE, contracts: { ...DEFAULT_FIXTURE.contracts, KA: [
    ...DEFAULT_FIXTURE.contracts.KA,
    contract("RA00044KA009", "KA", ["NH-44"], "Maintenance of Sankey Road NH-44 section in Rajamahal, Bengaluru"),
  ] } };
  const { post } = await service({
    repository,
    catalogue: catalogueFor(fixture),
    kgis: (url) => (url.pathname.includes("MapServer/1/")
      ? { features: [{ attributes: { KGISTownName: "Bengaluru", LGD_TownCode: 802 } }] } : { features: [] }),
  });
  const result = await post("/v1/tenders/resolve", { lat: 13.0027, lng: 77.5860 });
  const body = JSON.parse(result.body);
  assert.equal(body.jurisdiction.road_ownership, "municipal");
  assert.equal(body.jurisdiction.address_parts.highway_ref, "NH-44", "the ref is reported");
  assert.equal(body.tender, null, "but the register's verdict decides whether contracts are searched");
  assert.equal(body.reason, "no_tenders_for_jurisdiction");
  // Outside Karnataka the geocoder's ref is the only word on the matter and it is used.
  const delhi = await post("/v1/tenders/resolve", { lat: 28.55, lng: 77.12 });
  assert.equal(JSON.parse(delhi.body).catalogue, "nh_contract");
});

test("a Karnataka town with nothing anywhere keeps its index reason", async () => {
  const repository = { ...memoryRepository(), async queryTenders() { return []; } };
  const { post } = await service({
    repository,
    kgis: (url) => (url.pathname.includes("MapServer/1/")
      ? { features: [{ attributes: { KGISTownName: "Bengaluru", LGD_TownCode: 802 } }] } : { features: [] }),
  });
  const result = await post("/v1/tenders/resolve", { lat: 13.00271, lng: 77.58406 });
  const body = JSON.parse(result.body);
  assert.equal(body.jurisdiction.road_ownership, "municipal");
  assert.equal(body.tender, null);
  assert.equal(body.reason, "no_tenders_for_jurisdiction");
});

test("a Karnataka index match is answered from the index, as before", async () => {
  const repository = { ...memoryRepository(), async queryTenders() {
    return [{ tender_number: "BBMP/2026/1", title: "Resurfacing of Bellary Road in Hebbal ward", location: "BBMP" }];
  } };
  const { post, lines } = await service({
    repository,
    kgis: (url) => (url.pathname.includes("MapServer/1/")
      ? { features: [{ attributes: { KGISTownName: "Bengaluru", LGD_TownCode: 802 } }] } : { features: [] }),
  });
  const result = await post("/v1/tenders/resolve", { lat: 13.00271, lng: 77.58406 });
  const body = JSON.parse(result.body);
  assert.equal(body.catalogue, "ka_index");
  assert.equal(body.tender.tender_number, "BBMP/2026/1");
  assert.equal(body.tender.match_method, "deterministic_location_scope");
  const logged = JSON.parse(lines.log.findLast((line) => line.includes('"http_request"')));
  assert.equal(logged.tender_catalogue, "ka_index");
});

test("without a catalogue the service answers exactly as before", async () => {
  const { post } = await service({ catalogue: null });
  const result = await post("/v1/tenders/resolve", { lat: 18.52, lng: 73.86 });
  const body = JSON.parse(result.body);
  assert.equal(body.tender, null);
  assert.equal(body.reason, "outside_state");
});

// ------------------------------------------------------------------ the real catalogue
test("the repo's manifests and packs stage for the Lambda with every hash intact", () => {
  const out = mkdtempSync(path.join(tmpdir(), "national-stage-"));
  const staged = stageNationalCatalogue(new URL(".", root).pathname, out);
  assert.ok(staged.packs >= 90, `${staged.packs} packs`);
  assert.ok(staged.bytes > 5_000_000 && staged.bytes < 40_000_000, `${staged.bytes} bytes`);
  const phone = read("static/standalone.js");
  for (const file of Object.values(staged.manifests)) assert.ok(phone.includes(`"${file}"`), file);
  for (const name of ["contract-manifest.json", "road-notice-manifest.json", "road-agreement-manifest.json"]) {
    assert.ok(Object.keys(JSON.parse(readFileSync(path.join(out, name), "utf8")).resources).length > 20, name);
  }
});

test("the real MH notices pack loads under its manifest hash and the Pune notice is in it", async () => {
  const out = mkdtempSync(path.join(tmpdir(), "national-stage-"));
  stageNationalCatalogue(new URL(".", root).pathname, out);
  const catalogue = createNationalCatalogue({ dir: out, now: () => CLOCK, logger: { error() {} } });
  const loaded = await catalogue.load("road_notice", "MH");
  assert.ok(loaded, "MH notices pack did not load");
  assert.equal(loaded.pack.state_code, "MH");
  const contracts = await catalogue.load("nh_contract", "MH");
  assert.ok(contracts.pack.contracts.length > 50);
  const agreements = await catalogue.load("road_agreement", "BR");
  assert.equal(typeof agreements.pack.agreements[0].road_from, "string", "agreement rows decode to objects");
  assert.equal(agreements.pack.agreements[0].contractor, null);
  const telangana = await catalogue.load("nh_contract", "TG");
  assert.equal(telangana?.resource.pack_id, "in-nh-contracts-ts", "Telangana contracts are filed under TS");
});
