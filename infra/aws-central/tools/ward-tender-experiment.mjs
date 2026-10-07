#!/usr/bin/env node
// Re-runs the 6 Oct 2026 measurement that ward tenders were built on: real Bengaluru
// pothole locations from the public map, put through the street matcher and the ward
// tender matcher with the Bengaluru rows of the tender pack.
//
//   node infra/aws-central/tools/ward-tender-experiment.mjs [--limit 40] [--skip 0] [--cache <file>] [--examples 10] [--live-kgis]
//        [--points <file>] [--pairs]
//
// Reads GET /v1/map, keeps the features whose town starts with "GBA", one per 100 m cell
// (lat and lng to 3 decimals), and reverse-geocodes each exactly as geolocation.mjs does,
// at most one request a second. --cache keeps the geocoder's answers in a file so a
// second run asks for nothing it already has. The ward comes from the packaged KGIS
// snapshot, as it does in the service. KGIS itself is not called, so the town and the
// road class come from the snapshots too (what the service answers when KGIS is down);
// --live-kgis asks KGIS for those two, as the service does when it is up. Read-only.
//
// --points reads the locations from a JSON array of {lat, lng} instead of the map (one
// point inside each Bengaluru ward, say), and --pairs lists every ward tender of every
// point, not only the five answered, with its basis and its division, for a person to
// read: a tender matched on a locality's name is the kind that can be a namesake.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createGeolocator } from "../service/geolocation.mjs";
import { matchTender } from "../service/tenders.mjs";
import { matchWardTenders } from "../service/ward-tenders.mjs";
import { loadBodyTenders } from "./ward-tender-vocabulary.mjs";

const args = process.argv.slice(2);
const arg = (key, fallback) => (args.includes(key) ? args[args.indexOf(key) + 1] : fallback);
const API_URL = (process.env.API_URL || "https://ffjvg34k07.execute-api.ap-south-1.amazonaws.com").replace(/\/$/, "");
const GEOCODER = "https://nominatim.openstreetmap.org/reverse";
const limit = Number(arg("--limit", "40"));
const skip = Number(arg("--skip", "0"));
const examples = Number(arg("--examples", "10"));
const cachePath = arg("--cache", "");
const liveKgis = args.includes("--live-kgis");
const pointsPath = arg("--points", "");
const listPairs = args.includes("--pairs");

