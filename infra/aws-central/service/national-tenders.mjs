// National tender matching for any point in India, server side.
//
// The phone already matches three official catalogues (national highway contracts from
// MoRTH and NHIDCL, State/UT road tender notices from the e-procurement portals, PMGSY
// rural road agreements) in static/standalone.js. The service matched only the Karnataka
// municipal index, so every point outside Karnataka answered "outside_state" with no
// tender. This module ports the phone's evidence rules without change and runs them
// against the same checksum-pinned packs, bundled with the Lambda by
// tools/stage-national-tenders.mjs.
//
// Everything between the PARITY markers is copied byte for byte from static/standalone.js
// and national-tenders.test.mjs fails if the two drift. Do not edit it here; change the
// phone and re-copy.

import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

import { tenderCoversCarriageway } from "./tender-scope.mjs";

// PARITY-START: static/standalone.js
function catalogResourceWithinReview(resource, now = Date.now()) {
  if (!resource || !/^\d{4}-\d{2}-\d{2}$/.test(String(resource.review_after || ""))) {
    return false;
  }
  const deadline = Date.parse(`${resource.review_after}T23:59:59.999Z`);
  return Number.isFinite(deadline) && Number.isFinite(now) && now <= deadline;
}

const HIGHWAY_REF_RE = /^N[HE]-[0-9]{1,4}[A-Z]{0,3}(?: \/ N[HE]-[0-9]{1,4}[A-Z]{0,3})*$/;

const INDIA_STATE_CODE_BY_NAME = new Map(Object.entries({
  "andaman and nicobar islands": "AN", "andaman nicobar islands": "AN",
  "andhra pradesh": "AP", "arunachal pradesh": "AR", assam: "AS", bihar: "BR",
  chhattisgarh: "CG", chattisgarh: "CG", chandigarh: "CH",
  "dadra and nagar haveli and daman and diu": "DH",
  "dadra nagar haveli daman diu": "DH", delhi: "DL",
  "national capital territory of delhi": "DL", "nct of delhi": "DL",
  goa: "GA", gujarat: "GJ", haryana: "HR", "himachal pradesh": "HP",
  jharkhand: "JH", "jammu and kashmir": "JK", karnataka: "KA", kerala: "KL",
  ladakh: "LA", lakshadweep: "LD", maharashtra: "MH", manipur: "MN",
  meghalaya: "ML", mizoram: "MZ", nagaland: "NL", odisha: "OD", orissa: "OD",
  puducherry: "PY", pondicherry: "PY", punjab: "PB", rajasthan: "RJ",
  sikkim: "SK", "tamil nadu": "TN", telangana: "TG", tripura: "TR",
  "uttar pradesh": "UP", uttarakhand: "UK", uttaranchal: "UK",
  "west bengal": "WB",
}));

function candidateLeadIsUnambiguous(ranked, minimumGap) {
  if (!Array.isArray(ranked) || !ranked.length) return false;
  return !ranked[1]
    || Number(ranked[0].score) - Number(ranked[1].score) >= minimumGap;
}

const tenderTokens = (value) => (String(value || "").toLowerCase().match(/[a-z0-9]+/g) || []);

const TENDER_STOP = new Set(["road", "roads", "street", "cross", "main", "layout", "bengaluru", "bangalore",
  "karnataka", "india", "ward", "city", "corporation", "south", "north", "east",
  "west", "central", "urban", "sector", "stage", "block", "phase"]);

