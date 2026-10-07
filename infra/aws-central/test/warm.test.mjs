import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createWarmHandler } from "../service/warm.mjs";

// The first lookup on a new function instance read the ownership layers, the ward
// polygons and a street tile inside the request: 580 to 664 ms in geo.resolve on
// 7 Oct 2026 against 11 to 100 ms on a warm instance. The reads now happen while the
// instance starts, and a scheduled warm event keeps one instance started.

test("a warm event runs the start-up reads once and never reaches the service", async () => {
  let warmed = 0;
  let served = 0;
  const handler = createWarmHandler({
    service: async () => { served += 1; return { statusCode: 200 }; },
    warm: async () => { warmed += 1; },
  });
  assert.deepEqual(await handler({ warm: true }), { warmed: true });
  assert.deepEqual(await handler({ warm: true }), { warmed: true });
  assert.equal(warmed, 1);
  assert.equal(served, 0);
});

test("a request waits for the start-up reads and is served as before", async () => {
  const order = [];
  const handler = createWarmHandler({
    service: async (event, context) => { order.push(`served ${event.rawPath} ${context.id}`); return { statusCode: 200 }; },
    warm: async () => { order.push("warmed"); },
  });
  assert.deepEqual(await handler({ rawPath: "/v1/health" }, { id: "c1" }), { statusCode: 200 });
  assert.deepEqual(order, ["warmed", "served /v1/health c1"]);
});

test("start-up reads that fail never fail a request, and say so once", async () => {
  const logged = [];
  const handler = createWarmHandler({
    service: async () => ({ statusCode: 200 }),
    warm: async () => { throw new Error("bundle missing"); },
    logger: { error: (line) => logged.push(line) },
  });
  assert.deepEqual(await handler({ rawPath: "/v1/map" }), { statusCode: 200 });
  assert.deepEqual(await handler({ warm: true }), { warmed: true });
  assert.equal(logged.length, 1);
  assert.match(logged[0], /warm_failed/);
});

test("the stack schedules the warm event and may invoke the function with it", () => {
  const template = readFileSync(new URL("../template.yaml", import.meta.url), "utf8");
  assert.match(template, /WarmSchedule:\s*\n\s*Type: AWS::Events::Rule/);
  assert.match(template, /ScheduleExpression: rate\(1 minute\)/);
  assert.match(template, /Input: '\{"warm": true\}'/);
  assert.match(template, /WarmInvokePermission:\s*\n\s*Type: AWS::Lambda::Permission/);
  const handler = readFileSync(new URL("../service/handler.mjs", import.meta.url), "utf8");
  assert.match(handler, /createWarmHandler\(/);
});

// The first request on a fresh instance also opened the connections to the table and to
// the secret store: on 7 Oct 2026, after nine deploys in six hours, that put the p90 of
// the service's own time on a lookup at 401 ms against a budget of 400. The warm-up
// reads once through the cached geolocator (a table read) and fetches the detector
// secret, each failing on its own without failing the other.
test("the warm-up opens the table and the secret store, not only the files", () => {
  const handler = readFileSync(new URL("../service/handler.mjs", import.meta.url), "utf8");
  const warm = handler.slice(handler.indexOf("createWarmHandler("));
  assert.match(warm, /Promise\.allSettled\(\[/);
  assert.match(warm, /liveGeolocator\.resolve\(/, "the files: a stored answer reads none");
  assert.match(warm, /[^e]geolocator\.resolve\(/, "the cached geolocator, whose first step is a table read");
  assert.match(warm, /secretProvider\(\)/);
});

test("the deploy passes the concurrency the template declares", () => {
  // CloudFormation keeps a stack's old parameter value unless it is passed again.
  const deploy = readFileSync(new URL("../deploy.sh", import.meta.url), "utf8");
  assert.match(deploy, /for name in [^;]*\bReservedConcurrency\b[^;]*; do/);
});

// Under real, sparse traffic the map's rows had always expired (they are fresh for 30 s
// and served stale for 2 minutes): on 7 Oct 2026 the map's typical time was 40 to 90 ms
// in hours with ten requests, against 8 ms when the rows were in memory. And the first
// Bengaluru lookup on an instance spent 350 to 550 ms building the town's tender index.
// The scheduled event now keeps both in memory, so a person never pays for either.
test("every warm event refreshes what the service keeps in memory", async () => {
  let ticks = 0;
  const handler = createWarmHandler({
    service: async () => ({ statusCode: 200 }),
    warm: async () => {},
    tick: async () => { ticks += 1; },
  });
  await handler({ warm: true });
  await handler({ warm: true });
  await handler({ rawPath: "/v1/map" });
  assert.equal(ticks, 2, "once per warm event, never on a request");
});

test("a refresh that fails never fails the warm event", async () => {
  const logged = [];
  const handler = createWarmHandler({
    service: async () => ({ statusCode: 200 }),
    warm: async () => {},
    tick: async () => { throw new Error("table throttled"); },
    logger: { error: (line) => logged.push(line) },
  });
  assert.deepEqual(await handler({ warm: true }), { warmed: true });
  assert.match(logged[0], /warm_tick_failed/);
});

test("keepWarm reads the map the app asks for, the impact period and a Bengaluru routing, and logs no request", async () => {
  const { harness, memoryRepository } = await import("./support.mjs");
  const repository = memoryRepository();
  const reads = { map: [], impact: 0, tenders: [] };
  Object.assign(repository, {
    async listPotholes(options) { reads.map.push(options.limit); return []; },
    async impact() { reads.impact += 1; return { activeInstallations: 0, requests: [], captures: [], outcomes: [] }; },
    async queryTenders(lgd) { reads.tenders.push(String(lgd)); return []; },
  });
  const geolocator = { async resolve({ lat, lng }) {
    return { lat, lng, road_ownership: "municipal", lgd: "305851", town: "GBA - Central", state_code: "KA",
      address: "MM Road, Cox Town, Bengaluru", address_source: "packaged_streets", lookup: { kgis: "snapshot" } };
  }, async wardRoster() { return []; } };
  const h = await harness({ repository, geolocator });
  assert.equal(typeof h.handle.keepWarm, "function");
  const requestLines = () => h.lines.log.filter((line) => line.includes("http_request")).length;
  const logged = requestLines();
  await h.handle.keepWarm({ lat: 12.99717, lng: 77.62094 });
  assert.deepEqual(reads.map.sort(), [1000, 2000], "the plain map and the app's limit=2000");
  assert.equal(reads.impact, 1);
  assert.deepEqual(reads.tenders, ["305851"]);
  assert.equal(requestLines(), logged, "the warm-up is not a request");
  // The app's own map request is then answered from memory.
  const before = reads.map.length;
  const map = await h.handle({ rawPath: "/v1/map", rawQueryString: "limit=2000",
    queryStringParameters: { limit: "2000" }, requestContext: { http: { method: "GET" } } });
  assert.equal(map.statusCode, 200);
  assert.equal(reads.map.length, before);
});
