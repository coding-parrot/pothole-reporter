#!/usr/bin/env node
// Production health gate. Runs on every deploy and every few hours from CI.
//
//   node infra/aws-central/tools/production-health.mjs --window 24h   last N hours of logs
//   node infra/aws-central/tools/production-health.mjs --canary        live calls, now
//   node infra/aws-central/tools/production-health.mjs --window 6h --canary
//
// Why this exists. Between 19 Sept and 6 Oct 2026 the service answered 227 tender lookups
// with "state GIS unavailable", 197 with "no address", rejected 125 reports on their own
// receipt, turned 74 detections away at the daily cap and never matched a tender for a
// real user. Every number was in the logs and nobody read them. This script reads them,
// says which rule is broken, and exits 1 so a deploy or a scheduled run cannot be green
// while users are failing. The thresholds are rules, not observations: change them only
// with a reason in the commit.
//
// --window needs the aws CLI with Logs Insights rights on the function's log group.
// --canary needs only the public API URL (API_URL, default the production stack).

import { createHash, generateKeyPairSync, randomUUID, sign } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  SHADOW_SCREEN_QUERY, WARD_TENDER_QUERY, judgeWardSnapshot, judgeWardTenders, reportShadowScreen,
} from "./health-rules.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const args = process.argv.slice(2);
const flag = (name) => args.includes(`--${name}`);
const value = (name, fallback) => {
  const index = args.indexOf(`--${name}`);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
};
const API_URL = (process.env.API_URL || "https://ffjvg34k07.execute-api.ap-south-1.amazonaws.com").replace(/\/$/, "");
const LOG_GROUP = process.env.LOG_GROUP || "/aws/lambda/pothole-reporter-central";
const REGION = process.env.AWS_REGION || "ap-south-1";

// A real Bengaluru street inside GBA Central, used by 49 real reports. The hint is what
// the phone would send; the second lookup sends none to prove the server's own geocoder.
const CANARY_POINT = { lat: 12.99657, lng: 77.62034, hint: "Cambridge Road, Halasuru, Bengaluru" };

const failures = [];
const notes = [];
const fail = (rule, detail) => { failures.push(`${rule}: ${detail}`); console.log(`  FAIL ${rule}: ${detail}`); };
const ok = (rule, detail) => console.log(`  ok   ${rule}: ${detail}`);

// ---------------------------------------------------------------- log window rules
function aws(...params) {
  return JSON.parse(execFileSync("aws", [...params, "--region", REGION, "--output", "json"],
    { encoding: "utf8", timeout: 120_000 }));
}

async function insights(query, hours) {
  const end = Math.floor(Date.now() / 1000);
  const start = end - hours * 3600;
  const { queryId } = aws("logs", "start-query", "--log-group-name", LOG_GROUP,
    "--start-time", String(start), "--end-time", String(end), "--query-string", query);
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const result = aws("logs", "get-query-results", "--query-id", queryId);
    if (result.status === "Complete") {
      return result.results.map((row) => Object.fromEntries(row.map(({ field, value: v }) => [field, v])));
    }
    if (["Failed", "Cancelled", "Timeout"].includes(result.status)) throw new Error(`Logs Insights ${result.status}`);
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  throw new Error("Logs Insights query did not complete");
}

function parseWindow(text) {
  const match = /^(\d+)(h|d)$/.exec(String(text || ""));
  if (!match) throw new Error(`--window takes hours or days, like 24h or 7d, not ${text}`);
  return Number(match[1]) * (match[2] === "d" ? 24 : 1);
}

