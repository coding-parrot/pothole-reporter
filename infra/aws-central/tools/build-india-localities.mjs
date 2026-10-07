#!/usr/bin/env node
// Builds data/wards/india-localities.json: every named place OpenStreetMap holds for
// India (city, town, village, hamlet, suburb, neighbourhood, quarter, locality) as a
// point, filed under its State. It is the fallback where no ward polygons exist: the
// places near a pothole are names a tender title can be searched for, with no network
// call. Offline tool: the service does not import it.
//
//   node infra/aws-central/tools/build-india-localities.mjs [--pbf <file>] [--download]
//
// Needs the `osmium` command (osmium-tool) and the Geofabrik India extract, about
// 1.7 GB, kept under data/wards/.work/ (gitignored). --download fetches
// https://download.geofabrik.de/asia/india-latest.osm.pbf there first (one request,
// streamed to disk). Without --pbf the newest india-*.osm.pbf in .work is read.
//
// What is kept: OpenStreetMap objects tagged place=<one of the eight kinds> that carry a
// name in Latin letters (name:en, else name when it is written in Latin letters). A node
// is its own point; an area (a village or a neighbourhood drawn as a polygon) is the
// centre of its bounding box. A place is filed under the State whose outline holds it,
// the outline being the one the app already ships in its routing packs. Nothing is
// invented: a place with no Latin-letter name is counted and left out, not transliterated.
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

import { USER_AGENT, WARDS_DIR, WORK_DIR, root } from "./snapshot-india-wards.mjs";

export const LOCALITIES_PATH = path.join(WARDS_DIR, "india-localities.json");
export const LOCALITIES_FORMAT = "pothole-india-localities";
export const SCALE = 100_000;
// One letter per kind, in the order a lookup should prefer them inside a town.
export const KINDS = {
  neighbourhood: "n", quarter: "q", suburb: "s", locality: "l", hamlet: "h", village: "v", town: "t", city: "c",
};
const GEOFABRIK = "https://download.geofabrik.de/asia/india-latest.osm.pbf";
// Two objects of one name and kind this close are one place mapped twice (a node and the
// polygon it labels). 0.003 degrees is about 330 m.
const SAME_PLACE = 0.003 * SCALE;

const today = () => new Date().toISOString().slice(0, 10);

function fileSha256(file) {
  const hash = crypto.createHash("sha256");
  const handle = fs.openSync(file, "r");
  const buffer = Buffer.allocUnsafe(8 << 20);
  for (let read = fs.readSync(handle, buffer); read > 0; read = fs.readSync(handle, buffer)) hash.update(buffer.subarray(0, read));
  fs.closeSync(handle);
  return hash.digest("hex");
}

// The State outlines the app ships (OpenStreetMap, in the routing packs), each with its
// edges bucketed by latitude so that a point is tested against a few dozen edges.
const BAND = 0.02;
function loadStates() {
  const dir = path.join(root, "static");
  const newest = fs.readdirSync(dir).map((name) => name.match(/^pack-manifest-v(\d+)\.(\d+)\.json$/)).filter(Boolean)
    .sort((left, right) => Number(left[1]) - Number(right[1]) || Number(left[2]) - Number(right[2])).pop()[0];
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, newest), "utf8"));
  const states = [];
  for (const resource of Object.values(manifest.resources)) {
    if (resource.kind !== "routing") continue;
    const payload = JSON.parse(fs.readFileSync(path.join(root, "docs", resource.path), "utf8")).payload;
    for (const region of [payload?.region, ...Object.values(payload?.regions || {})].filter(Boolean)) {
      if (!/^Full (?:State|Union Territory)|^Delhi NCT/.test(String(region.scope || "")) || !region.geometry) continue;
      const polygons = region.geometry.type === "Polygon" ? [region.geometry.coordinates] : region.geometry.coordinates;
      const bands = new Map();
      const box = [Infinity, Infinity, -Infinity, -Infinity];
      for (const ring of polygons.flat()) {
        for (let index = 1; index < ring.length; index += 1) {
          const [x1, y1] = ring[index - 1];
          const [x2, y2] = ring[index];
          box[0] = Math.min(box[0], x1, x2);
          box[1] = Math.min(box[1], y1, y2);
          box[2] = Math.max(box[2], x1, x2);
          box[3] = Math.max(box[3], y1, y2);
          for (let band = Math.floor(Math.min(y1, y2) / BAND); band <= Math.floor(Math.max(y1, y2) / BAND); band += 1) {
            if (!bands.has(band)) bands.set(band, []);
            bands.get(band).push(x1, y1, x2, y2);
          }
        }
      }
      states.push({ code: resource.state_code, name: region.name, pack_id: resource.pack_id, pack_sha256: resource.sha256, box, bands });
    }
  }
  return { manifest: newest, states };
}

