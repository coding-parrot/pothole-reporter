#!/usr/bin/env node
// The data the ward tender name rules (service/ward-tenders.mjs) were read off, and the
// check that they still hold. Reads only files in the repo:
//
//   node infra/aws-central/tools/ward-tender-vocabulary.mjs [--body BLR] [--towns 20G] [--seed 1]
//
// Prints: the place-name vocabulary of the body's tender titles and of the KGIS ward
// names, how many ward names find a tender, every pair of spellings that each rule is
// there for (found by switching the rule off), and 40 random ward and tender pairs for a
// person to read.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  ENDING_RULES, SPELLING_EDITS, SPELLING_RULES, localityKeys, matchWardTenders, offeredFor, sameLocality,
  titleNameKeys,
} from "../service/ward-tenders.mjs";
import { prepareTenders } from "./seed-tenders.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const args = process.argv.slice(2);
const arg = (key, fallback) => (args.includes(key) ? args[args.indexOf(key) + 1] : fallback);
const body = arg("--body", "BLR");
const townPrefix = arg("--towns", "20G");
let seed = Number(arg("--seed", "1"));

// The pack is read for its titles, not to seed a table, so its review date is not enforced
// here: `now` defaults to a date before any pack.
export function loadBodyTenders(bodyLgd, now = new Date(0)) {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, "static/pack-manifest-v1.35.json")));
  const resource = manifest.resources["in-ka-tenders"];
  const bytes = fs.readFileSync(path.join(root, "docs", resource.path));
  const raw = JSON.parse(bytes).tenders.filter((row) => String(row.b) === bodyLgd).length;
  return { raw, rows: prepareTenders(resource, bytes, now).filter((row) => row.body_lgd === bodyLgd) };
}

export function loadWardNames(prefix) {
  const bundle = JSON.parse(fs.readFileSync(path.join(root, "data/karnataka-ward-geometry.json")));
  return Object.entries(bundle.towns).filter(([code]) => code.startsWith(prefix))
    .flatMap(([, town]) => town.wards.map(([code, no, name, bbox]) => ({
      code, no, name, bbox: bbox.map((value) => value / bundle.coordinate_scale),
    })))
    .filter((ward) => ward.name);
}

function main() {
  const { raw, rows } = loadBodyTenders(body);
  const wards = loadWardNames(townPrefix);
  // A name behind another name (see titleNameKeys) is left out: it counts only for a
  // point in both places, and this tool has ward names, not points.
  const namesIn = (title, without = null) => titleNameKeys(title, without).filter((entry) => !entry.lead);
  const titleKeys = rows.map((row) => namesIn(row.title));
  const vocabulary = new Map();
  for (const keys of titleKeys) for (const entry of keys) vocabulary.set(entry.key, (vocabulary.get(entry.key) || 0) + 1);
  const wardKeys = new Map();
  for (const ward of wards) for (const entry of localityKeys(ward.name)) wardKeys.set(entry.key, ward.name);
  console.log(`body ${body}: ${raw} tender rows, ${rows.length} with a road-surface scope`);
  console.log(`title vocabulary: ${vocabulary.size} distinct place-name spellings (runs of 1 to 3 words)`);
  console.log(`KGIS wards under ${townPrefix}*: ${wards.length} named, ${wardKeys.size} distinct spellings, `
    + `${wards.filter((ward) => !localityKeys(ward.name).length).length} too generic to match`);

  // Coverage, and the pairs that are spelled differently.
  const matched = [];
  const variants = new Map();
  const pairs = [];
  const namesakes = [];
  for (const ward of wards) {
    const keys = localityKeys(ward.name);
    // The ward's centre stands in for a point in it.
    const point = { lat: (ward.bbox[1] + ward.bbox[3]) / 2, lng: (ward.bbox[0] + ward.bbox[2]) / 2 };
    const everyHit = matchWardTenders({ wardName: ward.name, tenders: rows, limit: Infinity });
    const hits = matchWardTenders({ wardName: ward.name, tenders: rows, point, roster: wards, limit: Infinity });
    for (const hit of everyHit) {
      if (!hits.some((kept) => kept.tender_number === hit.tender_number)) namesakes.push({ ward: ward.name, title: hit.title });
    }
    if (hits.length) matched.push({ ward, hits });
    for (const hit of hits) pairs.push({ ward: ward.name, title: hit.title });
    for (const entry of keys) {
      for (const [index, names] of titleKeys.entries()) {
        for (const name of names) {
          if (sameLocality(entry, name) !== "variant" || !offeredFor(entry, name)) continue;
          const label = `${entry.key} ~ ${name.key}`;
          if (!variants.has(label)) variants.set(label, { ward: ward.name, title: rows[index].title, count: 0 });
          variants.get(label).count += 1;
        }
      }
    }
  }
  console.log(`\nward names with at least one tender: ${matched.length} of ${wards.length} `
    + `(${pairs.length} ward and tender pairs, ${new Set(pairs.map((pair) => pair.title)).size} distinct tenders)`);
  console.log(`\nleft out once the point and the register's wards are given (a namesake ward over 3 km away, a division `
    + `that works nowhere near, an old ward number that is not the ward's): ${namesakes.length}`);
  for (const pair of namesakes) console.log(`  [${pair.ward}]  ${pair.title.slice(0, 130)}`);
  console.log(`\nspelling variants accepted (not written the same after normalising): ${variants.size}`);
  for (const [label, { ward, title, count }] of [...variants].sort()) {
    console.log(`  ${label}  [${ward}] x${count}  e.g. ${title.slice(0, 110)}`);
  }

  // What each rule is there for: the ward names that lose every tender without it.
  const covered = (without) => new Set(wards.filter((ward) => {
    const keys = localityKeys(ward.name, without);
    return keys.length && rows.some((row) => namesIn(row.title, without)
      .some((name) => keys.some((entry) => offeredFor(entry, name, without))));
  }).map((ward) => ward.name));
  const everything = covered(null);
  console.log("\nwhat each rule is there for (ward names that find no tender without it):");
  for (const name of [...SPELLING_RULES, ...ENDING_RULES].map(([rule]) => rule).concat(SPELLING_EDITS)) {
    const without = covered(new Set([name]));
    const lost = [...everything].filter((ward) => !without.has(ward));
    console.log(`  ${name}: ${lost.length}  ${lost.slice(0, 12).join(", ")}${lost.length > 12 ? ", ..." : ""}`);
  }

  // 40 pairs for a person to read, the same 40 for the same seed.
  const random = () => {
    seed = (Math.imul(seed, 1_103_515_245) + 12_345) & 0x7fffffff;
    return seed / 0x7fffffff;
  };
  const sample = [];
  const pool = [...pairs];
  while (sample.length < 40 && pool.length) sample.push(pool.splice(Math.floor(random() * pool.length), 1)[0]);
  console.log("\n40 random ward and tender pairs to read:");
  sample.forEach((pair, index) => console.log(`  ${String(index + 1).padStart(2)}. [${pair.ward}]  ${pair.title}`));

  const unmatched = wards.filter((ward) => !everything.has(ward.name)).map((ward) => ward.name).sort();
  console.log(`\nward names with no tender (${unmatched.length}): ${unmatched.join(" | ")}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
