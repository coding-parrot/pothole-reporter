#!/usr/bin/env node
// The equivalence gate for service/local-address.mjs: does the packaged street data
// answer what the public Nominatim server answers, and does tender matching come out
// the same?
//
//   node infra/aws-central/tools/verify-local-address.mjs import <set>=<file> [<set>=<file>]...
//     Copies answers another tool already has into this tool's cache, under a set name.
//     The files are ward-tender-experiment.mjs caches: { "<lat>,<lng>": <reverse JSON> }.
//
//   node infra/aws-central/tools/verify-local-address.mjs sample [--towns 30] [--per-town 5] [--cities 150]
//     Adds new points to the cache and asks Nominatim for each, exactly as
//     geolocation.mjs asks (format=jsonv2, zoom=17, addressdetails, namedetails), at most
//     one request a second, under this project's data User-Agent. Karnataka towns are the
//     ones with the most road tenders, Bengaluru left out, with points uniform inside the
//     town's KGIS polygon; city points are uniform in the middle half of the city's box,
//     two in three of them kept only when a packaged street is within 30 m, as a pothole
//     report would be. Points come from a fixed seed, and a point already in the cache is
//     never asked again.
//
//   node infra/aws-central/tools/verify-local-address.mjs compare [--list] [--set <name>]...
//     Per set and field, how often the local answer equals Nominatim's. No network.
//
//   node infra/aws-central/tools/verify-local-address.mjs tenders [--set <name>]...
//     Runs the service's own geolocator and tender matching (matchTender and
//     matchWardTenders, with the town's rows of the Karnataka tender pack) twice for each
//     Karnataka point: once with Nominatim's cached answer as the geocoder, once with the
//     local answer in its place. Lists every point whose `tender` or `ward_tenders`
//     differ, with both addresses. No network; KGIS is not called (the packaged
//     snapshot answers, as it does in the service when KGIS is down).
//
// The cache is eval/results/local-address/nominatim-cache.json (not committed).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createGeolocator } from "../service/geolocation.mjs";
import { createLocalAddress } from "../service/local-address.mjs";
import { pointInRings, withinBox } from "../service/spatial.mjs";
import { matchTender } from "../service/tenders.mjs";
import { matchWardTenders } from "../service/ward-tenders.mjs";
import { REGIONS } from "./build-street-index.mjs";
import { prepareTenders } from "./seed-tenders.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const CACHE = path.join(root, "eval/results/local-address/nominatim-cache.json");
const GEOCODER = "https://nominatim.openstreetmap.org/reverse";
const USER_AGENT = "PotholeReporter-data/1 (+https://coding-parrot.github.io/pothole-reporter/; contact@aiengg.dev)";
const BENGALURU_BODY = "BLR";
const quiet = { error() {}, log() {} };

const args = process.argv.slice(2);
const option = (key, fallback) => (args.includes(key) ? args[args.indexOf(key) + 1] : fallback);
const options = (key) => args.flatMap((value, index) => (args[index - 1] === key ? [value] : []));

function readCache() {
  return fs.existsSync(CACHE) ? JSON.parse(fs.readFileSync(CACHE, "utf8")) : {};
}
function writeCache(cache) {
  fs.mkdirSync(path.dirname(CACHE), { recursive: true });
  fs.writeFileSync(CACHE, JSON.stringify(cache, null, 1));
}

// A small seeded generator (mulberry32), so a rerun asks for the same points.
function random(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function tenderRows() {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, "static/pack-manifest-v1.35.json")));
  const resource = manifest.resources["in-ka-tenders"];
  // Read for its titles, not to seed a table: the review date is not enforced here.
  return prepareTenders(resource, fs.readFileSync(path.join(root, "docs", resource.path)), new Date(0));
}

