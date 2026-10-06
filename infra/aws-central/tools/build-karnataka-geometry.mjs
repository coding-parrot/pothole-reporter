#!/usr/bin/env node
// Builds the geometry the central service answers Karnataka road ownership from when
// KGIS cannot. Two steps, both deterministic:
//
//   node infra/aws-central/tools/build-karnataka-geometry.mjs --snapshot
//     Reads the 319 urban local body polygons from the KGIS Town layer (the layer the
//     live lookup queries) into data/karnataka-town-polygons.json. Needs the network.
//
//   node infra/aws-central/tools/build-karnataka-geometry.mjs
//     Combines that snapshot, the app's pinned Karnataka state boundary (OpenStreetMap,
//     in-ka-state-routing) and the app's national highway tiles (OpenStreetMap) into
//     data/karnataka-local-geometry.json, the one file the Lambda package carries.
//
// Nothing here draws geometry: every ring and line is copied from a register the app or
// the service already trusts, and the bundle records the hash of each source so the
// test suite can tell when it has gone stale.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const SNAPSHOT_PATH = path.join(root, "data/karnataka-town-polygons.json");
const BUNDLE_PATH = path.join(root, "data/karnataka-local-geometry.json");
const SCALE = 100_000;
const KGIS_TOWN_LAYER = "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/Admin_Dynamic_New/MapServer/1";
const TOWN_FIELDS = "OBJECTID,KGISTownName,Town_Type,KGISTownCode,LGD_TownCode,KGISDistrictCode,last_edited_date";
// The app matches a fix to a highway centre line at 15 m when it has no GPS accuracy to
// widen it with (static/highway-manifest.json match.minimum_match_distance_m). The server
// is never told the accuracy, so it uses the same floor.
const HIGHWAY_MATCH_METRES = 15;

const sha256 = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");
const today = () => new Date().toISOString().slice(0, 10);

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function writeJsonIfChanged(file, value) {
  const bytes = Buffer.from(`${JSON.stringify(value)}\n`);
  if (fs.existsSync(file) && fs.readFileSync(file).equals(bytes)) {
    console.log(`unchanged ${path.relative(root, file)} (${bytes.length} bytes)`);
    return false;
  }
  fs.writeFileSync(file, bytes);
  console.log(`wrote ${path.relative(root, file)} (${bytes.length} bytes)`);
  return true;
}

// [x0, y0, dx1, dy1, ...] at SCALE, consecutive duplicates (after rounding) dropped.
function encodeRun(positions) {
  const out = [];
  let previousX = null;
  let previousY = null;
  for (const [lng, lat] of positions) {
    const x = Math.round(lng * SCALE);
    const y = Math.round(lat * SCALE);
    if (x === previousX && y === previousY) continue;
    if (previousX === null) out.push(x, y);
    else out.push(x - previousX, y - previousY);
    previousX = x;
    previousY = y;
  }
  return out;
}

function boxOfRuns(runs) {
  const box = [Infinity, Infinity, -Infinity, -Infinity];
  for (const run of runs) {
    let x = run[0];
    let y = run[1];
    const visit = () => {
      if (x < box[0]) box[0] = x;
      if (y < box[1]) box[1] = y;
      if (x > box[2]) box[2] = x;
      if (y > box[3]) box[3] = y;
    };
    visit();
    for (let index = 2; index < run.length; index += 2) {
      x += run[index];
      y += run[index + 1];
      visit();
    }
  }
  return box;
}

async function fetchTownPage(offset, size) {
  const url = `${KGIS_TOWN_LAYER}/query?where=1%3D1&outFields=${encodeURIComponent(TOWN_FIELDS)}`
    + "&returnGeometry=true&outSR=4326&geometryPrecision=6&orderByFields=OBJECTID"
    + `&resultOffset=${offset}&resultRecordCount=${size}&f=json`;
  const response = await fetch(url, { signal: AbortSignal.timeout(120_000) });
  if (!response.ok) throw new Error(`KGIS answered ${response.status} at offset ${offset}`);
  const page = await response.json();
  if (!Array.isArray(page.features)) {
    throw new Error(`KGIS answered without features at offset ${offset}: ${JSON.stringify(page).slice(0, 200)}`);
  }
  if (page.spatialReference?.wkid !== 4326) throw new Error("KGIS did not answer in WGS84");
  return page;
}

