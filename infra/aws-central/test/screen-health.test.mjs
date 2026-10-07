import assert from "node:assert/strict";
import test from "node:test";

import { createDetector } from "../service/detectors.mjs";
import { createReport } from "../service/health/report.mjs";
import {
  SCREEN_QUERY, estimateLiveRecall, judgeLiveScreen, liveRows, reportShadowReadiness, reportShadowScreen,
  shadowRows, wilson,
} from "../service/health/rules.mjs";
import { judgeWindow } from "../service/health/window.mjs";
import { HEALTHY_WINDOW, scriptedQuery } from "./health-support.mjs";
import { detectBody, harness, secretFrom, undamaged, upstream } from "./support.mjs";

// The health rules for the fast screen once it answers users (yolo_then_openai), and the
// report that says whether shadow mode has shown enough to switch it on. In the live
// mode gpt-5-mini never sees most of the frames the screen clears, so the screen's
// recall can only be estimated: from the flagged frames gpt-5-mini confirmed and from
// the audited share of the cleared ones. Rows here are what Logs Insights answers
// SCREEN_QUERY with: strings, absent fields left out, true as "1".

process.env.SHARED_SECRET_ARN = "arn:aws:secretsmanager:test";

const row = (n, fields) => Object.fromEntries(Object.entries({ n, ...fields }).map(([key, value]) => [key, String(value)]));
// Live lines (they carry the audit rate), by what happened to the frame.
const caught = (n, rate = 0.1) => row(n, { outcome: "damaged", screen_assessment: "damaged", detector_provider: "openai", screen_audit_rate: rate, slow: 0 });
const overruled = (n, rate = 0.1) => row(n, { outcome: "undamaged", screen_assessment: "damaged", detector_provider: "openai", screen_audit_rate: rate, slow: 0 });
const cleared = (n, rate = 0.1) => row(n, { outcome: "undamaged", screen_assessment: "undamaged", detector_provider: "yolo", screen_audit_rate: rate, slow: 0 });
const auditedClear = (n, rate = 0.1) => row(n, { outcome: "undamaged", screen_assessment: "undamaged", detector_provider: "openai", screen_audit_rate: rate, screen_audited: 1, slow: 0 });
const auditedMiss = (n, rate = 0.1) => row(n, { outcome: "damaged", screen_assessment: "undamaged", detector_provider: "openai", screen_audit_rate: rate, screen_audited: 1, slow: 0 });
const auditLost = (n, rate = 0.1) => row(n, { outcome: "undamaged", screen_assessment: "undamaged", detector_provider: "yolo", screen_audit_rate: rate, screen_audited: 1, slow: 0 });
const failed = (n, rate = 0.1) => row(n, { outcome: "undamaged", screen_error: "shared_vision_unavailable", detector_provider: "openai", screen_audit_rate: rate, slow: 0 });
const slowFrames = (n, rate = 0.1) => ({ ...cleared(n, rate), slow: "1" });
// Shadow lines: no audit rate, gpt-5-mini answered every one.
const shadow = (n, outcome, screen, extra = {}) => row(n, { outcome, ...(screen ? { screen_assessment: screen } : {}), detector_provider: "openai", slow: 0, ...extra });

// 490 potholes caught, 2000 frames cleared of which 200 were audited.
const healthy = (missed = 1) => [caught(490), overruled(200), cleared(1800), auditedClear(200 - missed), ...(missed ? [auditedMiss(missed)] : [])];
const verdicts = (rows) => Object.fromEntries(judgeLiveScreen(rows));
const NAMES = ["live screen recall", "live screen answers", "live screen is fast", "live audit is running"];

test("the Wilson score interval matches the textbook values", () => {
  const ten = wilson(10, 100);
  assert.equal(ten.low.toFixed(4), "0.0552");
  assert.equal(ten.high.toFixed(4), "0.1744");
  // None seen in 100: the upper bound is z squared over (n plus z squared).
  const none = wilson(0, 100);
  assert.equal(none.low, 0);
  assert.equal(none.high.toFixed(4), (1.96 ** 2 / (100 + 1.96 ** 2)).toFixed(4));
  assert.deepEqual(wilson(0, 0), { low: 0, high: 1 });
});