async function ask(lat, lng) {
  const url = new URL(GEOCODER);
  url.searchParams.set("lat", String(lat));
  url.searchParams.set("lon", String(lng));
  url.searchParams.set("format", "jsonv2");
  url.searchParams.set("zoom", "17");
  url.searchParams.set("addressdetails", "1");
  url.searchParams.set("namedetails", "1");
  const response = await fetch(url, { headers: { "user-agent": USER_AGENT }, signal: AbortSignal.timeout(20_000) });
  if (!response.ok) throw new Error(`Nominatim answered ${response.status}`);
  return response.json();
}

function importSets() {
  const cache = readCache();
  for (const pair of args.slice(1)) {
    const [set, file] = pair.split("=");
    if (!set || !file) throw new Error(`expected <set>=<file>, got ${pair}`);
    let added = 0;
    for (const [key, response] of Object.entries(JSON.parse(fs.readFileSync(file, "utf8")))) {
      if (cache[key]) continue;
      cache[key] = { set, response };
      added += 1;
    }
    console.log(`${set}: ${added} answers imported from ${file}`);
  }
  writeCache(cache);
}

async function sample() {
  const local = createLocalAddress({ logger: quiet });
  const cache = readCache();
  const wanted = [];
  const next = random(20261007);
  const fixed = (value) => Number(value.toFixed(5));
  // Karnataka towns by how many road tenders the index holds for them.
  const towns = Number(option("--towns", "30"));
  const perTown = Number(option("--per-town", "5"));
  const counts = new Map();
  for (const row of tenderRows()) counts.set(row.body_lgd, (counts.get(row.body_lgd) || 0) + 1);
  const geometry = JSON.parse(fs.readFileSync(path.join(root, "data/karnataka-local-geometry.json"), "utf8"));
  const scale = geometry.coordinate_scale;
  const ranked = geometry.towns.features
    .filter((town) => town.lgd != null && !/^30585[0-4]$/.test(String(town.lgd)) && counts.has(String(town.lgd)))
    .sort((left, right) => counts.get(String(right.lgd)) - counts.get(String(left.lgd)) || String(left.name).localeCompare(String(right.name)))
    .slice(0, towns);
  for (const town of ranked) {
    let made = 0;
    for (let attempt = 0; attempt < 5_000 && made < perTown; attempt += 1) {
      const x = town.bbox[0] + next() * (town.bbox[2] - town.bbox[0]);
      const y = town.bbox[1] + next() * (town.bbox[3] - town.bbox[1]);
      if (!withinBox(x, y, town.bbox) || !pointInRings(x, y, town.rings)) continue;
      wanted.push({ lat: fixed(y / scale), lng: fixed(x / scale), set: "karnataka towns", place: town.name, lgd: String(town.lgd) });
      made += 1;
    }
  }
  // The cities outside Karnataka, in proportion to the size of their box.
  const cities = REGIONS.filter((region) => region.boxes);
  const total = Number(option("--cities", "150"));
  const area = (region) => region.boxes.reduce((sum, box) => sum + (box[2] - box[0]) * (box[3] - box[1]), 0);
  const whole = cities.reduce((sum, region) => sum + Math.sqrt(area(region)), 0);
  for (const region of cities) {
    const share = Math.max(6, Math.round((total * Math.sqrt(area(region))) / whole));
    const [west, south, east, north] = region.boxes[0];
    let made = 0;
    for (let attempt = 0; attempt < 50_000 && made < share; attempt += 1) {
      const lng = fixed(west + (0.25 + next() * 0.5) * (east - west));
      const lat = fixed(south + (0.25 + next() * 0.5) * (north - south));
      const nearStreet = made % 3 !== 2;
      if (nearStreet) {
        const found = local.lookup(lat, lng);
        if (!found || found.basis !== "street" || found.distance_m > 30) continue;
      }
      wanted.push({ lat, lng, set: region.id, place: region.name, kind: nearStreet ? "within 30 m of a street" : "anywhere" });
      made += 1;
    }
  }
  const missing = wanted.filter((point) => !cache[`${point.lat},${point.lng}`]);
  console.log(`${wanted.length} points, ${missing.length} not yet asked`);
  let last = 0;
  for (const point of missing) {
    const wait = last + 1_100 - Date.now();
    if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
    last = Date.now();
    try {
      const response = await ask(point.lat, point.lng);
      const { lat, lng, ...about } = point;
      cache[`${lat},${lng}`] = { ...about, response };
      writeCache(cache);
    } catch (error) {
      console.log(`${point.lat},${point.lng}: ${error.message}`);
    }
  }
  console.log(`cache holds ${Object.keys(cache).length} answers`);
}

