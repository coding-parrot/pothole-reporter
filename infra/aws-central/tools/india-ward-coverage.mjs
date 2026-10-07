#!/usr/bin/env node
// Measures what the ward snapshots under data/wards/ can do for the State road notices
// the app ships, and writes the result to data/wards/COVERAGE.md. Reads only files in
// the repo. Offline tool: the service does not import it.
//
//   node infra/aws-central/tools/india-ward-coverage.mjs            writes COVERAGE.md
//     --with-unlicensed adds rows for refused sources snapshotted under data/wards/.work/
//     (snapshot-india-wards.mjs --allow-unlicensed); never commit a report made that way
//   node infra/aws-central/tools/india-ward-coverage.mjs --json     prints the measurements
//   node infra/aws-central/tools/india-ward-coverage.mjs --pairs UP/kanpur [--seed 1]
//     prints the 30 ward and notice pairs a person is to read for that city
//   node infra/aws-central/tools/india-ward-coverage.mjs --gazetteer-pairs [--seed 1]
//     prints 30 titles with the gazetteer place each was found to name
//
// The name rules are the service's own (service/ward-tenders.mjs, imported and not
// changed): a ward's name is looked for in a title exactly as it is for Bengaluru. Ward
// numbers are read by the same module's wardNumbers. Where this tool reads a marker the
// service does not ("Div-128", "W06"), it says so and counts it apart.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { pointInRings } from "../service/spatial.mjs";
import {
  localityKeys, offeredFor, sameLocality, titleNameKeys, titledWardKeys, wardNumbers,
} from "../service/ward-tenders.mjs";
import { loadRoadNotices, root, urbanBodyOf } from "./india-notice-bodies.mjs";
import { INDEX_PATH, SCALE, SOURCES, committable, snapshotPath, sourceById } from "./snapshot-india-wards.mjs";

export const COVERAGE_PATH = path.join(root, "data/wards/COVERAGE.md");
const LOCALITIES_PATH = path.join(root, "data/wards/india-localities.json");
const HANDREAD_PATH = path.join(root, "data/wards/handread.json");

// Ward markers the service's parser does not read, met in other States' titles:
// "Div-128", "Dn 62", "D150" (Chennai's divisions), "W06", "W.No. 17", "Prabhag 12",
// and the Hindi word in Latin letters, "vard".
const BROAD_MARKER = /\b(?:ward|wad|vard|w|div|divn|dn|d|division|prabhag)\s*(?:\.|-|:)?\s*(?:no|number|num)?\s*(?:\.|-|:)?\s*0*(\d{1,3})\b/gi;
export function broadWardNumbers(title) {
  const numbers = new Set();
  for (const match of String(title || "").matchAll(BROAD_MARKER)) {
    const lone = /^(?:w|d)$/i.test(match[0].replace(/[^a-z]/gi, ""));
    // A lone letter is a marker only when the number is glued to it or to a separator.
    if (lone && !/^[wd]\s*(?:\.|-|:)?\s*(?:no\.?)?\s*\d/i.test(match[0])) continue;
    if (Number(match[1]) > 0) numbers.add(Number(match[1]));
  }
  return numbers;
}
// The numbers a title gives to something larger than a ward: "Zone-6", "Circle-46",
// "Cir-20", "Z20".
const UNIT_MARKER = /\b(?:zone|circle|cir|z)\s*(?:\.|-|:)?\s*(?:no|number)?\s*(?:\.|-|:)?\s*0*(\d{1,3})\b/gi;
export function unitNumbers(title) {
  const numbers = new Set();
  for (const match of String(title || "").matchAll(UNIT_MARKER)) numbers.add(Number(match[1]));
  return numbers;
}
// A number the service's parser returns that the title gives to a zone or a circle and
// not to a ward: "WARD 47 ZONE 06" yields 47 and 6.
export function misreadUnitNumbers(title) {
  const units = unitNumbers(title);
  const wards = broadWardNumbers(title);
  return [...wardNumbers(title)].filter((number) => units.has(number) && !wards.has(number));
}
const NAMES_ROAD = /\b[a-z][a-z.]*\s+(?:road|rd|marg|path|street|salai|gali|lane|highway|bypass|chowk|chhak|chauraha)\b|\bfrom\s+\S+.*\bto\s+\S+/i;