function stateOf(states, lng, lat) {
  for (const state of states) {
    if (lng < state.box[0] || lng > state.box[2] || lat < state.box[1] || lat > state.box[3]) continue;
    const edges = state.bands.get(Math.floor(lat / BAND));
    if (!edges) continue;
    let inside = false;
    for (let index = 0; index < edges.length; index += 4) {
      const x1 = edges[index];
      const y1 = edges[index + 1];
      const x2 = edges[index + 2];
      const y2 = edges[index + 3];
      if ((y1 > lat) !== (y2 > lat) && lng < x1 + ((lat - y1) * (x2 - x1)) / (y2 - y1)) inside = !inside;
    }
    if (inside) return state.code;
  }
  return null;
}

// The name a Latin-letter tender title could carry, or null.
const LATIN = /^[\p{Script=Latin}\p{N}\p{P}\p{Zs}]+$/u;
export function latinName(tags) {
  for (const key of ["name:en", "name", "int_name", "official_name:en"]) {
    const value = String(tags[key] || "").replace(/[\u2013\u2014]/g, "-").replace(/\s+/g, " ").trim();
    if (value.length >= 2 && value.length <= 80 && /\p{Script=Latin}/u.test(value) && LATIN.test(value)) return value;
  }
  return null;
}

async function main() {
  const args = process.argv.slice(2);
  const value = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : null);
  fs.mkdirSync(WORK_DIR, { recursive: true });
  if (args.includes("--download")) {
    const response = await fetch(GEOFABRIK, { headers: { "User-Agent": USER_AGENT } });
    if (!response.ok) throw new Error(`Geofabrik answered ${response.status}`);
    const name = path.basename(new URL(response.url).pathname);
    const out = fs.createWriteStream(path.join(WORK_DIR, name));
    for await (const chunk of response.body) if (!out.write(chunk)) await new Promise((resolve) => out.once("drain", resolve));
    await new Promise((resolve) => out.end(resolve));
    console.log(`downloaded ${name}`);
  }
  const pbf = value("--pbf") || fs.readdirSync(WORK_DIR).filter((name) => /^india-.*\.osm\.pbf$/.test(name)).sort()
    .map((name) => path.join(WORK_DIR, name)).pop();
  if (!pbf || !fs.existsSync(pbf)) throw new Error("No India extract: pass --pbf <file> or --download");
  const filtered = path.join(WORK_DIR, "places-nwr.osm.pbf");
  const exported = path.join(WORK_DIR, "places-nwr.geojsonl");
  execFileSync("osmium", ["tags-filter", pbf, "nwr/place", "-o", filtered, "--overwrite"], { stdio: "inherit" });
  execFileSync("osmium", ["export", filtered, "-f", "geojsonseq", "-o", exported, "-u", "type_id", "-x", "print_record_separator=false", "--overwrite"], { stdio: "inherit" });
  const osmTimestamp = execFileSync("osmium", ["fileinfo", "-g", "header.option.osmosis_replication_timestamp", pbf]).toString().trim() || null;

  const { manifest, states } = loadStates();
  const counts = { objects: 0, other_kind: 0, unnamed: 0, no_latin_name: 0, way_outline: 0, outside_every_state: 0, mapped_twice: 0, kept: 0 };
  const byState = new Map(states.map((state) => [state.code, []]));
  const lines = readline.createInterface({ input: fs.createReadStream(exported) });
  for await (const line of lines) {
    if (!line) continue;
    const feature = JSON.parse(line);
    const tags = feature.properties;
    if (!tags.place) continue;
    counts.objects += 1;
    const kind = KINDS[tags.place];
    if (!kind) {
      counts.other_kind += 1;
      continue;
    }
    // A closed way is exported twice: as its outline and as the area it encloses.
    if (feature.geometry.type !== "Point" && String(feature.id)[0] !== "a") {
      counts.way_outline += 1;
      continue;
    }
    if (!tags.name && !tags["name:en"]) {
      counts.unnamed += 1;
      continue;
    }
    const name = latinName(tags);
    if (!name) {
      counts.no_latin_name += 1;
      continue;
    }
    let lng;
    let lat;
    if (feature.geometry.type === "Point") [lng, lat] = feature.geometry.coordinates;
    else {
      const box = [Infinity, Infinity, -Infinity, -Infinity];
      const polygons = feature.geometry.type === "Polygon" ? [feature.geometry.coordinates] : feature.geometry.coordinates;
      for (const [x, y] of polygons.flatMap((polygon) => polygon[0])) {
        box[0] = Math.min(box[0], x);
        box[1] = Math.min(box[1], y);
        box[2] = Math.max(box[2], x);
        box[3] = Math.max(box[3], y);
      }
      lng = (box[0] + box[2]) / 2;
      lat = (box[1] + box[3]) / 2;
    }
    const state = stateOf(states, lng, lat);
    if (!state) {
      counts.outside_every_state += 1;
      continue;
    }
    byState.get(state).push({ name, kind, x: Math.round(lng * SCALE), y: Math.round(lat * SCALE), node: feature.geometry.type === "Point" });
  }

  const out = {};
  const kindCounts = Object.fromEntries(Object.values(KINDS).map((letter) => [letter, 0]));
  for (const state of states.slice().sort((left, right) => (left.code < right.code ? -1 : 1))) {
    // South to north, then west to east, so a lookup can cut the list by latitude.
    const places = byState.get(state.code).sort((left, right) => left.y - right.y || left.x - right.x || (left.name < right.name ? -1 : 1) || Number(right.node) - Number(left.node));
    const kept = [];
    const recent = new Map();
    for (const place of places) {
      const slot = `${place.kind} ${place.name.toLowerCase()}`;
      const twins = recent.get(slot) || [];
      if (twins.some((twin) => Math.abs(twin.x - place.x) <= SAME_PLACE && Math.abs(twin.y - place.y) <= SAME_PLACE)) {
        counts.mapped_twice += 1;
        continue;
      }
      twins.push(place);
      recent.set(slot, twins);
      kept.push(place);
    }
    counts.kept += kept.length;
    for (const place of kept) kindCounts[place.kind] += 1;
    out[state.code] = {
      name: state.name,
      count: kept.length,
      n: kept.map((place) => place.name),
      k: kept.map((place) => place.kind).join(""),
      x: kept.map((place) => place.x),
      y: kept.map((place) => place.y),
    };
  }
  const bundle = {
    _comment: "Named places of India from OpenStreetMap, as points, filed under the State whose outline holds them. "
      + "Per State four parallel lists sorted south to north then west to east: n (name in Latin letters), k (one letter "
      + "per place, see kinds), x and y (WGS84 longitude and latitude as integers at coordinate_scale). Generated by "
      + "infra/aws-central/tools/build-india-localities.mjs. Do not edit by hand.",
    format: LOCALITIES_FORMAT,
    schema_version: 1,
    coordinate_scale: SCALE,
    kinds: Object.fromEntries(Object.entries(KINDS).map(([kind, letter]) => [letter, kind])),
    provenance: {
      source_url: GEOFABRIK,
      source_file: path.basename(pbf),
      source_page: "https://download.geofabrik.de/asia/india.html",
      publisher: "OpenStreetMap contributors (extract by Geofabrik GmbH)",
      licence: "Open Data Commons Open Database License (ODbL) 1.0",
      licence_url: "https://opendatacommons.org/licenses/odbl/1-0/",
      licence_status: "open",
      attribution: "© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)",
      retrieved_at: fs.statSync(pbf).mtime.toISOString().slice(0, 10),
      osm_data_as_of: osmTimestamp,
      raw_sha256: fileSha256(pbf),
      raw_bytes: fs.statSync(pbf).size,
      filter: "osmium tags-filter nwr/place; kinds kept: " + Object.keys(KINDS).join(", "),
      state_outlines: `${manifest} routing packs (OpenStreetMap State relations)`,
      generated_at: today(),
      counts,
    },
    count: counts.kept,
    kind_counts: kindCounts,
    states: out,
  };
  const bytes = Buffer.from(`${JSON.stringify(bundle)}\n`);
  if (fs.existsSync(LOCALITIES_PATH) && fs.readFileSync(LOCALITIES_PATH).equals(bytes)) {
    console.log(`unchanged ${path.relative(root, LOCALITIES_PATH)} (${bytes.length} bytes)`);
  } else {
    fs.writeFileSync(LOCALITIES_PATH, bytes);
    console.log(`wrote ${path.relative(root, LOCALITIES_PATH)} (${bytes.length} bytes)`);
  }
  console.log(JSON.stringify(counts));
  console.log(JSON.stringify(kindCounts));
}

// Every place of a State within `metres` of a point, nearest first: what a lookup that
// has no ward polygon can offer the tender matcher as locality names. Exported for the
// coverage tool and the tests; the service has its own reader to write.
export function placesNear(state, lat, lng, metres) {
  if (!state) return [];
  const y = lat * SCALE;
  const x = lng * SCALE;
  const dy = (metres / 110_540) * SCALE;
  const metresPerX = (111_320 * Math.cos((lat * Math.PI) / 180)) / SCALE;
  let low = 0;
  let high = state.y.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (state.y[middle] < y - dy) low = middle + 1;
    else high = middle;
  }
  const found = [];
  for (let index = low; index < state.y.length && state.y[index] <= y + dy; index += 1) {
    const east = (state.x[index] - x) * metresPerX;
    const north = ((state.y[index] - y) / SCALE) * 110_540;
    const distance = Math.hypot(east, north);
    if (distance <= metres) found.push({ name: state.n[index], kind: state.k[index], metres: Math.round(distance) });
  }
  return found.sort((left, right) => left.metres - right.metres);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
