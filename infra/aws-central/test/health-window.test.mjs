import assert from "node:assert/strict";
import test from "node:test";

import { createInsights } from "../service/health/insights.mjs";
import { createReport } from "../service/health/report.mjs";
import { CRASH_QUERY, KNOWN_ANSWER_QUERY, OWN_TIME_QUERY, REQUEST_QUERY, judgeWindow, parseWindow } from "../service/health/window.mjs";
import { BROKEN_WINDOW, HEALTHY_WINDOW, QUIET_WINDOW, scriptedQuery } from "./health-support.mjs";

// The log window rules as a library: scripted Logs Insights rows in, one verdict per
// rule out. The command-line script and the scheduled function both call judgeWindow,
// so what is pinned here is what each of them judges. The lines a person reads are
// pinned in health-cli.test.mjs.

async function judge(script, hours = 6) {
  const query = scriptedQuery(script);
  const report = createReport();
  await judgeWindow({ query, hours, logGroup: "/aws/lambda/pothole-reporter-central", report });
  return { ...report.conclude(), asked: query.asked };
}

// HEALTHY_WINDOW with one query's rows replaced.
const withRows = (match, rows) => HEALTHY_WINDOW.map((entry) => (entry.match === match ? { ...entry, rows } : entry));
// HEALTHY_WINDOW with request groups added to, or taken from, the first query.
const requestRows = (change) => withRows("by route, outcome, status", change(HEALTHY_WINDOW[0].rows));
const group = (route, outcome, status, n, p50 = 5, p90 = 9) => ({ route, outcome, status: String(status), n: String(n), p50: String(p50), p90: String(p90) });

test("a healthy window judges every rule ok, as one part", async () => {
  const result = await judge(HEALTHY_WINDOW);
  assert.equal(result.healthy, true);
  assert.deepEqual(result.failures, []);
  assert.deepEqual(result.rules.map((rule) => rule.name), [
    "no internal errors",
    "reports never rejected on their receipt",
    "cap never reached (daily_vision_limit)",
    "cap never reached (shared_rate_limit)",
    "cap never reached (shared_daily_budget_reached)",
    "cap never reached (shared_budget_reached)",
    "road ownership answered",
    "street address resolved",
    "reports land",
    "tenders match",
    "tenders match somewhere in India",
    "road ownership layers are in the package",
    "ward snapshot is in the package",
    "wards find their tenders",
    "ward snapshots outside Karnataka are in the package",
    "detection is fast",
    "shadow screen (report only)",
    "shadow screen threshold for 98% live recall (report only)",
    "service overhead is small (/v1/vision/detect)",
    "service overhead is small (/v1/tenders/resolve)",
    "known answers are instant (/v1/tenders/resolve)",
    "known answers are instant (/v1/map)",
    "no runtime crashes",
  ]);
  assert.ok(result.rules.every((rule) => rule.part === "window" && rule.state === "ok"));
  assert.deepEqual(result.notes, ["771 requests in 6 h"]);
  assert.equal(result.lines[0], "\nLog window: last 6 h of /aws/lambda/pothole-reporter-central");
  assert.equal(result.lines.at(-1), "\nHEALTHY (771 requests in 6 h)");
});

test("every rule that can break is reported broken, with the number that broke it", async () => {
  const result = await judge(BROKEN_WINDOW, 24);
  assert.equal(result.healthy, false);
  assert.deepEqual(result.failures.map((rule) => [rule.name, rule.detail.split(/[;.] /)[0]]), [
    ["no internal errors", "4 requests crashed"],
    ["reports never rejected on their receipt", "2 rejected"],
    ["cap never reached (daily_vision_limit)", "7 detections refused"],
    ["cap never reached (shared_rate_limit)", "1 detections refused"],
    ["cap never reached (shared_daily_budget_reached)", "1 detections refused"],
    ["cap never reached (shared_budget_reached)", "1 detections refused"],
    ["road ownership answered", "26 of 122 lookups (21.3%) could not classify the road"],
    ["street address resolved", "24 of 110 lookups (21.8%) had no usable street"],
    ["reports land", "7 of 12 reports (58.3%) were told to retry on the location lock"],
    ["tenders match", "60 lookups reached matching and none matched"],
    ["tenders match somewhere in India", "60 lookups had a street and none matched any catalogue"],
    ["road ownership layers are in the package", "26 lookups could not read the road ownership layers"],
    ["ward snapshot is in the package", "5 municipal lookups could not read the ward snapshot"],
    ["wards find their tenders", "5 of 55 lookups with a ward (9.1%) answered a ward tender or a street tender"],
    ["ward snapshots outside Karnataka are in the package", "6 lookups outside Karnataka could not read a ward snapshot the package should hold: GJ/ahmedabad (4), data/wards/runtime.json (2)"],
    ["detection is fast", "p50 2600 ms, p90 4200 ms over 30 detections"],
    ["service overhead is small (/v1/tenders/resolve)", "p90 401 ms outside the detector and the geolocator over 110 requests"],
    ["known answers are instant (/v1/map)", "p50 60 ms, p90 140 ms over 40 requests"],
    ["no runtime crashes", "2 timeouts or runtime exits"],
  ]);
  assert.equal(result.lines.at(-1), "\nUNHEALTHY: 19 rule(s) broken (166 requests in 24 h)");
});

