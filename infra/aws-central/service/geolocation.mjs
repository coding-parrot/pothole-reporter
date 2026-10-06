import { readFile } from "node:fs/promises";

import { HttpError } from "./errors.mjs";
import { highwayRefsFromAddress, stateCodeFor } from "./national-tenders.mjs";
import { metresToPolyline, pointInRings, withinBox } from "./spatial.mjs";

const GEOCODER_USER_AGENT = "PotholeReporter-central/1 (+https://coding-parrot.github.io/pothole-reporter/; contact@aiengg.dev)";
const KGIS_TOWN = "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/Admin_Dynamic_New/MapServer/1/query";
const KGIS_NH = "https://kgis.ksrsac.in/kgismaps/rest/services/State_Basemap/State_Basemap_Dynamic/MapServer/289/query";
const KGIS_SH = "https://kgis.ksrsac.in/kgismaps/rest/services/State_Basemap/State_Basemap_Dynamic/MapServer/290/query";
const KGIS_DH = "https://kgis.ksrsac.in/kgismaps/rest/services/State_Basemap/State_Basemap_Dynamic/MapServer/291/query";
const KGIS_GP = "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/GP_Boundary/MapServer/0/query";

// What the service answers from when KGIS cannot: the 319 KGIS town polygons, the state
// boundary and the national highway centre lines, built by
// infra/aws-central/tools/build-karnataka-geometry.mjs and shipped in the Lambda package
// at this same path relative to the service.
export const LOCAL_GEOMETRY_PATH = new URL("../../../data/karnataka-local-geometry.json", import.meta.url);
export const LOCAL_GEOMETRY_FORMAT = "pothole-karnataka-local-geometry";

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
const HIGHWAY_BUFFER_METRES = 5;

// KGIS is Karnataka's register. Outside Karnataka it answers "no features" for every
// layer, which is indistinguishable from an outage, so a Gujarat street reported on
// 30 Sep 2026 came back as ownership "unknown" during a KGIS blip and the app told the
// user to check a signal that was already full 5G. Karnataka spans lat 11.59 to 18.46
// and lng 74.04 to 78.59; this envelope pads that by half a degree (about 55 km) so
// every border point, and anything a coarse GPS fix could place near one, is still put
// to KGIS exactly as before. Only points far outside can skip it, and for those
// "outside Karnataka" is a fact about geography, not about whether KGIS is reachable.
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
    city: bounded(address.city || address.town || address.municipality, 160) || null,
    state: bounded(address.state, 80) || null,
    highway_ref: highwayRefsFromAddress(road, ref),
  };
}

function validLocalGeometry(geometry) {
  return geometry && geometry.format === LOCAL_GEOMETRY_FORMAT
    && Number.isFinite(geometry.coordinate_scale) && geometry.coordinate_scale > 0
    && Array.isArray(geometry.towns?.features) && geometry.towns.features.length > 0
    && Array.isArray(geometry.state?.rings) && Array.isArray(geometry.state?.bbox)
    && Array.isArray(geometry.highways?.features)
    && Number.isFinite(geometry.highways?.match_metres);
}

// One parse per process per file. The Lambda keeps it across invocations; the tests
// share it across the many geolocators they build.
const localGeometryCache = new Map();
function loadLocalGeometry(path, logger) {
  const key = String(path);
  if (!localGeometryCache.has(key)) {
    localGeometryCache.set(key, readFile(path, "utf8").then(JSON.parse).then((geometry) => {
      if (!validLocalGeometry(geometry)) throw new Error("not a local geometry bundle");
      return geometry;
    }).catch((error) => {
      // Logged once, not per lookup: the file is either in the package or it is not.
      logger.error(JSON.stringify({
        event: "local_geometry_unavailable",
        path: key,
        error_message: String(error?.message || error).slice(0, 300),
      }));
      return null;
    }));
  }
  return localGeometryCache.get(key);
}