async function snapshot() {
  const countResponse = await fetch(`${KGIS_TOWN_LAYER}/query?where=1%3D1&returnCountOnly=true&f=json`,
    { signal: AbortSignal.timeout(30_000) });
  const expected = (await countResponse.json()).count;
  if (!Number.isInteger(expected) || expected < 300) throw new Error(`Town layer count is ${expected}`);
  const features = [];
  for (let offset = 0; offset < expected;) {
    const page = await fetchTownPage(offset, 20);
    if (!page.features.length) throw new Error(`KGIS returned an empty page at offset ${offset}`);
    features.push(...page.features);
    offset += page.features.length;
    console.log(`fetched ${offset} of ${expected}`);
  }
  if (features.length !== expected) throw new Error(`Fetched ${features.length} of ${expected} towns`);
  const seen = new Set();
  let lastEdited = 0;
  const towns = features.map(({ attributes, geometry }) => {
    if (!Array.isArray(geometry?.rings) || !geometry.rings.length) {
      throw new Error(`Town ${attributes.KGISTownName} has no rings`);
    }
    const lgd = attributes.LGD_TownCode == null ? null : Number(attributes.LGD_TownCode);
    if (lgd !== null) {
      if (!Number.isInteger(lgd) || seen.has(lgd)) throw new Error(`Bad or repeated LGD code ${lgd}`);
      seen.add(lgd);
    }
    lastEdited = Math.max(lastEdited, Number(attributes.last_edited_date) || 0);
    const rings = geometry.rings.map(encodeRun).filter((run) => run.length >= 8);
    if (!rings.length) throw new Error(`Town ${attributes.KGISTownName} collapsed to nothing`);
    return {
      objectid: attributes.OBJECTID,
      lgd,
      name: String(attributes.KGISTownName || "").trim(),
      type: String(attributes.Town_Type || "").trim(),
      kgis_code: attributes.KGISTownCode == null ? null : String(attributes.KGISTownCode),
      district: attributes.KGISDistrictCode == null ? null : String(attributes.KGISDistrictCode),
      bbox: boxOfRuns(rings),
      rings,
    };
  }).sort((left, right) => (left.lgd ?? Infinity) - (right.lgd ?? Infinity) || left.objectid - right.objectid);
  writeJsonIfChanged(SNAPSHOT_PATH, {
    _comment: "Karnataka urban local body polygons copied from the KGIS Town layer, the layer "
      + "the central service queries live. The service reads them only when KGIS cannot "
      + "answer. Coordinates are WGS84 integers at coordinate_scale, each ring encoded as "
      + "[x0, y0, dx1, dy1, ...]. Rebuild with: node infra/aws-central/tools/build-karnataka-geometry.mjs --snapshot",
    format: "pothole-kgis-town-polygons",
    schema_version: 1,
    source: KGIS_TOWN_LAYER,
    source_fields: TOWN_FIELDS,
    source_last_edited: new Date(lastEdited).toISOString().slice(0, 10),
    retrieved_at: today(),
    spatial_reference: 4326,
    coordinate_scale: SCALE,
    count: towns.length,
    towns,
  });
}

function newestPackManifest() {
  const dir = path.join(root, "static");
  const versioned = fs.readdirSync(dir)
    .map((name) => name.match(/^pack-manifest-v(\d+)\.(\d+)\.json$/))
    .filter(Boolean)
    .sort((left, right) => Number(left[1]) - Number(right[1]) || Number(left[2]) - Number(right[2]));
  if (!versioned.length) throw new Error("No versioned pack manifest in static/");
  return path.join(dir, versioned[versioned.length - 1][0]);
}

function stateBoundary() {
  const manifestPath = newestPackManifest();
  const resource = readJson(manifestPath).resources?.["in-ka-state-routing"];
  if (!resource?.path) throw new Error(`${path.basename(manifestPath)} has no in-ka-state-routing`);
  const packPath = path.join(root, "docs", resource.path);
  const bytes = fs.readFileSync(packPath);
  if (sha256(bytes) !== resource.sha256) throw new Error("State routing pack hash mismatch");
  const region = JSON.parse(bytes).payload?.region;
  const geometry = region?.geometry;
  if (!geometry || !["Polygon", "MultiPolygon"].includes(geometry.type)) {
    throw new Error("State routing pack has no polygon");
  }
  const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  const rings = polygons.flat().map(encodeRun).filter((run) => run.length >= 8);
  return {
    pack_id: resource.pack_id,
    pack_path: resource.path,
    pack_sha256: resource.sha256,
    manifest: path.basename(manifestPath),
    osm_relation_id: region.osm_relation_id,
    geometry_sha256: region.geometry_sha256,
    source_name: region.source_name,
    source_license: region.source_license,
    attribution: region.attribution,
    bbox: boxOfRuns(rings),
    rings,
  };
}