function highwayContractCandidates(records, highwayRef, address = "") {
  const routeRefs = new Set(highwayRefsOf(highwayRef));
  if (!routeRefs.size || !Array.isArray(records)) return [];
  const addressParts = String(address || "").split(",").slice(0, 3).map((part) =>
    tenderTokens(part).filter((token) => token.length > 2
      && !HIGHWAY_CONTRACT_LOCATION_STOP.has(token))).filter((part) => part.length);
  const addressTokens = new Set(addressParts.flat());
  if (!addressTokens.size) return [];
  const eligible = [];
  for (const record of records) {
    if (!record || record.scope_verified !== true
        || !tenderCoversCarriageway(record.title, record.reference_value)) continue;
    const matchingRefs = (record.highway_refs || []).filter((ref) => routeRefs.has(ref));
    if (matchingRefs.length) eligible.push({ record, matching_refs: matchingRefs });
  }
  const titleTokensByRecord = eligible.map(({ record }) =>
    new Set(tenderTokens(record.title)));
  const frequencies = new Map();
  for (const token of addressTokens) {
    frequencies.set(token, titleTokensByRecord.reduce(
      (count, tokens) => count + (tokens.has(token) ? 1 : 0), 0));
  }
  const scored = [];
  for (let index = 0; index < eligible.length; index++) {
    const { record, matching_refs: matchingRefs } = eligible[index];
    const titleTokens = titleTokensByRecord[index];
    const localityHits = [...addressTokens].filter((token) => titleTokens.has(token));
    const normalisedTitle = tenderTokens(record.title).join(" ");
    const phraseHits = addressParts.filter((part) => part.length >= 2
      && normalisedTitle.includes(part.join(" ")));
    const uniqueLongHits = localityHits.filter((token) => token.length >= 6
      && frequencies.get(token) === 1);
    // An NH reference identifies a route, not which package covers this point; feeder
    // roads also cite the NH they meet. A single place word is never enough, even when
    // unique in this snapshot: require a multi-word phrase or two address words.
    if (!phraseHits.length && localityHits.length < 2) continue;
    let score = matchingRefs.length * 100 + localityHits.length * 8;
    score += phraseHits.length * 30 + uniqueLongHits.length * 16;
    if (record.lifecycle === "current_project") score += 30;
    if (record.award_verified && record.contractor) score += 15;
    if (/maintenance|o\s*&\s*m|under construction/i.test(record.lifecycle_status)) score += 8;
    if (record.chainages && record.chainages.length) score += 2;
    scored.push({ record, matching_refs: matchingRefs, locality_hits: localityHits,
      phrase_hits: phraseHits, unique_long_hits: uniqueLongHits, score });
  }
  scored.sort((left, right) => (right.score - left.score)
    || (right.phrase_hits.length - left.phrase_hits.length)
    || (right.locality_hits.length - left.locality_hits.length)
    || String(left.record.record_id).localeCompare(String(right.record.record_id)));
  return scored;
}

const HIGHWAY_CONTRACT_LOCATION_STOP = new Set([
  ...TENDER_STOP,
  ...[...INDIA_STATE_CODE_BY_NAME.keys()].flatMap((name) => tenderTokens(name)),
  "area", "at", "district", "from", "highway", "junction", "near", "number", "route",
  "state", "towards", "via",
]);

const highwayRefsOf = (value) => String(value || "").split(" / ")
  .map((ref) => ref.trim().toUpperCase()).filter((ref) => HIGHWAY_REF_RE.test(ref));

