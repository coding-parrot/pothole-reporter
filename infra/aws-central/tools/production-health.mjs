#!/usr/bin/env node
// Production health gate, from a terminal. deploy.sh runs it on every deploy; the same
// rules run on a schedule inside the stack (service/health/function.mjs, the
// HealthFunction in template.yaml), which is where the hourly check lives.
//
//   node infra/aws-central/tools/production-health.mjs --window 24h   last N hours of logs
//   node infra/aws-central/tools/production-health.mjs --canary        live calls, now
//   node infra/aws-central/tools/production-health.mjs --window 6h --canary
//
// It says which rule is broken and exits 1, so a deploy cannot be green while users are
// failing. The rules themselves are in service/health (window.mjs, canary.mjs,
// rules.mjs); this file only gives them the aws CLI, fetch and a fresh install key.
//
// --window needs the aws CLI with Logs Insights rights on the function's log group.
// --canary needs only the public API URL (API_URL, default the production stack).

import { execFileSync } from "node:child_process";
import { generateKeyPairSync } from "node:crypto";

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

function aws(...params) {
  return JSON.parse(execFileSync("aws", [...params, "--region", REGION, "--output", "json"],
    { encoding: "utf8", timeout: 120_000 }));
}

// Logs Insights through the aws CLI: asked every 1.5 s, given up on after 90 s.
const insights = createInsights({
  logGroupName: LOG_GROUP,
  pollMs: 1500,
  patienceMs: 90_000,
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

const windowArg = value("window", null);
if (!windowArg && !flag("canary")) {
  console.error("usage: production-health.mjs [--window 24h] [--canary]");
  process.exit(2);
}
const report = createReport({ print: (line) => console.log(line) });
try {
  if (windowArg) await judgeWindow({ query: insights, hours: parseWindow(windowArg), logGroup: LOG_GROUP, report });
  if (flag("canary")) {
    await runCanary({
      apiUrl: API_URL,
      fetch,
      report,
      // A new key, so a new install, on every run. The scheduled function keeps one.
      identity: async () => generateKeyPairSync("ec", { namedCurve: "prime256v1" }),
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
