import { readFile } from "node:fs/promises";

import { HttpError } from "./errors.mjs";
import {
  HIGHWAY_LAYERS, OWNERSHIP_BUNDLE_PATH, OWNERSHIP_FORMAT, loadOwnershipBundle, polygonAttributes, polygonsAt,
} from "./local-ownership.mjs";
import { highwayRefsFromAddress, stateCodeFor } from "./national-tenders.mjs";
import { pointInRings, withinBox } from "./spatial.mjs";

const GEOCODER_USER_AGENT = "PotholeReporter-central/1 (+https://coding-parrot.github.io/pothole-reporter/; contact@aiengg.dev)";
const KGIS_TOWN = "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/Admin_Dynamic_New/MapServer/1/query";
const KGIS_NH = "https://kgis.ksrsac.in/kgismaps/rest/services/State_Basemap/State_Basemap_Dynamic/MapServer/289/query";
const KGIS_SH = "https://kgis.ksrsac.in/kgismaps/rest/services/State_Basemap/State_Basemap_Dynamic/MapServer/290/query";
const KGIS_DH = "https://kgis.ksrsac.in/kgismaps/rest/services/State_Basemap/State_Basemap_Dynamic/MapServer/291/query";
const KGIS_GP = "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/GP_Boundary/MapServer/0/query";

// What the service answers Karnataka road ownership from: a packaged copy of every KGIS
// layer the live lookup read (towns, the three highway land-cover layers, gram
// panchayats) and the state boundary, built by
// infra/aws-central/tools/build-karnataka-geometry.mjs and shipped in the Lambda package
// at this same path relative to the service. See local-ownership.mjs for the file.
export const LOCAL_GEOMETRY_PATH = OWNERSHIP_BUNDLE_PATH;
export const LOCAL_GEOMETRY_FORMAT = OWNERSHIP_FORMAT;

// The ward a municipal point is in: every polygon of the KGIS "Ward New" layer, grouped
// by town, built by the same tool and shipped at this same path relative to the service.
// It is read on every municipal lookup, like the ownership bundle, because a live ward
// query would add a slow call (20 s answers happen) to the request path for a fact that
// changes when a delimitation does.
export const WARD_GEOMETRY_PATH = new URL("../../../data/karnataka-ward-geometry.json", import.meta.url);
export const WARD_GEOMETRY_FORMAT = "pothole-karnataka-ward-geometry";

const bounded = (value, maximum) => typeof value === "string"
  ? value.trim().slice(0, maximum) : "";

function pointUrl(endpoint, lat, lng, fields, distance = 0) {
  const geometry = encodeURIComponent(JSON.stringify({
    x: lng,
    y: lat,
    spatialReference: { wkid: 4326 },
  }));
  return `${endpoint}?geometry=${geometry}`
    + "&geometryType=esriGeometryPoint&spatialRel=esriSpatialRelIntersects"
    + (distance ? `&distance=${distance}&units=esriSRUnit_Meter` : "")
    + `&outFields=${encodeURIComponent(fields)}&returnGeometry=false&f=json`;
}

// The highway layers are land-cover polygons that stop a few metres short of the
// carriageway edge, so exact containment misses Bellary Road (NH 7) at 13.00271,77.58406.
// Measured on 21 Sep 2026: 5 m finds it, while 10 m picks up an unnamed State Highway in
// central Hubballi and 20 m picks up OBJECTID 3059, Bengaluru's MG Road, which the layer
// misclassifies as a National Highway. Past 5 m, city reports were told to write to NHAI.
// The packaged polygons are tested with the same rule: within this many metres of the
// polygon, measured on the ground.
export const HIGHWAY_BUFFER_METRES = 5;

