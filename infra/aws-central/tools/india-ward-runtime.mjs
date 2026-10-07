#!/usr/bin/env node
// Decides which ward snapshots outside Karnataka the service uses, and shows what it
// would answer with them. Reads only files in the repo. Offline tool: the service reads
// its output, data/wards/runtime.json, and never this file.
//
//   node infra/aws-central/tools/india-ward-runtime.mjs --pairs GJ/ahmedabad [--per-ward 3] [--as number]
//     prints every ward and notice pair the service would return for points inside that
//     body's wards, whether or not the snapshot is switched on, for a person to read.
//     The clock is set to the day the notices were retrieved, so a notice that has
//     closed since is still read. --as reads a snapshot the index says to match by
//     nothing as if it were matched by number or by name: how Chennai's pairs were read
//     before the reading said its numbers are not the tenders'.
//   node infra/aws-central/tools/india-ward-runtime.mjs --write
//     writes data/wards/runtime.json: the snapshots that pass the gate below
//   node infra/aws-central/tools/india-ward-runtime.mjs --coverage
//     prints, as JSON, how many of the shipped road notices the service can now return
//     for some point, by State and body
//
// The gate. A snapshot is switched on only if all of these hold:
//   its index entry says match by name or by number (`use.by`), and for a match by number
//   that the numbering is the tenders' (`use.numbers` "current");
//   a person read the pairs the service returns for it (data/wards/runtime-handread.json);
//   none of them was wrong and at most one in ten could not be told;
//   at least MINIMUM_PAIRS were read: two right pairs do not show a method works.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { matchIndiaWardTenders, noticesOfBody } from "../service/india-ward-tenders.mjs";
import {
  INDIA_WARD_RUNTIME_FILE, INDIA_WARD_RUNTIME_FORMAT, createIndiaWards,
} from "../service/india-wards.mjs";
import { CATALOGUES } from "../service/national-tenders.mjs";
import { noticeForBody, urbanBodyOf } from "../service/notice-bodies.mjs";
import { INDEX_PATH, SCALE, WARDS_DIR, root, sourceById, wardHolds } from "./snapshot-india-wards.mjs";
import { phoneManifestFiles } from "./stage-national-tenders.mjs";

export const RUNTIME_PATH = path.join(WARDS_DIR, INDIA_WARD_RUNTIME_FILE);
export const RUNTIME_HANDREAD_PATH = path.join(WARDS_DIR, "runtime-handread.json");
export const RUNTIME_HANDREAD_FORMAT = "pothole-india-ward-runtime-handread";
export const MINIMUM_PAIRS = 10;
export const MOST_UNDECIDED = 0.1;

// The ward markers a body's titles use beyond the word "ward", read off its notices of
// 5 Oct 2026. Bhopal writes "w06,z20", "W-64 Z-15" and "W30 Z08" (9 of its 41 titles with
// a ward number); Chennai numbers its wards as divisions, "Div-128", "Dn 62 and 45",
// "D128 129 136" (all 11); Kochi writes "Div 44" and "Division-39". Nowhere else: in
// Faridabad "ward no. 27 div-5" is ward 27 of works division 5.
export const TITLE_MARKERS = {
  "MP/bhopal": { letters: ["w"], words: [] },
  "TN/chennai": { letters: ["d"], words: ["div", "divn", "dn", "division"] },
  "KL/kochi": { letters: [], words: ["div", "divn", "division"] },
};

const readJson = (file) => JSON.parse(fs.readFileSync(file, "utf8"));

// What the service needs to know of one snapshot, whether or not it is switched on.
export function runtimeEntry(indexEntry) {
  const source = sourceById(indexEntry.id);
  const snapshot = readJson(path.join(root, indexEntry.path));
  const bbox = [Infinity, Infinity, -Infinity, -Infinity];
  for (const ward of snapshot.wards) {
    bbox[0] = Math.min(bbox[0], ward.bbox[0]);
    bbox[1] = Math.min(bbox[1], ward.bbox[1]);
    bbox[2] = Math.max(bbox[2], ward.bbox[2]);
    bbox[3] = Math.max(bbox[3], ward.bbox[3]);
  }
  return {
    id: indexEntry.id,
    file: path.relative(WARDS_DIR, path.join(root, indexEntry.path)),
    sha256: indexEntry.sha256,
    bytes: indexEntry.bytes,
    state_code: indexEntry.state_code,
    body: indexEntry.body,
    town_code: indexEntry.town_code,
    // The city notice-bodies.mjs files this body's notices under.
    notices_city: source?.notices?.city || null,
    by: indexEntry.use?.by || "nothing",
    numbers: indexEntry.use?.numbers || "untested",
    markers: TITLE_MARKERS[indexEntry.id] || { letters: [], words: [] },
    bbox,
    wards: snapshot.count,
    source_last_edited: indexEntry.source_last_edited || null,
    vintage: indexEntry.vintage || null,
  };
}

