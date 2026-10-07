import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

import { installationPublicKey } from "../service/auth.mjs";
import { createStoredIdentity } from "../service/health/canary-key.mjs";
import { readExampleImage } from "../service/health/example-image.mjs";
import { BROKEN_RULES_METRIC, CANARY_FAILED_METRIC, createHealthFunction, metricLine } from "../service/health/function.mjs";
import { BROKEN_WINDOW, HEALTHY_WINDOW, fakeApi, fetchFrom } from "./health-support.mjs";

// The scheduled health function end to end, with a stand-in for each thing it reaches:
// Logs Insights, the parameter store that keeps the canary's key, and the public API.
// What must hold: an unhealthy production is returned and written, never thrown, so the
// alarms on the two metrics carry the news; one key is one install for ever; the
// private key appears in nothing the function writes.

const KEY_NAME = "/pothole-reporter-central/health/canary-key";
const INSTALL_NAME = "/pothole-reporter-central/health/canary-install-id";

// Logs Insights answering from a window script, 1,000 bytes scanned a query.
function logsFrom(script, { status = "Complete" } = {}) {
  const calls = [];
  return {
    calls,
    startQuery: async (input) => {
      calls.push(["startQuery", input]);
      return { queryId: String(script.findIndex((entry) => input.queryString.includes(entry.match))) };
    },
    getQueryResults: async ({ queryId }) => {
      const entry = script[Number(queryId)];
      if (!entry) throw new Error("no scripted answer");
      return { status: entry.status || status, statistics: { bytesScanned: 1000 },
        results: entry.rows.map((row) => Object.entries(row).map(([field, value]) => ({ field, value }))) };
    },
    stopQuery: async (input) => { calls.push(["stopQuery", input]); },
  };
}

// The parameter store, with the errors the SDK raises by name.
function parameterStore(initial = {}) {
  const values = new Map(Object.entries(initial));
  const modified = new Map();
  const calls = [];
  const named = (name) => Object.assign(new Error(name), { name });
  return {
    values,
    modified,
    calls,
    getParameter: async (input) => {
      calls.push(["getParameter", input]);
      if (!values.has(input.Name)) throw named("ParameterNotFound");
      return { Parameter: { Name: input.Name, Value: values.get(input.Name), LastModifiedDate: modified.get(input.Name) } };
    },
    putParameter: async (input) => {
      calls.push(["putParameter", input]);
      if (values.has(input.Name) && !input.Overwrite) throw named("ParameterAlreadyExists");
      values.set(input.Name, input.Value);
      modified.delete(input.Name);
      return { Version: 1 };
    },
  };
}

function setup({ window = HEALTHY_WINDOW, api = fakeApi(), parameters = parameterStore(), logs = logsFrom(window), ...rest } = {}) {
  const written = { log: [], emit: [] };
  const clock = { t: 1_760_000_000_000 };
  const health = createHealthFunction({
    apiUrl: "https://api.test", logGroup: "/aws/lambda/pothole-reporter-central", namespace: "pothole-reporter-central",
    keyParameter: KEY_NAME, installParameter: INSTALL_NAME, logs, parameters, fetch: fetchFrom(api), readImage: readExampleImage,
    log: (text) => written.log.push(text), emit: (line) => written.emit.push(line),
    now: () => (clock.t += 7), sleep: async () => {}, ...rest,
  });
  const line = () => JSON.parse(written.emit.at(-1));
  return { health, written, api, parameters, logs, line };
}

