#!/usr/bin/env node
// Builds the geometry the central service answers Karnataka road ownership from. The
// request path never asks KGIS: everything it would have asked is copied here first.
//
//   node infra/aws-central/tools/build-karnataka-geometry.mjs --refresh
//     The weekly refresh. Re-reads every KGIS layer below, then rebuilds both bundles.
//     Needs the network; safe to stop and run again (a partial download resumes).
//
//   node infra/aws-central/tools/build-karnataka-geometry.mjs
//     No network. Rebuilds data/karnataka-ownership.bin from the town snapshot, the app's
//     pinned Karnataka state boundary (OpenStreetMap, in-ka-state-routing) and the highway
//     and panchayat polygons the bundle already holds, and data/karnataka-ward-geometry.json
//     from the ward snapshot.
//
// --refresh is these three, which can also be run one at a time:
//
//   --snapshot
//     The 319 urban local body polygons of the KGIS Town layer into
//     data/karnataka-town-polygons.json.
//
//   --snapshot-wards
//     Every polygon of the KGIS "Ward New" layer (7,421 wards in 309 towns on 6 Oct 2026)
//     into data/karnataka-ward-polygons.json, grouped by town into
//     data/karnataka-ward-geometry.json, which names a municipal point's ward.
//
//   --snapshot-ownership
//     The three highway land-cover layers (national 289, state 290, district 291) and the
//     gram panchayat layer, whole, into the work directory data/.kgis-work (gitignored),
//     one request at a time, then into data/karnataka-ownership.bin. --from-work rebuilds
//     from a download that is already complete, without the network.
//
// Nothing here draws geometry: every ring is copied from a register the app or the
// service already trusts, and the bundle records where each layer came from, how many
// features KGIS counted, and a hash of what was stored, so the test suite can tell when
// it has gone stale or been edited.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

import { loadOwnershipBundle, polygonRings } from "../service/local-ownership.mjs";
import { decodeRun } from "../service/spatial.mjs";
import { KGIS_LAYERS, downloadLayer, politeJson } from "./kgis-layers.mjs";
import { createOwnershipWriter } from "./ownership-bundle-writer.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const SNAPSHOT_PATH = path.join(root, "data/karnataka-town-polygons.json");
const OWNERSHIP_PATH = path.join(root, "data/karnataka-ownership.bin");
const WORK_DIR = path.join(root, "data/.kgis-work");
const WARD_SNAPSHOT_PATH = path.join(root, "data/karnataka-ward-polygons.json");
const WARD_BUNDLE_PATH = path.join(root, "data/karnataka-ward-geometry.json");
const SCALE = 100_000;
const KGIS_TOWN_LAYER = "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/Admin_Dynamic_New/MapServer/1";
const TOWN_FIELDS = "OBJECTID,KGISTownName,Town_Type,KGISTownCode,LGD_TownCode,KGISDistrictCode,last_edited_date";
const KGIS_WARD_LAYER = "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/Admin_Dynamic_New/MapServer/2";
const WARD_FIELDS = "OBJECTID,KGISWardID,KGISWardCode,LGD_WardCode,KGISWardNo,KGISWardName,KGISTownCode,last_edited_date";
// Towns, panchayats and the state are only ever asked "is the point inside", so the
// 1.1 m grid the town snapshot already uses is enough for them. A highway is asked "is
// the point within 5 m" and its polygons are about 8 m wide, so those are stored twice as
// finely: no stored vertex is more than 0.39 m from where KGIS has it. Measured on
// 7 Oct 2026, highways at 1/100,000 degree made the file 34 MB, at this scale 38 MB and
// at 1/1,000,000 degree 46 MB.
const HIGHWAY_SCALE = 200_000;

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

// The town and ward layers are small enough to hold in memory, so they are read straight
// into their snapshots, through the same polite client as the large layers: one request
// at a time, a pause after each, retries with backoff.
async function fetchPage(layer, fields, offset, size) {
  const page = await politeJson(`${layer}/query?where=1%3D1&outFields=${encodeURIComponent(fields)}`
    + "&returnGeometry=true&outSR=4326&geometryPrecision=6&orderByFields=OBJECTID"
    + `&resultOffset=${offset}&resultRecordCount=${size}&f=json`, { timeoutMs: 120_000, attempts: 4 });
  if (!Array.isArray(page.features)) {
    throw new Error(`KGIS answered without features at offset ${offset}: ${JSON.stringify(page).slice(0, 200)}`);
  }
  if (page.spatialReference?.wkid !== 4326) throw new Error("KGIS did not answer in WGS84");
  return page;
}

