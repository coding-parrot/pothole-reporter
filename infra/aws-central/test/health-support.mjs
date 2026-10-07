import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { gzipSync } from "node:zlib";

import { installationPublicKey, verifyInstallationSignature } from "../service/auth.mjs";

// What the health gate reads and calls, scripted: the rows Logs Insights answers each of
// its queries with, and an API that answers the canary the way production did on
// 7 Oct 2026. Not a test file itself: npm test only picks up *.test.mjs.
//
// The same script feeds three runners, so they are judged on identical input: the
// library directly, the command-line script as a process (through a stand-in `aws`
// executable and a local HTTP server) and the scheduled function.

const row = (fields) => Object.fromEntries(Object.entries(fields).map(([key, value]) => [key, String(value)]));
const requests = (...groups) => groups.map(([route, outcome, status, n, p50 = 5, p90 = 9]) => row({ route, outcome, status, n, p50, p90 }));

// One entry per query the gate asks. `match` is a piece of the query text that only
// that query contains.
export const HEALTHY_WINDOW = [
  { match: "by route, outcome, status", rows: requests(
    ["/v1/health", "healthy", 200, 400, 2, 5],
    ["/v1/map", "map_read", 200, 100, 9, 179],
    ["/v1/vision/detect", "undamaged", 200, 50, 1800, 2900],
    ["/v1/vision/detect", "damaged", 200, 18, 2200, 3100],
    ["/v1/tenders/resolve", "no_location_match", 200, 140],
    ["/v1/tenders/resolve", "tender_matched", 200, 19],
    ["/v1/tenders/resolve", "no_confident_match", 200, 17],
    ["/v1/tenders/resolve", "outside_state", 200, 3],
    ["/v1/tenders/resolve", "preflight", 204, 4],
    ["/v1/potholes/report", "created", 201, 12],
    ["/v1/potholes/report", "deduplicated", 200, 8],
  ) },
  { match: "by local_lookup", rows: [row({ local_lookup: "municipal_polygon", n: 190 }), row({ n: 13 })] },
  { match: "by ward_lookup, ward_tender_count, tender_catalogue", rows: [
    row({ ward_lookup: "resolved", ward_tender_count: 5, n: 150 }),
    row({ ward_lookup: "resolved", ward_tender_count: 0, tender_catalogue: "ka_index", n: 22 }),
    row({ ward_lookup: "resolved", ward_tender_count: 0, n: 3 }),
    row({ ward_lookup: "no_ward", ward_tender_count: 0, n: 9 }),
  ] },
  { match: "by ward_lookup, ward_snapshot", rows: [
    row({ ward_lookup: "resolved", ward_snapshot: "GJ/ahmedabad", n: 1 }),
    row({ ward_lookup: "out_of_scope", n: 6 }),
  ] },
  { match: "by outcome, screen_assessment, screen_error", rows: [
    row({ outcome: "damaged", screen_assessment: "damaged", n: 9 }),
    row({ outcome: "undamaged", screen_assessment: "undamaged", n: 5 }),
    row({ outcome: "undamaged", screen_assessment: "damaged", n: 11 }),
    row({ outcome: "undamaged", screen_error: "screen_timeout", n: 3 }),
  ] },
  { match: "by outcome, bucket", rows: [
    row({ outcome: "damaged", bucket: 40, n: 9 }),
    row({ outcome: "undamaged", bucket: 3, n: 16 }),
  ] },
  { match: "pct(db_ms, 90)", rows: [
    row({ route: "/v1/vision/detect", n: 68, db90: 149, own90: 158 }),
    row({ route: "/v1/tenders/resolve", n: 183, db90: 12, own90: 240 }),
    row({ route: "/v1/potholes/report", n: 12, db90: 30, own90: 900 }),
  ] },
  { match: 'answer_cache="hit"', rows: [
    row({ route: "/v1/tenders/resolve", n: 77, p50: 8, p90: 9 }),
    row({ route: "/v1/map", n: 100, p50: 9, p90: 179 }),
  ] },
  { match: "Task timed out", rows: [] },
];

