#!/usr/bin/env node
// Stage the ward snapshots outside Karnataka for the Lambda package.
//
//   node infra/aws-central/tools/stage-india-wards.mjs <out-dir>
//
// data/wards holds 28 ward snapshots, a 9 MB locality gazetteer and the reports about
// them. The service reads two kinds of file from it (service/india-wards.mjs): the
// runtime list, data/wards/runtime.json, and the snapshots that list switches on. This
// copies exactly those, at the same paths relative to <out-dir>, and checks each
// snapshot against the hash the list pins on the way: a deploy with a switched-on
// snapshot missing or edited stops here, before anything is uploaded.

import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { INDIA_WARD_RUNTIME_FILE, INDIA_WARD_RUNTIME_FORMAT } from "../service/india-wards.mjs";

export function stageIndiaWards(root, outDir) {
  const wardsDir = path.join(root, "data/wards");
  const listPath = path.join(wardsDir, INDIA_WARD_RUNTIME_FILE);
  const list = JSON.parse(readFileSync(listPath, "utf8"));
  if (list.format !== INDIA_WARD_RUNTIME_FORMAT || !Array.isArray(list.snapshots)) {
    throw new Error(`data/wards/${INDIA_WARD_RUNTIME_FILE} is not a ${INDIA_WARD_RUNTIME_FORMAT}`);
  }
  mkdirSync(outDir, { recursive: true });
  copyFileSync(listPath, path.join(outDir, INDIA_WARD_RUNTIME_FILE));
  const staged = { snapshots: 0, bytes: readFileSync(listPath).length, ids: [] };
  for (const entry of list.snapshots) {
    const source = path.join(wardsDir, entry.file);
    if (!existsSync(source)) throw new Error(`${entry.id}: data/wards/${entry.file} is switched on and is not in the checkout`);
    const bytes = readFileSync(source);
    if (createHash("sha256").update(bytes).digest("hex") !== entry.sha256) {
      throw new Error(`${entry.id}: data/wards/${entry.file} does not match the hash in the runtime list; `
        + "run node infra/aws-central/tools/india-ward-runtime.mjs --write");
    }
    const target = path.join(outDir, entry.file);
    mkdirSync(path.dirname(target), { recursive: true });
    copyFileSync(source, target);
    staged.snapshots += 1;
    staged.bytes += bytes.length;
    staged.ids.push(entry.id);
  }
  return staged;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
  const outDir = process.argv[2];
  if (!outDir) {
    console.error("usage: stage-india-wards.mjs <out-dir>");
    process.exit(2);
  }
  console.log(JSON.stringify({ out: path.resolve(outDir), ...stageIndiaWards(root, path.resolve(outDir)) }));
}