// The parts of an answer that the service reads, as geolocation.mjs reads them.
function read(address = {}) {
  return {
    road: address.road || "",
    street: address.road || address.pedestrian || address.residential || address.footway || "",
    neighbourhood: address.neighbourhood || "",
    quarter: address.quarter || "",
    suburb: address.suburb || "",
    hamlet: address.hamlet || "",
    village: address.village || "",
    city: address.city || address.town || address.municipality || "",
    postcode: address.postcode || "",
    state: address.state || "",
    line: [address.road || address.pedestrian || address.residential || address.footway, address.neighbourhood || address.hamlet,
      address.suburb || address.village, address.city || address.town || address.municipality, address.postcode]
      .filter((value, index, all) => value && all.indexOf(value) === index).join(", "),
    localities: [address.neighbourhood, address.hamlet, address.quarter, address.suburb, address.village]
      .filter((value, index, all) => value && all.indexOf(value) === index).join(" | "),
  };
}

const SHORT = { rd: "road", st: "street", ln: "lane", ave: "avenue", av: "avenue", cr: "cross", crs: "cross", mn: "main", hwy: "highway", blvd: "boulevard", nr: "near" };
// Case, punctuation and the usual abbreviations: "M.G. Rd" is "mg road".
export function normalised(name) {
  return String(name || "").toLowerCase().replace(/[.'’]/g, "").replace(/[^\p{L}\p{N}]+/gu, " ").trim().split(" ")
    .map((word) => SHORT[word] || word).join(" ");
}

function chosen(cache) {
  const sets = options("--set");
  return Object.entries(cache).filter(([, entry]) => !sets.length || sets.includes(entry.set))
    .map(([key, entry]) => ({ key, lat: Number(key.split(",")[0]), lng: Number(key.split(",")[1]), ...entry }));
}

function compare() {
  const local = createLocalAddress({ logger: quiet });
  const points = chosen(readCache());
  const fields = ["road", "street", "neighbourhood", "quarter", "suburb", "hamlet", "village", "city", "postcode", "state", "line", "localities"];
  const sets = [...new Set(points.map((point) => point.set)), "ALL"];
  const table = new Map(sets.map((set) => [set, { points: 0, covered: 0, snapped: 0, same: Object.fromEntries(fields.map((field) => [field, 0])),
    roadNamed: 0, roadNamedSame: 0, roadNormalised: 0, either: 0, eitherOf: 0, whole: 0 }]));
  const differences = [];
  for (const point of points) {
    const found = local.lookup(point.lat, point.lng);
    const want = read(point.response.address);
    const got = read(found?.address);
    for (const set of [point.set, "ALL"]) {
      const row = table.get(set);
      row.points += 1;
      if (!found) continue;
      row.covered += 1;
      if (found.basis === "street") row.snapped += 1;
      let whole = true;
      for (const field of fields) {
        if (want[field] === got[field]) row.same[field] += 1;
        else if (field !== "line" && field !== "localities" && field !== "street") whole = false;
      }
      if (whole) row.whole += 1;
      if (want.road) {
        row.roadNamed += 1;
        if (want.road === got.road) row.roadNamedSame += 1;
        if (normalised(want.road) === normalised(got.road)) row.roadNormalised += 1;
      }
      // Of the points where the local answer names a suburb or neighbourhood, how many
      // of those names are one of the two Nominatim gives.
      for (const name of [got.suburb, got.neighbourhood]) {
        if (!name) continue;
        row.eitherOf += 1;
        if (name === want.suburb || name === want.neighbourhood) row.either += 1;
      }
    }
    if (!found) { differences.push(`${point.key} [${point.set}] not covered by a packaged region`); continue; }
    const unlike = fields.filter((field) => field !== "line" && field !== "localities" && field !== "street" && want[field] !== got[field]);
    if (unlike.length) {
      differences.push(`${point.key} [${point.set}${point.place ? `, ${point.place}` : ""}] ${found.basis} at ${found.distance_m} m; Nominatim ${point.response.osm_type || ""} ${point.response.osm_id || ""} ${point.response.category || ""}/${point.response.type || ""}\n`
        + unlike.map((field) => `      ${field}: Nominatim ${JSON.stringify(want[field])}, local ${JSON.stringify(got[field])}`).join("\n"));
    }
  }
  const share = (count, of) => (of ? `${count}/${of} (${((100 * count) / of).toFixed(1)}%)` : "0/0");
  for (const [set, row] of table) {
    if (!row.points) continue;
    console.log(`\n${set}: ${row.points} points, ${row.covered} covered, ${row.snapped} snapped to a street`);
    console.log(`  every field the same         ${share(row.whole, row.covered)}`);
    console.log(`  road, exact                  ${share(row.same.road, row.covered)}   (where Nominatim names a road: ${share(row.roadNamedSame, row.roadNamed)})`);
    console.log(`  road, after normalising      where Nominatim names a road: ${share(row.roadNormalised, row.roadNamed)}`);
    for (const field of ["neighbourhood", "quarter", "suburb", "hamlet", "village", "city", "postcode", "state"]) {
      console.log(`  ${field.padEnd(28)} ${share(row.same[field], row.covered)}`);
    }
    console.log(`  local suburb or neighbourhood is one of Nominatim's two   ${share(row.either, row.eitherOf)}`);
    console.log(`  the address line the tender matcher reads                 ${share(row.same.line, row.covered)}`);
    console.log(`  the localities the ward tender matcher reads              ${share(row.same.localities, row.covered)}`);
  }
  if (args.includes("--list")) {
    console.log(`\n${differences.length} points with a difference:`);
    for (const line of differences) console.log(`  ${line}`);
  }
}

async function tenders() {
  const local = createLocalAddress({ logger: quiet });
  const points = chosen(readCache());
  const byBody = new Map();
  for (const row of tenderRows()) {
    if (!byBody.has(row.body_lgd)) byBody.set(row.body_lgd, []);
    byBody.get(row.body_lgd).push(row);
  }
  // The service's own geolocator with the geocoder's answer supplied. One per point, so
  // its 11 m answer cache never hands one point another's address.
  const resolveWith = (answer) => createGeolocator({
    geocoderUrl: GEOCODER,
    logger: quiet,
    fetchImpl: async (input) => {
      if (new URL(input).origin !== new URL(GEOCODER).origin) throw new Error("KGIS is not called by this tool");
      return answer ? new Response(JSON.stringify(answer)) : new Response("{}", { status: 503 });
    },
  });
  const outcome = async (answer, point) => {
    const geolocator = resolveWith(answer);
    const jurisdiction = await geolocator.resolve({ lat: point.lat, lng: point.lng });
    const municipal = jurisdiction.road_ownership === "municipal";
    // As dynamo-repository.mjs reads them: the town's own rows, and for the Greater
    // Bengaluru corporations the rows filed under "BLR" as well.
    const lgd = String(jurisdiction.lgd || "");
    const rows = municipal ? [...(byBody.get(lgd) || []), ...(/^30585[0-4]$/.test(lgd) ? byBody.get(BENGALURU_BODY) || [] : [])] : [];
    const street = municipal && rows.length ? matchTender(jurisdiction.address, rows)
      : { tender: null, reason: municipal ? "no_tenders_for_jurisdiction" : jurisdiction.road_ownership };
    const roster = jurisdiction.ward_code ? await geolocator.wardRoster(jurisdiction.ward_code) : null;
    const ward = municipal && rows.length ? matchWardTenders({
      wardName: jurisdiction.ward_name, localities: jurisdiction.address_parts?.localities || [], tenders: rows,
      point: { lat: point.lat, lng: point.lng }, roster,
    }).filter((entry) => entry.tender_number !== street.tender?.tender_number) : [];
    return {
      jurisdiction, rows: rows.length,
      tender: street.tender ? street.tender.tender_number : null,
      reason: street.reason || null,
      title: street.tender?.title || null,
      ward: ward.map((entry) => entry.tender_number),
    };
  };
  const sets = [...new Set(points.map((point) => point.set)), "ALL"];
  const table = new Map(sets.map((set) => [set, { points: 0, municipal: 0, withRows: 0, tender: 0, ward: 0, either: 0, reason: 0, matched: 0, wardAny: 0 }]));
  const differences = [];
  for (const point of points) {
    const found = local.lookup(point.lat, point.lng);
    const before = await outcome(point.response, point);
    if (before.jurisdiction.road_ownership === "outside_state") continue;
    const after = await outcome(found ? { address: found.address, namedetails: found.namedetails } : null, point);
    const tenderDiffers = before.tender !== after.tender;
    const wardDiffers = before.ward.join("|") !== after.ward.join("|");
    // No tender either way, for a different stated reason: the answer a user sees is the
    // same, the `reason` field is not.
    const reasonDiffers = !tenderDiffers && before.reason !== after.reason;
    for (const set of [point.set, "ALL"]) {
      const row = table.get(set);
      row.points += 1;
      if (before.jurisdiction.road_ownership === "municipal") row.municipal += 1;
      if (before.rows) row.withRows += 1;
      if (before.tender) row.matched += 1;
      if (reasonDiffers) row.reason += 1;
      if (before.ward.length) row.wardAny += 1;
      if (tenderDiffers) row.tender += 1;
      if (wardDiffers) row.ward += 1;
      if (tenderDiffers || wardDiffers) row.either += 1;
    }
    if (tenderDiffers || wardDiffers || reasonDiffers) {
      differences.push([
        `${point.key} [${point.set}${point.place ? `, ${point.place}` : ""}] ward ${before.jurisdiction.ward_name || "none"}`,
        `    Nominatim: ${before.jurisdiction.address} | localities ${JSON.stringify(before.jurisdiction.address_parts?.localities || [])}`,
        `    local:     ${after.jurisdiction.address} | localities ${JSON.stringify(after.jurisdiction.address_parts?.localities || [])}`,
        `    tender:       Nominatim ${before.tender || `none (${before.reason})`}${before.title ? ` "${before.title}"` : ""}`,
        `                  local     ${after.tender || `none (${after.reason})`}${after.title ? ` "${after.title}"` : ""}`,
        `    ward_tenders: Nominatim ${JSON.stringify(before.ward)}`,
        `                  local     ${JSON.stringify(after.ward)}`,
      ].join("\n"));
    }
  }
  for (const [set, row] of table) {
    if (!row.points) continue;
    console.log(`${set}: ${row.points} Karnataka points, ${row.municipal} municipal, ${row.withRows} in a town with tender rows; `
      + `with Nominatim's address ${row.matched} get a street tender and ${row.wardAny} at least one ward tender`);
    console.log(`  different tender: ${row.tender}   different ward_tenders: ${row.ward}   either: ${row.either}`
      + `   same answer (no tender) for a different reason: ${row.reason}`);
  }
  console.log(`\n${differences.length} points with a difference:`);
  for (const text of differences) console.log(text);
}

async function main() {
  const command = args[0];
  if (command === "import") importSets();
  else if (command === "sample") await sample();
  else if (command === "compare") compare();
  else if (command === "tenders") await tenders();
  else {
    console.error("usage: verify-local-address.mjs import <set>=<file>... | sample | compare [--list] [--set <name>]... | tenders [--set <name>]...");
    process.exitCode = 2;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
}