// KGIS is Karnataka's register. Outside Karnataka it answers "no features" for every
// layer, which is indistinguishable from an outage, so a Gujarat street reported on
// 30 Sep 2026 came back as ownership "unknown" during a KGIS blip and the app told the
// user to check a signal that was already full 5G. Karnataka spans lat 11.59 to 18.46
// and lng 74.04 to 78.59; this envelope pads that by half a degree (about 55 km) so
// every border point, and anything a coarse GPS fix could place near one, is still put
// to the register's polygons. Only points far outside skip them, and for those
// "outside Karnataka" is a fact about geography, not about any lookup.
const KARNATAKA_ENVELOPE = Object.freeze({
  minLat: 11.09, maxLat: 18.96, minLng: 73.54, maxLng: 79.09,
});

function withinKarnatakaEnvelope(lat, lng) {
  return Number.isFinite(lat) && Number.isFinite(lng)
    && lat >= KARNATAKA_ENVELOPE.minLat && lat <= KARNATAKA_ENVELOPE.maxLat
    && lng >= KARNATAKA_ENVELOPE.minLng && lng <= KARNATAKA_ENVELOPE.maxLng;
}

const UNAVAILABLE = Object.freeze({ available: false, data: null });

async function readJson(fetchImpl, url, { headers = {}, timeoutMs = 6_000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(url, {
      headers,
      redirect: "error",
      signal: controller.signal,
    });
    if (!response.ok) return { available: false, data: null };
    const data = await response.json();
    if (!data || typeof data !== "object" || data.error) {
      return { available: false, data: null };
    }
    return { available: true, data };
  } catch {
    return { available: false, data: null, timedOut: controller.signal.aborted };
  } finally {
    clearTimeout(timer);
  }
}

function addressFromGeocoder(data) {
  const address = data?.address || {};
  const parts = [
    address.road || address.pedestrian || address.residential || address.footway,
    address.neighbourhood || address.hamlet,
    address.suburb || address.village,
    address.city || address.town || address.municipality,
    address.postcode,
  ].filter((value, index, all) => value && all.indexOf(value) === index);
  return bounded(parts.join(", ") || data?.display_name, 500) || null;
}

// The parts of the geocoder's answer that national tender matching reads on their own:
// the State/UT code picks the catalogue pack, and a road that is a national highway
// (by its name, or by the ref OpenStreetMap carries for it) opens the highway contracts.
function partsFromGeocoder(data) {
  if (!data || typeof data !== "object" || !data.address) return null;
  const address = data.address;
  const ref = bounded(data.namedetails?.ref, 80) || null;
  const road = bounded(address.road || address.pedestrian || address.residential || address.footway, 160) || null;
  return {
    road,
    ref,
    suburb: bounded(address.suburb || address.village || address.neighbourhood, 160) || null,
    // Every named place the geocoder put the point in, most specific first. Ward-level
    // tender matching reads these; in Bengaluru the neighbourhood ("Doddigunta") is often
    // the name a tender title uses when the ward's own name is not.
    localities: [address.neighbourhood, address.hamlet, address.quarter, address.suburb, address.village]
      .map((value) => bounded(value, 160))
      .filter((value, index, all) => value && all.indexOf(value) === index),
    city: bounded(address.city || address.town || address.municipality, 160) || null,
    state: bounded(address.state, 80) || null,
    highway_ref: highwayRefsFromAddress(road, ref),
  };
}

function validWardGeometry(geometry) {
  return geometry && geometry.format === WARD_GEOMETRY_FORMAT
    && Number.isFinite(geometry.coordinate_scale) && geometry.coordinate_scale > 0
    && geometry.towns && typeof geometry.towns === "object"
    && Object.values(geometry.towns).every((town) => Array.isArray(town?.bbox) && Array.isArray(town?.wards));
}