function roadAgreementCandidates(records, address) {
  if (!Array.isArray(records) || !records.length) return [];
  const addressParts = roadAgreementAddressParts(address);
  const addressTokens = new Set(addressParts.flat());
  if (!addressTokens.size) return [];
  const districtTokensByRecord = records.map((record) => new Set(
    tenderTokens(record && record.district_name)
      .filter((token) => token.length >= 3 && !ROAD_NOTICE_STOP.has(token))));
  const roadTokensByRecord = records.map((record, index) => new Set(tenderTokens([
    record && record.title, record && record.road_from, record && record.road_to,
  ].filter(Boolean).join(" ")).filter((token) => token.length >= 3
    && !ROAD_NOTICE_STOP.has(token) && !districtTokensByRecord[index].has(token))));
  const frequencies = new Map();
  for (const token of addressTokens) {
    frequencies.set(token, roadTokensByRecord.reduce(
      (count, tokens) => count + (tokens.has(token) ? 1 : 0), 0));
  }
  const scored = [];
  for (let index = 0; index < records.length; index++) {
    const record = records[index];
    if (!record || record.lifecycle !== "current_project"
        || record.lifecycle_status !== "In Progress" || record.scope_verified !== true
        || record.segment_verified !== false || record.contractor !== null
        || record.contractor_assignment_verified !== false || record.dlp_verified !== false) {
      continue;
    }
    const roadTokens = roadTokensByRecord[index];
    const roadHits = [...addressTokens].filter((token) => roadTokens.has(token));
    const districtTokens = districtTokensByRecord[index];
    const districtHits = [...addressTokens].filter((token) => districtTokens.has(token));
    const normalisedRoad = tenderTokens([
      record.title, record.road_from, record.road_to,
    ].filter(Boolean).join(" ")).join(" ");
    const phraseHits = addressParts.filter((part) => {
      const phrase = part.join(" ");
      return part.some((token) => !districtTokens.has(token))
        && phrase.length >= 6 && normalisedRoad.includes(phrase);
    });
    const multiTokenPhrase = phraseHits.some((part) => part.length >= 2);
    const uniqueLongHits = roadHits.filter((token) => token.length >= 6
      && frequencies.get(token) === 1);
    // The source has no geometry. A State match or district name alone is never enough:
    // require an exact multi-word road phrase, two road-name words, or a unique long
    // road word corroborated by the district in the reverse-geocoded address.
    const strongLocationEvidence = multiTokenPhrase || roadHits.length >= 2
      || (uniqueLongHits.length > 0 && districtHits.length > 0);
    if (!strongLocationEvidence) continue;
    const rarity = roadHits.reduce((sum, token) => {
      const frequency = frequencies.get(token) || records.length;
      return sum + Math.log((records.length + 1) / (frequency + 0.5));
    }, 0);
    const score = (multiTokenPhrase ? 80 : 0) + phraseHits.length * 20
      + roadHits.length * 16 + uniqueLongHits.length * 12
      + districtHits.length * 10 + rarity;
    scored.push({ record, score, road_hits: roadHits, district_hits: districtHits,
      phrase_hits: phraseHits, unique_long_hits: uniqueLongHits });
  }
  scored.sort((left, right) => (right.score - left.score)
    || (right.phrase_hits.length - left.phrase_hits.length)
    || (right.road_hits.length - left.road_hits.length)
    || String(left.record.record_id).localeCompare(String(right.record.record_id)));
  return scored;
}