test("a healthy window and full canary return healthy and write the report and one line with no metric", async () => {
  const { health, written, line, logs } = setup();
  const result = await health({ window: "6h", canary: "full" });
  assert.deepEqual({ ...result, report: null }, { healthy: true, broken_rules: 0, canary_failed: 0, could_not_run: [], report: null });
  assert.equal(written.log.length, 1, "the whole report is one log entry");
  assert.equal(written.log[0], result.report);
  assert.match(result.report, /^\nLog window: last 6 h of \/aws\/lambda\/pothole-reporter-central\n {2}ok {3}no internal errors: 0\n/);
  assert.match(result.report, /\nCanary against https:\/\/api\.test\n {2}ok {3}health: 200, openai_with_shadow_screen\n/);
  assert.match(result.report, /\n\nHEALTHY \(771 requests in 6 h\)$/);
  assert.equal(written.emit.length, 1);
  assert.ok(!written.emit[0].includes("\n"), "one line");
  const summary = line();
  assert.equal(summary._aws, undefined, "a healthy run writes no metric: each written hour of a custom metric is charged");
  assert.ok(!(BROKEN_RULES_METRIC in summary) && !(CANARY_FAILED_METRIC in summary));
  assert.deepEqual({ ...summary, duration_ms: null, canary_install_id: null }, {
    event: "health_run", healthy: true, window: "6h", canary: "full", broken_rules: 0, canary_failed: 0, failures: [],
    scanned_bytes: 6000, duration_ms: null, canary_install_id: null,
  });
  assert.ok(summary.duration_ms > 0);
  assert.match(summary.canary_install_id, /^[a-f0-9]{32}$/);
  assert.equal(logs.calls.filter(([name]) => name === "startQuery").length, 6);
  assert.ok(logs.calls.every(([, input]) => !input.logGroupName || input.logGroupName === "/aws/lambda/pothole-reporter-central"));
});

test("broken log rules are returned, not thrown, and written as the HealthBrokenRules metric", async () => {
  const { health, line } = setup({ window: BROKEN_WINDOW });
  const result = await health({ window: "6h", canary: "reads" });
  assert.equal(result.healthy, false);
  assert.equal(result.broken_rules, 19);
  assert.equal(result.canary_failed, 0);
  assert.deepEqual(result.could_not_run, []);
  const summary = line();
  assert.deepEqual(summary._aws.CloudWatchMetrics, [{ Namespace: "pothole-reporter-central", Dimensions: [[]],
    Metrics: [{ Name: "HealthBrokenRules", Unit: "Count" }] }]);
  assert.equal(summary._aws.Timestamp, 1_760_000_000_007);
  assert.equal(summary.HealthBrokenRules, 19);
  assert.ok(!("HealthCanaryFailed" in summary), "the canary passed: its metric is not written");
  assert.equal(summary.failures.length, 19);
  assert.equal(summary.failures[0], "no internal errors: 4 requests crashed");
  assert.match(result.report, /\n\nUNHEALTHY: 19 rule\(s\) broken \(166 requests in 6 h\)$/);
});

test("a failed canary is returned, not thrown, and written as the HealthCanaryFailed metric", async () => {
  const { health, line } = setup({ api: fakeApi({ "GET /v1/map": { status: 500, body: { error: "internal_error" } },
    "POST /v1/vision/detect": { assessment: "undamaged" } }) });
  const result = await health({ canary: "full" });
  assert.deepEqual({ ...result, report: null }, { healthy: false, broken_rules: null, canary_failed: 1, could_not_run: [], report: null });
  const summary = line();
  assert.deepEqual(summary._aws.CloudWatchMetrics[0].Metrics, [{ Name: "HealthCanaryFailed", Unit: "Count" }]);
  assert.equal(summary.HealthCanaryFailed, 1, "one failed canary, however many of its checks failed");
  assert.equal(summary.broken_rules, null);
  assert.equal(summary.failures.length, 2);
});

test("a canary that cannot register is a failed canary", async () => {
  const { health, line } = setup({ api: fakeApi({ "POST /v1/installations": { status: 503, body: { message: "Service Unavailable" } } }) });
  const result = await health({ canary: "full" });
  assert.equal(result.canary_failed, 1);
  assert.deepEqual(line().failures, ['install registers: 503 {"message":"Service Unavailable"}']);
  assert.equal(line().canary_install_id, null);
});