// Every rule that can fail, failing.
export const BROKEN_WINDOW = [
  { match: "by route, outcome, status", rows: requests(
    ["/v1/map", "internal_error", 500, 4],
    ["/v1/potholes/report", "invalid_detection_receipt", 400, 2],
    ["/v1/vision/detect", "daily_vision_limit", 503, 7],
    ["/v1/vision/detect", "shared_rate_limit", 429, 1],
    ["/v1/vision/detect", "shared_daily_budget_reached", 503, 1],
    ["/v1/vision/detect", "shared_budget_reached", 503, 1],
    ["/v1/vision/detect", "undamaged", 200, 30, 2600, 4200],
    ["/v1/tenders/resolve", "road_ownership_unavailable", 200, 26],
    ["/v1/tenders/resolve", "address_unresolved", 200, 24],
    ["/v1/tenders/resolve", "no_location_match", 200, 60],
    ["/v1/potholes/report", "location_dedupe_in_progress", 409, 7],
    ["/v1/potholes/report", "created", 201, 3],
  ) },
  { match: "by local_lookup", rows: [row({ local_lookup: "unavailable", n: 26 }), row({ local_lookup: "municipal_polygon", n: 60 })] },
  { match: "by ward_lookup, ward_tender_count, tender_catalogue", rows: [
    row({ ward_lookup: "unavailable", n: 5 }),
    row({ ward_lookup: "resolved", ward_tender_count: 0, n: 50 }),
    row({ ward_lookup: "resolved", ward_tender_count: 2, n: 5 }),
  ] },
  { match: "by ward_lookup, ward_snapshot", rows: [
    row({ ward_lookup: "unavailable", ward_snapshot: "GJ/ahmedabad", n: 4 }),
    row({ ward_lookup: "unavailable", n: 2 }),
  ] },
  { match: "by outcome, screen_assessment, screen_error", rows: [] },
  { match: "by outcome, bucket", rows: [] },
  { match: "pct(db_ms, 90)", rows: [row({ route: "/v1/tenders/resolve", n: 110, db90: 300, own90: 401 })] },
  { match: 'answer_cache="hit"', rows: [row({ route: "/v1/map", n: 40, p50: 60, p90: 140 })] },
  { match: "Task timed out", rows: [row({ n: 2 })] },
];

export const QUIET_WINDOW = [{ match: "by route, outcome, status", rows: [] }];

// The library's "run this query" dependency, answered from a script. `asked` records
// every query, in the order it was started.
export function scriptedQuery(script, { asked = [] } = {}) {
  const query = async (text, hours) => {
    asked.push({ text, hours });
    const entry = script.find((item) => text.includes(item.match));
    if (!entry) throw new Error(`no scripted answer for: ${text}`);
    if (entry.error) throw new Error(entry.error);
    return entry.rows;
  };
  query.asked = asked;
  return query;
}

const MAP = JSON.stringify({
  type: "FeatureCollection",
  total: 40,
  features: Array.from({ length: 40 }, (_, index) => ({
    type: "Feature",
    geometry: { type: "Point", coordinates: [77.62 + index / 1000, 12.99] },
    properties: { id: index + 1, damage_type: "pothole_cavity", size: "medium" },
  })),
});

const BENGALURU = {
  jurisdiction: {
    road_ownership: "municipal", lgd: "305851", town: "GBA - Central", ward_name: "Cox Town", ward_no: "10",
    lookup: { kgis: "snapshot", local: "municipal_polygon", ward: "resolved" },
  },
  tender: null,
  reason: "no_location_match",
  ward_tenders: [
    { title: "Construction of Cement concrete roads and other improvement works at Doddigunta in ward 108" },
    { title: "Asphalting of roads in Cox Town" },
  ],
};
const AHMEDABAD = {
  jurisdiction: {
    road_ownership: "outside_state", ward_name: "SHAHIBAG", ward_no: "16",
    urban_body: { name: "Ahmedabad Municipal Corporation", road_notices_open: 10 },
    lookup: { ward: "resolved", ward_snapshot: "GJ/ahmedabad" },
  },
  tender: null,
  reason: "no_location_match",
  ward_tenders: [{ title: "In the area of Shahibaug ward of the Central zone, resurfacing of Bhogilal Chali road" }],
};
const UNHINTED = {
  jurisdiction: {
    road_ownership: "municipal", address_source: "packaged_streets",
    address: "MM Road, Doddigunta, Cox Town, Bengaluru, 560005", lookup: { streets: "street" },
  },
  tender: null,
  reason: "no_location_match",
};

// A stand-in for the public API. It registers installs and checks signatures with the
// service's own auth module, so a canary that signs the wrong bytes fails here as it
// would in production. `answers` replaces what one route says: a value, or a function
// of the request. Lookups are named by the point they ask about.
export function fakeApi(answers = {}) {
  const installs = new Map();
  const calls = [];
  const json = (status, body, headers = {}) => ({ status, body: JSON.stringify(body), headers: { "content-type": "application/json", ...headers } });
  const defaults = {
    "GET /v1/health": () => json(200, { ok: true, shared_vision_primary_configured: true, shared_vision_provider: "openai_with_shadow_screen" }),
    "GET /v1/map": () => ({ status: 200, body: MAP, headers: { "content-type": "application/json" }, compress: true }),
    "GET /v1/impact": () => json(200, { active_installations: 262 }),
    "POST /v1/installations": (request) => {
      const key = installationPublicKey(JSON.parse(request.body).public_key);
      installs.set(key.installId, { public_key: key.encoded });
      return json(201, { install_id: key.installId });
    },
    "POST /v1/vision/detect": () => json(200, { assessment: "damaged", damage_type: "pothole_cavity", size: "medium",
      detector: { backend_provider: "openai" } }),
    bengaluru: () => json(200, BENGALURU),
    ahmedabad: () => json(200, AHMEDABAD),
    unhinted: () => json(200, UNHINTED),
  };
  const answer = (name, request) => {
    const chosen = Object.hasOwn(answers, name) ? answers[name] : defaults[name];
    if (typeof chosen !== "function") return json(chosen.status ?? 200, chosen.body ?? chosen);
    const made = chosen(request, { json, installs });
    return made.status ? made : json(200, made);
  };
  async function handle(request) {
    const name = `${request.method} ${request.path}`;
    calls.push({ name, headers: request.headers, body: request.body });
    if (request.method === "GET" || name === "POST /v1/installations") {
      return defaults[name] || Object.hasOwn(answers, name) ? answer(name, request) : json(404, { error: "not_found" });
    }
    const installation = installs.get(request.headers["x-install-id"]);
    const signed = installation && verifyInstallationSignature({
      installation, signature: request.headers["x-signature"], method: "POST", path: request.path,
      timestamp: request.headers["x-timestamp"], idempotencyKey: request.headers["idempotency-key"], body: request.body,
    });
    if (!signed) return json(401, { error: installation ? "bad_signature" : "unknown_installation" });
    if (request.path === "/v1/vision/detect") return answer(name, request);
    if (request.path !== "/v1/tenders/resolve") return json(404, { error: "not_found" });
    const point = JSON.parse(request.body);
    const which = point.lat > 20 ? "ahmedabad" : point.address_hint ? "bengaluru" : "unhinted";
    calls.at(-1).lookup = which;
    return answer(which, request);
  }
  return { handle, calls, installs };
}

