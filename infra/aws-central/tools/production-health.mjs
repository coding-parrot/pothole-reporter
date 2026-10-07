#!/usr/bin/env node
// Production health gate, from a terminal. deploy.sh runs it on every deploy; the same
// rules run on a schedule inside the stack (service/health/function.mjs, the
// HealthFunction in template.yaml), which is where the hourly check lives.
//
//   node infra/aws-central/tools/production-health.mjs --window 24h   last N hours of logs
//   node infra/aws-central/tools/production-health.mjs --window 7d    or days: a week is
//                                    what the screen's readiness report is read over
//   node infra/aws-central/tools/production-health.mjs --canary        live calls, now
//   node infra/aws-central/tools/production-health.mjs --window 6h --canary
//
// It says which rule is broken and exits 1, so a deploy cannot be green while users are
// failing. The rules themselves are in service/health (window.mjs, canary.mjs,
// rules.mjs); this file only gives them the aws CLI, fetch and a fresh install key.
//
// --window needs the aws CLI with Logs Insights rights on the function's log group.
// --canary needs only the public API URL (API_URL, default the production stack). With
// credentials that can read the scheduled canary's key it runs as that install.

import { execFileSync } from "node:child_process";
import { createPrivateKey, createPublicKey, generateKeyPairSync } from "node:crypto";

import { runCanary } from "../service/health/canary.mjs";
import { readExampleImage } from "../service/health/example-image.mjs";
import { createInsights } from "../service/health/insights.mjs";
import { createReport } from "../service/health/report.mjs";
import { INDIA_WARD_CANARY } from "../service/health/rules.mjs";
import { judgeWindow, parseWindow } from "../service/health/window.mjs";

const args = process.argv.slice(2);
const flag = (name) => args.includes(`--${name}`);
const value = (name, fallback) => {
  const index = args.indexOf(`--${name}`);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
};
const API_URL = (process.env.API_URL || "https://ffjvg34k07.execute-api.ap-south-1.amazonaws.com").replace(/\/$/, "");
const LOG_GROUP = process.env.LOG_GROUP || "/aws/lambda/pothole-reporter-central";
const REGION = process.env.AWS_REGION || "ap-south-1";
const KEY_PARAMETER = process.env.CANARY_KEY_PARAMETER || "/pothole-reporter-central/health/canary-key";

function aws(...params) {
  return JSON.parse(execFileSync("aws", [...params, "--region", REGION, "--output", "json"],
    { encoding: "utf8", timeout: 120_000 }));
}

// Logs Insights through the aws CLI: asked every 1.5 s, given up on after 90 s. What the
// queries scanned is what the run cost (USD 0.0067 a GB in Mumbai), and is said at the
// end: the longer windows (--window 7d, for the screen's readiness report) are the ones
// worth knowing it for.
const scanned = { bytes: 0, queries: 0 };
const insights = createInsights({
  logGroupName: LOG_GROUP,
  pollMs: 1500,
  patienceMs: 90_000,
  onScanned: (bytes) => { if (bytes) { scanned.bytes += bytes; scanned.queries += 1; } },
  logs: {
    startQuery: ({ logGroupName, startTime, endTime, queryString }) => aws("logs", "start-query", "--log-group-name", logGroupName,
      "--start-time", String(startTime), "--end-time", String(endTime), "--query-string", queryString),
    getQueryResults: ({ queryId }) => aws("logs", "get-query-results", "--query-id", queryId),
    stopQuery: ({ queryId }) => aws("logs", "stop-query", "--query-id", queryId),
  },
});

// How many open notices this checkout's own catalogue holds for the canary's ward today,
// by the service's own lookup and matcher over the repo's files; null if they cannot be
// read. Only asked for when the catalogue just deployed is this checkout's.
async function openNoticesForCanaryWard() {
  try {
    const { createIndiaWards } = await import("../service/india-wards.mjs");
    const { matchIndiaWardTenders } = await import("../service/india-ward-tenders.mjs");
    const { loadNoticePacks } = await import("./india-ward-runtime.mjs");
    const found = await createIndiaWards({ logger: { error() {} } }).locate(INDIA_WARD_CANARY.lat, INDIA_WARD_CANARY.lng);
    const held = found.ward ? loadNoticePacks().packs.get(found.snapshot.state_code) : null;
    if (!held) return null;
    return matchIndiaWardTenders({ ward: found.ward, snapshot: found.snapshot, pack: held.pack, now: Date.now(), limit: Infinity }).length;
  } catch {
    return null;
  }
}

// The scheduled canary's one install, when this caller may read its key (the deploying
// user can): the service leaves that install out of the public figures and marks its
// request lines, so a deploy's canary is no longer a new "active installation" each
// time. Anyone else (no aws CLI, or the GitHub workflow's user, who may only query the
// log) runs as a new install, and the "install registers" line says which it was. The
// key is held in memory and never printed.
function canaryIdentity() {
  try {
    const pem = JSON.parse(execFileSync("aws", ["ssm", "get-parameter", "--name", KEY_PARAMETER, "--with-decryption",
      "--region", REGION, "--output", "json"], { encoding: "utf8", timeout: 30_000, stdio: ["ignore", "pipe", "pipe"] })).Parameter.Value;
    const privateKey = createPrivateKey(pem);
    return { privateKey, publicKey: createPublicKey(privateKey), described: "the scheduled canary's stored install" };
  } catch {
    return { ...generateKeyPairSync("ec", { namedCurve: "prime256v1" }), described: "a new install: the stored canary key could not be read" };
  }
}

const windowArg = value("window", null);
if (!windowArg && !flag("canary")) {
  console.error("usage: production-health.mjs [--window 24h] [--canary]");
  process.exit(2);
}
const report = createReport({ print: (line) => console.log(line) });
try {
  if (windowArg) {
    await judgeWindow({ query: insights, hours: parseWindow(windowArg), logGroup: LOG_GROUP, report });
    if (scanned.bytes) report.note(`${(scanned.bytes / 1e6).toFixed(1)} MB of log scanned by ${scanned.queries} queries`);
  }
  if (flag("canary")) {
    await runCanary({
      apiUrl: API_URL,
      fetch,
      report,
      identity: async () => canaryIdentity(),
      readImage: readExampleImage,
      // CANARY_CATALOGUE_IS_THIS_CHECKOUT: deploy.sh sets it, because the notices
      // production serves are then the ones this checkout staged.
      expectedOpenNotices: async () => (process.env.CANARY_CATALOGUE_IS_THIS_CHECKOUT === "1" ? openNoticesForCanaryWard() : null),
    });
  }
} catch (error) {
  report.crashed(error);
}
process.exit(report.conclude().healthy ? 0 : 1);