function roadNoticeCandidates(records, address, route = null, now = Date.now()) {
  if (!Array.isArray(records) || !records.length) return [];
  const addressParts = roadNoticeAddressParts(address);
  const addressTokens = new Set(addressParts.flat());
  const routeRefs = new Set(highwayRefsOf(route && route.highway_ref));
  if (!addressTokens.size && !routeRefs.size) return [];

  const titleTokens = records.map((record) => new Set(tenderTokens(record && record.title)));
  const frequencies = new Map();
  for (const token of addressTokens) {
    frequencies.set(token, titleTokens.reduce(
      (count, tokens) => count + (tokens.has(token) ? 1 : 0), 0));
  }
  const routeAuthorityTokens = new Set(tenderTokens(route && route.authority_name)
    .filter((token) => token.length >= 4 && !ROAD_NOTICE_STOP.has(token)));
  const scored = [];
  for (let index = 0; index < records.length; index++) {
    const record = records[index];
    if (!record || record.lifecycle !== "procurement_notice" || record.scope !== "road_surface"
        || record.segment_verified !== false || record.award_verified !== false
        || record.dlp_verified !== false
        || !Number.isFinite(Date.parse(String(record.closing_at || "")))
        || Date.parse(record.closing_at) < now
        || !tenderCoversCarriageway(record.title, record.tender_reference)) continue;
    const tokens = titleTokens[index];
    const tokenHits = [...addressTokens].filter((token) => tokens.has(token));
    const phraseHits = addressParts.filter((part) => {
      const phrase = part.join(" ");
      return part.length >= 2 && phrase.length >= 6
        && tenderTokens(record.title).join(" ").includes(phrase);
    });
    const rareHits = tokenHits.filter((token) => token.length >= 6
      && frequencies.get(token) > 0 && frequencies.get(token) <= 2);
    const noticeRefs = highwayRefsInNotice(`${record.title} ${record.tender_reference}`);
    const highwayHits = [...routeRefs].filter((ref) => noticeRefs.has(ref));
    // One locality word is too weak for a statewide title index, even if it happens to
    // be rare in today's snapshot. Require an exact multi-word phrase or two distinct
    // address words; the rare-word signal may rank, but never admit, a record.
    const locationEvidence = phraseHits.length > 0 || tokenHits.length >= 2;
    if (!locationEvidence) continue;
    const organisationTokens = new Set(tenderTokens(record.organisation_chain));
    const authorityHits = [...routeAuthorityTokens].filter(
      (token) => organisationTokens.has(token));
    const rarity = tokenHits.reduce((sum, token) => {
      const frequency = frequencies.get(token) || records.length;
      return sum + Math.log((records.length + 1) / (frequency + 0.5));
    }, 0);
    const score = highwayHits.length * 100 + phraseHits.length * 30
      + rareHits.length * 16 + tokenHits.length * 8 + rarity + authorityHits.length * 3;
    scored.push({ record, score, token_hits: tokenHits, phrase_hits: phraseHits,
      rare_hits: rareHits, highway_hits: highwayHits, authority_hits: authorityHits });
  }
  scored.sort((left, right) => (right.score - left.score)
    || (right.phrase_hits.length - left.phrase_hits.length)
    || (right.token_hits.length - left.token_hits.length)
    || String(left.record.record_id).localeCompare(String(right.record.record_id)));
  return scored;
}

const ROAD_NOTICE_STOP = new Set([...TENDER_STOP,
  "area", "avenue", "bazaar", "bazar", "bridge", "chowk", "circle", "colony",
  "district", "extension", "galli", "lane", "locality", "market", "municipal",
  "municipality", "nagar", "near", "number", "path", "place", "sector", "state",
  "village", "zone"]);

function highwayRefsInNotice(value) {
  const refs = new Set();
  const pattern = /\bN([HE])\s*[-:]?\s*([0-9]{1,4}[A-Z]{0,3})\b/gi;
  for (const match of String(value || "").matchAll(pattern)) {
    refs.add(`N${match[1].toUpperCase()}-${match[2].toUpperCase()}`);
  }
  return refs;
}

function roadAgreementAddressParts(address) {
  return String(address || "").split(",").slice(0, 4).map((part) =>
    tenderTokens(part).filter((token) => token.length >= 3
      && !/^\d{5,6}$/.test(token) && !ROAD_NOTICE_STOP.has(token)))
    .filter((tokens) => tokens.length);
}

function roadNoticeAddressParts(address) {
  // Nominatim's compact address ends with the city. A city name is shared by hundreds
  // of unrelated notices and once made Kanjur, Mumbai select a Pune road whose title
  // merely contained "old Mumbai-Pune". Road plus immediate locality are the evidence.
  return String(address || "").split(",").slice(0, 2).map((part) =>
    tenderTokens(part).filter((token) => token.length >= 3
      && !/^\d{5,6}$/.test(token) && !ROAD_NOTICE_STOP.has(token)))
    .filter((tokens) => tokens.length);
}

// PARITY-END

export {
  INDIA_STATE_CODE_BY_NAME,
  candidateLeadIsUnambiguous,
  catalogResourceWithinReview,
  highwayContractCandidates,
  highwayRefsInNotice,
  roadAgreementCandidates,
  roadNoticeCandidates,
};