async function windowRules(hours) {
  console.log(`\nLog window: last ${hours} h of ${LOG_GROUP}`);
  const rows = await insights(
    'filter event="http_request" | stats count() as n, pct(duration_ms, 50) as p50, pct(duration_ms, 90) as p90 by route, outcome, status',
    hours,
  );
  const count = (route, outcome) => rows.filter((row) => (!route || row.route === route) && (!outcome || row.outcome === outcome))
    .reduce((sum, row) => sum + Number(row.n), 0);
  const total = count();
  notes.push(`${total} requests in ${hours} h`);
  if (!total) {
    ok("traffic", "no requests in the window; nothing to judge");
    return;
  }
  const internalErrors = count(null, "internal_error");
  internalErrors ? fail("no internal errors", `${internalErrors} requests crashed`) : ok("no internal errors", "0");

  const receipts = count("/v1/potholes/report", "invalid_detection_receipt");
  receipts ? fail("reports never rejected on their receipt", `${receipts} rejected; this is a bug in the receipt key`)
    : ok("reports never rejected on their receipt", "0");

  for (const outcome of ["daily_vision_limit", "shared_rate_limit", "shared_daily_budget_reached", "shared_budget_reached"]) {
    const hits = count("/v1/vision/detect", outcome);
    hits ? fail(`cap never reached (${outcome})`, `${hits} detections refused; raise the cap in template.yaml and test/caps.test.mjs`)
      : ok(`cap never reached (${outcome})`, "0");
  }

  const resolves = count("/v1/tenders/resolve");
  const reports = count("/v1/potholes/report");
  const lookups = resolves + reports;
  const unavailable = count(null, "road_ownership_unavailable");
  const unavailablePct = lookups ? (100 * unavailable) / lookups : 0;
  unavailable > 20 || unavailablePct > 5
    ? fail("road ownership answered", `${unavailable} of ${lookups} lookups (${unavailablePct.toFixed(1)}%) could not classify the road; the state GIS is down and the local fallback did not cover them`)
    : ok("road ownership answered", `${unavailable} of ${lookups} unclassified`);

  const unresolved = count("/v1/tenders/resolve", "address_unresolved");
  const unresolvedPct = resolves ? (100 * unresolved) / resolves : 0;
  unresolved > 20 || unresolvedPct > 5
    ? fail("street address resolved", `${unresolved} of ${resolves} lookups (${unresolvedPct.toFixed(1)}%) had no usable street; the reverse geocoder is failing`)
    : ok("street address resolved", `${unresolved} of ${resolves} without a street`);

  const inProgress = count("/v1/potholes/report", "location_dedupe_in_progress");
  const inProgressPct = reports ? (100 * inProgress) / reports : 0;
  inProgressPct > 10
    ? fail("reports land", `${inProgress} of ${reports} reports (${inProgressPct.toFixed(1)}%) were told to retry on the location lock`)
    : ok("reports land", `${inProgress} of ${reports} retried on the lock`);

  const matched = count("/v1/tenders/resolve", "tender_matched") + count("/v1/potholes/report", "tender_matched");
  const municipal = resolves - unresolved - unavailable - count("/v1/tenders/resolve", "outside_state")
    - count("/v1/tenders/resolve", "preflight") - count("/v1/tenders/resolve", "idempotent_replay");
  if (municipal >= 20 && matched === 0) {
    fail("tenders match", `${municipal} lookups reached matching and none matched; the tender table is empty or matching is broken`);
  } else {
    ok("tenders match", `${matched} matched of ${resolves} lookups`);
  }

  // Since 6 Oct 2026 every lookup with a street and a State/UT is matched against the
  // national catalogues too, so a window with many resolved streets and no match anywhere
  // in India means the packed catalogues are missing, expired past their review date
  // (the weekly refresh was not deployed) or matching is broken. These four outcomes are
  // exactly the lookups whose street reached a matcher.
  const streetResolved = ["tender_matched", "no_location_match", "no_confident_match", "no_tenders_for_jurisdiction"]
    .reduce((sum, outcome) => sum + count("/v1/tenders/resolve", outcome), 0);
  const resolveMatched = count("/v1/tenders/resolve", "tender_matched");
  if (streetResolved >= 50 && resolveMatched === 0) {
    fail("tenders match somewhere in India", `${streetResolved} lookups had a street and none matched any catalogue; check the packed national catalogues and their review dates`);
  } else {
    ok("tenders match somewhere in India", `${resolveMatched} matched of ${streetResolved} lookups with a street`);
  }

  // Most Bengaluru tenders name a ward or a locality, never a street, so since the ward
  // tender release a municipal lookup with a resolved ward should often come back with
  // something a person can read.
  const wardRows = await insights(WARD_TENDER_QUERY, hours);
  const wardSnapshot = judgeWardSnapshot(wardRows);
  wardSnapshot.broken ? fail("ward snapshot is in the package", wardSnapshot.detail) : ok("ward snapshot is in the package", wardSnapshot.detail);
  const wardTenders = judgeWardTenders(wardRows);
  wardTenders.broken ? fail("wards find their tenders", wardTenders.detail) : ok("wards find their tenders", wardTenders.detail);

  const detect = rows.filter((row) => row.route === "/v1/vision/detect" && Number(row.status) === 200);
  const detections = detect.reduce((sum, row) => sum + Number(row.n), 0);
  if (detections >= 20) {
    // Weighted by count across outcomes; the exact pct across groups is close enough
    // for a rule of 2.5 s typical and 4 s slow.
    const p50 = detect.reduce((sum, row) => sum + Number(row.n) * Number(row.p50), 0) / detections;
    const p90 = Math.max(...detect.map((row) => Number(row.p90)));
    p50 > 2500 || p90 > 4000
      ? fail("detection is fast", `p50 ${p50.toFixed(0)} ms, p90 ${p90.toFixed(0)} ms over ${detections} detections; rule is p50 under 2.5 s and p90 under 4 s`)
      : ok("detection is fast", `p50 ${p50.toFixed(0)} ms, p90 ${p90.toFixed(0)} ms over ${detections} detections`);
  } else {
    ok("detection is fast", `${detections} detections, too few to judge`);
  }

  // Reported, never failed: in openai_with_shadow_screen this is the evidence for (or
  // against) letting the fast screen answer drive frames. See ml/classifier/MODEL_CARD.md
  // for the numbers that justify the flip to yolo_then_openai.
  ok("shadow screen (report only)", reportShadowScreen(await insights(SHADOW_SCREEN_QUERY, hours)).detail);

  // Our own share of a request. The detector is the model's time; everything the
  // service itself does around it (ten database calls on a detection) has a budget, so
  // a slow query or a serial wait added later shows up here and not as "the app is slow".
  const own = await insights(
    'filter event="http_request" and ispresent(db_ms) and status=200 and route in ["/v1/vision/detect","/v1/potholes/report","/v1/tenders/resolve"] | stats count() as n, pct(db_ms, 90) as db90, pct(duration_ms - detector_ms - geo_ms, 90) as own90 by route',
    hours,
  );
  for (const row of own) {
    if (Number(row.n) < 20) continue;
    const own90 = Number(row.own90);
    own90 > 400
      ? fail(`service overhead is small (${row.route})`, `p90 ${own90.toFixed(0)} ms outside the detector and the geolocator over ${row.n} requests; budget 400 ms`)
      : ok(`service overhead is small (${row.route})`, `p90 ${own90.toFixed(0)} ms, database p90 ${Number(row.db90).toFixed(0)} ms over ${row.n} requests`);
  }

  const lambdaErrors = await insights('filter @message like /Task timed out|Runtime exited|Error: Runtime/ | stats count() as n', hours);
  const crashes = Number(lambdaErrors[0]?.n || 0);
  crashes ? fail("no runtime crashes", `${crashes} timeouts or runtime exits`) : ok("no runtime crashes", "0");
}