// One load per process per file. The Lambda keeps it across invocations; the tests
// share it across the many geolocators they build. A failure is logged once, not per
// lookup: the file is either in the package or it is not.
const bundleCache = new Map();
function loadOnce(path, logger, event, load) {
  const key = String(path);
  if (!bundleCache.has(key)) {
    bundleCache.set(key, load(path).catch((error) => {
      logger.error(JSON.stringify({
        event,
        path: key,
        error_message: String(error?.message || error).slice(0, 300),
      }));
      return null;
    }));
  }
  return bundleCache.get(key);
}
const loadLocalGeometry = (path, logger) => loadOnce(path, logger, "local_geometry_unavailable", loadOwnershipBundle);
const loadWardGeometry = (path, logger) => loadOnce(path, logger, "ward_geometry_unavailable",
  (file) => readFile(file, "utf8").then(JSON.parse).then((geometry) => {
    if (!validWardGeometry(geometry)) throw new Error("not the expected geometry bundle");
    return geometry;
  }));

// KGIS names a Bengaluru ward "41 - Munnenkolalu". The number is the current (Greater
// Bengaluru) numbering and is reported on its own; the name is what people and tender
// titles use.
export function wardNameWithoutNumber(name) {
  return bounded(String(name ?? "").replace(/^\s*\d+\s*-\s*/, ""), 160) || null;
}

// The ward polygon containing the point, as the register names it. The caller's town is
// tried first, which settles any overlap at a town line in that town's favour. Every other
// town whose box holds the point is tried after it, because the two KGIS layers do not
// agree everywhere: the 20 wards filed under town code 1006 lie inside the polygon the
// town layer calls Bhatkal (1002).
export function wardAt(geometry, lat, lng, townCode) {
  const scale = geometry.coordinate_scale;
  const x = lng * scale;
  const y = lat * scale;
  const own = townCode ? geometry.towns[townCode] : null;
  const towns = own ? [own, ...Object.values(geometry.towns).filter((town) => town !== own)]
    : Object.values(geometry.towns);
  for (const town of towns) {
    if (!withinBox(x, y, town.bbox)) continue;
    for (const [code, no, name, bbox, rings] of town.wards) {
      if (withinBox(x, y, bbox) && pointInRings(x, y, rings)) return { code, no, name };
    }
  }
  return null;
}

// The named wards whose towns share a tender index with the given ward, each with its box
// in degrees. Ward tender matching uses it to tell a ward from its namesake across town.
// A town's index is its own, but for the five Greater Bengaluru corporations (KGIS town
// codes 20G1 to 20G5), whose tenders dynamo-repository.mjs files together under "BLR".
const wardRosters = new WeakMap();
export function wardRosterOf(geometry, wardCode) {
  const own = Object.keys(geometry.towns).find((code) => String(wardCode || "").startsWith(code)
    && geometry.towns[code].wards.some((ward) => ward[0] === wardCode));
  if (!own) return [];
  const group = /^20G\d$/.test(own) ? "20G" : own;
  if (!wardRosters.has(geometry)) wardRosters.set(geometry, new Map());
  const rosters = wardRosters.get(geometry);
  if (!rosters.has(group)) {
    const scale = geometry.coordinate_scale;
    rosters.set(group, Object.entries(geometry.towns)
      .filter(([code]) => (group === "20G" ? /^20G\d$/.test(code) : code === own))
      .flatMap(([, town]) => town.wards)
      .map(([code, , name, bbox]) => ({ code, name: wardNameWithoutNumber(name), bbox: bbox.map((value) => value / scale) }))
      .filter((ward) => ward.name));
  }
  return rosters.get(group);
}

// What one polygon says about a point it covers.
function verdictOf(bundle, hit) {
  const record = polygonAttributes(bundle, hit.polygon);
  if (HIGHWAY_LAYERS.includes(record.layer)) {
    return {
      road_ownership: record.layer,
      highway_name: bounded(record.name, 160) || null,
      local: `${record.layer}_polygon`,
    };
  }
  if (record.layer === "town") {
    // ELCITA, the Electronic City industrial township, is in the KGIS layer with no LGD
    // code. KGIS itself answers "unknown" for it (a town the directory cannot key), and
    // the snapshot must not say more than the register does.
    if (record.lgd == null) return { road_ownership: "unknown", local: "town_without_lgd" };
    return {
      road_ownership: "municipal",
      lgd: String(record.lgd),
      town: record.name,
      town_code: record.kgis_code || null,
      local: "municipal_polygon",
    };
  }
  const name = bounded(record.name, 160);
  return name ? { road_ownership: "rural", rural_body: name, local: "gp_polygon" }
    : { road_ownership: "outside_state", local: "gp_polygon_unnamed" };
}

