#!/usr/bin/env node
// Is the packaged copy of the KGIS layers the same register as KGIS? This puts points to
// both and lists every disagreement in road ownership, town and LGD code, highway name
// and panchayat. It is the only thing in the repository that asks live KGIS for a road
// class; the request path never does.
//
//   node infra/aws-central/tools/verify-local-ownership.mjs
//     The default sample, about 1,075 points:
//       map          the pothole coordinates on the public map that fall in the envelope
//                    the service treats as Karnataka
//       town         150 random points inside town polygons
//       on_<class>   100 points inside highway polygons of each class
//       near_<class> 50 points per class just outside a highway polygon, at 5, 15, 25 and
//                    40 m from the nearest highway polygon of any class
//       edge_<class> 40 points per class at 4 m and 6 m, either side of the 5 m buffer
//       rural        50 random points inside the state and in no town
//       junction     30 points within the buffer of two highway polygons of one class
//                    that KGIS names differently, where the answer is whichever KGIS
//                    lists first
//
//   --classes a,b         only these classes of the default sample
//   --points <file>       a JSON array of { lat, lng, class } to use instead
//   --save-points <file>  write the sample that was used
//   --seed <n>            a different random sample (default 1)
//   --limit <n>           only the first n points of each class
//   --bundle <file>       a bundle other than data/karnataka-ownership.bin
//   --report <file>       where to write the full result (default data/.kgis-work/verify-report.json)
//   --offline             answer from the disk cache only; a point KGIS was never asked is skipped
//
// KGIS is asked one request at a time with a pause, and every answer is kept in
// data/.kgis-work/verify-cache.json, so a run that is stopped carries on where it was
// and a second run costs KGIS nothing.
//
// A point is one of:
//   agree     every field the same
//   order     several polygons of the deciding layer cover the point (two highways of one
//             class at a junction, a named stretch of road meeting an unnamed one), KGIS
//             answered with one of them and the bundle with another. KGIS's pick is the
//             first row its spatial index returns; the bundle's is a stated rule (see
//             localVerdicts). Both hold the same polygons.
//   contour   the two differ over a highway, and the point is as far from the highway
//             polygon KGIS holds as the buffer, give or take the grid the bundle stores
//             highway coordinates on (0.39 m at 1/200,000 degree). Judged against the
//             download in data/.kgis-work when it is the one the bundle was built from.
//   disagree  anything else. The tool exits non-zero if there is one.
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

import { HIGHWAY_BUFFER_METRES, createKgisClient, localVerdicts } from "../service/geolocation.mjs";
import {
  HIGHWAY_LAYERS, OWNERSHIP_BUNDLE_PATH, loadOwnershipBundle, polygonAttributes, polygonRings, polygonsAt,
} from "../service/local-ownership.mjs";
import { metresPerDegree } from "../service/spatial.mjs";
import { KGIS_LAYERS, politeJson } from "./kgis-layers.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const WORK_DIR = path.join(root, "data/.kgis-work");
const MAP_URL = "https://ffjvg34k07.execute-api.ap-south-1.amazonaws.com/v1/map";
// geolocation.mjs KARNATAKA_ENVELOPE: what the service puts to the register at all.
const ENVELOPE = { minLat: 11.09, maxLat: 18.96, minLng: 73.54, maxLng: 79.09 };

const argv = process.argv.slice(2);
const option = (name, fallback = null) => {
  const index = argv.indexOf(name);
  if (index < 0) return fallback;
  const [, value] = argv.splice(index, 2);
  return value;
};
const flag = (name) => {
  const index = argv.indexOf(name);
  if (index >= 0) argv.splice(index, 1);
  return index >= 0;
};
const pointsFile = option("--points");
const onlyClasses = option("--classes")?.split(",") || null;
const savePoints = option("--save-points");
const seed = Number(option("--seed", "1"));
const limit = Number(option("--limit", "0"));
const bundlePath = option("--bundle");
const reportFile = option("--report", path.join(WORK_DIR, "verify-report.json"));
const offline = flag("--offline");
if (argv.length) throw new Error(`Unknown arguments: ${argv.join(" ")}`);