// The pairs a person read for one body, counted.
export function readingOf(handread, id) {
  const body = handread?.bodies?.[id];
  const pairs = body?.pairs || [];
  const count = (verdict) => pairs.filter((pair) => pair.verdict === verdict).length;
  return {
    read: pairs.length, right: count("right"), wrong: count("wrong"), cannot_tell: count("cannot tell"),
    // Sampled, with no pair to read: the body's notices and the file's wards never met.
    sampled_points: body?.points ?? 0, notices_of_body: body?.notices_of_body ?? null, read_on: handread?.read_on || null,
  };
}

// Why a snapshot is off, or null where it is on.
export function keptOff(indexEntry, reading) {
  const use = indexEntry.use || {};
  if (use.by !== "name" && use.by !== "number") return `the index says match by nothing: ${use.evidence}`;
  if (use.by === "number" && use.numbers !== "current") return `its numbering is ${use.numbers}: ${use.evidence}`;
  if (!reading.read && reading.sampled_points) return `the service returns no pair for it to read: ${reading.sampled_points} points sampled, ${reading.notices_of_body} notices of the body, none on a ward the file draws`;
  if (!reading.read) return "no pair of it has been read";
  if (reading.wrong) return `${reading.wrong} of ${reading.read} pairs read were wrong`;
  if (reading.cannot_tell > MOST_UNDECIDED * reading.read) return `${reading.cannot_tell} of ${reading.read} pairs read could not be told (the rule is one in ten)`;
  if (reading.read < MINIMUM_PAIRS) return `only ${reading.read} pairs to read (${reading.right} right); the rule is ${MINIMUM_PAIRS}`;
  return null;
}

// Two snapshots of one body can both be on (Mumbai and Pune each have two files). The one
// whose numbers are the tenders' comes first, then the one its publisher dated later, and
// the service answers with the first whose ward holds the point.
const preference = (left, right) => Number(right.numbers === "current") - Number(left.numbers === "current")
  || String(right.source_last_edited || "").localeCompare(String(left.source_last_edited || ""));

export function buildRuntime({ index = readJson(INDEX_PATH), handread = fs.existsSync(RUNTIME_HANDREAD_PATH) ? readJson(RUNTIME_HANDREAD_PATH) : null } = {}) {
  const on = [];
  const off = [];
  for (const indexEntry of index.snapshots) {
    const reading = readingOf(handread, indexEntry.id);
    const reason = keptOff(indexEntry, reading);
    if (reason) off.push({ id: indexEntry.id, body: indexEntry.body, by: indexEntry.use?.by || "nothing", reason });
    else on.push({ ...runtimeEntry(indexEntry), read: reading });
  }
  // Stable: bodies keep the index's order, and only snapshots of one body are reordered.
  const ordered = on.map((entry, at) => ({ entry, at }))
    .sort((left, right) => (left.entry.notices_city === right.entry.notices_city && left.entry.state_code === right.entry.state_code
      ? preference(left.entry, right.entry) || left.at - right.at : left.at - right.at))
    .map(({ entry }) => entry);
  return {
    _comment: "Generated by infra/aws-central/tools/india-ward-runtime.mjs --write. The ward snapshots the service uses "
      + "outside Karnataka, in order of preference, each with the hash of its file and the count of pairs a person read "
      + "(data/wards/runtime-handread.json). `off` says why every other snapshot is not used. Do not edit by hand.",
    format: INDIA_WARD_RUNTIME_FORMAT,
    schema_version: 1,
    coordinate_scale: index.coordinate_scale,
    gate: { wrong: 0, most_undecided: MOST_UNDECIDED, minimum_pairs: MINIMUM_PAIRS },
    snapshots: ordered,
    off,
  };
}

// Every State's road notice pack, as the shipped phone and the service read it.
export function loadNoticePacks() {
  const file = phoneManifestFiles(root).road_notice;
  const manifest = readJson(path.join(root, "static", file));
  const packs = new Map();
  for (const resource of Object.values(manifest.resources)) {
    const pack = readJson(path.join(root, "docs", resource.path));
    if (pack.format !== CATALOGUES.road_notice.packFormat) throw new Error(`${resource.pack_id} is not a road notice pack`);
    packs.set(resource.state_code, { pack, resource });
  }
  return { manifest: file, generated_at: manifest.generated_at, packs };
}