// ---------------------------------------------------------------- live canary
const sha = (v) => createHash("sha256").update(v).digest("hex");

async function timed(label, promise, limitMs) {
  const started = Date.now();
  const result = await promise;
  const took = Date.now() - started;
  if (took > limitMs) fail(`${label} within ${limitMs} ms`, `${took} ms`);
  return { ...result, took };
}

async function publicGet(route) {
  const response = await fetch(API_URL + route, { signal: AbortSignal.timeout(20_000) });
  const text = await response.text();
  let body = null;
  try { body = JSON.parse(text); } catch { body = null; }
  return { status: response.status, body };
}

async function canary() {
  console.log(`\nCanary against ${API_URL}`);
  const health = await timed("health", publicGet("/v1/health"), 5000);
  health.status === 200 && health.body?.ok === true && health.body?.shared_vision_primary_configured === true
    ? ok("health", `200, ${health.body.shared_vision_provider}`)
    : fail("health", `${health.status} ${JSON.stringify(health.body).slice(0, 200)}`);
  for (const route of ["/v1/map", "/v1/impact"]) {
    const result = await timed(route, publicGet(route), 10_000);
    result.status === 200 ? ok(route, `200 in ${result.took} ms`) : fail(route, `${result.status}`);
  }
  // The map is the largest thing the app downloads from the service. It went over the
  // air uncompressed (52.8 KB) until 6 Oct 2026; the wire size is asked for raw here,
  // because fetch would quietly decompress and hide a regression.
  const wire = await fetch(`${API_URL}/v1/map`, { headers: { "accept-encoding": "gzip" },
    signal: AbortSignal.timeout(20_000) });
  const encoding = wire.headers.get("content-encoding");
  const plainBytes = Buffer.byteLength(await wire.text());
  const sentBytes = Number(wire.headers.get("content-length")) || null;
  plainBytes < 1024 || encoding === "gzip"
    ? ok("map is compressed", `${plainBytes} bytes of JSON${sentBytes ? ` sent as ${sentBytes}` : ""}, ${encoding || "small enough to send plain"}`)
    : fail("map is compressed", `${plainBytes} bytes sent with content-encoding ${encoding}`);

  const { publicKey, privateKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const registration = await fetch(`${API_URL}/v1/installations`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ public_key: publicKey.export({ type: "spki", format: "der" }).toString("base64") }),
    signal: AbortSignal.timeout(20_000),
  });
  const install = await registration.json();
  if (registration.status !== 201 || !install.install_id) {
    fail("install registers", `${registration.status} ${JSON.stringify(install).slice(0, 200)}`);
    return;
  }
  ok("install registers", install.install_id.slice(0, 8));

  async function signedPost(route, payload, timeoutMs = 30_000) {
    const body = JSON.stringify(payload);
    const timestamp = String(Date.now());
    const idempotencyKey = randomUUID();
    const canonical = ["POST", route, timestamp, idempotencyKey, sha(body)].join("\n");
    const signature = sign("sha256", Buffer.from(canonical), privateKey).toString("base64");
    const started = Date.now();
    const response = await fetch(API_URL + route, {
      method: "POST", body, signal: AbortSignal.timeout(timeoutMs),
      headers: { "content-type": "application/json", "x-install-id": install.install_id, "x-timestamp": timestamp,
        "x-signature": signature, "idempotency-key": idempotencyKey },
    });
    const text = await response.text();
    let parsed = null;
    try { parsed = JSON.parse(text); } catch { parsed = { raw: text.slice(0, 200) }; }
    return { status: response.status, body: parsed, took: Date.now() - started };
  }

  const image = readFileSync(path.join(root, "docs/example-pothole.jpg"));
  const detect = await signedPost("/v1/vision/detect", {
    prompt_version: "road-damage-v5", capture_mode: "manual",
    images: [`data:image/jpeg;base64,${image.toString("base64")}`],
  });
  if (detect.status === 200 && detect.body.assessment === "damaged") {
    ok("shared detection finds the example pothole", `${detect.body.damage_type} ${detect.body.size} via ${detect.body.detector?.backend_provider} in ${detect.took} ms`);
    if (detect.took > 6000) fail("detection within 6000 ms", `${detect.took} ms`);
  } else {
    fail("shared detection finds the example pothole", `${detect.status} ${JSON.stringify(detect.body).slice(0, 300)}`);
  }

  const withHint = await signedPost("/v1/tenders/resolve", { ...CANARY_POINT, address_hint: CANARY_POINT.hint });
  const jurisdiction = withHint.body?.jurisdiction;
  if (withHint.status === 200 && jurisdiction?.road_ownership === "municipal" && jurisdiction?.lgd) {
    ok("Bengaluru street is classified municipal", `LGD ${jurisdiction.lgd} ${jurisdiction.town} via ${jurisdiction.lookup?.kgis === "available" ? "state GIS" : "local fallback"}; tender ${withHint.body.tender ? withHint.body.tender.tender_number : `none (${withHint.body.reason})`} in ${withHint.took} ms`);
    if (withHint.body.reason === "address_unresolved") fail("hinted address is used for matching", "address_unresolved with a hint present");
    if (withHint.body.reason === "no_tenders_for_jurisdiction") fail("tender table has rows for Bengaluru", "no_tenders_for_jurisdiction; seed the table");
    // The canary point is in KGIS ward 10, Cox Town, whose tenders the index names by the
    // old BBMP ward 108. A service from before the ward release answers no lookup.ward at
    // all and is not judged here.
    if (jurisdiction.lookup?.ward !== undefined) {
      jurisdiction.lookup.ward === "resolved" && jurisdiction.ward_name === "Cox Town"
        ? ok("ward is named from the packaged snapshot", `${jurisdiction.ward_name}, KGIS ward ${jurisdiction.ward_no}`)
        : fail("ward is named from the packaged snapshot", `lookup.ward ${jurisdiction.lookup.ward}, ward_name ${jurisdiction.ward_name}`);
      Array.isArray(withHint.body.ward_tenders) && withHint.body.ward_tenders.length
        ? ok("the ward's tenders are answered", `${withHint.body.ward_tenders.length}, first: ${withHint.body.ward_tenders[0].title.slice(0, 80)}`)
        : fail("the ward's tenders are answered", `ward_tenders ${JSON.stringify(withHint.body.ward_tenders)?.slice(0, 120)}`);
    }
  } else {
    fail("Bengaluru street is classified municipal", `${withHint.status} ${JSON.stringify(withHint.body).slice(0, 300)}`);
  }

  const withoutHint = await signedPost("/v1/tenders/resolve", { lat: CANARY_POINT.lat + 0.0006, lng: CANARY_POINT.lng + 0.0006 });
  const source = withoutHint.body?.jurisdiction?.address_source;
  withoutHint.status === 200 && source === "operator_geocoder"
    ? ok("server finds the street itself", `${withoutHint.body.jurisdiction.address}`)
    : fail("server finds the street itself", `${withoutHint.status} address_source ${source}; reason ${withoutHint.body?.reason || withoutHint.body?.error}`);
}

// ---------------------------------------------------------------- main
const windowArg = value("window", null);
if (!windowArg && !flag("canary")) {
  console.error("usage: production-health.mjs [--window 24h] [--canary]");
  process.exit(2);
}
try {
  if (windowArg) await windowRules(parseWindow(windowArg));
  if (flag("canary")) await canary();
} catch (error) {
  fail("health check ran", error.message);
}
console.log(`\n${failures.length ? `UNHEALTHY: ${failures.length} rule(s) broken` : "HEALTHY"}${notes.length ? ` (${notes.join("; ")})` : ""}`);
process.exit(failures.length ? 1 : 0);