const bundle = await loadOwnershipBundle(bundlePath ? path.resolve(bundlePath) : OWNERSHIP_BUNDLE_PATH);
const scale = bundle.scale;

// mulberry32: the sample is a function of the seed and the bundle, nothing else.
let state = seed >>> 0;
const random = () => {
  state = (state + 0x6d2b79f5) >>> 0;
  let value = state;
  value = Math.imul(value ^ (value >>> 15), value | 1);
  value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
  return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
};

const layerOf = (name) => bundle.layers[bundle.layerIndex[name]];
const isInside = (lat, lng, polygon) => polygonsAt(bundle, lat, lng, 0).some((hit) => hit.polygon === polygon);
// How far the point is from the nearest highway polygon of any class, in metres
// (zero inside one), as far as the bundle's margin lets it be measured.
function metresToHighways(lat, lng) {
  let nearest = Infinity;
  for (const hit of polygonsAt(bundle, lat, lng, bundle.header.max_buffer_metres)) {
    if (bundle.buffered[hit.layer] && hit.metres < nearest) nearest = hit.metres;
  }
  return nearest;
}

function randomPolygon(layer, weighted) {
  if (!weighted) return layer.first_polygon + Math.floor(random() * layer.polygons);
  // By ring count is close enough to "by length" for strips of road, and needs no pass
  // over the geometry: pick a ring, then the polygon that owns it.
  const ring = layer.first_ring + Math.floor(random() * layer.rings);
  let low = layer.first_polygon;
  let high = layer.first_polygon + layer.polygons - 1;
  while (low < high) {
    const middle = (low + high + 1) >> 1;
    if (bundle.polyFirstRing[middle] <= ring) low = middle;
    else high = middle - 1;
  }
  return low;
}

function insidePolygonPoints(layerName, count, weighted = false) {
  const layer = layerOf(layerName);
  const points = [];
  while (points.length < count) {
    const polygon = randomPolygon(layer, weighted);
    const box = [Infinity, Infinity, -Infinity, -Infinity];
    for (const ring of polygonRings(bundle, polygon)) {
      for (let index = 0; index < ring.length; index += 2) {
        box[0] = Math.min(box[0], ring[index]);
        box[1] = Math.min(box[1], ring[index + 1]);
        box[2] = Math.max(box[2], ring[index]);
        box[3] = Math.max(box[3], ring[index + 1]);
      }
    }
    for (let attempt = 0; attempt < 200; attempt += 1) {
      const lng = (box[0] + random() * (box[2] - box[0])) / scale;
      const lat = (box[1] + random() * (box[3] - box[1])) / scale;
      if (!isInside(lat, lng, polygon)) continue;
      points.push({ lat, lng, objectid: bundle.polyObjectId[polygon] });
      break;
    }
  }
  return points;
}

// A point at a set distance from a highway polygon: the midpoint of a random edge, moved
// along the edge's normal. `metres` below zero is inside the polygon.
function alongHighwayEdge(layerName, metres) {
  const layer = layerOf(layerName);
  for (;;) {
    const polygon = randomPolygon(layer, true);
    const rings = polygonRings(bundle, polygon);
    const ring = rings[Math.floor(random() * rings.length)];
    if (ring.length < 8) continue;
    const index = 2 * (1 + Math.floor(random() * (ring.length / 2 - 1)));
    const [ax, ay, bx, by] = ring.subarray(index - 2, index + 2);
    const midLat = (ay + by) / 2 / scale;
    const perDegree = metresPerDegree(midLat);
    const ex = ((bx - ax) / scale) * perDegree.lng;
    const ey = ((by - ay) / scale) * perDegree.lat;
    const length = Math.hypot(ex, ey);
    if (length < 0.5) continue;
    for (const side of [1, -1]) {
      const lat = midLat + (side * metres * (ex / length)) / perDegree.lat;
      const lng = (ax + bx) / 2 / scale + (side * metres * (-ey / length)) / perDegree.lng;
      if (metres < 0) {
        if (isInside(lat, lng, polygon)) return { lat, lng, objectid: bundle.polyObjectId[polygon] };
      } else if (Math.abs(metresToHighways(lat, lng) - metres) < 0.05) {
        // The nearest highway of any class really is this far: no other polygon is
        // closer, and the point is not inside one.
        return { lat, lng, objectid: bundle.polyObjectId[polygon], metres };
      }
    }
  }
}

