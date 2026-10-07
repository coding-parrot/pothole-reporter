import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import test from "node:test";

import { installationPublicKey } from "../service/auth.mjs";
import { runCanary } from "../service/health/canary.mjs";
import { readExampleImage } from "../service/health/example-image.mjs";
import { createReport } from "../service/health/report.mjs";
import { DETECT_PROMPT_VERSION } from "../../../llm/generated/contract.mjs";
import { fakeApi, fetchFrom } from "./health-support.mjs";

// The canary as a library, against a stand-in API that registers installs and checks
// every signature with the service's own auth module. Each case is one way production
// has failed or could: the rule that must break is named, and no other may.

const API = "https://api.test";
const freshKey = async () => generateKeyPairSync("ec", { namedCurve: "prime256v1" });

async function canary({ api = fakeApi(), identity = freshKey, depth = "full", clock = { t: 1_760_000_000_000 }, ...rest } = {}) {
  const report = createReport();
  // Every reading of the clock is 7 ms after the last, so each call "takes" 7 ms.
  const now = () => (clock.t += 7);
  let made;
  let thrown = null;
  try {
    made = await runCanary({ apiUrl: API, fetch: fetchFrom(api), identity, readImage: readExampleImage, report, depth, now, ...rest });
  } catch (error) {
    thrown = error;
  }
  return { ...report.conclude(), made, thrown, api, clock };
}
const names = (rules) => rules.map((rule) => rule.name);

test("a healthy full canary judges every check ok and says which install it ran as", async () => {
  const result = await canary();
  assert.equal(result.thrown, null);
  assert.equal(result.healthy, true);
  assert.deepEqual(result.rules.map((rule) => [rule.part, rule.state, rule.name]), [
    ["canary", "ok", "health"],
    ["canary", "ok", "/v1/map"],
    ["canary", "ok", "/v1/impact"],
    ["canary", "ok", "map is compressed"],
    ["canary", "ok", "install registers"],
    ["canary", "ok", "shared detection finds the example pothole"],
    ["canary", "ok", "Bengaluru street is classified municipal"],
    ["canary", "ok", "road class needs no state GIS call"],
    ["canary", "ok", "ward is named from the packaged snapshot"],
    ["canary", "ok", "the ward's tenders are answered"],
    ["canary", "ok", "Ahmedabad point is answered with its ward by name"],
    ["canary", "ok", "server finds the street itself, with no geocoder call"],
  ]);
  assert.equal(result.lines[0], "\nCanary against https://api.test");
  assert.equal(result.lines[2], "  ok   /v1/map: 200 in 7 ms");
  assert.match(result.made.installId, /^[a-f0-9]{32}$/);
  assert.deepEqual([...result.api.installs.keys()], [result.made.installId]);
});

test("the full canary makes nine requests: four reads, one registration, one detection, three lookups", async () => {
  const { api } = await canary();
  assert.deepEqual(api.calls.map((call) => call.lookup || call.name), [
    "GET /v1/health", "GET /v1/map", "GET /v1/impact", "GET /v1/map", "POST /v1/installations",
    "POST /v1/vision/detect", "bengaluru", "ahmedabad", "unhinted",
  ]);
  // No report is ever sent: the canary cannot put a pothole on the public map.
  assert.ok(!api.calls.some((call) => call.name.includes("/v1/potholes")));
  const detect = JSON.parse(api.calls[5].body);
  assert.equal(detect.capture_mode, "manual");
  assert.equal(detect.images.length, 1);
  assert.equal(detect.images[0], `data:image/jpeg;base64,${readExampleImage().toString("base64")}`);
  // The service refuses any other prompt version with a 409, so a contract bump that
  // forgets the canary fails here and not after the deploy.
  assert.equal(detect.prompt_version, DETECT_PROMPT_VERSION);
});

test("a reads canary asks health, the map and the impact figures, and needs no key and no photograph", async () => {
  const refuse = () => { throw new Error("not needed for reads"); };
  const result = await canary({ depth: "reads", identity: refuse, readImage: refuse });
  assert.equal(result.thrown, null);
  assert.equal(result.healthy, true);
  assert.deepEqual(names(result.rules), ["health", "/v1/map", "/v1/impact", "map is compressed"]);
  assert.deepEqual(result.api.calls.map((call) => call.name), ["GET /v1/health", "GET /v1/map", "GET /v1/impact", "GET /v1/map"]);
  assert.deepEqual(result.made, {});
});