test("the estimator scales audited misses up to all cleared frames", () => {
  const estimate = estimateLiveRecall(healthy(1));
  assert.deepEqual([estimate.caught, estimate.cleared, estimate.audited, estimate.missed], [490, 2000, 200, 1]);
  // One miss in 200 audited frames stands for 10 in the 2000 cleared.
  assert.equal(estimate.missedEstimate, 10);
  assert.equal(estimate.damagedEstimate, 500);
  assert.equal(estimate.recall, 0.98);
  // Wilson on 1 of 200 is 0.088% to 2.78%; over the 1800 unaudited frames, plus the one seen.
  const { low, high } = wilson(1, 200);
  assert.equal(estimate.recallLow, 490 / (490 + (1 + high * 1800)));
  assert.equal(estimate.recallHigh, 490 / (490 + (1 + low * 1800)));
  assert.equal(estimate.recallLow.toFixed(3), "0.906");
  assert.equal(estimate.recallHigh.toFixed(3), "0.995");
});

test("the realised share audited is what scales, so lost audits and another rate need no correction", () => {
  // 20 of the 200 drawn audits never reached gpt-5-mini: 180 judged, 1 miss.
  const lossy = estimateLiveRecall([caught(490), cleared(1800), auditedClear(179), auditedMiss(1), auditLost(20)]);
  assert.deepEqual([lossy.cleared, lossy.drawn, lossy.audited, lossy.missed], [2000, 200, 180, 1]);
  assert.equal(lossy.missedEstimate, 2000 / 180);
  // A quarter audited: one miss stands for four.
  const quarter = estimateLiveRecall([caught(96), cleared(300, 0.25), auditedClear(99, 0.25), auditedMiss(1, 0.25)]);
  assert.equal(quarter.missedEstimate, 4);
  assert.equal(quarter.recall, 0.96);
});

test("with every cleared frame audited the estimate is a count and the interval closes on it", () => {
  const all = estimateLiveRecall([caught(98, 1), auditedClear(98, 1), auditedMiss(2, 1)]);
  assert.equal(all.missedEstimate, 2);
  assert.equal(all.recall, 0.98);
  assert.equal(all.recallLow, 0.98);
  assert.equal(all.recallHigh, 0.98);
});

test("a healthy live window: every live rule holds", () => {
  const judged = judgeLiveScreen(healthy(1));
  assert.deepEqual(judged.map(([name]) => name), NAMES);
  assert.ok(judged.every(([, verdict]) => verdict.broken === false), JSON.stringify(judged));
  const recall = verdicts(healthy(1))["live screen recall"];
  assert.match(recall.detail, /estimated recall 98\.0% \(95% interval 90\.6% to 99\.5%\)/);
  assert.match(recall.detail, /490 flagged frames gpt-5-mini confirmed damaged/);
  assert.match(recall.detail, /1 of 200 audited frames was a pothole the screen had cleared/);
  assert.match(recall.detail, /about 10 missed among 2000 cleared/);
});

test("estimated recall under 98% with 50 or more damaged frames breaks the rule", () => {
  const low = verdicts(healthy(2))["live screen recall"];
  // 2 misses in 200 stand for 20: 490 of 510.
  assert.equal(low.broken, true);
  assert.match(low.detail, /estimated recall 96\.1%/);
  assert.match(low.detail, /the rule is 98%/);
  assert.match(low.detail, /openai_with_shadow_screen/, "the failure says how to go back");
  // The other three are not about recall.
  const others = judgeLiveScreen(healthy(2)).filter(([name]) => name !== "live screen recall");
  assert.ok(others.every(([, verdict]) => !verdict.broken));
  // No miss seen: 100%, and it holds.
  assert.equal(verdicts(healthy(0))["live screen recall"].broken, false);
});

test("under 50 estimated damaged frames is too few to judge, whatever the share", () => {
  // 39 caught and 1 miss in 20 audited of 200 cleared (10 estimated): 49.
  const few = verdicts([caught(39), cleared(180), auditedClear(19), auditedMiss(1)])["live screen recall"];
  assert.equal(few.broken, false);
  assert.match(few.detail, /about 49 damaged frames .*too few to judge; the rule needs 50/);
  // One more caught makes 50, and 40 of 50 is 80%.
  const enough = verdicts([caught(40), cleared(180), auditedClear(19), auditedMiss(1)])["live screen recall"];
  assert.equal(enough.broken, true);
  assert.match(enough.detail, /estimated recall 80\.0%/);
});