// The same order of precedence as the KGIS path: a highway through a town is not the
// town's road, and a town is checked before the state line because a town polygon can
// overhang the OpenStreetMap boundary by a few metres.
function classifyLocally(geometry, lat, lng) {
  const scale = geometry.coordinate_scale;
  const x = lng * scale;
  const y = lat * scale;
  const metres = geometry.highways.match_metres;
  const padY = Math.ceil((metres / 110_540) * scale);
  const padX = Math.ceil((metres / Math.max(20_000, 111_320 * Math.cos((lat * Math.PI) / 180))) * scale);
  let nearest = null;
  for (const [ref, box, encoded] of geometry.highways.features) {
    if (!withinBox(x, y, box, padX, padY)) continue;
    const distance = metresToPolyline(lng, lat, encoded, scale);
    if (distance <= metres && (!nearest || distance < nearest.distance)) nearest = { ref, distance };
  }
  if (nearest) {
    return { road_ownership: "national_highway", highway_name: nearest.ref, local: "national_highway_geometry" };
  }
  for (const town of geometry.towns.features) {
    if (!withinBox(x, y, town.bbox) || !pointInRings(x, y, town.rings)) continue;
    // ELCITA, the Electronic City industrial township, is in the KGIS layer with no LGD
    // code. KGIS itself answers "unknown" for it (a town the directory cannot key), and
    // the snapshot must not say more than the register does.
    if (town.lgd == null) return { road_ownership: "unknown", local: "town_without_lgd" };
    return {
      road_ownership: "municipal",
      lgd: String(town.lgd),
      town: town.name,
      local: "municipal_polygon",
    };
  }
  if (withinBox(x, y, geometry.state.bbox) && pointInRings(x, y, geometry.state.rings)) {
    // Inside Karnataka and in no urban body: gram panchayat country. The snapshot holds
    // no panchayat polygons, so the body is not named.
    return { road_ownership: "rural", local: "state_polygon" };
  }
  return { road_ownership: "outside_state", local: "outside_state_polygon" };
}