// The API as the library's `fetch` dependency. A compressed answer is given the way
// fetch hands it over: the content-encoding header kept, the body already plain.
export function fetchFrom(api) {
  return async (url, init = {}) => {
    const target = new URL(url);
    const headers = Object.fromEntries(Object.entries(init.headers || {}).map(([key, value]) => [key.toLowerCase(), value]));
    const made = await api.handle({ method: init.method || "GET", path: target.pathname, headers, body: init.body || "" });
    const compressed = made.compress && /gzip/.test(headers["accept-encoding"] || "");
    return new Response(made.body, { status: made.status, headers: {
      ...made.headers,
      ...(compressed ? { "content-encoding": "gzip", "content-length": String(gzipSync(made.body).length) } : {}),
    } });
  };
}

// The API on a local port, for the script run as a process.
export async function serve(api) {
  const server = http.createServer(async (incoming, outgoing) => {
    const chunks = [];
    for await (const chunk of incoming) chunks.push(chunk);
    const made = await api.handle({ method: incoming.method, path: new URL(incoming.url, "http://localhost").pathname,
      headers: incoming.headers, body: Buffer.concat(chunks).toString("utf8") });
    const compressed = made.compress && /gzip/.test(incoming.headers["accept-encoding"] || "");
    const bytes = compressed ? gzipSync(made.body) : Buffer.from(made.body);
    outgoing.writeHead(made.status, { ...made.headers, "content-length": bytes.length,
      ...(compressed ? { "content-encoding": "gzip" } : {}) });
    outgoing.end(bytes);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { url: `http://127.0.0.1:${server.address().port}`, close: () => new Promise((resolve) => server.close(resolve)) };
}

// A stand-in `aws` executable for the script run as a process: it answers `logs
// start-query` and `logs get-query-results` from a window script and writes every
// start-query it is given to a file. Returns the directory to put first on PATH.
export function fakeAwsCli(script) {
  const directory = mkdtempSync(path.join(os.tmpdir(), "fake-aws-"));
  const asked = path.join(directory, "asked.jsonl");
  writeFileSync(path.join(directory, "script.json"), JSON.stringify(script));
  writeFileSync(asked, "");
  writeFileSync(path.join(directory, "aws"), `#!/usr/bin/env node
const { appendFileSync, readFileSync } = require("node:fs");
const args = process.argv.slice(2);
const option = (name) => args[args.indexOf(name) + 1];
const script = JSON.parse(readFileSync(${JSON.stringify(path.join(directory, "script.json"))}, "utf8"));
if (args[0] === "logs" && args[1] === "start-query") {
  const text = option("--query-string");
  appendFileSync(${JSON.stringify(asked)}, JSON.stringify({ text, group: option("--log-group-name"),
    seconds: Number(option("--end-time")) - Number(option("--start-time")), region: option("--region") }) + "\\n");
  console.log(JSON.stringify({ queryId: String(script.findIndex((item) => text.includes(item.match))) }));
} else if (args[0] === "logs" && args[1] === "get-query-results") {
  const entry = script[Number(option("--query-id"))];
  if (!entry) { console.error("no scripted answer"); process.exit(254); }
  console.log(JSON.stringify({ status: entry.status || "Complete",
    results: entry.rows.map((row) => Object.entries(row).map(([field, value]) => ({ field, value }))) }));
} else {
  console.error("unexpected aws call: " + args.join(" "));
  process.exit(2);
}
`);
  chmodSync(path.join(directory, "aws"), 0o755);
  return { directory, asked: () => readFileSync(asked, "utf8").trim().split("\n").filter(Boolean).map((line) => JSON.parse(line)) };
}