test("no audited frame: recall is not estimated, and is not called healthy either", () => {
  const blind = verdicts([caught(300), cleared(100)]);
  assert.equal(blind["live screen recall"].broken, false);
  assert.match(blind["live screen recall"].detail, /cannot be estimated/);
  assert.doesNotMatch(blind["live screen recall"].detail, /%/);
});

test("a dead audit breaks its rule: cleared frames that should have produced over 20 audits and produced none", () => {
  // 201 cleared frames at 0.1 should have sent about 20.1 to gpt-5-mini.
  const dead = verdicts([caught(300), cleared(201)])["live audit is running"];
  assert.equal(dead.broken, true);
  assert.match(dead.detail, /201 cleared frames at an audit rate of 0\.1 should have sent about 20 to gpt-5-mini and none was judged/);
  // Exactly 20 expected is not "more than".
  assert.equal(verdicts([caught(300), cleared(200)])["live audit is running"].broken, false);
  // The same number of frames at a lower rate expects 2, and proves nothing.
  assert.equal(verdicts([caught(300), cleared(201, 0.01)])["live audit is running"].broken, false);
  // Audits drawn and every one lost to gpt-5-mini errors is a dead audit too.
  const lost = verdicts([caught(300), cleared(400), auditLost(40)])["live audit is running"];
  assert.equal(lost.broken, true);
  assert.match(lost.detail, /40 were drawn and lost/);
  // One judged audit and the rule holds.
  assert.equal(verdicts([caught(300), cleared(400), auditedClear(1)])["live audit is running"].broken, false);
});

test("an audit rate of zero is said, not failed: the operator switched the measuring off", () => {
  const off = verdicts([caught(300, 0), cleared(5000, 0)]);
  assert.equal(off["live audit is running"].broken, false);
  assert.match(off["live audit is running"].detail, /audit rate is 0/);
  assert.match(off["live screen recall"].detail, /cannot be estimated/);
});

test("a screen that fails on more than 1% of drive frames breaks its rule", () => {
  const over = verdicts([...healthy(1), failed(28)])["live screen answers"];
  // 28 of 2718.
  assert.equal(over.broken, true);
  assert.match(over.detail, /28 of 2718 drive frames \(1\.0%\) had no screen answer/);
  assert.equal(verdicts([...healthy(1), failed(27)])["live screen answers"].broken, false);
  // Exactly 1% is not more than 1%.
  assert.equal(verdicts([cleared(990), failed(10)])["live screen answers"].broken, false);
  assert.equal(verdicts([cleared(989), failed(11)])["live screen answers"].broken, true);
  // Under 100 frames one failure is already over 1%: said, not judged.
  const few = verdicts([cleared(90), failed(9)])["live screen answers"];
  assert.equal(few.broken, false);
  assert.match(few.detail, /too few to judge/);
});

test("a screen whose p90 is over 300 ms breaks its rule: more than one frame in ten took longer", () => {
  const slow = verdicts([cleared(899), slowFrames(101)])["live screen is fast"];
  assert.equal(slow.broken, true);
  assert.match(slow.detail, /101 of 1000 drive frames \(10\.1%\) took the screen over 300 ms/);
  assert.equal(verdicts([cleared(900), slowFrames(100)])["live screen is fast"].broken, false);
  assert.match(verdicts([cleared(50), slowFrames(40)])["live screen is fast"].detail, /too few to judge/);
});

test("shadow lines and live lines are told apart by the audit rate, and neither is judged as the other", () => {
  const mixed = [shadow(300, "damaged", "damaged"), shadow(700, "undamaged", "undamaged"), ...healthy(1)];
  assert.equal(liveRows(mixed).length, healthy(1).length);
  assert.equal(shadowRows(mixed).length, 2);
  // An unaudited cleared frame in live mode is not a frame gpt-5-mini judged undamaged.
  const report = reportShadowScreen(shadowRows(mixed));
  assert.deepEqual([report.damaged, report.undamaged, report.cleared], [300, 700, 700]);
  assert.equal(estimateLiveRecall(liveRows(mixed)).cleared, 2000);
  // A rate of 0 is still a live line.
  assert.equal(liveRows([cleared(5, 0)]).length, 1);
});