// One change to a healthy window breaks one rule and no other.
const ONE_AT_A_TIME = [
  ["no internal errors", requestRows((rows) => [...rows, group("/v1/impact", "internal_error", 500, 1)])],
  ["reports never rejected on their receipt", requestRows((rows) => [...rows, group("/v1/potholes/report", "invalid_detection_receipt", 400, 1)])],
  ["cap never reached (daily_vision_limit)", requestRows((rows) => [...rows, group("/v1/vision/detect", "daily_vision_limit", 503, 1)])],
  ["reports land", requestRows((rows) => [...rows, group("/v1/potholes/report", "location_dedupe_in_progress", 409, 3)])],
  ["road ownership layers are in the package", withRows("by local_lookup", [{ local_lookup: "unavailable", n: "1" }])],
  ["ward snapshot is in the package", withRows("by ward_lookup, ward_tender_count, tender_catalogue",
    [{ ward_lookup: "unavailable", n: "1" }, { ward_lookup: "resolved", ward_tender_count: "5", n: "40" }])],
  ["wards find their tenders", withRows("by ward_lookup, ward_tender_count, tender_catalogue",
    [{ ward_lookup: "resolved", ward_tender_count: "0", n: "30" }])],
  ["ward snapshots outside Karnataka are in the package", withRows("by ward_lookup, ward_snapshot",
    [{ ward_lookup: "unavailable", ward_snapshot: "MP/bhopal", n: "1" }])],
  ["service overhead is small (/v1/potholes/report)", withRows("pct(db_ms, 90)",
    [{ route: "/v1/potholes/report", n: "20", db90: "30", own90: "401" }])],
  ["known answers are instant (/v1/tenders/resolve)", withRows('answer_cache="hit"',
    [{ route: "/v1/tenders/resolve", n: "20", p50: "16", p90: "20" }])],
  ["no runtime crashes", withRows("Task timed out", [{ n: "1" }])],
];
for (const [rule, script] of ONE_AT_A_TIME) {
  test(`one fault breaks one rule: ${rule}`, async () => {
    const result = await judge(script);
    assert.deepEqual(result.failures.map((failure) => failure.name), [rule]);
  });
}

test("the percentage rules break past 5% or past 20 lookups, and not at them", async () => {
  // 203 lookups: 183 resolves and 20 reports. 10 unclassified is 4.9%.
  const share = (n) => judge(requestRows((rows) => [...rows.filter((row) => row.outcome !== "no_location_match"),
    group("/v1/tenders/resolve", "no_location_match", 200, 140 - n), group("/v1/tenders/resolve", "road_ownership_unavailable", 200, n)]));
  assert.deepEqual((await share(10)).failures, []);
  assert.deepEqual((await share(11)).failures.map((failure) => failure.name), ["road ownership answered"]);
  const unresolved = (n) => judge(requestRows((rows) => [...rows.filter((row) => row.outcome !== "no_location_match"),
    group("/v1/tenders/resolve", "no_location_match", 200, 140 - n), group("/v1/tenders/resolve", "address_unresolved", 200, n)]));
  assert.deepEqual((await unresolved(9)).failures, [], "9 of 183 is 4.9%");
  assert.deepEqual((await unresolved(10)).failures.map((failure) => failure.name), ["street address resolved"]);
});