// Where tools/stage-national-tenders.mjs puts the three manifests (under fixed names)
// and the packs they pin, at the same path relative to the service as in the Lambda
// package. The repo itself holds them under static/ and docs/packs/.
export const NATIONAL_CATALOGUE_DIR = new URL("../../../data/national-tenders/", import.meta.url);

export const CATALOGUES = Object.freeze({
  nh_contract: Object.freeze({
    manifest: "contract-manifest.json",
    manifestFormat: "pothole-contract-manifest",
    packFormat: "pothole-highway-contract-pack",
    packId: (state) => `in-nh-contracts-${state.toLowerCase()}`,
    rows: "contracts",
  }),
  road_notice: Object.freeze({
    manifest: "road-notice-manifest.json",
    manifestFormat: "pothole-road-notice-manifest",
    packFormat: "pothole-official-road-notice-pack",
    packId: (state) => `in-road-notices-${state.toLowerCase()}`,
    rows: "notices",
  }),
  road_agreement: Object.freeze({
    manifest: "road-agreement-manifest.json",
    manifestFormat: "pothole-road-agreement-manifest",
    packFormat: "pothole-pmgsy-road-agreement-pack",
    packId: (state) => `in-road-agreements-${state.toLowerCase()}`,
    rows: "agreements",
  }),
});

// The contract manifest files Telangana under TS (its pre-2014 abbreviation) while the
// notice and agreement manifests, and the phone's state table, say TG. The phone asks
// for in-nh-contracts-tg, finds nothing and never matches a Telangana highway contract;
// the server looks under both so the pack that exists is used.
const CONTRACT_STATE_ALIASES = Object.freeze({ TG: ["TG", "TS"] });

// Nominatim's ISO3166-2-lvl4 for an Indian state. OpenStreetMap still carries the codes
// ISO retired (CT, OR, UT, DN, DD) on some boundaries; the manifests use the current ones.
const ISO_STATE_ALIASES = Object.freeze({ CT: "CG", OR: "OD", UT: "UK", DN: "DH", DD: "DH" });
const KNOWN_STATE_CODES = new Set(INDIA_STATE_CODE_BY_NAME.values());

const normaliseAuthorityValue = (value) => String(value || "")
  .normalize("NFKC").trim().toLowerCase().replace(/\s+/g, " ");

// The two-letter State/UT code the manifests key packs on, from a Nominatim address
// object. The ISO field is exact when present; the state name is the phone's own route
// (stateCodeForGeocode in standalone.js) and the fallback here.
export function stateCodeFor(address) {
  if (!address || typeof address !== "object") return null;
  const country = String(address.country_code || "").toLowerCase();
  if (country && country !== "in") return null;
  const iso = /^IN-([A-Z]{2})$/.exec(String(address["ISO3166-2-lvl4"] || "").toUpperCase());
  if (iso) {
    const code = ISO_STATE_ALIASES[iso[1]] || iso[1];
    if (KNOWN_STATE_CODES.has(code)) return code;
  }
  return INDIA_STATE_CODE_BY_NAME.get(normaliseAuthorityValue(address.state)) || null;
}

// National highway references in what the geocoder said about the road ("NH 48",
// "NH-44", "National Highway 48" as a name, "NH 48" as its ref) or what KGIS named it
// ("BELLARY ROAD NH 7", "NH-44"), in the "NH-48 / NH-7" form the phone's matchers take.
export function highwayRefsFromAddress(...values) {
  const refs = new Set();
  for (const value of values) {
    const text = String(value || "");
    if (!text) continue;
    for (const ref of highwayRefsInNotice(text)) refs.add(ref);
    for (const match of text.matchAll(/\bnational\s+(highway|expressway)\s*(?:no\.?\s*)?-?\s*([0-9]{1,4}[A-Za-z]{0,3})\b/gi)) {
      refs.add(`${match[1].toLowerCase() === "expressway" ? "NE" : "NH"}-${match[2].toUpperCase()}`);
    }
  }
  return [...refs].join(" / ") || null;
}