// A point the live lookup answers by order alone: the highest class of highway within the
// buffer has two polygons there, and KGIS names them differently.
function junctionPoint() {
  for (;;) {
    const name = HIGHWAY_LAYERS[Math.floor(random() * HIGHWAY_LAYERS.length)];
    const polygon = randomPolygon(layerOf(name), false);
    const rings = polygonRings(bundle, polygon);
    const ring = rings[Math.floor(random() * rings.length)];
    const index = 2 * Math.floor(random() * (ring.length / 2));
    const lat = ring[index + 1] / scale + (random() - 0.5) * 2e-5;
    const lng = ring[index] / scale + (random() - 0.5) * 2e-5;
    const hits = polygonsAt(bundle, lat, lng, HIGHWAY_BUFFER_METRES).filter((hit) => bundle.buffered[hit.layer]);
    if (!hits.length) continue;
    const top = Math.min(...hits.map((hit) => hit.layer));
    const names = new Set(hits.filter((hit) => hit.layer === top).map((hit) => polygonAttributes(bundle, hit.polygon).name));
    // Clear of the contour, so the comparison is about order and nothing else.
    const settled = hits.every((hit) => hit.metres < HIGHWAY_BUFFER_METRES - 1)
      && polygonsAt(bundle, lat, lng, HIGHWAY_BUFFER_METRES + 1).filter((hit) => bundle.buffered[hit.layer]).length === hits.length;
    if (names.size > 1 && settled) return { lat, lng, objectid: bundle.polyObjectId[polygon] };
  }
}

async function samplePoints() {
  const points = [];
  // A class that is not wanted is not drawn, and does not move the draws of the others:
  // each class starts from its own seed.
  const add = (group, draw) => {
    if (onlyClasses && !onlyClasses.includes(group)) return null;
    state = (seed ^ [...group].reduce((hash, letter) => (Math.imul(hash, 31) + letter.charCodeAt(0)) >>> 0, 7)) >>> 0;
    const list = draw();
    return list.forEach ? list.forEach((point) => points.push({ class: group, ...point })) : list;
  };
  if (!onlyClasses || onlyClasses.includes("map")) {
    const map = await (await fetch(MAP_URL, { signal: AbortSignal.timeout(30_000) })).json();
    const seen = new Set();
    add("map", () => map.features.map((feature) => ({ lng: feature.geometry.coordinates[0], lat: feature.geometry.coordinates[1] }))
      .filter(({ lat, lng }) => lat >= ENVELOPE.minLat && lat <= ENVELOPE.maxLat && lng >= ENVELOPE.minLng && lng <= ENVELOPE.maxLng)
      .filter(({ lat, lng }) => !seen.has(`${lat},${lng}`) && seen.add(`${lat},${lng}`)));
  }
  add("town", () => insidePolygonPoints("town", 150));
  for (const name of HIGHWAY_LAYERS) {
    // Half well inside the polygon (wherever a uniform draw lands), half 1 m in from an edge.
    add(`on_${name}`, () => [...insidePolygonPoints(name, 50, true),
      ...Array.from({ length: 50 }, () => alongHighwayEdge(name, -1))]);
    add(`near_${name}`, () => Array.from({ length: 50 }, (unused, index) => alongHighwayEdge(name, [5, 15, 25, 40][index % 4])));
    add(`edge_${name}`, () => Array.from({ length: 40 }, (unused, index) => alongHighwayEdge(name, index % 2 ? 6 : 4)));
  }
  add("rural", () => {
    const rural = [];
    while (rural.length < 50) {
      const [point] = insidePolygonPoints("state", 1);
      const hits = polygonsAt(bundle, point.lat, point.lng, 0);
      if (hits.some((hit) => hit.layer === bundle.layerIndex.town)) continue;
      rural.push({ lat: point.lat, lng: point.lng });
    }
    return rural;
  });
  add("junction", () => Array.from({ length: 30 }, junctionPoint));
  return points;
}

