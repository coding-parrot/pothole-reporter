import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { INDIA_WARDS_DIR, createIndiaWards } from "../service/india-wards.mjs";
import { RUNTIME_PATH } from "../tools/india-ward-runtime.mjs";
import { WARDS_DIR, root } from "../tools/snapshot-india-wards.mjs";
import { stageIndiaWards } from "../tools/stage-india-wards.mjs";

// What deploy.sh puts in the Lambda package for wards outside Karnataka: the runtime list
// and the snapshot files it switches on, and nothing else of data/wards (28 snapshots and
// a 9 MB gazetteer the service does not read).

const runtime = JSON.parse(readFileSync(RUNTIME_PATH));
const filesUnder = (dir, prefix = "") => readdirSync(dir).flatMap((name) => (statSync(path.join(dir, name)).isDirectory()
  ? filesUnder(path.join(dir, name), `${prefix}${name}/`) : [`${prefix}${name}`]));

test("the deploy stages the runtime list and the switched-on snapshots where the service reads them", () => {
  assert.equal(INDIA_WARDS_DIR.pathname, `${WARDS_DIR}/`);
  const deploy = readFileSync(path.join(root, "infra/aws-central/deploy.sh"), "utf8");
  assert.match(deploy, /node infra\/aws-central\/tools\/stage-india-wards\.mjs "\$TMP_DIR\/package\/data\/wards"/);
  assert.doesNotMatch(deploy, /cp -R data\/wards/, "the whole directory is never copied");
});

test("the staged copy holds only what is switched on, and the service answers from it alone", async () => {
  const out = mkdtempSync(path.join(os.tmpdir(), "staged-wards-"));
  const staged = stageIndiaWards(root, out);
  assert.deepEqual(filesUnder(out).sort(), ["runtime.json", ...runtime.snapshots.map((entry) => entry.file)].sort());
  assert.equal(staged.snapshots, runtime.snapshots.length);
  assert.equal(staged.bytes, filesUnder(out).reduce((sum, file) => sum + statSync(path.join(out, file)).size, 0));
  // 160,411 bytes on 7 Oct 2026 for Bhopal and Ahmedabad. Past half a megabyte someone
  // has switched on a great many cities, which is worth a look at the package and at
  // the first-load time of the largest State.
  assert.ok(staged.bytes < 500_000, `${staged.bytes} bytes staged`);
  const wards = createIndiaWards({ dir: out, logger: { error(line) { throw new Error(line); } } });
  assert.equal((await wards.locate(22.95558, 72.53967)).ward.name, "LAMBHA");
  assert.equal((await wards.locate(23.21383, 77.42127)).ward.no, "47");
});

// The package as deploy.sh lays it out: infra/aws-central/service beside data/. The
// service finds the ward files by a path relative to itself, so a copy of the service in
// that layout has to answer from the staged files with nothing else of the repo present.
test("a copy of the service in the package layout names the ward from the staged files", async () => {
  const pkg = mkdtempSync(path.join(os.tmpdir(), "package-"));
  cpSync(path.join(root, "infra/aws-central/service"), path.join(pkg, "infra/aws-central/service"), { recursive: true });
  stageIndiaWards(root, path.join(pkg, "data/wards"));
  const { createGeolocator } = await import(pathToFileURL(path.join(pkg, "infra/aws-central/service/geolocation.mjs")).href);
  const errors = [];
  const geolocator = createGeolocator({ logger: { error: (line) => errors.push(line), log() {} },
    fetchImpl: async () => { throw new Error("no network in this test"); } });
  const lambha = await geolocator.resolve({ lat: 22.95558, lng: 72.53967 });
  assert.deepEqual([lambha.ward_name, lambha.lookup.ward, lambha.lookup.ward_snapshot], ["LAMBHA", "resolved", "GJ/ahmedabad"]);
  const bhopal = await geolocator.resolve({ lat: 23.21383, lng: 77.42127 });
  assert.deepEqual([bhopal.ward_no, bhopal.lookup.ward_snapshot], ["47", "MP/bhopal"]);
  // Jaipur's file is committed and not switched on: it is not in the package, and is not missed.
  assert.equal((await geolocator.resolve({ lat: 26.9239, lng: 75.8267 })).lookup.ward, "out_of_scope");
  assert.deepEqual(errors, []);
});

test("staging refuses a snapshot that is missing or is not the file the list pinned", () => {
  // A checkout of its own: the list and the switched-on files, each as `patch` leaves it.
  const checkout = (patch) => {
    const dir = mkdtempSync(path.join(os.tmpdir(), "repo-"));
    for (const file of ["runtime.json", ...runtime.snapshots.map((entry) => entry.file)]) {
      const bytes = patch(file, readFileSync(path.join(WARDS_DIR, file)));
      if (bytes === null) continue;
      mkdirSync(path.dirname(path.join(dir, "data/wards", file)), { recursive: true });
      writeFileSync(path.join(dir, "data/wards", file), bytes);
    }
    return dir;
  };
  const out = () => mkdtempSync(path.join(os.tmpdir(), "out-"));
  const first = runtime.snapshots[0];
  assert.equal(stageIndiaWards(checkout((file, bytes) => bytes), out()).snapshots, runtime.snapshots.length);
  assert.throws(() => stageIndiaWards(checkout((file, bytes) => (file === first.file ? null : bytes)), out()),
    new RegExp(`${first.id}.*is not in the checkout`));
  assert.throws(() => stageIndiaWards(checkout((file, bytes) => (file === first.file ? Buffer.concat([bytes, Buffer.from(" ")]) : bytes)), out()),
    /does not match the hash/);
  assert.throws(() => stageIndiaWards(checkout((file, bytes) => (file === "runtime.json" ? Buffer.from("{}") : bytes)), out()),
    /not a pothole-india-ward-runtime/);
});