const sha256Hex = (bytes) => createHash("sha256").update(bytes).digest("hex");

function decodeAgreements(pack, resource) {
  const fields = pack.agreement_fields;
  const firstSource = pack.sources?.[0] || {};
  const retrievedAt = resource.source_retrieved_at;
  // Field names and the constant verification flags are the phone's
  // validateRoadAgreementPack; the matcher reads the flags, so they must be identical.
  return pack.agreements.map((values) => {
    const row = Object.fromEntries(fields.map((field, index) => [field, values[index]]));
    return {
      ...row,
      reference_label: "PMGSY package",
      state_code: resource.state_code,
      agency: "NRIDA / OMMAS",
      lifecycle: "current_project",
      lifecycle_status: "In Progress",
      lifecycle_basis: "source-reported WORK_STATUS; agreement date within five-year snapshot window",
      package_number: row.reference_value,
      contractor: null,
      scope_verified: true,
      segment_verified: false,
      agreement_verified: true,
      award_verified: false,
      contractor_assignment_verified: false,
      dlp_verified: false,
      source_name: firstSource.source_name,
      source_url: firstSource.source_url,
      retrieved_at: retrievedAt,
    };
  });
}

function provenance(kind, resource) {
  return {
    catalogue: kind,
    tender_pack_id: resource.pack_id,
    tender_pack_version: resource.pack_version,
    tender_pack_sha256: resource.sha256,
    tender_pack_state_code: resource.state_code,
  };
}

const common = {
  match_method: "deterministic_title_evidence",
  warranty: "current liability not established by the publication record",
  warranty_code: "unverified",
  confidence: null,
};

// The three record shapes, flattened to what /v1/tenders/resolve already returns for a
// Karnataka match plus lifecycle, match_basis and pack provenance. The wording of
// tender_number, lifecycle_status and match_basis is the phone's (matchHighwayContract,
// matchRoadNotice, matchRoadAgreement in standalone.js).
function publicHighwayContract(best, resource, stateCode) {
  const { record, matching_refs: matchingRefs, locality_hits: localityHits } = best;
  const basis = `State/UT ${stateCode}; mapped ${matchingRefs.join(" / ")}`
    + (localityHits.length ? `; title/address ${localityHits.join(", ")}` : "");
  return {
    tender_number: record.reference_value,
    reference_label: record.reference_label,
    title: record.title,
    location: record.division || null,
    contractor: record.contractor || null,
    published: record.published_at || record.start_date || null,
    source_name: record.source_name,
    source_url: record.source_url,
    lifecycle: record.lifecycle,
    lifecycle_status: record.lifecycle_status,
    award_verified: record.award_verified === true,
    match_basis: basis,
    reason: basis,
    ...common,
    ...provenance("nh_contract", resource),
  };
}

function publicRoadNotice(best, pack, resource, stateCode) {
  const { record } = best;
  const source = (pack.sources || []).find((item) => item.source_id === record.source_id);
  const locationEvidence = [...new Set([...best.phrase_hits.map((part) => part.join(" ")),
    ...best.token_hits])];
  const basis = `State/UT ${stateCode}`
    + (best.highway_hits.length ? `; mapped ${best.highway_hits.join(" / ")}` : "")
    + (locationEvidence.length ? `; title/address ${locationEvidence.join(", ")}` : "");
  return {
    tender_number: record.tender_reference === record.tender_id
      ? record.tender_id : `${record.tender_reference} [${record.tender_id}]`,
    reference_label: record.tender_reference === record.tender_id
      ? "Tender ID" : "Tender reference / ID",
    title: record.title,
    location: record.organisation_chain || null,
    contractor: null,
    published: record.published_at || null,
    bid_closing: record.closing_at,
    bid_opening: record.opening_at || null,
    detail_url: record.source_url,
    source_name: source ? source.source_name : "Official State/UT e-Procurement portal",
    source_url: source ? source.source_url : record.source_url,
    lifecycle: "procurement_notice",
    lifecycle_status: `Open procurement notice; bid closing ${record.closing_at}`,
    award_verified: false,
    match_basis: basis,
    reason: basis,
    ...common,
    ...provenance("road_notice", resource),
  };
}