// A seeded shuffle, so the pairs a person reads can be printed again.
function shuffled(list, seed) {
  let state = seed >>> 0 || 1;
  const next = () => {
    state = (Math.imul(state, 1_664_525) + 1_013_904_223) >>> 0;
    return state / 4_294_967_296;
  };
  const out = list.slice();
  for (let index = out.length - 1; index > 0; index -= 1) {
    const other = Math.floor(next() * (index + 1));
    [out[index], out[other]] = [out[other], out[index]];
  }
  return out;
}

const plainNames = (title) => titleNameKeys(title).filter((entry) => !entry.lead);

function loadLocalities() {
  return fs.existsSync(LOCALITIES_PATH) ? JSON.parse(fs.readFileSync(LOCALITIES_PATH, "utf8")) : null;
}

// The spelling keys of every place the gazetteer holds for a State, each with its
// points. A key under five letters is left out: "Agar", "Kota" and "Mau" are also words.
const gazetteerCache = new Map();
function gazetteerOf(localities, stateCode) {
  if (gazetteerCache.has(stateCode)) return gazetteerCache.get(stateCode);
  const keys = new Map();
  const state = localities?.states?.[stateCode];
  if (state) {
    for (let index = 0; index < state.count; index += 1) {
      for (const entry of localityKeys(state.n[index])) {
        if (entry.key.length < 5 || entry.own < 5) continue;
        if (!keys.has(entry.key)) keys.set(entry.key, []);
        keys.get(entry.key).push(index);
      }
    }
  }
  gazetteerCache.set(stateCode, { keys, state });
  return gazetteerCache.get(stateCode);
}

// The notices that belong to a snapshot's city: those its body tendered, and other
// notices of the State that name the city in the title or as the office that tendered
// them (the last link of the organisation chain: "Provincial Division, Lucknow"). A
// notice of another urban body is never the city's: "Join Director-Bhopal Division"
// tenders for Budni, Lateri and Biaora, and "Chief Engineer-Sharda Sahayak, Lucknow" for
// canals in Pratapgarh.
export function noticesFor(source, notices) {
  const rule = source.notices;
  if (!rule) return { body: [], naming: [] };
  const inState = notices.filter((notice) => notice.state === rule.state);
  const body = inState.filter((notice) => (rule.any ? true : urbanBodyOf(notice)?.city === rule.city));
  const held = new Set(body);
  const naming = rule.names ? inState.filter((notice) => !held.has(notice) && !urbanBodyOf(notice)
    && (rule.names.test(notice.title) || rule.names.test(notice.chain[notice.chain.length - 1] || ""))) : [];
  return { body, naming };
}

// Does the file sit where the city is? For every ward with a name, the gazetteer places
// of that same name inside the snapshot's box: in the ward of their name, in another
// ward, or in none.
function positionCheck(snapshot, gazetteer, box) {
  const result = { places: 0, in_own_ward: 0, in_another_ward: 0, in_no_ward: 0 };
  if (!gazetteer.state) return result;
  for (const ward of snapshot.wards) {
    if (!ward.name) continue;
    const seen = new Set();
    for (const entry of localityKeys(ward.name)) {
      for (const index of gazetteer.keys.get(entry.key) || []) {
        if (seen.has(index)) continue;
        seen.add(index);
        const x = gazetteer.state.x[index];
        const y = gazetteer.state.y[index];
        if (x < box[0] || x > box[2] || y < box[1] || y > box[3]) continue;
        result.places += 1;
        if (pointInRings(x, y, ward.rings)) result.in_own_ward += 1;
        else if (snapshot.wards.some((other) => other !== ward && x >= other.bbox[0] && x <= other.bbox[2] && y >= other.bbox[1] && y <= other.bbox[3] && pointInRings(x, y, other.rings))) result.in_another_ward += 1;
        else result.in_no_ward += 1;
      }
    }
  }
  return result;
}