// ------------------------------------------------------------------ shadow readiness
const READY = [shadow(294, "damaged", "damaged"), shadow(6, "damaged", "undamaged"),
  shadow(300, "undamaged", "undamaged"), shadow(690, "undamaged", "damaged"), shadow(10, "undamaged", null, { screen_error: "screen_timeout", slow: 1 })];

test("ready to switch on: 300 damaged frames, 98% recall, 30% cleared, errors under 1%, p90 under 300 ms", () => {
  const ready = reportShadowReadiness(READY);
  assert.equal(ready.ready, true);
  assert.equal(ready.broken, false);
  assert.match(ready.detail, /^READY to switch on by the request log/);
  assert.match(ready.detail, /300 frames gpt-5-mini judged damaged \(300 needed\)/);
  assert.match(ready.detail, /the screen flagged 294 of 300 damaged frames, live recall 98\.0% \(98% needed\)/);
  assert.match(ready.detail, /cleared 300 of 990 frames gpt-5-mini judged undamaged, 30\.3% \(30% needed\)/);
  assert.match(ready.detail, /10 of 1300 frames had no screen answer, 0\.8% \(under 1% needed\)/);
  assert.match(ready.detail, /10 of 1300 frames took the screen over 300 ms, 0\.8% \(p90 is under 300 ms up to 10%\)/);
  assert.doesNotMatch(ready.detail, /Not met/);
});

test("readiness always says the distinct-phone condition cannot be read from the log", () => {
  for (const rows of [READY, [shadow(5, "damaged", "damaged")]]) {
    assert.match(reportShadowReadiness(rows).detail, /Not checked: that the damaged frames come from more than one phone \(the request log carries no install marker\)/);
  }
});

for (const [label, rows, missing] of [
  ["299 damaged frames", [shadow(294, "damaged", "damaged"), shadow(5, "damaged", "undamaged"), ...READY.slice(2)], /299 frames gpt-5-mini judged damaged \(300 needed\)/],
  ["recall of 97.7%", [shadow(293, "damaged", "damaged"), shadow(7, "damaged", "undamaged"), ...READY.slice(2)], /live recall 97\.7% \(98% needed\)/],
  ["29.9% cleared", [...READY.slice(0, 2), shadow(299, "undamaged", "undamaged"), shadow(701, "undamaged", "damaged")], /cleared 299 of 1000 frames gpt-5-mini judged undamaged, 29\.9% \(30% needed\)/],
  ["errors at exactly 1%", [...READY.slice(0, 3), shadow(687, "undamaged", "damaged"), shadow(13, "undamaged", null, { screen_error: "screen_timeout" })],
    /13 of 1300 frames had no screen answer, 1\.0% \(under 1% needed\)/],
  ["a slow screen", [...READY.slice(0, 3), shadow(690, "undamaged", "damaged", { slow: 1 })], /690 of 1290 frames took the screen over 300 ms, 53\.5%/],
]) {
  test(`not ready on ${label}, and the line says which condition and its count`, () => {
    const report = reportShadowReadiness(rows);
    assert.equal(report.ready, false);
    assert.equal(report.broken, false, "readiness is a report and never fails a run");
    assert.match(report.detail, /^NOT READY to switch on: 4 of 5 conditions met/);
    const notMet = report.detail.slice(report.detail.indexOf("Not met: "), report.detail.indexOf("Not checked: "));
    assert.match(notMet, missing);
    assert.equal(notMet.split("; ").length, 1, "only the one condition is listed as not met");
  });
}

test("readiness with no shadow traffic says so and never fails", () => {
  const none = reportShadowReadiness([]);
  assert.equal(none.ready, false);
  assert.equal(none.broken, false);
  assert.match(none.detail, /no drive frames were shadow screened in the window/);
  // Live lines are not shadow evidence.
  assert.match(reportShadowReadiness(shadowRows(healthy(1))).detail, /no drive frames were shadow screened/);
});