// Live KGIS through the service's own client, so the comparison is with exactly what the
// request path used to answer. Its fetch is replaced by the polite, cached one.
const cacheFile = path.join(WORK_DIR, "verify-cache.json");
fs.mkdirSync(WORK_DIR, { recursive: true });
const cache = fs.existsSync(cacheFile) ? JSON.parse(fs.readFileSync(cacheFile, "utf8")) : {};
let unsaved = 0;
const saveCache = () => {
  fs.writeFileSync(`${cacheFile}.tmp`, JSON.stringify(cache));
  fs.renameSync(`${cacheFile}.tmp`, cacheFile);
  unsaved = 0;
};
let asked = 0;
const kgis = createKgisClient({
  timeoutMs: 15 * 60_000,
  breakerMs: 0,
  fetchImpl: async (url) => {
    if (!(url in cache)) {
      if (offline) return { ok: false, json: async () => null };
      cache[url] = await politeJson(url, { pauseMs: 150, timeoutMs: 45_000, attempts: 4, log: (line) => console.log(`  KGIS: ${line}`) });
      asked += 1;
      unsaved += 1;
      if (unsaved >= 40) saveCache();
    }
    return { ok: true, json: async () => cache[url] };
  },
});

const FIELDS = ["road_ownership", "lgd", "town", "town_code", "highway_name", "rural_body"];
const comparable = (verdict) => ({
  road_ownership: verdict.road_ownership,
  lgd: verdict.lgd || null,
  town: verdict.town || null,
  town_code: verdict.town_code || null,
  highway_name: verdict.highway_name || null,
  rural_body: verdict.rural_body || null,
});

let points = pointsFile ? JSON.parse(fs.readFileSync(pointsFile, "utf8")) : await samplePoints();
if (limit) {
  const taken = new Map();
  points = points.filter((point) => {
    taken.set(point.class, (taken.get(point.class) || 0) + 1);
    return taken.get(point.class) <= limit;
  });
}
if (savePoints) fs.writeFileSync(savePoints, `${JSON.stringify(points)}\n`);
console.log(`${points.length} points; bundle ${bundlePath || "data/karnataka-ownership.bin"}, highways at 1/${layerOf("national_highway").scale} degree, buffer ${HIGHWAY_BUFFER_METRES} m`);

const rows = [];
for (const [index, point] of points.entries()) {
  const live = await kgis.verdict(point.lat, point.lng);
  const [local, ...others] = localVerdicts(bundle, point.lat, point.lng);
  const row = { ...point, live: live.available ? comparable(live) : null, local: comparable(local), local_layer: local.local };
  if (!live.available || (live.gp_asked && !live.gp_available)) {
    // KGIS did not answer every layer for this point: nothing to compare.
    row.live = null;
    row.outcome = "kgis_unavailable";
  } else {
    row.differs = FIELDS.filter((field) => row.live[field] !== row.local[field]);
    const sameAs = (verdict) => FIELDS.every((field) => row.live[field] === comparable(verdict)[field]);
    row.outcome = !row.differs.length ? "agree" : others.some(sameAs) ? "order" : "disagree";
  }
  if (row.outcome === "order") row.alternatives = others.length;
  if (row.outcome === "disagree") {
    row.metres_to_highways = Number(metresToHighways(point.lat, point.lng).toFixed(3));
    row.local_hits = polygonsAt(bundle, point.lat, point.lng, bundle.header.max_buffer_metres)
      .map((hit) => ({ ...polygonAttributes(bundle, hit.polygon), metres: Number(hit.metres.toFixed(3)) }));
  }
  rows.push(row);
  if ((index + 1) % 50 === 0) console.log(`  ${index + 1} of ${points.length} (${asked} KGIS requests so far)`);
}
saveCache();