test("an API that does not answer is a failed canary that could not run, and nothing is thrown", async () => {
  const { health, line } = setup({ fetch: async () => { throw new TypeError("fetch failed"); } });
  const result = await health({ window: "6h", canary: "reads" });
  assert.equal(result.healthy, false);
  assert.equal(result.broken_rules, 0, "the log rules were still judged");
  assert.equal(result.canary_failed, 1);
  assert.deepEqual(result.could_not_run, ["canary: fetch failed"]);
  assert.equal(line().HealthCanaryFailed, 1);
});

test("a query that times out is one broken rule, the query is stopped, and the canary still runs", async () => {
  const script = HEALTHY_WINDOW.map((entry) => (entry.match === "Task timed out" ? { ...entry, status: "Running" } : entry));
  const logs = logsFrom(script);
  const clock = { t: 0 };
  const { health, line, api } = setup({ logs, now: () => clock.t, sleep: async (ms) => { clock.t += ms; } });
  const result = await health({ window: "6h", canary: "reads" });
  assert.equal(result.broken_rules, 1);
  assert.deepEqual(result.could_not_run, ["window: Logs Insights query did not complete"]);
  assert.equal(result.canary_failed, 0);
  assert.equal(api.calls.length, 4, "the canary ran after the window gave up");
  assert.equal(line().HealthBrokenRules, 1);
  assert.equal(logs.calls.filter(([name]) => name === "stopQuery").length, 1);
  assert.ok(clock.t >= 45_000 && clock.t < 60_000, `gave up after ${clock.t} ms`);
  assert.match(result.report, / {2}ok {3}known answers are instant \(\/v1\/map\)[^\n]*\n {2}FAIL health check ran: Logs Insights query did not complete\n/);
});

test("a log group that may not be queried is one broken rule that says why", async () => {
  const denied = { ...logsFrom(HEALTHY_WINDOW), startQuery: async () => { throw new Error("User is not authorized to perform: logs:StartQuery"); } };
  const { health } = setup({ logs: denied });
  const result = await health({ window: "6h" });
  assert.deepEqual([result.healthy, result.broken_rules, result.canary_failed], [false, 1, null]);
  assert.deepEqual(result.could_not_run, ["window: User is not authorized to perform: logs:StartQuery"]);
});

test("a window that is not hours or days is a broken rule, not a crash", async () => {
  const { health } = setup();
  const result = await health({ window: "soon" });
  assert.deepEqual(result.could_not_run, ["window: --window takes hours or days, like 24h or 7d, not soon"]);
  assert.equal(result.broken_rules, 1);
});

test("the event decides what runs: a window touches no API, a reads canary no log and no key", async () => {
  const windowOnly = setup();
  const judged = await windowOnly.health({ window: "6h" });
  assert.deepEqual([judged.broken_rules, judged.canary_failed], [0, null]);
  assert.deepEqual(windowOnly.api.calls, []);
  assert.deepEqual(windowOnly.parameters.calls, []);
  assert.equal(windowOnly.line().canary, null);

  const reads = setup();
  const probed = await reads.health({ canary: "reads" });
  assert.deepEqual([probed.healthy, probed.broken_rules, probed.canary_failed], [true, null, 0]);
  assert.deepEqual(reads.logs.calls, []);
  assert.deepEqual(reads.parameters.calls, [], "reads need no install, so the key store is not asked");
  assert.equal(reads.api.calls.length, 4);
  assert.equal(reads.line().scanned_bytes, 0);
});

// A fault in the function itself is not an unhealthy production. It must not be turned
// into a tidy "unhealthy" (or worse, "healthy") result: it leaves as an error, the run
// writes no line, and the alarm on Invocations and Errors is what reports it.
test("an event that asks for nothing, or for an unknown canary, is an error and writes nothing", async () => {
  const { health, written } = setup();
  await assert.rejects(health({}), /nothing to run/);
  await assert.rejects(health(undefined), /nothing to run/);
  await assert.rejects(health({ warm: true }), /nothing to run/);
  await assert.rejects(health({ canary: "deep" }), /canary is "reads" or "full", not "deep"/);
  assert.deepEqual(written, { log: [], emit: [] });
});

test("a fault while writing the result leaves as an error", async () => {
  const { health } = setup({ emit: () => { throw new Error("EPIPE"); } });
  await assert.rejects(health({ canary: "reads" }), /EPIPE/);
});