export function measureSnapshot(source, snapshot, notices, localities) {
  const { body, naming } = noticesFor(source, notices);
  const set = [...body, ...naming];
  const wardKeys = snapshot.wards.map((ward) => (ward.name ? localityKeys(ward.name) : []));
  const byNumber = new Map();
  for (const ward of snapshot.wards) {
    if (!ward.no) continue;
    if (!byNumber.has(Number(ward.no))) byNumber.set(Number(ward.no), []);
    byNumber.get(Number(ward.no)).push(ward);
  }
  const gazetteer = gazetteerOf(localities, snapshot.state_code);
  const box = [Infinity, Infinity, -Infinity, -Infinity];
  for (const ward of snapshot.wards) {
    box[0] = Math.min(box[0], ward.bbox[0]);
    box[1] = Math.min(box[1], ward.bbox[1]);
    box[2] = Math.max(box[2], ward.bbox[2]);
    box[3] = Math.max(box[3], ward.bbox[3]);
  }
  // 0.02 degrees is about 2 km: a place this far outside every ward is another town's.
  const reach = 0.02 * SCALE;
  const pairs = [];
  const wardsByName = new Set();
  const wardsByNumber = new Set();
  let noticesByName = 0;
  let noticesWithNumber = 0;
  let noticesWithDrawnNumber = 0;
  let noticesWithBroadNumber = 0;
  let noticesWithBroadDrawn = 0;
  let noticesWithMisread = 0;
  let pairsFromUnitNumbers = 0;
  const numbering = { agree: 0, disagree: 0, examples: [] };
  const geography = { located: 0, inside: 0, within_500_m: 0, elsewhere: 0, examples: [] };
  for (const notice of set) {
    const names = plainNames(notice.title);
    const titled = titledWardKeys(notice.title);
    let named = false;
    snapshot.wards.forEach((ward, at) => {
      if (!wardKeys[at].some((key) => names.some((name) => offeredFor(key, name)))) return;
      named = true;
      wardsByName.add(ward.code);
      pairs.push({ basis: "name", ward, notice });
    });
    if (named) noticesByName += 1;
    const numbers = wardNumbers(notice.title);
    const broad = broadWardNumbers(notice.title);
    const misread = new Set(misreadUnitNumbers(notice.title));
    if (misread.size) noticesWithMisread += 1;
    if (numbers.size) noticesWithNumber += 1;
    if (broad.size) noticesWithBroadNumber += 1;
    if ([...broad].some((number) => byNumber.has(number))) noticesWithBroadDrawn += 1;
    let drawn = false;
    for (const number of numbers) {
      for (const ward of byNumber.get(number) || []) {
        drawn = true;
        wardsByNumber.add(ward.code);
        pairs.push({ basis: "number", ward, notice, misread: misread.has(number) });
        if (misread.has(number)) pairsFromUnitNumbers += 1;
        // Does the name the title gives beside the number agree with the snapshot's name
        // for that number? Only a title with one number can be asked.
        // A ward named in another script has no Latin spelling to compare.
        const wardSpellings = ward.name ? localityKeys(ward.name) : [];
        if (wardSpellings.length && titled.length && numbers.size === 1) {
          const same = wardSpellings.some((key) => titled.some((name) => sameLocality(key, name)));
          numbering[same ? "agree" : "disagree"] += 1;
          if (!same && numbering.examples.length < 6) numbering.examples.push(`ward ${ward.no} is "${ward.name}" in the snapshot; the title says "${notice.title.slice(0, 110)}"`);
        }
        // Where does the gazetteer put the places the title names: in that ward?
        if (gazetteer.state && numbers.size === 1) {
          const points = [];
          for (const name of names) {
            for (const index of gazetteer.keys.get(name.key) || []) {
              const x = gazetteer.state.x[index];
              const y = gazetteer.state.y[index];
              if (x < box[0] - reach || x > box[2] + reach || y < box[1] - reach || y > box[3] + reach) continue;
              points.push({ x, y, name: gazetteer.state.n[index] });
            }
          }
          if (points.length) {
            geography.located += 1;
            const metres = (point) => {
              const dx = Math.max(ward.bbox[0] - point.x, 0, point.x - ward.bbox[2]) * 1.02;
              const dy = Math.max(ward.bbox[1] - point.y, 0, point.y - ward.bbox[3]) * 1.1;
              return Math.hypot(dx, dy);
            };
            if (points.some((point) => pointInRings(point.x, point.y, ward.rings))) geography.inside += 1;
            else if (points.some((point) => metres(point) <= 500)) geography.within_500_m += 1;
            else {
              geography.elsewhere += 1;
              if (geography.examples.length < 4) geography.examples.push(`ward ${ward.no}: "${notice.title.slice(0, 90)}" (${points[0].name} is ${Math.round(Math.min(...points.map(metres)))} m from the ward's box)`);
            }
          }
        }
      }
    }
    if (drawn) noticesWithDrawnNumber += 1;
  }
  return {
    id: source.id,
    state: snapshot.state_code,
    body: snapshot.body,
    wards: snapshot.count,
    named_wards: snapshot.named,
    numbered_wards: snapshot.numbered,
    notices_of_body: body.length,
    notices_naming_city: naming.length,
    notices: set.length,
    by_name: { wards_with_a_notice: wardsByName.size, notices_naming_a_ward: noticesByName },
    by_number: {
      notices_with_a_ward_number: noticesWithNumber,
      notices_whose_number_is_drawn: noticesWithDrawnNumber,
      wards_with_a_notice: wardsByNumber.size,
      notices_with_a_number_by_broader_markers: noticesWithBroadNumber,
      notices_whose_broader_number_is_drawn: noticesWithBroadDrawn,
      notices_where_a_zone_or_circle_number_is_read_as_a_ward: noticesWithMisread,
      pairs: pairs.filter((pair) => pair.basis === "number").length,
      pairs_made_from_a_zone_or_circle_number: pairsFromUnitNumbers,
    },
    position: positionCheck(snapshot, gazetteer, box),
    numbering,
    geography,
    pairs,
  };
}