export function createGeolocator({
  fetchImpl = fetch,
  geocoderUrl = "",
  geocoderBearerToken = "",
  kgisTimeoutMs = 3_000,
  kgisBreakerMs = 60_000,
  localGeometryPath = LOCAL_GEOMETRY_PATH,
  logger = console,
} = {}) {
  const cache = new Map();
  // KGIS stalls on its query endpoints for minutes at a time while its root still
  // answers. One timeout opens the breaker so later reports skip KGIS at once instead
  // of each holding a Lambda slot while it waits. While it is open, and whenever KGIS
  // gives no verdict, the local geometry answers instead. After kgisBreakerMs the next
  // lookup puts KGIS back on trial.
  let kgisClosedAt = 0;
  const kgis = async (url) => {
    if (Date.now() < kgisClosedAt) return UNAVAILABLE;
    const result = await readJson(fetchImpl, url, { timeoutMs: kgisTimeoutMs });
    if (result.timedOut) kgisClosedAt = Date.now() + kgisBreakerMs;
    // A JSON error/proxy envelope is not proof of zero matching features.
    if (result.available && (!Array.isArray(result.data.features)
        || result.data.features.some((feature) => !feature || typeof feature !== 'object'
          || !feature.attributes || typeof feature.attributes !== 'object'))) return UNAVAILABLE;
    return result;
  };
  return {
    kgisTimeoutMs,
    kgisBreakerMs,
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
      // A point far outside Karnataka is not a KGIS question, so do not spend a lookup
      // (or a Lambda slot) on one. Its answer would be an empty feature set, which is
      // indistinguishable from an outage, and an outage then reads as "we could not
      // determine the road class" for a street KGIS was never going to know.
      const inKarnataka = withinKarnatakaEnvelope(lat, lng);
      const askKgis = (url) => (inKarnataka ? kgis(url) : Promise.resolve(UNAVAILABLE));
      const [town, nh, sh, dh, geocoded] = await Promise.all([
        askKgis(pointUrl(KGIS_TOWN, lat, lng,
          "KGISTownName,Town_Type,KGISTownCode,LGD_TownCode")),
        askKgis(pointUrl(KGIS_NH, lat, lng, "Name", HIGHWAY_BUFFER_METRES)),
        askKgis(pointUrl(KGIS_SH, lat, lng, "Name", HIGHWAY_BUFFER_METRES)),
        askKgis(pointUrl(KGIS_DH, lat, lng, "Name", HIGHWAY_BUFFER_METRES)),
        geocoder
          ? readJson(fetchImpl, geocoder.url, { headers: geocoder.headers })
          : Promise.resolve({ available: false, data: null }),
      ]);
      const highwayLayers = [
        [nh, "national_highway"],
        [sh, "state_highway"],
        [dh, "district_highway"],
      ];
      const kgisAvailable = town.available && highwayLayers.every(([item]) => item.available);
      const townFeature = town.data?.features?.[0];
      const highway = highwayLayers.find(([item]) => item.data?.features?.[0]);
      const attrs = townFeature?.attributes || {};
      let lgd = attrs.LGD_TownCode == null ? "" : bounded(String(attrs.LGD_TownCode), 64);
      let townName = bounded(attrs.KGISTownName, 160) || null;
      let roadOwnership = "unknown";
      let highwayName = null;
      let ruralBody = null;
      let gpAvailable = false;
      let source = "unresolved";
      let local = !inKarnataka ? "out_of_scope" : "not_needed";
      if (!inKarnataka) {
        // Geography, not availability: this coordinate is hundreds of kilometres from
        // the Karnataka line. The client turns this into a regional-routing answer that
        // never asks the user to retry on a better signal.
        roadOwnership = "outside_state";
      } else if (kgisAvailable) {
        if (highway) {
          roadOwnership = highway[1];
          highwayName = bounded(highway[0].data.features[0]?.attributes?.Name, 160) || null;
        } else if (townFeature && lgd) {
          roadOwnership = "municipal";
          source = "kgis";
        } else if (!townFeature) {
          const gp = await askKgis(pointUrl(KGIS_GP, lat, lng, "KGISGPName"));
          gpAvailable = gp.available;
          ruralBody = bounded(gp.data?.features?.[0]?.attributes?.KGISGPName, 160) || null;
          roadOwnership = gp.available ? (ruralBody ? "rural" : "outside_state") : "unknown";
        }
      }
      if (inKarnataka && roadOwnership === "unknown") {
        // KGIS gave no verdict: a layer was down, stalled, malformed, or the breaker is
        // open. 227 of 450 tender lookups in the 30 days to 6 Oct 2026 ended here as a
        // 503. The same register's polygons, snapshotted, answer instead; only a point
        // they genuinely cannot place stays unknown.
        const geometry = await loadLocalGeometry(localGeometryPath, logger);
        if (!geometry) {
          local = "unavailable";
        } else {
          const verdict = classifyLocally(geometry, lat, lng);
          local = verdict.local;
          roadOwnership = verdict.road_ownership;
          highwayName = verdict.highway_name || null;
          lgd = verdict.lgd || "";
          townName = verdict.town || null;
          if (roadOwnership === "municipal") source = "kgis_snapshot";
        }
      }
      const municipal = roadOwnership === "municipal";
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
        source: municipal && lgd ? source : "unresolved",
        address_source: geocoded.available
          ? "operator_geocoder" : addressHint ? "client_hint" : "unresolved",
        road_ownership: roadOwnership,
        highway_name: highwayName,
        rural_body: ruralBody,
        lookup: {
          kgis: !inKarnataka ? "out_of_scope"
            : kgisAvailable ? "available" : "unavailable",
          kgis_town: !inKarnataka ? "out_of_scope"
            : town.available ? "available" : "unavailable",
          kgis_highway: !inKarnataka ? "out_of_scope"
            : highwayLayers.every(([item]) => item.available)
              ? "available" : "unavailable",
          kgis_gp: gpAvailable ? "available" : "not_needed_or_unavailable",
          local,
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