// For the points that differ over a highway: how far each really is from KGIS's highway
// polygons of each class, measured on the download itself (coordinates to 1 cm), not on
// the bundle.
async function exactMetresToHighways(list) {
  const byClass = new Map(list.map((row) => [row, {}]));
  for (const name of HIGHWAY_LAYERS) {
    const nearest = new Map(list.map((row) => [row, Infinity]));
    const { key } = KGIS_LAYERS[name];
    const doneFile = path.join(WORK_DIR, `${key}.done.json`);
    if (!fs.existsSync(doneFile)
        || JSON.parse(fs.readFileSync(doneFile, "utf8")).raw_sha256 !== layerOf(name).raw_sha256) return null;
    const lines = readline.createInterface({ input: fs.createReadStream(path.join(WORK_DIR, `${key}.ndjson`)), crlfDelay: Infinity });
    for await (const line of lines) {
      if (!line) continue;
      const { rings } = JSON.parse(line);
      const box = [Infinity, Infinity, -Infinity, -Infinity];
      for (const ring of rings) {
        for (const [x, y] of ring) {
          if (x < box[0]) box[0] = x;
          if (y < box[1]) box[1] = y;
          if (x > box[2]) box[2] = x;
          if (y > box[3]) box[3] = y;
        }
      }
      for (const row of list) {
        if (row.lng < box[0] - 0.001 || row.lng > box[2] + 0.001 || row.lat < box[1] - 0.001 || row.lat > box[3] + 0.001) continue;
        const perDegree = metresPerDegree(row.lat);
        let inside = false;
        let best = nearest.get(row);
        for (const ring of rings) {
          for (let index = 1; index < ring.length; index += 1) {
            const [xj, yj] = ring[index - 1];
            const [xi, yi] = ring[index];
            if ((yi > row.lat) !== (yj > row.lat) && row.lng < xi + ((row.lat - yi) * (xj - xi)) / (yj - yi)) inside = !inside;
            const ux = (xj - row.lng) * perDegree.lng;
            const uy = (yj - row.lat) * perDegree.lat;
            const vx = (xi - xj) * perDegree.lng;
            const vy = (yi - yj) * perDegree.lat;
            const length = vx * vx + vy * vy;
            const turn = length ? Math.max(0, Math.min(1, -(ux * vx + uy * vy) / length)) : 0;
            best = Math.min(best, Math.hypot(ux + turn * vx, uy + turn * vy));
          }
        }
        nearest.set(row, inside ? 0 : best);
      }
    }
    for (const row of list) byClass.get(row)[name] = Number(nearest.get(row).toFixed(3));
  }
  return byClass;
}

const overHighway = (row) => [row.live.road_ownership, row.local.road_ownership].some((value) => HIGHWAY_LAYERS.includes(value));
const disputed = rows.filter((row) => row.outcome === "disagree" && overHighway(row));
const exact = disputed.length ? await exactMetresToHighways(disputed) : null;
// The farthest a stored vertex can be from where KGIS has it: half a grid step each way.
const highwayScale = layerOf("national_highway").scale;
const gridMetres = 0.5 * Math.hypot(111_320 / highwayScale, 110_600 / highwayScale);
for (const row of disputed) {
  if (!exact) continue;
  row.exact_metres_to_highways = exact.get(row);
  // The classes the two answers do not share: one side matched it and the other did not.
  const classes = [row.live.road_ownership, row.local.road_ownership].filter((value) => HIGHWAY_LAYERS.includes(value));
  if (classes.some((name) => Math.abs(row.exact_metres_to_highways[name] - HIGHWAY_BUFFER_METRES) <= gridMetres + 0.01)) {
    row.outcome = "contour";
  }
}