async function main() {
  const map = pointsPath ? { features: [] }
    : await (await fetch(`${API_URL}/v1/map`, { signal: AbortSignal.timeout(30_000) })).json();
  const cells = new Set();
  const points = pointsPath
    ? JSON.parse(fs.readFileSync(pointsPath, "utf8")).map(({ lat, lng }) => ({ lat, lng })) : [];
  for (const feature of map.features || []) {
    if (!String(feature.properties?.town || "").startsWith("GBA")) continue;
    const [lng, lat] = feature.geometry.coordinates;
    const cell = `${lat.toFixed(3)},${lng.toFixed(3)}`;
    if (cells.has(cell)) continue;
    cells.add(cell);
    points.push({ lat, lng });
  }
  const chosen = points.slice(skip, skip + limit);
  const cache = cachePath && fs.existsSync(cachePath) ? JSON.parse(fs.readFileSync(cachePath, "utf8")) : {};
  let lastAsked = 0;
  // The service's own geolocator, with the network narrowed to the geocoder (and to KGIS
  // with --live-kgis, given 20 s where the service gives it 3).
  const geolocator = createGeolocator({
    geocoderUrl: GEOCODER,
    liveKgis,
    kgisTimeoutMs: 20_000,
    logger: { error() {}, log() {} },
    fetchImpl: async (input, options) => {
      const url = new URL(input);
      if (url.origin !== new URL(GEOCODER).origin) {
        if (liveKgis && url.hostname === "kgis.ksrsac.in") return fetch(input, options);
        throw new Error("KGIS is not called by this tool");
      }
      const key = `${url.searchParams.get("lat")},${url.searchParams.get("lon")}`;
      if (!cache[key]) {
        const wait = lastAsked + 1_100 - Date.now();
        if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
        lastAsked = Date.now();
        const response = await fetch(url, options);
        if (!response.ok) return response;
        cache[key] = await response.json();
        if (cachePath) fs.writeFileSync(cachePath, JSON.stringify(cache, null, 1));
      }
      return new Response(JSON.stringify(cache[key]));
    },
  });
  const tenders = loadBodyTenders("BLR").rows;
  const rows = [];
  for (const point of chosen) {
    const jurisdiction = await geolocator.resolve(point);
    // As core.mjs routes it: a town's index is read for a municipal point only, and the
    // street-level tender is not repeated among the ward tenders.
    const municipal = jurisdiction.road_ownership === "municipal";
    const street = municipal ? matchTender(jurisdiction.address, tenders) : { tender: null, reason: jurisdiction.road_ownership };
    const roster = jurisdiction.ward_code ? await geolocator.wardRoster(jurisdiction.ward_code) : null;
    const input = {
      wardName: jurisdiction.ward_name, localities: jurisdiction.address_parts?.localities || [], tenders, point, roster,
    };
    const besides = (found) => found.filter((entry) => entry.tender_number !== street.tender?.tender_number);
    rows.push({
      ...point, jurisdiction, street,
      ward: municipal ? besides(matchWardTenders(input)) : [],
      every: municipal ? besides(matchWardTenders({ ...input, limit: Infinity })) : [],
    });
  }
  const located = rows.filter((row) => row.jurisdiction.road_ownership === "municipal");
  const reasons = {};
  for (const row of rows) reasons[row.street.reason || "tender_matched"] = (reasons[row.street.reason || "tender_matched"] || 0) + 1;
  console.log(`${rows.length} points (${points.length} cells on the map, skipped ${skip}), ${tenders.length} Bengaluru road tenders`);
  console.log(`road class from ${rows.filter((row) => row.jurisdiction.source === "kgis").length ? "live KGIS" : "the packaged snapshot"}`);
  console.log(`municipal: ${located.length}, ward resolved: ${rows.filter((row) => row.jurisdiction.ward_name).length}, `
    + `geocoded: ${rows.filter((row) => row.jurisdiction.address_source === "operator_geocoder").length}`);
  console.log(`street-level tender: ${rows.filter((row) => row.street.tender).length} of ${rows.length}  ${JSON.stringify(reasons)}`);
  console.log(`at least one ward tender: ${rows.filter((row) => row.ward.length).length} of ${rows.length}`);
  console.log(`either: ${rows.filter((row) => row.ward.length || row.street.tender).length} of ${rows.length}`);
  const byBasis = (kind) => rows.reduce((sum, row) => sum
    + row.every.filter((entry) => entry.match_basis.startsWith(kind)).length, 0);
  console.log(`ward tender pairs, every one and not only the five: ${byBasis("ward name")} on the ward's name, `
    + `${byBasis("locality")} on a locality's (${rows.filter((row) => row.every.some((entry) => entry.match_basis.startsWith("locality"))).length} points)`);
  const crowded = rows.filter((row) => row.every.length > row.ward.length
    && row.every.slice(row.ward.length).some((entry) => entry.match_basis.startsWith("locality")));
  console.log(`points where a locality tender fell outside the five: ${crowded.length}`);
  if (listPairs) {
    console.log("\nevery pair:");
    for (const [index, row] of rows.entries()) {
      for (const entry of row.every) {
        console.log(`P${index + 1} | ${row.jurisdiction.ward_name} | ${entry.match_basis} | ${entry.location} | ${entry.title}`);
      }
    }
  }
  console.log("\nevery point:");
  for (const [index, row] of rows.entries()) {
    const top = row.ward[0];
    console.log(`${String(index + 1).padStart(2)}. ${row.lat},${row.lng} | ${row.jurisdiction.address} | ward ${row.jurisdiction.ward_no} ${row.jurisdiction.ward_name}`
      + ` | localities ${JSON.stringify(row.jurisdiction.address_parts?.localities || [])}`
      + ` | street ${row.street.tender ? row.street.tender.title : row.street.reason}`
      + ` | ward tenders ${row.ward.length} of ${row.every.length}${top ? ` | top [${top.match_basis}] ${top.title}` : ""}`);
  }
  // Reports cluster, so many points share a ward and a top tender. Each distinct answer is
  // shown once first; other addresses with a repeated answer fill what is left.
  const answers = new Set();
  const addresses = new Set();
  const fresh = [];
  const repeats = [];
  for (const row of rows) {
    if (!row.ward.length || addresses.has(row.jurisdiction.address)) continue;
    addresses.add(row.jurisdiction.address);
    const answer = row.ward.map((entry) => entry.tender_number).join("|");
    (answers.has(answer) ? repeats : fresh).push(row);
    answers.add(answer);
  }
  const shown = [...fresh, ...repeats].slice(0, examples);
  console.log(`\ndistinct answers among the points with a ward tender: ${fresh.length}`);
  console.log(`\n${shown.length} examples to judge:`);
  for (const row of shown) {
    console.log(`- address: ${row.jurisdiction.address}\n  ward: ${row.jurisdiction.ward_name} (KGIS ward ${row.jurisdiction.ward_no}, current numbering)`);
    for (const entry of row.ward) console.log(`  [${entry.match_basis}] ${entry.published} ${entry.title}`);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
}