// ------------------------------------------------------------------ the health window
const withScreenRows = (rows) => HEALTHY_WINDOW.map((entry) => (entry.match === "by outcome, screen_assessment, screen_error, bucket" ? { ...entry, rows } : entry));
async function judge(rows) {
  const query = scriptedQuery(withScreenRows(rows));
  const report = createReport();
  await judgeWindow({ query, hours: 6, logGroup: "/aws/lambda/test", report });
  return { ...report.conclude(), asked: query.asked };
}

test("one query serves the shadow report, the curve, readiness and the live rules", async () => {
  const result = await judge(healthy(1));
  assert.equal(result.asked.filter((asked) => asked.text === SCREEN_QUERY).length, 1);
  assert.equal(result.asked.length, 6, "the live rules cost no further scan of the window");
  for (const field of ["screen_assessment", "screen_error", "screen_score", "screen_ms", "screen_audited", "screen_audit_rate", "detector_provider", "outcome"]) {
    assert.ok(SCREEN_QUERY.includes(field), field);
  }
  assert.match(SCREEN_QUERY, /not ispresent\(canary\)/);
});

test("a healthy live window is healthy and prints the four live rules after the shadow lines", async () => {
  const result = await judge(healthy(1));
  assert.equal(result.healthy, true, JSON.stringify(result.failures));
  const names = result.rules.map((rule) => rule.name);
  const at = names.indexOf("shadow screen ready to switch on (report only)");
  assert.ok(at > 0);
  assert.deepEqual(names.slice(at + 1, at + 5), NAMES);
  assert.match(result.rules[at].detail, /no drive frames were shadow screened/);
});

for (const [rule, rows] of [
  ["live screen recall", healthy(2)],
  ["live screen answers", [...healthy(1), failed(40)]],
  ["live screen is fast", [...healthy(1), { ...overruled(400), slow: "1" }]],
  ["live audit is running", [caught(490), cleared(2000)]],
]) {
  test(`one fault in a live window breaks one rule: ${rule}`, async () => {
    const result = await judge(rows);
    assert.deepEqual(result.failures.map((failure) => failure.name), [rule]);
    assert.equal(result.failures[0].part, "window", "so the scheduled function counts it in HealthBrokenRules");
  });
}

test("a window with no live frames says so in one line and fails nothing", async () => {
  const result = await judge([shadow(9, "damaged", "damaged"), shadow(5, "undamaged", "undamaged")]);
  assert.equal(result.healthy, true);
  const live = result.rules.filter((rule) => rule.name.startsWith("live "));
  assert.deepEqual(live.map((rule) => [rule.name, rule.state]), [["live screen", "ok"]]);
  assert.match(live[0].detail, /no drive frames were screened live in the window/);
});

test("shadow readiness is printed and cannot fail a run, however unready", async () => {
  const result = await judge([shadow(400, "damaged", "undamaged"), shadow(90, "undamaged", null, { screen_error: "screen_timeout", slow: 1 })]);
  assert.equal(result.healthy, true);
  const readiness = result.rules.find((rule) => rule.name === "shadow screen ready to switch on (report only)");
  assert.equal(readiness.state, "ok");
  assert.match(readiness.detail, /^NOT READY to switch on: 1 of 5 conditions met/);
});

// ------------------------------------------- the rows are what the service really logs
// The query's own group-by, applied to request lines the service wrote: the rules then
// have to count what was sent. A field renamed on one side fails here.
const keys = { openai_api_key: "sk-test-not-a-real-key", yolo_api_key: "screen-test-token" };
const driveBody = { ...detectBody, capture_mode: "drive", capture_source: "drive_live" };
const screenSays = (assessment, score, options = {}) => ({
  async send() {
    if (options.fail) return { FunctionError: "Unhandled", Payload: Buffer.from("{}") };
    const verdict = { image_quality: "acceptable", assessment, damage_type: assessment === "damaged" ? "pothole_cavity" : null, size: null, description: "x" };
    const body = JSON.stringify({ verdict, model: "road-screen-v2", score });
    return { Payload: Buffer.from(JSON.stringify({ statusCode: 200, headers: {}, body })) };
  },
});
const openaiSays = (verdict) => upstream(200, { output_text: JSON.stringify(verdict) });
const openaiDamaged = { image_quality: "acceptable", assessment: "damaged", damage_type: "pothole_cavity", size: "small", description: "Open cavity." };