// A highway polygon KGIS gives no name, a town it gives no LGD code, a panchayat polygon
// with a blank name.
const nameless = (verdict) => verdict.highway_name === null
  || verdict.road_ownership === "unknown" || verdict.road_ownership === "outside_state";

// The live lookup's own rules, in its order, on the packaged copy of the same layers:
// a highway within HIGHWAY_BUFFER_METRES (national, then state, then district) is not
// the town's road even inside the town; then the town; then the gram panchayat; and a
// point with no named panchayat is outside_state.
//
// That last rule is copied, not endorsed. KGIS's panchayat layer holds 309 polygons with
// a blank name (2.7% of the state's area lies in one and in no town) and has gaps
// (0.1%), and the live code read "no panchayat name" as outside_state for all of it.
// lookup.local says which it was ("gp_polygon_unnamed", "state_polygon_no_panchayat",
// "outside_state_polygon"), so the request log can count them.
//
// Where several polygons of the deciding layer cover the point (a junction of two state
// highways, the seam between a named stretch of road and an unnamed one), the live code
// read whichever KGIS listed first. That order is KGIS's spatial index's, changes with
// the output fields a query asks for, and followed no property of the polygons on the
// 28 junctions tried on 7 Oct 2026. So the choice here is a rule of its own: the polygon
// the point is in or nearest to, then one with a name (or an LGD code) over one without,
// then the lowest OBJECTID. Every candidate is returned, the answer first.
export function localVerdicts(bundle, lat, lng, bufferMetres = HIGHWAY_BUFFER_METRES) {
  const hits = polygonsAt(bundle, lat, lng, bufferMetres);
  for (const name of [...HIGHWAY_LAYERS, "town", "gram_panchayat"]) {
    const layer = bundle.layerIndex[name];
    const verdicts = hits.filter((hit) => hit.layer === layer)
      .map((hit) => ({ hit, verdict: verdictOf(bundle, hit) }))
      .sort((left, right) => left.hit.metres - right.hit.metres
        || Number(nameless(left.verdict)) - Number(nameless(right.verdict))
        || left.hit.polygon - right.hit.polygon)
      .map(({ verdict }) => verdict);
    if (verdicts.length) return verdicts;
  }
  const inState = hits.some((hit) => hit.layer === bundle.layerIndex.state);
  return [{ road_ownership: "outside_state", local: inState ? "state_polygon_no_panchayat" : "outside_state_polygon" }];
}

export function classifyLocally(bundle, lat, lng, bufferMetres = HIGHWAY_BUFFER_METRES) {
  return localVerdicts(bundle, lat, lng, bufferMetres)[0];
}