test("a kept key is one install however often the canary runs", async () => {
  const kept = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const api = fakeApi();
  const first = await canary({ api, identity: async () => kept });
  const second = await canary({ api, identity: async () => kept });
  assert.equal(first.made.installId, second.made.installId);
  assert.equal(first.made.installId, installationPublicKey(kept.publicKey.export({ type: "spki", format: "der" }).toString("base64")).installId);
  assert.equal(api.installs.size, 1);
  assert.equal(second.healthy, true);
  // A fresh key each run, as the command-line script uses, is an install each run.
  await canary({ api });
  assert.equal(api.installs.size, 2);
});

test("an install that cannot register is one failure, and nothing signed is sent", async () => {
  const result = await canary({ api: fakeApi({ "POST /v1/installations": { status: 503, body: { message: "Service Unavailable" } } }) });
  assert.deepEqual(result.failures.map((rule) => [rule.name, rule.detail]), [["install registers", '503 {"message":"Service Unavailable"}']]);
  assert.equal(result.api.calls.at(-1).name, "POST /v1/installations");
  assert.deepEqual(result.made, {});
});

// [what the API says, the rules that must break]
const bengaluru = (change) => (request, { json }) => {
  const body = { jurisdiction: { road_ownership: "municipal", lgd: "305851", town: "GBA - Central", ward_name: "Cox Town", ward_no: "10",
    lookup: { kgis: "snapshot", local: "municipal_polygon", ward: "resolved" } },
  tender: null, reason: "no_location_match", ward_tenders: [{ title: "Asphalting of roads in Cox Town" }] };
  change(body);
  return json(200, body);
};
const FAULTS = [
  ["the service is up but the detector secret is empty", { "GET /v1/health": { ok: true, shared_vision_primary_configured: false } }, ["health"]],
  ["health answers 500", { "GET /v1/health": { status: 500, body: { error: "internal_error" } } }, ["health"]],
  ["the map answers 500, as it did for ten days on a missing IAM grant", { "GET /v1/map": { status: 500, body: { error: "internal_error" } } }, ["/v1/map"]],
  ["the impact figures answer 500", { "GET /v1/impact": { status: 500, body: {} } }, ["/v1/impact"]],
  ["the map goes over the air uncompressed", { "GET /v1/map": () => ({ status: 200, body: JSON.stringify({ features: "x".repeat(2000) }), headers: {} }) }, ["map is compressed"]],
  ["the detector calls the pothole undamaged", { "POST /v1/vision/detect": { assessment: "undamaged" } }, ["shared detection finds the example pothole"]],
  ["detection is refused at a cap", { "POST /v1/vision/detect": { status: 503, body: { error: "shared_daily_budget_reached" } } }, ["shared detection finds the example pothole"]],
  ["the Bengaluru road is not classified", { bengaluru: { status: 200, body: { reason: "road_ownership_unavailable" } } }, ["Bengaluru street is classified municipal"]],
  ["the hinted address is not used", { bengaluru: bengaluru((body) => { body.reason = "address_unresolved"; }) }, ["hinted address is used for matching"]],
  ["the tender table is empty", { bengaluru: bengaluru((body) => { body.reason = "no_tenders_for_jurisdiction"; }) }, ["tender table has rows for Bengaluru"]],
  ["the road class waits on the state GIS again", { bengaluru: bengaluru((body) => { body.jurisdiction.lookup.kgis = "available"; }) }, ["road class needs no state GIS call"]],
  ["the ward polygons are not in the package", { bengaluru: bengaluru((body) => { body.jurisdiction.lookup.ward = "unavailable"; body.jurisdiction.ward_name = null; }) },
    ["ward is named from the packaged snapshot"]],
  ["the ward has no tenders", { bengaluru: bengaluru((body) => { body.ward_tenders = []; }) }, ["the ward's tenders are answered"]],
  ["Ahmedabad is answered without its ward", { ahmedabad: { jurisdiction: { road_ownership: "outside_state", urban_body: null, lookup: { ward: "unavailable" } } } },
    ["Ahmedabad point is answered with its ward by name"]],
  ["the street comes from the public geocoder", { unhinted: { jurisdiction: { address_source: "operator_geocoder", lookup: { streets: "unavailable" } } } },
    ["server finds the street itself, with no geocoder call"]],
];
for (const [what, answers, broken] of FAULTS) {
  test(`one fault, one failure: ${what}`, async () => {
    const result = await canary({ api: fakeApi(answers) });
    assert.equal(result.thrown, null);
    assert.deepEqual(names(result.failures), broken);
    assert.ok(result.failures.every((rule) => rule.part === "canary"));
  });
}

