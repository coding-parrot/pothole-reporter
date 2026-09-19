#!/usr/bin/env node
// The web engine and the native service must agree on the contracts they exchange.
//
//   node tools/harness/check-native-contracts.mjs
//
// The repair contract crossed that boundary as a version string. The Kotlin side moved
// to road-repair-v2 and the JavaScript side stayed on v1, so applyRepairObservation
// rejected every observation the native service sent as repair_provenance_invalid, and
// no pothole could ever be marked repaired. Nothing failed: the app just stopped
// verifying repairs.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "../..");
const read = (path) => readFileSync(`${repoRoot}/${path}`, "utf8");
const kotlinRoot = "android-app/android/app/src/main/java/dev/aiengg/potholereporter";
const engine = read("static/standalone.js");

const pairs = [
  {
    what: "native repair prompt version",
    js: /const NATIVE_REPAIR_CONTRACT_VERSION = "([^"]+)"/,
    kotlin: [`${kotlinRoot}/drive/NativeRepairContract.kt`, /const val PROMPT_VERSION = "([^"]+)"/],
  },
  {
    what: "native repair schema version",
    js: /const REPAIR_SCHEMA_VERSION = (\d+)/,
    kotlin: [`${kotlinRoot}/drive/NativeRepairContract.kt`, /const val SCHEMA_VERSION = (\d+)/],
  },
];

const failures = [];
for (const pair of pairs) {
  const js = pair.js.exec(engine);
  const [path, pattern] = pair.kotlin;
  const kotlin = pattern.exec(read(path));
  if (!js) { failures.push(`${pair.what}: not found in static/standalone.js`); continue; }
  if (!kotlin) { failures.push(`${pair.what}: not found in ${path}`); continue; }
  if (js[1] !== kotlin[1]) {
    failures.push(`${pair.what}: standalone.js says ${js[1]}, ${path} says ${kotlin[1]}`);
  }
}

if (failures.length) {
  console.log(`FAIL ${failures.length} web/native contract mismatch(es):`);
  for (const failure of failures) console.log("  - " + failure);
  process.exit(1);
}
console.log(`web and native agree on ${pairs.length} shared contract value(s).`);