// The live KGIS lookup, as the request path ran it until 7 Oct 2026: four layer queries
// at once, and the panchayat layer when the point is in no town. It is kept for
// tools/verify-local-ownership.mjs, which compares it with the packaged polygons, and is
// off the request path unless a geolocator is built with liveKgis.
//
// KGIS stalls on its query endpoints for minutes at a time while its root still
// answers. One timeout opens the breaker so later lookups skip KGIS at once instead of
// each waiting; after breakerMs the next lookup puts KGIS back on trial.
export function createKgisClient({ fetchImpl = fetch, timeoutMs = 3_000, breakerMs = 60_000 } = {}) {
  let closedUntil = 0;
  const ask = async (url) => {
    if (Date.now() < closedUntil) return UNAVAILABLE;
    const result = await readJson(fetchImpl, url, { timeoutMs });
    if (result.timedOut) closedUntil = Date.now() + breakerMs;
    // A JSON error/proxy envelope is not proof of zero matching features.
    if (result.available && (!Array.isArray(result.data.features)
        || result.data.features.some((feature) => !feature || typeof feature !== 'object'
          || !feature.attributes || typeof feature.attributes !== 'object'))) return UNAVAILABLE;
    return result;
  };
  return {
    // road_ownership "unknown" means KGIS gave no verdict: a layer was down, stalled or
    // malformed, the breaker is open, or the point is in a town with no LGD code.
    async verdict(lat, lng) {
      const [town, nh, sh, dh] = await Promise.all([
        ask(pointUrl(KGIS_TOWN, lat, lng, "KGISTownName,Town_Type,KGISTownCode,LGD_TownCode")),
        ask(pointUrl(KGIS_NH, lat, lng, "Name", HIGHWAY_BUFFER_METRES)),
        ask(pointUrl(KGIS_SH, lat, lng, "Name", HIGHWAY_BUFFER_METRES)),
        ask(pointUrl(KGIS_DH, lat, lng, "Name", HIGHWAY_BUFFER_METRES)),
      ]);
      const highwayLayers = [[nh, "national_highway"], [sh, "state_highway"], [dh, "district_highway"]];
      const highwaysAvailable = highwayLayers.every(([item]) => item.available);
      const verdict = {
        available: town.available && highwaysAvailable,
        town_available: town.available,
        highway_available: highwaysAvailable,
        gp_asked: false,
        gp_available: false,
        road_ownership: "unknown",
        highway_name: null,
        rural_body: null,
        lgd: "",
        town: null,
        town_code: "",
      };
      if (!verdict.available) return verdict;
      const townFeature = town.data.features[0];
      const highway = highwayLayers.find(([item]) => item.data.features[0]);
      const attrs = townFeature?.attributes || {};
      const lgd = attrs.LGD_TownCode == null ? "" : bounded(String(attrs.LGD_TownCode), 64);
      if (highway) {
        verdict.road_ownership = highway[1];
        verdict.highway_name = bounded(highway[0].data.features[0].attributes.Name, 160) || null;
      } else if (townFeature && lgd) {
        verdict.road_ownership = "municipal";
        verdict.lgd = lgd;
        verdict.town = bounded(attrs.KGISTownName, 160) || null;
        verdict.town_code = attrs.KGISTownCode == null ? "" : bounded(String(attrs.KGISTownCode), 16);
      } else if (!townFeature) {
        const gp = await ask(pointUrl(KGIS_GP, lat, lng, "KGISGPName"));
        verdict.gp_asked = true;
        verdict.gp_available = gp.available;
        verdict.rural_body = bounded(gp.data?.features?.[0]?.attributes?.KGISGPName, 160) || null;
        verdict.road_ownership = gp.available ? (verdict.rural_body ? "rural" : "outside_state") : "unknown";
      }
      return verdict;
    },
  };
}