// ------------------------------------------------------------------ the canary's key
test("the first run makes the key and stores it once; every later run is the same install", async () => {
  const parameters = parameterStore();
  const api = fakeApi();
  const first = setup({ parameters, api });
  await first.health({ canary: "full" });
  const puts = parameters.calls.filter(([name, input]) => name === "putParameter" && input.Name === KEY_NAME);
  assert.equal(puts.length, 1);
  assert.deepEqual({ ...puts[0][1], Value: null, Description: null }, { Name: KEY_NAME, Type: "SecureString", Overwrite: false, Value: null, Description: null });
  assert.match(puts[0][1].Value, /^-----BEGIN PRIVATE KEY-----\n/);
  assert.deepEqual(parameters.calls[0], ["getParameter", { Name: KEY_NAME, WithDecryption: true }]);
  const installId = first.line().canary_install_id;

  // The same function instance: the key is in memory, the store is not asked again.
  const asked = parameters.calls.length;
  await first.health({ canary: "full" });
  assert.equal(parameters.calls.length, asked);
  assert.equal(first.line().canary_install_id, installId);

  // A new function instance, days later: it reads the stored key and is the same install.
  const later = setup({ parameters, api });
  await later.health({ canary: "full" });
  assert.equal(later.line().canary_install_id, installId);
  assert.equal(parameters.calls.filter(([name, input]) => name === "putParameter" && input.Name === KEY_NAME).length, 1, "never made twice");
  assert.equal(api.installs.size, 1, "three runs, one install");
});

test("a stored key is reused and never replaced", async () => {
  const kept = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const parameters = parameterStore({ [KEY_NAME]: kept.privateKey.export({ type: "pkcs8", format: "pem" }) });
  const { health, line } = setup({ parameters });
  const result = await health({ canary: "full" });
  assert.equal(result.healthy, true);
  assert.equal(line().canary_install_id, installationPublicKey(kept.publicKey.export({ type: "spki", format: "der" }).toString("base64")).installId);
  assert.deepEqual(parameters.calls.filter(([, input]) => input.Name === KEY_NAME).map(([name]) => name), ["getParameter"]);
});

test("two first runs at once keep the key that was stored first", async () => {
  const parameters = parameterStore();
  const winner = generateKeyPairSync("ec", { namedCurve: "prime256v1" }).privateKey.export({ type: "pkcs8", format: "pem" });
  const put = parameters.putParameter;
  // Another run stores its key between this one's read and its write.
  parameters.putParameter = async (input) => {
    parameters.values.set(input.Name, winner);
    return put(input);
  };
  const identity = createStoredIdentity({ parameters, name: KEY_NAME });
  const { privateKey } = await identity();
  assert.equal(privateKey.export({ type: "pkcs8", format: "pem" }), winner);
  assert.equal(parameters.values.get(KEY_NAME), winner);
});

test("a key store that cannot be read is a failed canary, and no key is made in its place", async () => {
  const parameters = parameterStore();
  parameters.getParameter = async () => { throw Object.assign(new Error("not authorized to perform: ssm:GetParameter"), { name: "AccessDeniedException" }); };
  const { health, api } = setup({ parameters });
  const result = await health({ canary: "full" });
  assert.equal(result.canary_failed, 1);
  assert.deepEqual(result.could_not_run, ["canary: not authorized to perform: ssm:GetParameter"]);
  assert.deepEqual(parameters.calls, [], "nothing was written");
  assert.ok(!api.calls.some((call) => call.name === "POST /v1/installations"));
});

