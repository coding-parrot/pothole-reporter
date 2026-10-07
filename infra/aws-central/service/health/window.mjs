// The log window rules: the last hours of the central function's request log, judged.
//
// Why this exists. Between 19 Sept and 6 Oct 2026 the service answered 227 tender lookups
// with "state GIS unavailable", 197 with "no address", rejected 125 reports on their own
// receipt, turned 74 detections away at the daily cap and never matched a tender for a
// real user. Every number was in the logs and nobody read them. These rules read them
// and say which one is broken. The thresholds are rules, not observations: change them
// only with a reason in the commit.
//
// `query(text, hours)` runs one Logs Insights query over the last `hours` and returns its
// rows. Nothing here knows whether it is the aws CLI or the SDK that answers.

import {
  LOOKUP_QUERY, SCREEN_QUERY, judgeIndiaWardSnapshots, judgeLiveScreen, judgeRoadLayers, judgeWardSnapshot, judgeWardTenders,
  liveRows, municipalLookups, outsideStateLookups, reportShadowReadiness, reportShadowScreen, shadowRows, shadowScreenCurve,
} from "./rules.mjs";

export const REQUEST_QUERY = 'filter event="http_request" and not ispresent(canary) | stats count() as n, pct(duration_ms, 50) as p50, pct(duration_ms, 90) as p90 by route, outcome, status';

// Our own share of a request. The detector is the model's time; everything the service
// itself does around it (ten database calls on a detection) has a budget, so a slow
// query or a serial wait added later shows up here and not as "the app is slow".
export const OWN_TIME_QUERY = 'filter event="http_request" and not ispresent(canary) and ispresent(db_ms) and status=200 and route in ["/v1/vision/detect","/v1/potholes/report","/v1/tenders/resolve"] | stats count() as n, pct(db_ms, 90) as db90, pct(duration_ms - detector_ms - geo_ms, 90) as own90 by route';

// A place the service has already answered for is one map read and one metrics write:
// 6 ms on 7 Oct 2026, down from 90 to 145. The public map is the same. Anything that
// puts a table read or a recomputation back on that path shows up here.
export const KNOWN_ANSWER_QUERY = 'filter event="http_request" and not ispresent(canary) and status=200 and (route="/v1/map" or (route="/v1/tenders/resolve" and answer_cache="hit")) | stats count() as n, pct(duration_ms, 50) as p50, pct(duration_ms, 90) as p90 by route';

export const CRASH_QUERY = "filter @message like /Task timed out|Runtime exited|Error: Runtime/ | stats count() as n";

export function parseWindow(text) {
  const match = /^(\d+)(h|d)$/.exec(String(text || ""));
  if (!match) throw new Error(`--window takes hours or days, like 24h or 7d, not ${text}`);
  return Number(match[1]) * (match[2] === "d" ? 24 : 1);
}

export async function judgeWindow({ query, hours, logGroup, report }) {
  const { ok, fail } = report;
  report.begin("window", `Log window: last ${hours} h of ${logGroup}`);
  const rows = await query(REQUEST_QUERY, hours);
  const count = (route, outcome) => rows.filter((row) => (!route || row.route === route) && (!outcome || row.outcome === outcome))
    .reduce((sum, row) => sum + Number(row.n), 0);
  const total = count();
  report.note(`${total} requests in ${hours} h`);
  if (!total) {
    ok("traffic", "no requests in the window; nothing to judge");
    return;
  }
  // The other five are independent of each other, so they are all asked now and read
  // below in the order their rules print. One after another the queries took 18 s for a
  // one hour window on 7 Oct 2026, nearly all of it waiting, and the scheduled function
  // pays for every second it waits. A query that fails is reported where its rule is.
  const ask = (text) => {
    const answer = Promise.resolve().then(() => query(text, hours));
    answer.catch(() => {});
    return answer;
  };
  const asked = {
    lookups: ask(LOOKUP_QUERY),
    screen: ask(SCREEN_QUERY),
    ownTime: ask(OWN_TIME_QUERY),
    knownAnswers: ask(KNOWN_ANSWER_QUERY),
    crashes: ask(CRASH_QUERY),
  };

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

  const lookupRows = await asked.lookups;
  const roadLayers = judgeRoadLayers(lookupRows);
  roadLayers.broken ? fail("road ownership layers are in the package", roadLayers.detail) : ok("road ownership layers are in the package", roadLayers.detail);

  // Most Bengaluru tenders name a ward or a locality, never a street, so since the ward
  // tender release a municipal lookup with a resolved ward should often come back with
  // something a person can read.
  const wardRows = municipalLookups(lookupRows);
  const wardSnapshot = judgeWardSnapshot(wardRows);
  wardSnapshot.broken ? fail("ward snapshot is in the package", wardSnapshot.detail) : ok("ward snapshot is in the package", wardSnapshot.detail);
  const wardTenders = judgeWardTenders(wardRows);
  wardTenders.broken ? fail("wards find their tenders", wardTenders.detail) : ok("wards find their tenders", wardTenders.detail);

  // Outside Karnataka a ward comes from the snapshots the runtime list switches on. A
  // package without one of them still answers, with no ward, and only the log says why.
  const indiaWards = judgeIndiaWardSnapshots(outsideStateLookups(lookupRows));
  indiaWards.broken ? fail("ward snapshots outside Karnataka are in the package", indiaWards.detail)
    : ok("ward snapshots outside Karnataka are in the package", indiaWards.detail);

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
  const screenRows = await asked.screen;
  const shadow = shadowRows(screenRows);
  ok("shadow screen (report only)", reportShadowScreen(shadow).detail);
  ok("shadow screen threshold for 98% live recall (report only)", shadowScreenCurve(shadow).detail);
  ok("shadow screen ready to switch on (report only)", reportShadowReadiness(shadow).detail);

  // Judged, and failed on: once the mode is yolo_then_openai the screen is answering
  // users, and a frame it clears reaches gpt-5-mini only through the audit. A window
  // with no such line (any other mode) has nothing to judge and says so in one line.
  const live = liveRows(screenRows);
  if (!live.length) ok("live screen", "no drive frames were screened live in the window; nothing to judge");
  for (const [name, verdict] of live.length ? judgeLiveScreen(live) : []) {
    verdict.broken ? fail(name, verdict.detail) : ok(name, verdict.detail);
  }

  for (const row of await asked.ownTime) {
    if (Number(row.n) < 20) continue;
    const own90 = Number(row.own90);
    own90 > 400
      ? fail(`service overhead is small (${row.route})`, `p90 ${own90.toFixed(0)} ms outside the detector and the geolocator over ${row.n} requests; budget 400 ms`)
      : ok(`service overhead is small (${row.route})`, `p90 ${own90.toFixed(0)} ms, database p90 ${Number(row.db90).toFixed(0)} ms over ${row.n} requests`);
  }

  for (const row of await asked.knownAnswers) {
    if (Number(row.n) < 20) continue;
    const p50 = Number(row.p50);
    p50 > 15
      ? fail(`known answers are instant (${row.route})`, `p50 ${p50.toFixed(0)} ms, p90 ${Number(row.p90).toFixed(0)} ms over ${row.n} requests; budget p50 15 ms`)
      : ok(`known answers are instant (${row.route})`, `p50 ${p50.toFixed(0)} ms, p90 ${Number(row.p90).toFixed(0)} ms over ${row.n} requests`);
  }

  const crashes = Number((await asked.crashes)[0]?.n || 0);
  crashes ? fail("no runtime crashes", `${crashes} timeouts or runtime exits`) : ok("no runtime crashes", "0");
}