export function createGeolocator({
  fetchImpl = fetch,
  geocoderUrl = "",
  geocoderBearerToken = "",
  // Off: Karnataka road ownership comes from the packaged polygons and KGIS is never
  // called. On (the verification tool, and tests of the live path): KGIS is asked first
  // and the packaged polygons answer only when it gives no verdict.
  liveKgis = false,
  kgisTimeoutMs = 3_000,
  kgisBreakerMs = 60_000,
  localGeometryPath = LOCAL_GEOMETRY_PATH,
  wardGeometryPath = WARD_GEOMETRY_PATH,
  logger = console,
} = {}) {
  const cache = new Map();
  const kgis = liveKgis
    ? createKgisClient({ fetchImpl, timeoutMs: kgisTimeoutMs, breakerMs: kgisBreakerMs }) : null;
  return {
    liveKgis,
    kgisTimeoutMs,
    kgisBreakerMs,
    async wardRoster(wardCode) {
      const wards = wardCode ? await loadWardGeometry(wardGeometryPath, logger) : null;
      return wards ? wardRosterOf(wards, wardCode) : [];
    },
    async resolve({ lat, lng, addressHint = "" }) {
      // Four decimals is about 11 m, well inside a phone's GPS error. At five, that
      // jitter made nearly every report a miss.
      const cacheKey = `${lat.toFixed(4)},${lng.toFixed(4)}`;
      const cached = cache.get(cacheKey);
      if (cached && cached.expiresAt > Date.now()) {
        // The jurisdiction is a fact about the place and is shared. An address that
        // came from a caller's hint is that caller's claim: the next caller in the cell
        // brings their own, or none.
        const geocoded = cached.value.address_source === "operator_geocoder";
        const hint = bounded(addressHint, 500) || null;
        return {
          ...cached.value,
          lat,
          lng,
          address: geocoded ? cached.value.address : hint,
          address_parts: geocoded ? cached.value.address_parts : null,
          address_source: geocoded ? "operator_geocoder" : hint ? "client_hint" : "unresolved",
        };
      }
      let geocoder = null;
      if (geocoderUrl) {
        try {
          const url = new URL(geocoderUrl);
          if (url.protocol !== "https:" || url.username || url.password) throw new Error();
          url.searchParams.set("lat", String(lat));
          url.searchParams.set("lon", String(lng));
          url.searchParams.set("format", "jsonv2");
          url.searchParams.set("zoom", "17");
          url.searchParams.set("addressdetails", "1");
          // namedetails carries the road's ref ("NH 48") when its name does not say it.
          url.searchParams.set("namedetails", "1");
          geocoder = {
            url: url.href,
            headers: {
              // Nominatim's usage policy refuses anonymous clients.
              "user-agent": GEOCODER_USER_AGENT,
              ...(geocoderBearerToken ? { authorization: `Bearer ${geocoderBearerToken}` } : {}),
            },
          };
        } catch {
          throw new HttpError(503, "geocoder_misconfigured",
            "The operator geocoder URL is invalid.");
        }
      }
      // The street name is the one upstream call left on this path. It runs while the
      // polygons are read, never after them.
      const geocoding = geocoder
        ? readJson(fetchImpl, geocoder.url, { headers: geocoder.headers })
        : Promise.resolve({ available: false, data: null });
      // A point far outside Karnataka is not a question for Karnataka's register: it has
      // no polygon there, and (live) its empty answer is indistinguishable from an outage.
      const inKarnataka = withinKarnatakaEnvelope(lat, lng);
      let lgd = "";
      let townName = null;
      let townCode = "";
      let roadOwnership = "unknown";
      let highwayName = null;
      let ruralBody = null;
      let source = "unresolved";
      let local = !inKarnataka ? "out_of_scope" : "not_needed";
      // What each KGIS layer contributed: "snapshot" (the packaged copy, no call made),
      // or, with liveKgis, whether the live call answered.
      const state = !inKarnataka ? "out_of_scope" : liveKgis ? "unavailable" : "snapshot";
      const kgisLookup = {
        kgis: state,
        kgis_town: state,
        kgis_highway: state,
        kgis_gp: inKarnataka && !liveKgis ? "snapshot" : "not_needed_or_unavailable",
      };
      if (!inKarnataka) {
        // Geography, not availability: this coordinate is hundreds of kilometres from
        // the Karnataka line. The client turns this into a regional-routing answer that
        // never asks the user to retry on a better signal.
        roadOwnership = "outside_state";
      } else if (liveKgis) {
        const verdict = await kgis.verdict(lat, lng);
        kgisLookup.kgis = verdict.available ? "available" : "unavailable";
        kgisLookup.kgis_town = verdict.town_available ? "available" : "unavailable";
        kgisLookup.kgis_highway = verdict.highway_available ? "available" : "unavailable";
        kgisLookup.kgis_gp = verdict.gp_available ? "available" : "not_needed_or_unavailable";
        roadOwnership = verdict.road_ownership;
        highwayName = verdict.highway_name;
        ruralBody = verdict.rural_body;
        lgd = verdict.lgd;
        townName = verdict.town;
        townCode = verdict.town_code;
        if (roadOwnership === "municipal") source = "kgis";
      }
      if (inKarnataka && roadOwnership === "unknown") {
        // The packaged polygons: always, in the default configuration; with liveKgis,
        // when KGIS gave no verdict (227 of 450 tender lookups in the 30 days to
        // 6 Oct 2026 ended there as a 503). Only a point the register itself cannot
        // place stays unknown.
        const bundle = await loadLocalGeometry(localGeometryPath, logger);
        if (!bundle) {
          local = "unavailable";
        } else {
          const verdict = classifyLocally(bundle, lat, lng);
          local = verdict.local;
          roadOwnership = verdict.road_ownership;
          highwayName = verdict.highway_name || null;
          ruralBody = verdict.rural_body || null;
          lgd = verdict.lgd || "";
          townName = verdict.town || null;
          townCode = verdict.town_code || "";
          if (roadOwnership === "municipal") source = "kgis_snapshot";
        }
      }
      const geocoded = await geocoding;
      const municipal = roadOwnership === "municipal";
      // The ward, from the packaged copy of the KGIS ward layer. No live call: the town
      // is already known, and the polygons are local.
      let ward = null;
      let wardLookup = roadOwnership === "outside_state" ? "out_of_scope" : "not_municipal";
      if (municipal && lgd) {
        const wards = await loadWardGeometry(wardGeometryPath, logger);
        if (!wards) {
          wardLookup = "unavailable";
        } else {
          ward = wardAt(wards, lat, lng, townCode || null);
          // KGIS names 2,290 of its 7,421 wards (all of Bengaluru, Mangaluru and Mysuru;
          // none of Hubballi-Dharwad or Davanagere). A ward with only a number is said
          // apart, because nothing can be matched on it.
          wardLookup = !ward ? "no_ward" : ward.name ? "resolved" : "resolved_unnamed";
        }
      }
      const value = {
        lat,
        lng,
        address: addressFromGeocoder(geocoded.data) || bounded(addressHint, 500) || null,
        address_parts: partsFromGeocoder(geocoded.data),
        // The State/UT the geocoder places the point in, as the national tender
        // manifests key their packs. Karnataka's own index is still keyed by LGD code.
        state_code: stateCodeFor(geocoded.data?.address),
        lgd: municipal ? lgd || null : null,
        town: municipal ? townName : null,
        // ward_no is KGIS's number under the current delimitation. Bengaluru's tender
        // titles carry the old BBMP numbers (Cox Town is 10 here and 108 there), so the
        // number is labelled and nothing compares it with a tender's.
        ward_name: ward ? wardNameWithoutNumber(ward.name) : null,
        ward_no: ward ? ward.no : null,
        ward_code: ward ? ward.code : null,
        ward_numbering: ward ? "kgis_current" : null,
        source: municipal && lgd ? source : "unresolved",
        address_source: geocoded.available
          ? "operator_geocoder" : addressHint ? "client_hint" : "unresolved",
        road_ownership: roadOwnership,
        highway_name: highwayName,
        rural_body: ruralBody,
        lookup: {
          ...kgisLookup,
          local,
          ward: wardLookup,
          geocoder: geocoded.available ? "available"
            : addressHint ? "skipped_client_hint" : "unavailable",
        },
      };
      if (roadOwnership !== "unknown" && (!municipal || value.address)) {
        cache.set(cacheKey, { value, expiresAt: Date.now() + 300_000 });
        while (cache.size > 256) cache.delete(cache.keys().next().value);
      }
      return value;
    },
  };
}