function publicRoadAgreement(best, resource, stateCode) {
  const { record } = best;
  const agreement = record.agreement_verified && record.agreement_number && record.agreement_date
    ? `; agreement ${record.agreement_number} dated ${record.agreement_date}` : "";
  const evidence = [...new Set([
    ...best.phrase_hits.map((part) => part.join(" ")),
    ...best.road_hits, ...best.district_hits,
  ])];
  const basis = `State/UT ${stateCode}; title/from/to/district evidence ${evidence.join(", ")}`;
  return {
    tender_number: `${record.reference_value}${agreement}`,
    reference_label: agreement ? "PMGSY package / agreement" : record.reference_label,
    title: record.title,
    location: record.district_name ? `${record.district_name} district, PMGSY` : null,
    contractor: null,
    published: null,
    road_from: record.road_from || null,
    road_to: record.road_to || null,
    agreement_number: record.agreement_verified && record.agreement_number || null,
    agreement_date: record.agreement_verified && record.agreement_date || null,
    package_reference: record.reference_value || null,
    source_name: record.source_name || null,
    source_url: record.source_url || null,
    lifecycle: "current_project",
    lifecycle_status: `Source-reported In Progress as retrieved ${record.retrieved_at}; `
      + "not independently freshness-verified",
    award_verified: false,
    match_basis: basis,
    reason: basis,
    ...common,
    ...provenance("road_agreement", resource),
  };
}