const classes = [...new Set(rows.map((row) => row.class))];
const count = (list, outcome) => list.filter((row) => row.outcome === outcome).length;
console.log("\nclass                      points  agree  order  contour  disagree  no KGIS answer  agreement");
const summary = [];
for (const group of [...classes, "ALL"]) {
  const list = group === "ALL" ? rows : rows.filter((row) => row.class === group);
  const compared = list.length - count(list, "kgis_unavailable");
  const line = {
    class: group, points: list.length, agree: count(list, "agree"), order: count(list, "order"),
    contour: count(list, "contour"), disagree: count(list, "disagree"), kgis_unavailable: count(list, "kgis_unavailable"),
    agreement: compared ? `${((100 * count(list, "agree")) / compared).toFixed(1)}%` : "n/a",
  };
  summary.push(line);
  console.log(`${group.padEnd(26)} ${String(line.points).padStart(6)} ${String(line.agree).padStart(6)} ${String(line.order).padStart(6)} ${String(line.contour).padStart(8)} ${String(line.disagree).padStart(9)} ${String(line.kgis_unavailable).padStart(15)}  ${line.agreement.padStart(9)}`);
}
const headings = {
  disagree: "Disagreements",
  order: "Several polygons cover the point and KGIS listed another of them first",
  contour: `On the ${HIGHWAY_BUFFER_METRES} m contour (KGIS's polygon is ${HIGHWAY_BUFFER_METRES} m away, give or take the ${gridMetres.toFixed(2)} m the stored coordinates are rounded by)`,
};
for (const outcome of ["disagree", "order", "contour"]) {
  const list = rows.filter((row) => row.outcome === outcome);
  if (!list.length) continue;
  console.log(`\n${headings[outcome]}: ${list.length}`);
  for (const row of list) {
    console.log(`  ${row.class} ${row.lat},${row.lng}${row.metres != null ? ` (sampled at ${row.metres} m)` : ""}: `
      + `${row.differs.map((field) => `${field} KGIS ${JSON.stringify(row.live[field])} local ${JSON.stringify(row.local[field])}`).join("; ")}; `
      + `${row.metres_to_highways != null ? `nearest highway polygon ${row.metres_to_highways} m in the bundle` : `${1 + row.alternatives} polygons of the layer cover the point`}`
      + `${row.exact_metres_to_highways ? `; in KGIS's own coordinates ${Object.entries(row.exact_metres_to_highways).filter(([, metres]) => Number.isFinite(metres)).map(([name, metres]) => `${name} ${metres} m`).join(", ") || "no highway within 100 m"}` : ""}; local layer ${row.local_layer}`);
  }
}
if (disputed.length && !exact) {
  console.log("\nNo download matching this bundle in data/.kgis-work, so highway disagreements could not be measured against KGIS's own coordinates.");
}
fs.writeFileSync(reportFile, `${JSON.stringify({
  generated_at: new Date().toISOString(), seed, buffer_metres: HIGHWAY_BUFFER_METRES, grid_metres: Number(gridMetres.toFixed(3)),
  bundle: { path: bundlePath || "data/karnataka-ownership.bin", bytes: bundle.bytes, layers: bundle.layers.map(({ name, scale: layerScale, count: features, retrieved_at: retrievedAt, content_sha256: hash }) => ({ name, scale: layerScale, count: features, retrieved_at: retrievedAt, content_sha256: hash })) },
  summary, rows,
}, null, 1)}\n`);
console.log(`\n${asked} KGIS requests made; full result in ${path.relative(root, reportFile)}`);
process.exitCode = summary.at(-1).disagree ? 1 : 0;