function nationalHighways(stateBox) {
  const manifest = readJson(path.join(root, "static/highway-manifest.json"));
  if (manifest.match?.minimum_match_distance_m !== HIGHWAY_MATCH_METRES) {
    throw new Error("The highway manifest's minimum match distance moved; update HIGHWAY_MATCH_METRES");
  }
  // A feature's box may cross the state line by up to the match distance and still be
  // the carriageway a border fix sits on. 0.001 degrees is about 110 m.
  const pad = Math.ceil(0.001 * SCALE);
  const tiles = [];
  const features = [];
  for (const [tileId, resource] of Object.entries(manifest.tiles)) {
    const [west, south, east, north] = resource.bbox.map((value) => value * SCALE);
    if (east < stateBox[0] - pad || west > stateBox[2] + pad
        || north < stateBox[1] - pad || south > stateBox[3] + pad) continue;
    const bytes = fs.readFileSync(path.join(root, "docs", resource.path));
    if (sha256(bytes) !== resource.sha256) throw new Error(`Highway tile ${tileId} hash mismatch`);
    const tile = JSON.parse(bytes);
    if (tile.coordinate_scale !== SCALE) throw new Error(`Highway tile ${tileId} is not at scale ${SCALE}`);
    let kept = 0;
    for (const feature of tile.features) {
      const box = feature[1];
      if (box[2] < stateBox[0] - pad || box[0] > stateBox[2] + pad
          || box[3] < stateBox[1] - pad || box[1] > stateBox[3] + pad) continue;
      features.push(feature);
      kept += 1;
    }
    tiles.push({ tile_id: tileId, sha256: resource.sha256, features: tile.features.length, kept });
  }
  if (!tiles.length) throw new Error("No highway tile touches Karnataka");
  return {
    classes: ["national_highway"],
    match_metres: HIGHWAY_MATCH_METRES,
    source_name: manifest.source.source_name,
    source_retrieved_at: manifest.source.source_retrieved_at,
    source_license: manifest.source.source_license,
    attribution: manifest.source.attribution,
    tiles,
    features,
  };
}

function bundle() {
  const snapshotBytes = fs.readFileSync(SNAPSHOT_PATH);
  const towns = JSON.parse(snapshotBytes);
  if (towns.format !== "pothole-kgis-town-polygons" || towns.coordinate_scale !== SCALE) {
    throw new Error("Unexpected town snapshot format");
  }
  const state = stateBoundary();
  const highways = nationalHighways(state.bbox);
  writeJsonIfChanged(BUNDLE_PATH, {
    _comment: "Generated by infra/aws-central/tools/build-karnataka-geometry.mjs from "
      + "data/karnataka-town-polygons.json (KGIS), the in-ka-state-routing pack (OpenStreetMap) "
      + "and the national highway tiles (OpenStreetMap). The central service reads it when "
      + "KGIS cannot answer a Karnataka lookup. Do not edit by hand.",
    format: "pothole-karnataka-local-geometry",
    schema_version: 1,
    generated_at: today(),
    coordinate_scale: SCALE,
    towns: {
      source: towns.source,
      retrieved_at: towns.retrieved_at,
      source_last_edited: towns.source_last_edited,
      snapshot_sha256: sha256(snapshotBytes),
      count: towns.count,
      with_lgd: towns.towns.filter((town) => town.lgd !== null).length,
      features: towns.towns.map(({ lgd, name, type, kgis_code: kgisCode, bbox, rings }) => ({
        lgd, name, type, kgis_code: kgisCode, bbox, rings,
      })),
    },
    state,
    highways,
  });
}

const args = new Set(process.argv.slice(2));
if (args.has("--snapshot")) {
  await snapshot();
  args.delete("--snapshot");
}
if (args.size) throw new Error(`Unknown arguments: ${[...args].join(" ")}`);
bundle();