export function createNationalCatalogue({
  dir = NATIONAL_CATALOGUE_DIR, logger = console, now = () => Date.now(),
} = {}) {
  const base = dir instanceof URL ? dir : new URL(`${String(dir).replace(/\/?$/, "/")}`, "file://");
  const manifests = new Map();
  const packs = new Map();

  const note = (event, detail) => logger.error(JSON.stringify({ event, ...detail }));

  // One parse per process per manifest. A manifest that is missing or malformed is
  // logged once and that catalogue is simply absent, as on a phone that cannot fetch it.
  function manifest(kind) {
    if (!manifests.has(kind)) {
      const spec = CATALOGUES[kind];
      manifests.set(kind, readFile(new URL(spec.manifest, base), "utf8").then(JSON.parse)
        .then((value) => {
          if (value?.format !== spec.manifestFormat || !value.resources
              || typeof value.resources !== "object") {
            throw new Error(`not a ${spec.manifestFormat}`);
          }
          return value;
        })
        .catch((error) => {
          note("national_catalogue_unavailable", {
            catalogue: kind,
            path: new URL(spec.manifest, base).pathname,
            error_message: String(error?.message || error).slice(0, 300),
          });
          return null;
        }));
    }
    return manifests.get(kind);
  }

  function resourceFor(loaded, kind, stateCode) {
    const codes = kind === "nh_contract" ? CONTRACT_STATE_ALIASES[stateCode] || [stateCode] : [stateCode];
    for (const code of codes) {
      const resource = loaded.resources[CATALOGUES[kind].packId(code)];
      if (resource) return resource;
    }
    return null;
  }

  async function readPack(kind, resource) {
    const spec = CATALOGUES[kind];
    const path = new URL(resource.path, base);
    const bytes = await readFile(path);
    // The manifest pins the bytes the phone would accept; a pack that differs is not
    // the published catalogue and is not used, whatever it contains.
    if (bytes.length !== resource.bytes || sha256Hex(bytes) !== resource.sha256) {
      throw new Error("pack bytes do not match the manifest hash");
    }
    const pack = JSON.parse(bytes.toString("utf8"));
    const rows = pack?.[spec.rows];
    if (pack?.format !== spec.packFormat || pack.pack_id !== resource.pack_id
        || pack.state_code !== resource.state_code || !Array.isArray(rows)
        || rows.length !== resource.records) {
      throw new Error("pack envelope does not match its manifest entry");
    }
    if (kind === "road_agreement") pack.agreements = decodeAgreements(pack, resource);
    return { pack, resource };
  }

  // Lazily, once per process per (catalogue, state): the Lambda keeps it across
  // invocations. The result is null when the state has no pack, the pack is past its
  // review date (the phone refuses it then too), or the file fails its hash.
  function load(kind, stateCode) {
    const key = `${kind}:${stateCode}`;
    if (!packs.has(key)) {
      packs.set(key, (async () => {
        const loaded = await manifest(kind);
        if (!loaded) return null;
        const resource = resourceFor(loaded, kind, stateCode);
        if (!resource) return null;
        if (!catalogResourceWithinReview(resource, now())) {
          note("national_catalogue_pack_expired", {
            catalogue: kind, pack_id: resource.pack_id, review_after: resource.review_after,
          });
          return null;
        }
        try {
          return await readPack(kind, resource);
        } catch (error) {
          note("national_catalogue_pack_unavailable", {
            catalogue: kind,
            pack_id: resource.pack_id,
            path: new URL(resource.path, base).pathname,
            error_message: String(error?.message || error).slice(0, 300),
          });
          return null;
        }
      })());
    }
    return packs.get(key);
  }

  // The phone's order of preference is highway contract, Karnataka index, PMGSY
  // agreement, notice. The owner asked the service for highway contract, notice,
  // agreement; the admission rules of each matcher are untouched.
  async function match({ stateCode, address, highwayRef = null }) {
    const none = (reason) => ({ tender: null, reason, catalogue: null, state_code: stateCode || null });
    if (!String(address || "").trim()) return none("address_unresolved");
    if (!/^[A-Z]{2}$/.test(String(stateCode || ""))) return none("no_tenders_for_jurisdiction");
    let searched = 0;
    let suppressed = false;
    if (highwayRef) {
      const loaded = await load("nh_contract", stateCode);
      if (loaded) {
        searched += 1;
        const ranked = highwayContractCandidates(loaded.pack.contracts, highwayRef, address);
        if (candidateLeadIsUnambiguous(ranked, 20)) {
          return {
            tender: publicHighwayContract(ranked[0], loaded.resource, stateCode),
            reason: null,
            catalogue: "nh_contract",
            state_code: stateCode,
          };
        }
        if (ranked.length) suppressed = true;
      }
    }
    const notices = await load("road_notice", stateCode);
    if (notices) {
      searched += 1;
      const ranked = roadNoticeCandidates(notices.pack.notices, address, { highway_ref: highwayRef }, now());
      if (candidateLeadIsUnambiguous(ranked, 12)) {
        return {
          tender: publicRoadNotice(ranked[0], notices.pack, notices.resource, stateCode),
          reason: null,
          catalogue: "road_notice",
          state_code: stateCode,
        };
      }
      if (ranked.length) suppressed = true;
    }
    const agreements = await load("road_agreement", stateCode);
    if (agreements) {
      searched += 1;
      const ranked = roadAgreementCandidates(agreements.pack.agreements, address);
      const best = ranked[0];
      const second = ranked[1];
      // matchRoadAgreement: two equally supported road records cannot be told apart
      // without geometry.
      const tie = best && second && Math.abs(best.score - second.score) < 8
        && best.phrase_hits.length === second.phrase_hits.length
        && best.road_hits.length === second.road_hits.length
        && best.district_hits.length === second.district_hits.length;
      if (best && !tie) {
        return {
          tender: publicRoadAgreement(best, agreements.resource, stateCode),
          reason: null,
          catalogue: "road_agreement",
          state_code: stateCode,
        };
      }
      if (best) suppressed = true;
    }
    if (!searched) return none("no_tenders_for_jurisdiction");
    return none(suppressed ? "no_confident_match" : "no_location_match");
  }

  return { dir: base, load, match };
}