export function samplePairs(measure, seed = 1, size = 30) {
  return shuffled(measure.pairs.slice().sort((left, right) => (
    `${left.notice.tender_id} ${left.ward.code} ${left.basis}` < `${right.notice.tender_id} ${right.ward.code} ${right.basis}` ? -1 : 1)), seed)
    .slice(0, size);
}

// Per State: how many titles carry a ward marker, and of the rest how many name a place
// the gazetteer holds for that State, or a road.
export function measureStates(loaded, localities) {
  const rows = [];
  for (const { state } of loaded.states) {
    const notices = loaded.notices.filter((notice) => notice.state === state);
    const gazetteer = gazetteerOf(localities, state);
    const bodies = new Map();
    let urban = 0;
    let marker = 0;
    let broadOnly = 0;
    let locality = 0;
    let road = 0;
    let either = 0;
    let neither = 0;
    let misread = 0;
    const hits = [];
    for (const notice of notices) {
      const body = urbanBodyOf(notice);
      if (body) {
        urban += 1;
        bodies.set(body.label, (bodies.get(body.label) || 0) + 1);
      }
      if (misreadUnitNumbers(notice.title).length) misread += 1;
      const hasMarker = wardNumbers(notice.title).size > 0 || titledWardKeys(notice.title).length > 0;
      const hasBroad = broadWardNumbers(notice.title).size > 0;
      if (hasMarker) {
        marker += 1;
        continue;
      }
      if (hasBroad) {
        broadOnly += 1;
        continue;
      }
      const known = plainNames(notice.title).filter((name) => gazetteer.keys.has(name.key));
      const namesRoad = NAMES_ROAD.test(notice.title);
      if (known.length) {
        locality += 1;
        hits.push({ notice, place: gazetteer.state.n[gazetteer.keys.get(known[0].key)[0]] });
      }
      if (namesRoad) road += 1;
      if (known.length || namesRoad) either += 1;
      else neither += 1;
    }
    rows.push({
      state, notices: notices.length, urban, bodies: [...bodies].sort((left, right) => right[1] - left[1]),
      ward_marker: marker, ward_marker_broader_only: broadOnly, no_ward: notices.length - marker - broadOnly,
      names_gazetteer_place: locality, names_road: road, names_place_or_road: either, names_neither: neither,
      zone_or_circle_number_read_as_ward: misread,
      gazetteer_places: gazetteer.state?.count || 0, hits,
    });
  }
  return rows;
}

// Thirty of the titles that name no ward but a place the gazetteer holds, for a person to
// read: is the place the gazetteer offers the place the title means?
export function sampleGazetteerHits(states, seed = 1, size = 30) {
  const hits = states.flatMap((row) => row.hits.map((hit) => ({ state: row.state, ...hit })))
    .sort((left, right) => (`${left.state} ${left.notice.tender_id}` < `${right.state} ${right.notice.tender_id}` ? -1 : 1));
  return shuffled(hits, seed).slice(0, size);
}