test("the private key is in nothing the function logs, emits or returns", async () => {
  const kept = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const pem = kept.privateKey.export({ type: "pkcs8", format: "pem" });
  const secret = kept.privateKey.export({ format: "jwk" }).d;
  for (const answers of [{}, { "POST /v1/vision/detect": { status: 500, body: { error: "internal_error" } } }]) {
    const { health, written } = setup({ parameters: parameterStore({ [KEY_NAME]: pem }), api: fakeApi(answers) });
    const result = await health({ window: "6h", canary: "full" });
    const everything = JSON.stringify([written, result]);
    for (const piece of [secret, ...pem.split("\n").filter((line) => line && !line.startsWith("-----"))]) {
      assert.ok(!everything.includes(piece), "private key material was written");
    }
    assert.ok(!/PRIVATE KEY/.test(everything));
  }
  // Nor when the stored value is not a key at all.
  const garbled = setup({ parameters: parameterStore({ [KEY_NAME]: `-----BEGIN PRIVATE KEY-----\n${secret}\n-----END PRIVATE KEY-----\n` }) });
  const result = await garbled.health({ canary: "full" });
  assert.equal(result.canary_failed, 1);
  assert.ok(!JSON.stringify([garbled.written, result]).includes(secret));
});

// ------------------------------------------------------------------ the metric line
test("a metric line carries only the metrics above zero, with no dimension", () => {
  const both = JSON.parse(metricLine({ namespace: "ns", timestamp: 5, metrics: { A: 2, B: 1 }, fields: { event: "health_run" } }));
  assert.deepEqual(both, { _aws: { Timestamp: 5, CloudWatchMetrics: [{ Namespace: "ns", Dimensions: [[]],
    Metrics: [{ Name: "A", Unit: "Count" }, { Name: "B", Unit: "Count" }] }] }, event: "health_run", A: 2, B: 1 });
  assert.deepEqual(JSON.parse(metricLine({ namespace: "ns", timestamp: 5, metrics: { A: 0, B: 0 }, fields: { event: "health_run" } })),
    { event: "health_run" });
});

