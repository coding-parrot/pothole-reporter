#!/usr/bin/env node
// Stage the national tender catalogues for the Lambda package.
//
//   node infra/aws-central/tools/stage-national-tenders.mjs <out-dir>
//
// The phone reads three manifests (static/contract-manifest-v1.NN.json,
// road-notice-manifest-v1.NN.json, road-agreement-manifest-v1.NN.json) and downloads the
// packs they pin from docs/packs/v1/ on first use. The service reads the same files from
// its own package: this copies the manifests the shipped phone names to fixed file names
// and the pinned pack of every state next to them, verifying each pack's sha256 against
// its manifest entry on the way. Only pinned packs are copied; docs/packs keeps the
// previous week's files too, and those are not the catalogue.

import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { CATALOGUES } from "../service/national-tenders.mjs";

const PHONE = "static/standalone.js";
const MANIFEST_CONSTANTS = {
  nh_contract: "CONTRACT_MANIFEST_FILE",
  road_notice: "ROAD_NOTICE_MANIFEST_FILE",
  road_agreement: "ROAD_AGREEMENT_MANIFEST_FILE",
};

// The manifest file the shipped phone reads, so the phone and the service match against
// the same catalogue. A newer manifest in static/ that standalone.js does not name yet
// is not shipped either way.
export function phoneManifestFiles(root) {
  const source = readFileSync(path.join(root, PHONE), "utf8");
  const files = {};
  for (const [kind, constant] of Object.entries(MANIFEST_CONSTANTS)) {
    const match = new RegExp(`const ${constant} = "([a-z-]+-manifest-v[0-9.]+\\.json)";`).exec(source);
    if (!match) throw new Error(`${PHONE} does not name ${constant}`);
    files[kind] = match[1];
  }
  return files;
}

export function stageNationalCatalogue(root, outDir) {
  const files = phoneManifestFiles(root);
  const staged = { packs: 0, bytes: 0, manifests: {} };
  mkdirSync(outDir, { recursive: true });
  for (const [kind, file] of Object.entries(files)) {
    const spec = CATALOGUES[kind];
    const manifest = JSON.parse(readFileSync(path.join(root, "static", file), "utf8"));
    if (manifest.format !== spec.manifestFormat) {
      throw new Error(`${file} is a ${manifest.format}, not a ${spec.manifestFormat}`);
    }
    writeFileSync(path.join(outDir, spec.manifest), JSON.stringify(manifest));
    staged.manifests[kind] = file;
    for (const resource of Object.values(manifest.resources)) {
      const source = path.join(root, "docs", resource.path);
      const bytes = readFileSync(source);
      const sha = createHash("sha256").update(bytes).digest("hex");
      if (bytes.length !== resource.bytes || sha !== resource.sha256) {
        throw new Error(`${resource.pack_id}: docs/${resource.path} does not match its manifest entry`);
      }
      const target = path.join(outDir, resource.path);
      mkdirSync(path.dirname(target), { recursive: true });
      copyFileSync(source, target);
      staged.packs += 1;
      staged.bytes += bytes.length;
    }
  }
  return staged;
}

export function latestManifestVersions(root) {
  const names = readdirSync(path.join(root, "static"));
  const versions = {};
  for (const [kind, spec] of Object.entries(CATALOGUES)) {
    const prefix = spec.manifest.replace(/\.json$/, "-v");
    versions[kind] = names.filter((name) => name.startsWith(prefix) && name.endsWith(".json"))
      .sort((a, b) => Number(a.slice(prefix.length, -5).split(".")[1]) - Number(b.slice(prefix.length, -5).split(".")[1]))
      .pop() || null;
  }
  return versions;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
  const outDir = process.argv[2];
  if (!outDir) {
    console.error("usage: stage-national-tenders.mjs <out-dir>");
    process.exit(2);
  }
  const staged = stageNationalCatalogue(root, path.resolve(outDir));
  const latest = latestManifestVersions(root);
  for (const [kind, file] of Object.entries(staged.manifests)) {
    if (latest[kind] && latest[kind] !== file) {
      console.error(`note: ${PHONE} names ${file}; static/ also holds ${latest[kind]}, which the phone does not read yet`);
    }
  }
  console.log(JSON.stringify({ out: path.resolve(outDir), ...staged }));
}
