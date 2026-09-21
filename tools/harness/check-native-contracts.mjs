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
//
// The APK also makes promises outside the code: the user agent every tile request and
// drive log carries, and the permissions Play lists. The 1.39.x builds announced
// themselves as 1.38.0, and the manifest kept asking for CHANGE_NETWORK_STATE and
// POST_NOTIFICATIONS long after the code that used them was unwired.
//
// NATIVE_CONTRACTS_ROOT points the check at a copy of the repo, so a test can plant a
// mismatch and watch it fail.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";

const repoRoot = resolve(process.env.NATIVE_CONTRACTS_ROOT || resolve(import.meta.dirname, "../.."));
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

// The WebView appends this suffix to its user agent. Capacitor reads it from the
// packaged copy of the config, so that copy has to match the source too.
const versionName = /versionName "([^"]+)"/.exec(read("android-app/android/app/build.gradle"))?.[1];
const userAgent = (path) => JSON.parse(read(path)).appendUserAgent || "";
const sourceUserAgent = userAgent("android-app/capacitor.config.json");
if (!versionName) failures.push("versionName: not found in android-app/android/app/build.gradle");
else if (!sourceUserAgent.startsWith(`PotholeReporter/${versionName} `)) {
  failures.push(`user agent: capacitor.config.json says "${sourceUserAgent.split(" ")[0]}", build.gradle versionName is ${versionName}`);
}
const packagedConfig = "android-app/android/app/src/main/assets/capacitor.config.json";
if (existsSync(`${repoRoot}/${packagedConfig}`) && userAgent(packagedConfig) !== sourceUserAgent) {
  failures.push(`user agent: ${packagedConfig} differs from android-app/capacitor.config.json (run npx cap copy android)`);
}

// Every permission the manifest declares needs a user in the wired tree: either code
// that names it (comments do not count), or a witness for the library that uses it
// on the app's behalf. A witness that disappears takes the permission's excuse with it.
const wiredRoot = "android-app/android/app/src/main/java/com/gauravsen";
const kotlinFiles = (dir) => readdirSync(`${repoRoot}/${dir}`).flatMap((name) => {
  const path = `${dir}/${name}`;
  if (statSync(`${repoRoot}/${path}`).isDirectory()) return kotlinFiles(path);
  return /\.(kt|java)$/.test(name) ? [path] : [];
});
const wiredCode = kotlinFiles(wiredRoot)
  .map((path) => read(path).replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, ""))
  .join("\n");
const webCode = engine + read("static/index.html");
const implicitUsers = {
  INTERNET: ["the WebView page and CentralServiceClient", webCode, /fetch\(/],
  ACCESS_COARSE_LOCATION: [
    "Capacitor's BridgeWebChromeClient, which asks for it with FINE when the page reads navigator.geolocation",
    webCode, /navigator\.geolocation/],
  ACCESS_NETWORK_STATE: ["WorkManager, for UploadWorker's network constraint", wiredCode, /NetworkType\.CONNECTED/],
  WAKE_LOCK: ["WorkManager, which holds one while UploadWorker runs", wiredCode, /androidx\.work\./],
};
const permissions = [...read("android-app/android/app/src/main/AndroidManifest.xml")
  .replace(/<!--[\s\S]*?-->/g, "")
  .matchAll(/<uses-permission\s+android:name="android\.permission\.([A-Z_]+)"/g)].map((m) => m[1]);
for (const permission of permissions) {
  const named = new RegExp(`\\b${permission}\\b`).test(wiredCode);
  const implicit = implicitUsers[permission];
  if (named || (implicit && implicit[2].test(implicit[1]))) continue;
  failures.push(implicit
    ? `permission ${permission}: declared for ${implicit[0]}, but that user is gone`
    : `permission ${permission}: declared in the manifest, but no wired code under ${wiredRoot} uses it`);
}

if (failures.length) {
  console.log(`FAIL ${failures.length} web/native contract mismatch(es):`);
  for (const failure of failures) console.log("  - " + failure);
  process.exit(1);
}
console.log(`web and native agree on ${pairs.length} shared contract value(s); the user agent says ${versionName}; ${permissions.length} manifest permission(s) each have a user.`);