// ------------------------------------------------------------------ the entry point
// handler.mjs cannot be imported here: it imports the two AWS SDK clients the Lambda
// runtime provides, which are deliberately not in package.json. It is kept to wiring,
// and what it wires is checked as text.
test("the Lambda entry point only wires the runtime's SDK to the tested function", () => {
  const entry = readFileSync(new URL("../service/health/handler.mjs", import.meta.url), "utf8");
  assert.match(entry, /export const handler = createHealthFunction\(\{/);
  for (const call of ["startQuery: (input) => logs.send(new StartQueryCommand(input))", "getQueryResults: (input) => logs.send(new GetQueryResultsCommand(input))",
    "stopQuery: (input) => logs.send(new StopQueryCommand(input))", "getParameter: (input) => ssm.send(new GetParameterCommand(input))",
    "putParameter: (input) => ssm.send(new PutParameterCommand(input))", "process.stdout.write(`${line}\\n`)"]) {
    assert.ok(entry.includes(call), call);
  }
  const manifest = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  const packaged = Object.keys({ ...manifest.dependencies, ...manifest.devDependencies });
  const runtimeOnly = [...entry.matchAll(/from "(@aws-sdk\/[^"]+)"/g)].map((match) => match[1]);
  // One other module names the SSM client: canary-install.mjs loads it on first use, for
  // the central function, inside a read that may fail without failing anything.
  const lazily = { "canary-install.mjs": /sdk = \(\) => import\("@aws-sdk\/client-ssm"\)/ };
  assert.deepEqual(runtimeOnly, ["@aws-sdk/client-cloudwatch-logs", "@aws-sdk/client-ssm"]);
  for (const name of runtimeOnly) assert.ok(!packaged.includes(name), `${name} comes from the Lambda runtime and is not packaged`);
  // No other module of the service may need them: the central function's package has no such client.
  const healthDir = new URL("../service/health/", import.meta.url);
  for (const dir of [healthDir, new URL("../service/", import.meta.url)]) {
    for (const file of readdirSync(dir).filter((name) => name.endsWith(".mjs") && !(dir === healthDir && name === "handler.mjs"))) {
      const source = readFileSync(new URL(file, dir), "utf8").replace(lazily[file] || /$^/, "");
      for (const name of runtimeOnly) assert.ok(!source.includes(`"${name}"`), `${file} imports ${name}`);
    }
  }
});

// ------------------------------------------------------------------ out of the public figures
// The central service leaves one install out of the public figures: the one whose id it
// reads from a parameter. The health function is what writes that parameter.
const marking = (api) => {
  const fetch = fetchFrom(api);
  // The service marks the answers to an install it knows as the canary's.
  return async (url, init) => {
    const response = await fetch(url, init);
    const headers = new Headers(response.headers);
    headers.set("x-canary", "true");
    return new Response(await response.text(), { status: response.status, headers });
  };
};

test("a full canary publishes its install id once, as a plain parameter, and does not rewrite it", async () => {
  const parameters = parameterStore();
  const { health, line } = setup({ parameters });
  await health({ canary: "full" });
  const id = line().canary_install_id;
  assert.equal(parameters.values.get(INSTALL_NAME), id);
  const puts = () => parameters.calls.filter(([name, input]) => name === "putParameter" && input.Name === INSTALL_NAME);
  assert.deepEqual(puts().map(([, input]) => input), [{ Name: INSTALL_NAME, Type: "String", Overwrite: true, Value: id,
    Description: puts()[0][1].Description }]);
  await health({ canary: "full" });
  // A new function instance finds it already there.
  const later = setup({ parameters });
  await later.health({ canary: "full" });
  assert.equal(puts().length, 1);
  // A reads canary has no install and publishes nothing.
  const reads = setup();
  await reads.health({ canary: "reads" });
  assert.deepEqual(reads.parameters.calls, []);
});

test("a published id that is not this install's is replaced", async () => {
  const parameters = parameterStore({ [INSTALL_NAME]: "0".repeat(32) });
  const { health, line } = setup({ parameters });
  await health({ canary: "full" });
  assert.equal(parameters.values.get(INSTALL_NAME), line().canary_install_id);
});

test("the canary names itself on its reads, so the service can leave those out too", async () => {
  const { health, api, line } = setup();
  await health({ canary: "full" });
  const reads = api.calls.filter((call) => call.name.startsWith("GET "));
  assert.equal(reads.length, 4);
  assert.ok(reads.every((call) => call.headers["x-install-id"] === line().canary_install_id));
});

test("the run says whether the service is leaving the canary out, and fails when it should be and is not", async () => {
  const rule = (result) => result.report.split("\n").find((text) => text.includes("canary is left out of the public figures"));
  // Just published: the service reads the parameter within ten minutes. Said, not failed.
  const early = await setup().health({ canary: "full" });
  assert.equal(early.healthy, true);
  assert.match(rule(early), /^ {2}skip canary is left out of the public figures: its install id was published /);
  // Published an hour ago and still not marked: its requests are being counted.
  const parameters = parameterStore();
  const first = setup({ parameters });
  await first.health({ canary: "full" });
  parameters.modified.set(INSTALL_NAME, new Date(1_760_000_000_000 - 3_600_000));
  const stale = setup({ parameters });
  const late = await stale.health({ canary: "full" });
  assert.equal(late.canary_failed, 1);
  assert.match(rule(late), /^ {2}FAIL canary is left out of the public figures: the service did not mark/);
  // The same hour-old id, and the service marks the canary: ok.
  const api = fakeApi();
  const recognised = setup({ parameters, api, fetch: marking(api) });
  const fine = await recognised.health({ canary: "full" });
  assert.equal(fine.healthy, true);
  assert.match(rule(fine), /^ {2}ok {3}canary is left out of the public figures: /);
});

test("an id that cannot be published is a failed canary", async () => {
  const parameters = parameterStore();
  const put = parameters.putParameter;
  parameters.putParameter = async (input) => {
    if (input.Name === INSTALL_NAME) throw Object.assign(new Error("not authorized to perform: ssm:PutParameter"), { name: "AccessDeniedException" });
    return put(input);
  };
  const { health } = setup({ parameters });
  const result = await health({ canary: "full" });
  assert.equal(result.canary_failed, 1);
  assert.deepEqual(result.could_not_run, ["canary: not authorized to perform: ssm:PutParameter"]);
});