async function fetchLayer(layer, fields, pageSize, minimum) {
  const expected = (await politeJson(`${layer}/query?where=1%3D1&returnCountOnly=true&f=json`, { timeoutMs: 30_000 })).count;
  if (!Number.isInteger(expected) || expected < minimum) throw new Error(`${layer} count is ${expected}`);
  const features = [];
  for (let offset = 0; offset < expected;) {
    const page = await fetchPage(layer, fields, offset, pageSize);
    if (!page.features.length) throw new Error(`KGIS returned an empty page at offset ${offset}`);
    features.push(...page.features);
    offset += page.features.length;
    console.log(`fetched ${offset} of ${expected}`);
  }
  if (features.length !== expected) throw new Error(`Fetched ${features.length} of ${expected} features`);
  return features;
}

async function snapshot() {
  const features = await fetchLayer(KGIS_TOWN_LAYER, TOWN_FIELDS, 20, 300);
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

async function snapshotWards() {
  const features = await fetchLayer(KGIS_WARD_LAYER, WARD_FIELDS, 200, 7_000);
  const seen = new Set();
  let lastEdited = 0;
  const wards = features.map(({ attributes, geometry }) => {
    const code = String(attributes.KGISWardCode || "").trim();
    const townCode = String(attributes.KGISTownCode || "").trim();
    if (!code || !townCode || seen.has(code)) throw new Error(`Bad or repeated ward code ${code}`);
    seen.add(code);
    if (!Array.isArray(geometry?.rings) || !geometry.rings.length) throw new Error(`Ward ${code} has no rings`);
    lastEdited = Math.max(lastEdited, Number(attributes.last_edited_date) || 0);
    const rings = geometry.rings.map(encodeRun).filter((run) => run.length >= 8);
    if (!rings.length) throw new Error(`Ward ${code} collapsed to nothing`);
    return {
      objectid: attributes.OBJECTID,
      ward_id: attributes.KGISWardID ?? null,
      code,
      lgd_ward: attributes.LGD_WardCode == null ? null : Number(attributes.LGD_WardCode),
      // KGIS's own number and name, as published. For the five Bengaluru corporations
      // (town codes 20G1 to 20G5) the name reads "41 - Munnenkolalu" and the number is
      // the Greater Bengaluru numbering, not the old BBMP numbering tender titles use.
      no: String(attributes.KGISWardNo ?? "").trim() || null,
      name: String(attributes.KGISWardName ?? "").trim() || null,
      town_code: townCode,
      bbox: boxOfRuns(rings),
      rings,
    };
  }).sort((left, right) => (left.code < right.code ? -1 : left.code > right.code ? 1 : 0));
  writeJsonIfChanged(WARD_SNAPSHOT_PATH, {
    _comment: "Karnataka ward polygons copied from the KGIS Ward New layer. The central service "
      + "reads them (through data/karnataka-ward-geometry.json) to name the ward a municipal "
      + "point is in. Coordinates are WGS84 integers at coordinate_scale, each ring encoded as "
      + "[x0, y0, dx1, dy1, ...]. Rebuild with: node infra/aws-central/tools/build-karnataka-geometry.mjs --snapshot-wards",
    format: "pothole-kgis-ward-polygons",
    schema_version: 1,
    source: KGIS_WARD_LAYER,
    source_fields: WARD_FIELDS,
    source_last_edited: new Date(lastEdited).toISOString().slice(0, 10),
    retrieved_at: today(),
    spatial_reference: 4326,
    coordinate_scale: SCALE,
    count: wards.length,
    wards,
  });
}

// The file the Lambda carries: the same wards, grouped under their town's KGIS code so a
// lookup that already knows the town tests only that town's wards. No date of its own:
// it changes only when the snapshot does.
function wardBundle() {
  if (!fs.existsSync(WARD_SNAPSHOT_PATH)) {
    console.log("no ward snapshot; run with --snapshot-wards to create one");
    return;
  }
  const snapshotBytes = fs.readFileSync(WARD_SNAPSHOT_PATH);
  const snapshot = JSON.parse(snapshotBytes);
  if (snapshot.format !== "pothole-kgis-ward-polygons" || snapshot.coordinate_scale !== SCALE) {
    throw new Error("Unexpected ward snapshot format");
  }
  const towns = {};
  for (const ward of snapshot.wards) {
    const town = towns[ward.town_code] || (towns[ward.town_code] = { bbox: [Infinity, Infinity, -Infinity, -Infinity], wards: [] });
    town.bbox = [
      Math.min(town.bbox[0], ward.bbox[0]), Math.min(town.bbox[1], ward.bbox[1]),
      Math.max(town.bbox[2], ward.bbox[2]), Math.max(town.bbox[3], ward.bbox[3]),
    ];
    town.wards.push([ward.code, ward.no, ward.name, ward.bbox, ward.rings]);
  }
  writeJsonIfChanged(WARD_BUNDLE_PATH, {
    _comment: "Generated by infra/aws-central/tools/build-karnataka-geometry.mjs from "
      + "data/karnataka-ward-polygons.json (KGIS Ward New layer). The central service reads it "
      + "to name the ward of a municipal point. Each ward is [code, number, name, bbox, rings]. "
      + "Do not edit by hand.",
    format: "pothole-karnataka-ward-geometry",
    schema_version: 1,
    coordinate_scale: SCALE,
    source: snapshot.source,
    retrieved_at: snapshot.retrieved_at,
    source_last_edited: snapshot.source_last_edited,
    snapshot_sha256: sha256(snapshotBytes),
    numbering: "kgis_current",
    count: snapshot.count,
    named: snapshot.wards.filter((ward) => ward.name).length,
    town_count: Object.keys(towns).length,
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


const pointsOfRun = (run) => decodeRun(run);

// The highway and panchayat polygons as KGIS gave them, from a completed download in the
// work directory: one feature a line, in OBJECTID order.
async function* downloadedPolygons(layer, scale) {
  const lines = readline.createInterface({
    input: fs.createReadStream(path.join(WORK_DIR, `${layer.key}.ndjson`)), crlfDelay: Infinity,
  });
  for await (const line of lines) {
    if (!line) continue;
    const { attributes, rings } = JSON.parse(line);
    yield {
      objectid: attributes.OBJECTID,
      name: String(attributes.Name ?? attributes.KGISGPName ?? "").trim() || null,
      rings: rings.map((ring) => ring.map(([lng, lat]) => [Math.round(lng * scale), Math.round(lat * scale)])),
    };
  }
}

// The same polygons out of the bundle already on disk, when nothing was downloaded.
function* bundledPolygons(bundle, layer) {
  for (let polygon = layer.first_polygon; polygon < layer.first_polygon + layer.polygons; polygon += 1) {
    const attribute = bundle.polyAttr[polygon];
    yield {
      objectid: bundle.polyObjectId[polygon],
      name: attribute === 0xffffffff ? null : layer.names[attribute],
      rings: polygonRings(bundle, polygon).map((ring) => Array.from({ length: ring.length / 2 },
        (unused, index) => [ring[index * 2] / layer.step, ring[index * 2 + 1] / layer.step])),
    };
  }
}

async function ownershipBundle({ fromWork }) {
  const townBytes = fs.readFileSync(SNAPSHOT_PATH);
  const towns = JSON.parse(townBytes);
  if (towns.format !== "pothole-kgis-town-polygons" || towns.coordinate_scale !== SCALE) {
    throw new Error("Unexpected town snapshot format");
  }
  const previous = fromWork ? null
    : fs.existsSync(OWNERSHIP_PATH) ? await loadOwnershipBundle(OWNERSHIP_PATH) : null;
  if (!fromWork && !previous) {
    throw new Error("No data/karnataka-ownership.bin to rebuild from; run with --snapshot-ownership");
  }
  const scale = Math.max(SCALE, fromWork ? HIGHWAY_SCALE
    : previous.layers.find((layer) => layer.name === "national_highway").scale);
  const writer = createOwnershipWriter({ scale });

  const state = stateBoundary();
  const { rings: stateRings, ...stateSource } = state;
  writer.beginLayer("state", { scale: SCALE, ...stateSource });
  writer.addPolygon({ objectid: 0, rings: stateRings.map(pointsOfRun) });
  writer.endLayer();

  const kgisLayer = async (name) => {
    const kgis = KGIS_LAYERS[name];
    const layerScale = name === "gram_panchayat" ? SCALE : scale;
    let provenance;
    let polygons;
    let carried = {};
    if (fromWork) {
      const doneFile = path.join(WORK_DIR, `${kgis.key}.done.json`);
      if (!fs.existsSync(doneFile)) throw new Error(`${name} has no completed download in ${path.relative(root, WORK_DIR)}`);
      const done = readJson(doneFile);
      if (done.count !== done.kgis_count) throw new Error(`${name}: ${done.count} features read, KGIS counted ${done.kgis_count}`);
      provenance = {
        source: done.source, layer_id: done.layer_id, source_fields: done.source_fields,
        retrieved_at: done.retrieved_at, source_last_edited: done.source_last_edited,
        kgis_count: done.kgis_count, raw_bytes: done.raw_bytes, raw_sha256: done.raw_sha256,
      };
      polygons = downloadedPolygons(kgis, layerScale);
    } else {
      const layer = previous.layers.find((entry) => entry.name === name);
      provenance = Object.fromEntries(["source", "layer_id", "source_fields", "retrieved_at",
        "source_last_edited", "kgis_count", "raw_bytes", "raw_sha256"].map((key) => [key, layer[key]]));
      polygons = bundledPolygons(previous, layer);
      // How many vertices KGIS sent is a fact about the download, not about this rebuild.
      carried = { source_vertices: layer.source_vertices };
    }
    writer.beginLayer(name, { scale: layerScale, ...provenance });
    const names = [];
    const nameIndex = new Map();
    let count = 0;
    let unnamed = 0;
    for await (const polygon of polygons) {
      let attribute = 0xffffffff;
      if (polygon.name === null) unnamed += 1;
      else {
        if (!nameIndex.has(polygon.name)) nameIndex.set(polygon.name, names.push(polygon.name) - 1);
        attribute = nameIndex.get(polygon.name);
      }
      writer.addPolygon({ objectid: polygon.objectid, attribute, rings: polygon.rings });
      count += 1;
    }
    if (count !== provenance.kgis_count) throw new Error(`${name}: ${count} polygons, KGIS counted ${provenance.kgis_count}`);
    writer.endLayer({ count, unnamed, names, ...carried });
    console.log(`${name}: ${count} polygons`);
  };
  for (const name of ["national_highway", "state_highway", "district_highway"]) await kgisLayer(name);

  writer.beginLayer("town", {
    scale: SCALE,
    source: towns.source,
    retrieved_at: towns.retrieved_at,
    source_last_edited: towns.source_last_edited,
    snapshot_sha256: sha256(townBytes),
    count: towns.count,
    with_lgd: towns.towns.filter((town) => town.lgd !== null).length,
    towns: towns.towns.map(({ lgd, name, type, kgis_code: kgisCode }) => ({ lgd, name, type, kgis_code: kgisCode })),
  });
  towns.towns.forEach((town, index) => {
    writer.addPolygon({ objectid: town.objectid, attribute: index, rings: town.rings.map(pointsOfRun) });
  });
  writer.endLayer();

  await kgisLayer("gram_panchayat");

  const result = writer.finish(OWNERSHIP_PATH, {
    _comment: "Generated by infra/aws-central/tools/build-karnataka-geometry.mjs. Karnataka road "
      + "ownership as KGIS publishes it: the state (OpenStreetMap, in-ka-state-routing), the "
      + "KGIS towns, gram panchayats and national, state and district highway land cover. "
      + "The central service answers every Karnataka lookup from it. Do not edit by hand.",
  });
  console.log(`${result.changed ? "wrote" : "unchanged"} ${path.relative(root, OWNERSHIP_PATH)} (${result.size} bytes)`);
}

const args = new Set(process.argv.slice(2));
const take = (flag) => args.delete(flag);
const refresh = take("--refresh");
const snapshotTowns = take("--snapshot") || refresh;
const snapshotWardLayer = take("--snapshot-wards") || refresh;
const snapshotOwnership = take("--snapshot-ownership") || refresh;
const fromWork = take("--from-work") || snapshotOwnership;
if (args.size) throw new Error(`Unknown arguments: ${[...args].join(" ")}`);
if (snapshotTowns) await snapshot();
if (snapshotWardLayer) await snapshotWards();
if (snapshotOwnership) {
  // A download finished on an earlier day is a previous refresh: start again. One
  // finished today, or one that was stopped part way, is this refresh: carry on.
  for (const name of Object.keys(KGIS_LAYERS)) {
    const doneFile = path.join(WORK_DIR, `${KGIS_LAYERS[name].key}.done.json`);
    const stale = fs.existsSync(doneFile) && readJson(doneFile).retrieved_at !== today();
    await downloadLayer(name, { workDir: WORK_DIR, restart: stale });
  }
}
await ownershipBundle({ fromWork });
wardBundle();