async function requestLine({ mode = "yolo_then_openai", screen, openai, audit }) {
  const h = await harness({ detector: createDetector({ providerMode: mode, secretProvider: secretFrom(keys),
    yoloFunctionName: "pothole-reporter-central-screen", lambdaClient: screen, fetchImpl: openai, auditDraw: () => (audit ? 0 : 0.999) }) });
  await h.post("/v1/vision/detect", driveBody);
  return h.lines.log.map((entry) => JSON.parse(entry)).find((entry) => entry.event === "http_request" && entry.route === "/v1/vision/detect");
}

function rowsFrom(lines) {
  const by = SCREEN_QUERY.slice(SCREEN_QUERY.indexOf(" by ") + 4).split(", ");
  const groups = new Map();
  for (const line of lines) {
    const fields = { ...line, bucket: line.screen_score === null ? null : Math.floor(line.screen_score * 50), slow: line.screen_ms > 300 };
    // Logs Insights leaves a null field out of a row and prints true as 1.
    const kept = by.map((name) => [name, fields[name]]).filter(([, value]) => value !== null && value !== undefined)
      .map(([name, value]) => [name, typeof value === "boolean" ? String(Number(value)) : String(value)]);
    const key = JSON.stringify(kept);
    groups.set(key, { ...Object.fromEntries(kept), n: String(Number(groups.get(key)?.n || 0) + 1) });
  }
  return [...groups.values()];
}

test("the rules count the lines the service writes in yolo_then_openai", async () => {
  const lines = [
    await requestLine({ screen: screenSays("damaged", 0.9), openai: openaiSays(openaiDamaged) }),
    await requestLine({ screen: screenSays("damaged", 0.8), openai: openaiSays(openaiDamaged) }),
    await requestLine({ screen: screenSays("damaged", 0.3), openai: openaiSays(undamaged) }),
    await requestLine({ screen: screenSays("undamaged", 0.01), openai: openaiSays(undamaged) }),
    await requestLine({ screen: screenSays("undamaged", 0.01), openai: openaiSays(undamaged) }),
    await requestLine({ screen: screenSays("undamaged", 0.02), openai: openaiSays(undamaged), audit: true }),
    await requestLine({ screen: screenSays("undamaged", 0.04), openai: openaiSays(openaiDamaged), audit: true }),
    await requestLine({ screen: screenSays("undamaged", 0.02), openai: upstream(500, { error: { type: "server_error" } }), audit: true }),
    await requestLine({ screen: screenSays("undamaged", 0, { fail: true }), openai: openaiSays(undamaged) }),
  ];
  const rows = rowsFrom(lines);
  assert.equal(liveRows(rows).length, rows.length, "every line of this mode is a live line");
  const estimate = estimateLiveRecall(rows);
  assert.deepEqual([estimate.caught, estimate.cleared, estimate.drawn, estimate.audited, estimate.missed], [2, 5, 3, 2, 1]);
  assert.equal(estimate.missedEstimate, 2.5);
  const answers = verdicts(rows)["live screen answers"];
  assert.match(answers.detail, /^9 drive frames/);
});

test("the rules read a shadow line as shadow evidence", async () => {
  const lines = [
    await requestLine({ mode: "openai_with_shadow_screen", screen: screenSays("damaged", 0.9), openai: openaiSays(openaiDamaged) }),
    await requestLine({ mode: "openai_with_shadow_screen", screen: screenSays("undamaged", 0.01), openai: openaiSays(openaiDamaged) }),
    await requestLine({ mode: "openai_with_shadow_screen", screen: screenSays("undamaged", 0.01), openai: openaiSays(undamaged) }),
  ];
  const rows = rowsFrom(lines);
  assert.equal(shadowRows(rows).length, rows.length);
  const report = reportShadowScreen(rows);
  assert.deepEqual([report.damaged, report.flagged, report.undamaged, report.cleared], [2, 1, 1, 1]);
  assert.match(reportShadowReadiness(rows).detail, /2 frames gpt-5-mini judged damaged \(300 needed\)/);
});