// Up to `perWard` points inside each ward, spread over it: the ward's own points of a
// 9 by 9 grid over its box, taken evenly. A ward too thin for the grid gets none.
export function samplePoints(snapshot, perWard = 3) {
  const points = [];
  for (const ward of snapshot.wards) {
    const inside = [];
    for (let i = 0; i < 9; i += 1) {
      for (let j = 0; j < 9; j += 1) {
        const x = ward.bbox[0] + ((i + 0.5) / 9) * (ward.bbox[2] - ward.bbox[0]);
        const y = ward.bbox[1] + ((j + 0.5) / 9) * (ward.bbox[3] - ward.bbox[1]);
        if (wardHolds(ward, x, y)) inside.push({ lat: Number((y / SCALE).toFixed(5)), lng: Number((x / SCALE).toFixed(5)), ward: ward.code });
      }
    }
    const take = Math.min(perWard, inside.length);
    for (let n = 0; n < take; n += 1) points.push(inside[Math.floor(((n + 0.5) / take) * inside.length)]);
  }
  return points;
}

// The service's own lookup over a list of entries, switched on or not: a throwaway
// runtime list is written beside nothing and points at the committed files.
export async function wardsFor(entries) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "india-ward-runtime-"));
  fs.writeFileSync(path.join(dir, INDIA_WARD_RUNTIME_FILE), JSON.stringify({
    format: INDIA_WARD_RUNTIME_FORMAT, schema_version: 1, coordinate_scale: SCALE,
    snapshots: entries.map((entry) => ({ ...entry, file: pathToFileURL(path.join(WARDS_DIR, entry.file)).href })),
  }));
  const wards = createIndiaWards({ dir, logger: { error(line) { throw new Error(line); } } });
  await wards.locate(0, 0);
  fs.rmSync(dir, { recursive: true });
  return wards;
}

// Every ward and notice pair the service returns for sample points of one snapshot, as it
// would be configured if it were on (a number snapshot is read as if its numbering were
// the tenders', which is the thing the reading is to find out).
export async function returnedPairs(id, { perWard = 3, assume = null, packs = loadNoticePacks().packs, index = readJson(INDEX_PATH) } = {}) {
  const indexEntry = index.snapshots.find((item) => item.id.toLowerCase() === String(id).toLowerCase());
  if (!indexEntry) throw new Error(`No snapshot ${id}`);
  const entry = runtimeEntry(indexEntry);
  if (assume) entry.by = assume;
  if (entry.by !== "name" && entry.by !== "number") {
    throw new Error(`${entry.id} is matched by nothing (${indexEntry.use?.evidence}). Pass --as number or --as name to read it as a candidate.`);
  }
  if (entry.by === "number") entry.numbers = "current";
  const wards = await wardsFor([entry]);
  const snapshot = await wards.snapshot(entry.id);
  const held = packs.get(entry.state_code);
  const now = held ? Date.parse(`${held.resource.source_retrieved_at}T00:00:00+05:30`) : Date.now();
  const points = samplePoints(snapshot, perWard);
  const pairs = new Map();
  const wardsHit = new Set();
  let placed = 0;
  for (const point of points) {
    const found = await wards.locate(point.lat, point.lng);
    if (!found.ward) continue;
    placed += 1;
    wardsHit.add(found.ward.code);
    if (!held) continue;
    for (const tender of matchIndiaWardTenders({ ward: found.ward, snapshot: found.snapshot, pack: held.pack, now, limit: Infinity })) {
      const key = `${found.ward.code}|${tender.tender_number}`;
      if (!pairs.has(key)) pairs.set(key, { ward: found.ward, tender, points: 0 });
      pairs.get(key).points += 1;
    }
  }
  const notices = held ? noticesOfBody(held.pack, snapshot) : [];
  return {
    entry, points: points.length, placed, wards_sampled: wardsHit.size,
    notices_of_body: notices.length,
    notices_shown: notices.filter((row) => row.shown && row.closes >= now).length,
    pairs: [...pairs.values()].sort((left, right) => Number(left.ward.no || 0) - Number(right.ward.no || 0)
      || left.ward.code.localeCompare(right.ward.code)
      || left.tender.tender_number.localeCompare(right.tender.tender_number)),
  };
}