test("a slow answer breaks its time limit even when the answer is right", async () => {
  const clock = { t: 1_760_000_000_000 };
  const slow = (ms, name) => fakeApi({ [name]: (request, { json }) => {
    clock.t += ms;
    return name === "GET /v1/health" ? json(200, { ok: true, shared_vision_primary_configured: true, shared_vision_provider: "openai" })
      : json(200, { assessment: "damaged", damage_type: "pothole_cavity", size: "medium", detector: { backend_provider: "openai" } });
  } });
  const health = await canary({ clock, api: slow(5000, "GET /v1/health") });
  assert.deepEqual(health.failures.map((rule) => [rule.name, rule.detail]), [["health within 5000 ms", "5007 ms"]]);
  const detection = await canary({ clock, api: slow(6000, "POST /v1/vision/detect") });
  assert.deepEqual(detection.failures.map((rule) => [rule.name, rule.detail]), [["detection within 6000 ms", "6007 ms"]]);
  assert.deepEqual((await canary({ clock, api: slow(5900, "POST /v1/vision/detect") })).failures, []);
});

test("Ahmedabad's tenders are judged only by a caller that knows which catalogue is deployed", async () => {
  const noTenders = { ahmedabad: { jurisdiction: { road_ownership: "outside_state", ward_name: "SHAHIBAG", ward_no: "16",
    urban_body: { name: "Ahmedabad Municipal Corporation", road_notices_open: 10 }, lookup: { ward: "resolved", ward_snapshot: "GJ/ahmedabad" } }, ward_tenders: [] } };
  // The scheduled run: notices close every week, so an empty list is said, not failed.
  const scheduled = await canary({ api: fakeApi(noTenders) });
  assert.equal(scheduled.healthy, true);
  const skipped = scheduled.rules.find((rule) => rule.state === "skip");
  assert.equal(skipped.name, "Ahmedabad point is answered with its ward by name");
  assert.ok(scheduled.lines.some((line) => line.startsWith("  skip Ahmedabad point is answered with its ward by name: SHAHIBAG (ward 16, GJ/ahmedabad) answered with no ward tender")));
  // deploy.sh: the catalogue just deployed holds three open notices for the ward.
  const deployed = await canary({ api: fakeApi(noTenders), expectedOpenNotices: async () => 3 });
  assert.deepEqual(names(deployed.failures), ["Ahmedabad point is answered with its ward by name"]);
  assert.match(deployed.failures[0].detail, /holds 3 open notices for the ward/);
});

test("an API that does not answer at all ends the canary with the error, for the caller to report", async () => {
  const report = createReport();
  const down = async () => { throw new TypeError("fetch failed"); };
  await assert.rejects(runCanary({ apiUrl: API, fetch: down, identity: freshKey, readImage: readExampleImage, report }), /fetch failed/);
  const half = fakeApi();
  const fetch = fetchFrom(half);
  const dropsLookups = async (url, init) => {
    if (String(url).endsWith("/v1/tenders/resolve")) throw new TypeError("fetch failed");
    return fetch(url, init);
  };
  const second = createReport();
  await assert.rejects(runCanary({ apiUrl: API, fetch: dropsLookups, identity: freshKey, readImage: readExampleImage, report: second }), /fetch failed/);
  assert.deepEqual(names(second.conclude().rules).slice(-2), ["install registers", "shared detection finds the example pothole"]);
});

test("the stand-in API refuses a request signed with another key, as the service does", async () => {
  const api = fakeApi();
  const registered = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const other = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  // Registers one key and signs with another: every signed call is a 401.
  const result = await canary({ api, identity: async () => ({ publicKey: registered.publicKey, privateKey: other.privateKey }) });
  assert.deepEqual(names(result.failures), ["shared detection finds the example pothole", "Bengaluru street is classified municipal",
    "Ahmedabad point is answered with its ward by name", "server finds the street itself, with no geocoder call"]);
  assert.match(result.failures[0].detail, /^401 \{"error":"bad_signature"\}/);
});

test("the example photograph is a real JPEG of useful size", () => {
  const bytes = readExampleImage();
  assert.deepEqual([...bytes.subarray(0, 3)], [0xff, 0xd8, 0xff]);
  assert.ok(bytes.length > 50_000 && bytes.length < 1_000_000, `${bytes.length} bytes`);
});