// The scheduled canary's three lookups are in this log too, with nothing to tell them
// from a person's, and none of them matches a tender (its Bengaluru point answers
// no_location_match). So these two thresholds are also the ceiling on how many canary
// lookups a window may hold: test/template-health.test.mjs holds the schedule under it.
test("twenty lookups that reach matching with none matched break the rule, nineteen do not", async () => {
  const unmatched = (n) => judge(requestRows(() => [group("/v1/health", "healthy", 200, 50), group("/v1/tenders/resolve", "no_location_match", 200, n)]));
  assert.deepEqual((await unmatched(19)).failures, []);
  assert.deepEqual((await unmatched(20)).failures.map((failure) => failure.name), ["tenders match"]);
  assert.deepEqual((await unmatched(50)).failures.map((failure) => failure.name), ["tenders match", "tenders match somewhere in India"]);
  const oneMatched = await judge(requestRows(() => [group("/v1/tenders/resolve", "no_location_match", 200, 80),
    group("/v1/tenders/resolve", "tender_matched", 200, 1)]));
  assert.deepEqual(oneMatched.failures, []);
});

test("too few detections, or too few requests on a route, are said and not judged", async () => {
  const result = await judge(withRows("pct(db_ms, 90)", [{ route: "/v1/vision/detect", n: "19", db90: "900", own90: "900" }])
    .map((entry) => (entry.match === "by route, outcome, status"
      ? { ...entry, rows: entry.rows.filter((row) => row.route !== "/v1/vision/detect").concat(group("/v1/vision/detect", "undamaged", 200, 19, 9000, 9000)) } : entry)));
  assert.equal(result.healthy, true);
  assert.equal(result.rules.find((rule) => rule.name === "detection is fast").detail, "19 detections, too few to judge");
  assert.ok(!result.rules.some((rule) => rule.name.startsWith("service overhead")));
});

test("a window with no requests asks one query and judges nothing", async () => {
  const result = await judge(QUIET_WINDOW);
  assert.equal(result.healthy, true);
  assert.deepEqual(result.rules.map((rule) => [rule.name, rule.detail]), [["traffic", "no requests in the window; nothing to judge"]]);
  assert.deepEqual(result.asked.map((asked) => asked.text), [REQUEST_QUERY]);
  assert.equal(result.lines.at(-1), "\nHEALTHY (0 requests in 6 h)");
});

// One after another the nine queries took 18 s for a one hour window on 7 Oct 2026. The
// scheduled function is billed for the wait, so after the first (which decides whether
// there is anything to judge) the other eight run together.
test("after the request counts, the other eight queries are in flight together", async () => {
  let inFlight = 0;
  let most = 0;
  const scripted = scriptedQuery(HEALTHY_WINDOW);
  const query = async (text, hours) => {
    inFlight += 1;
    most = Math.max(most, inFlight);
    await new Promise((resolve) => setTimeout(resolve, 5));
    inFlight -= 1;
    return scripted(text, hours);
  };
  const report = createReport();
  await judgeWindow({ query, hours: 6, logGroup: "g", report });
  assert.equal(scripted.asked.length, 9);
  assert.equal(scripted.asked[0].text, REQUEST_QUERY);
  assert.equal(most, 8);
  for (const text of [OWN_TIME_QUERY, KNOWN_ANSWER_QUERY, CRASH_QUERY]) assert.ok(scripted.asked.some((asked) => asked.text === text));
  assert.ok(scripted.asked.every((asked) => asked.hours === 6));
  assert.equal(report.conclude().healthy, true);
});

test("a query that fails stops the window at its rule, after the rules before it", async () => {
  const unhandled = [];
  const note = (reason) => unhandled.push(reason);
  process.on("unhandledRejection", note);
  // Two fail: the one read first is reported, the other must not surface on its own.
  const script = HEALTHY_WINDOW.map((entry) => (["by ward_lookup, ward_snapshot", "Task timed out"].includes(entry.match)
    ? { ...entry, error: "Logs Insights Timeout" } : entry));
  const report = createReport();
  await assert.rejects(judgeWindow({ query: scriptedQuery(script), hours: 6, logGroup: "g", report }), /Logs Insights Timeout/);
  await new Promise((resolve) => setTimeout(resolve, 10));
  process.off("unhandledRejection", note);
  assert.deepEqual(unhandled, []);
  const result = report.conclude();
  assert.equal(result.rules.at(-1).name, "wards find their tenders", "every rule before the failed query was judged");
  assert.equal(result.healthy, true, "the caller says the window crashed; the rules judged so far are not failures");
});