export function measureAll({ withUnlicensed = false } = {}) {
  const loaded = loadRoadNotices();
  const localities = loadLocalities();
  const index = JSON.parse(fs.readFileSync(INDEX_PATH, "utf8"));
  const snapshots = index.snapshots.map((entry) => {
    const source = sourceById(entry.id);
    const snapshot = JSON.parse(fs.readFileSync(path.join(root, entry.path), "utf8"));
    return { entry, source, snapshot, measure: measureSnapshot(source, snapshot, loaded.notices, localities) };
  });
  // Refused sources snapshotted under data/wards/.work/ for measuring. Only on request:
  // the report has to come out the same on a machine that never fetched them.
  const refused = SOURCES.filter((source) => withUnlicensed && !committable(source) && fs.existsSync(snapshotPath(source))).map((source) => {
    const snapshot = JSON.parse(fs.readFileSync(snapshotPath(source), "utf8"));
    return { source, snapshot, measure: measureSnapshot(source, snapshot, loaded.notices, localities) };
  });
  return { loaded, localities, index, snapshots, refused, states: measureStates(loaded, localities) };
}

const args = process.argv.slice(2);
const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain && args.includes("--json")) {
  const all = measureAll({ withUnlicensed: args.includes("--with-unlicensed") });
  const lean = (measure) => ({ ...measure, pairs: measure.pairs.length });
  console.log(JSON.stringify({
    snapshots: all.snapshots.map(({ measure }) => lean(measure)),
    refused: all.refused.map(({ measure }) => lean(measure)),
    states: all.states.map(({ hits, bodies, ...row }) => ({ ...row, top_bodies: bodies.slice(0, 4) })),
  }, null, 1));
} else if (isMain && args.includes("--gazetteer-pairs")) {
  const seed = args.includes("--seed") ? Number(args[args.indexOf("--seed") + 1]) : 1;
  for (const [at, hit] of sampleGazetteerHits(measureAll().states, seed).entries()) {
    console.log(`${at + 1}. [${hit.state}] gazetteer place "${hit.place}" | ${hit.notice.tender_id}\n     ${hit.notice.title}`);
  }
} else if (isMain && args.includes("--pairs")) {
  const id = args[args.indexOf("--pairs") + 1];
  const seed = args.includes("--seed") ? Number(args[args.indexOf("--seed") + 1]) : 1;
  const all = measureAll({ withUnlicensed: true });
  const found = [...all.snapshots, ...all.refused].find(({ source }) => source.id.toLowerCase() === String(id).toLowerCase());
  if (!found) throw new Error(`No snapshot ${id}`);
  for (const [at, pair] of samplePairs(found.measure, seed).entries()) {
    console.log(`${at + 1}. [${pair.basis}] ward ${pair.ward.no ?? ""} ${pair.ward.name ?? ""} (${pair.ward.code}) | ${pair.notice.tender_id}\n     ${pair.notice.title}`);
  }
} else if (isMain) {
  const { render } = await import("./india-ward-coverage-report.mjs");
  // What the service uses of all this (section 11): the runtime list, the pairs read for
  // it, and how many notices that makes returnable. Counted with the clock on the day the
  // notices were retrieved, so the report comes out the same on any day.
  const { RUNTIME_HANDREAD_PATH, RUNTIME_PATH, coverage } = await import("./india-ward-runtime.mjs");
  const runtime = fs.existsSync(RUNTIME_PATH) && fs.existsSync(RUNTIME_HANDREAD_PATH) ? {
    list: JSON.parse(fs.readFileSync(RUNTIME_PATH, "utf8")),
    read: JSON.parse(fs.readFileSync(RUNTIME_HANDREAD_PATH, "utf8")),
    coverage: await coverage({ now: null }),
  } : null;
  const text = render(measureAll({ withUnlicensed: args.includes("--with-unlicensed") }), {
    handread: fs.existsSync(HANDREAD_PATH) ? JSON.parse(fs.readFileSync(HANDREAD_PATH, "utf8")) : null, samplePairs, sampleGazetteerHits, runtime,
  });
  fs.writeFileSync(COVERAGE_PATH, text);
  console.log(`wrote ${path.relative(root, COVERAGE_PATH)} (${Buffer.byteLength(text)} bytes)`);
}