// How many of the shipped notices the service can return for some point, by State and for
// each switched-on body. `returnable` sets the clock to the day the pack was retrieved, so
// it is the same on any day; `returnable_on_clock` uses the clock given (today, from the
// command line) and falls every day as bids close. Pass `now: null` to leave it out.
export async function coverage({ runtime = readJson(RUNTIME_PATH), now = Date.now(), loaded = loadNoticePacks() } = {}) {
  const wards = await wardsFor(runtime.snapshots);
  const states = [];
  const bodies = [];
  for (const [stateCode, { pack, resource }] of loaded.packs) {
    const retrieved = Date.parse(`${resource.source_retrieved_at}T00:00:00+05:30`);
    let urban = 0;
    for (const record of pack.notices) if (urbanBodyOf(noticeForBody(stateCode, record))) urban += 1;
    const row = { state: stateCode, notices: pack.notices.length, from_urban_bodies: urban, bodies_on: 0, returnable: 0, returnable_on_clock: 0 };
    for (const entry of runtime.snapshots.filter((item) => item.state_code === stateCode)) {
      const snapshot = await wards.snapshot(entry.id);
      const count = (clock) => {
        const tenders = new Set();
        const wardsWith = new Set();
        for (const ward of snapshot.wards) {
          for (const tender of matchIndiaWardTenders({ ward, snapshot, pack, now: clock, limit: Infinity })) {
            tenders.add(tender.tender_number);
            wardsWith.add(ward.code);
          }
        }
        return { tenders: tenders.size, wards: wardsWith.size };
      };
      const asRetrieved = count(retrieved);
      const onClock = now === null ? null : count(now);
      bodies.push({
        id: entry.id, state: stateCode, body: entry.body, by: entry.by, wards: snapshot.wards.length,
        notices_of_body: noticesOfBody(pack, snapshot).length,
        returnable: asRetrieved.tenders, wards_with_a_notice: asRetrieved.wards,
        returnable_on_clock: onClock?.tenders ?? null, wards_with_a_notice_on_clock: onClock?.wards ?? null,
      });
      row.bodies_on += 1;
      row.returnable += asRetrieved.tenders;
      row.returnable_on_clock += onClock?.tenders ?? 0;
    }
    states.push(row);
  }
  const sum = (key) => states.reduce((total, row) => total + row[key], 0);
  return {
    manifest: loaded.manifest, clock: now === null ? null : new Date(now).toISOString(),
    notices: sum("notices"), from_urban_bodies: sum("from_urban_bodies"), bodies_on: bodies.length,
    returnable: sum("returnable"), returnable_on_clock: now === null ? null : sum("returnable_on_clock"),
    states, bodies,
  };
}

const args = process.argv.slice(2);
const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
const value = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
if (isMain && args.includes("--pairs")) {
  const result = await returnedPairs(value("--pairs"), { perWard: Number(value("--per-ward", "3")), assume: value("--as", null) })
    .catch((error) => { console.error(error.message); process.exit(2); });
  console.log(`${result.entry.id} (${result.entry.body}), match by ${result.entry.by}: ${result.points} points in ${result.wards_sampled} of ${result.entry.wards} wards, `
    + `${result.placed} placed in one ward; ${result.notices_of_body} notices of the body, ${result.notices_shown} that could be shown; ${result.pairs.length} pairs`);
  for (const [at, pair] of result.pairs.entries()) {
    console.log(`${at + 1}. [${pair.tender.match_basis}] ward ${pair.ward.no ?? ""} ${pair.ward.name ?? ""}${pair.ward.zone ? ` (zone ${pair.ward.zone})` : ""} (${pair.ward.code}) | ${pair.tender.tender_number} | ${pair.points} points\n     ${pair.tender.title}`);
  }
} else if (isMain && args.includes("--coverage")) {
  console.log(JSON.stringify(await coverage(), null, 1));
} else if (isMain && args.includes("--write")) {
  const bytes = `${JSON.stringify(buildRuntime(), null, 1)}\n`;
  fs.writeFileSync(RUNTIME_PATH, bytes);
  const runtime = JSON.parse(bytes);
  console.log(`wrote ${path.relative(root, RUNTIME_PATH)} (${Buffer.byteLength(bytes)} bytes): ${runtime.snapshots.length} on (${runtime.snapshots.map((entry) => entry.id).join(", ") || "none"}), ${runtime.off.length} off`);
} else if (isMain) {
  console.error("Nothing to do: pass --pairs <STATE/city>, --write or --coverage");
  process.exitCode = 2;
}