test("--window takes hours or days", () => {
  assert.equal(parseWindow("6h"), 6);
  assert.equal(parseWindow("7d"), 168);
  assert.throws(() => parseWindow("soon"), /--window takes hours or days, like 24h or 7d, not soon/);
  assert.throws(() => parseWindow(undefined), /not undefined/);
});

test("a part that could not run is one broken rule that says so", () => {
  const report = createReport();
  report.begin("window", "Log window: last 6 h of g");
  report.ok("no internal errors", "0");
  report.crashed(new Error("Logs Insights Failed"));
  const result = report.conclude();
  assert.equal(result.healthy, false);
  assert.deepEqual(result.failures, [{ part: "window", name: "health check ran", state: "fail", detail: "Logs Insights Failed", crashed: true }]);
  assert.deepEqual(result.lines.slice(-2), ["  FAIL health check ran: Logs Insights Failed", "\nUNHEALTHY: 1 rule(s) broken"]);
});

// ------------------------------------------------------------------ one query, run
function logsApi(answers) {
  const calls = [];
  let polls = 0;
  return {
    calls,
    startQuery: async (input) => { calls.push(["startQuery", input]); return { queryId: "q-1" }; },
    getQueryResults: async (input) => {
      calls.push(["getQueryResults", input]);
      const answer = answers[Math.min(polls, answers.length - 1)];
      polls += 1;
      return answer;
    },
    stopQuery: async (input) => { calls.push(["stopQuery", input]); },
  };
}
const complete = { status: "Complete", statistics: { bytesScanned: 2353135 },
  results: [[{ field: "route", value: "/v1/map" }, { field: "n", value: "12" }], [{ field: "n", value: "3" }]] };

test("a query is asked over the last hours of the log group and its rows come back as objects", async () => {
  const logs = logsApi([{ status: "Running" }, { status: "Running" }, complete]);
  const slept = [];
  const scanned = [];
  const insights = createInsights({ logs, logGroupName: "/aws/lambda/central", pollMs: 500, patienceMs: 45_000,
    now: () => 1_760_000_000_999, sleep: async (ms) => { slept.push(ms); }, onScanned: (bytes) => scanned.push(bytes) });
  const rows = await insights("stats count() as n by route", 6);
  assert.deepEqual(rows, [{ route: "/v1/map", n: "12" }, { n: "3" }]);
  assert.deepEqual(logs.calls[0], ["startQuery", { logGroupName: "/aws/lambda/central", startTime: 1_760_000_000 - 6 * 3600,
    endTime: 1_760_000_000, queryString: "stats count() as n by route" }]);
  assert.deepEqual(slept, [500, 500]);
  assert.deepEqual(scanned, [2353135]);
  assert.ok(!logs.calls.some(([name]) => name === "stopQuery"));
});

for (const status of ["Failed", "Cancelled", "Timeout"]) {
  test(`a query Logs Insights ends as ${status} is an error`, async () => {
    const insights = createInsights({ logs: logsApi([{ status: "Running" }, { status }]), logGroupName: "g", pollMs: 1, patienceMs: 1000 });
    await assert.rejects(insights("q", 1), new RegExp(`^Error: Logs Insights ${status}$`));
  });
}

test("a query that never completes is given up on, and stopped so it scans no more", async () => {
  let clock = 0;
  const logs = logsApi([{ status: "Running" }]);
  const insights = createInsights({ logs, logGroupName: "g", pollMs: 500, patienceMs: 45_000,
    now: () => clock, sleep: async (ms) => { clock += ms; } });
  await assert.rejects(insights("q", 6), /^Error: Logs Insights query did not complete$/);
  assert.equal(clock, 45_000);
  assert.deepEqual(logs.calls.at(-1), ["stopQuery", { queryId: "q-1" }]);
  // A caller that may not stop queries still gets the timeout, not the refusal.
  clock = 0;
  const refused = { ...logsApi([{ status: "Running" }]), stopQuery: async () => { throw new Error("AccessDenied"); } };
  await assert.rejects(createInsights({ logs: refused, logGroupName: "g", pollMs: 500, patienceMs: 1000,
    now: () => clock, sleep: async (ms) => { clock += ms; } })("q", 6), /did not complete/);
});
