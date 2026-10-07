import assert from "node:assert/strict";
import { generateKeyPairSync, randomUUID, sign } from "node:crypto";
import test from "node:test";

import { canonicalRequest, installationPublicKey } from "../service/auth.mjs";
import { createCanaryInstall } from "../service/canary-install.mjs";
import { createService } from "../service/core.mjs";
import { createDynamoRepository } from "../service/dynamo-repository.mjs";
import { detectBody, memoryRepository } from "./support.mjs";

// The scheduled canary is not a person, and the public figures (/v1/impact: active
// installations, requests, capture checks) count people. The service knows the canary's
// install id (the health function publishes it; canary-install.mjs reads it) and, for
// that install only, writes no metric and marks the request line `canary: true`.
// Everything else is exactly as for a phone: the signature, the quota, idempotency.

const damaged = { image_quality: "acceptable", assessment: "damaged", damage_type: "pothole_cavity", size: "medium", description: "A pothole." };

// The metrics table in memory, behind the real repository: what recordRequest and
// recordCapture write is what impact() reads.
function metricsTable() {
  const rows = new Map();
  return {
    rows,
    async send(command) {
      const { input } = command;
      if (command.constructor.name === "UpdateCommand") {
        const id = `${input.Key.day}|${input.Key.metric}`;
        const row = rows.get(id) || { day: input.Key.day, metric: input.Key.metric, request_count: 0 };
        row.request_count += 1;
        rows.set(id, row);
        return {};
      }
      if (command.constructor.name === "QueryCommand") {
        return { Items: [...rows.values()].filter((row) => row.day === input.ExpressionAttributeValues[":day"]) };
      }
      throw new Error(`unexpected ${command.constructor.name}`);
    },
  };
}

function setup({ quota } = {}) {
  const table = metricsTable();
  const dynamo = createDynamoRepository({ client: table, tables: { metrics: "metrics" } });
  const memory = memoryRepository();
  const repository = { ...memory, calls: memory.calls,
    recordRequest: dynamo.recordRequest, recordCapture: dynamo.recordCapture, impact: dynamo.impact,
    ...(quota ? { takeVisionQuota: quota } : {}) };
  const lines = [];
  const known = { id: null };
  const handle = createService({
    repository,
    detector: { detect: async () => ({ provider: "openai", model: "gpt-5-mini", verdict: damaged }), status: () => ({ mode: "openai" }),
      readiness: async () => ({ openai_configured: true }) },
    geolocator: {},
    canaryInstall: () => known.id,
    logger: { log: (line) => lines.push(JSON.parse(line)), error() {} },
  });
  // An install with its own key, registered through the public route like any phone.
  async function install() {
    const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
    const der = publicKey.export({ type: "spki", format: "der" }).toString("base64");
    const id = installationPublicKey(der).installId;
    const register = () => handle({ rawPath: "/v1/installations", requestContext: { http: { method: "POST" } }, body: JSON.stringify({ public_key: der }) });
    const post = (path, value, { signWith = privateKey } = {}) => {
      const body = JSON.stringify(value);
      const timestamp = String(Date.now());
      const key = randomUUID();
      const signature = sign("sha256", Buffer.from(canonicalRequest({ method: "POST", path, timestamp, idempotencyKey: key, body })),
        { key: signWith, dsaEncoding: "ieee-p1363" }).toString("base64");
      return handle({ rawPath: path, requestContext: { http: { method: "POST" } }, body,
        headers: { "X-Install-ID": id, "X-Timestamp": timestamp, "X-Signature": signature, "Idempotency-Key": key } });
    };
    return { id, register, post };
  }
  const get = (path, headers = {}) => handle({ rawPath: path, requestContext: { http: { method: "GET" } }, headers });
  // The public figures, read the way the page reads them. A new service each time, so
  // nothing is answered from the 60 s the figures are kept in memory.
  async function impact() {
    const fresh = createService({ repository, detector: {}, geolocator: {}, canaryInstall: () => known.id, logger: { log() {}, error() {} } });
    const body = JSON.parse((await fresh({ rawPath: "/v1/impact", requestContext: { http: { method: "GET" } } })).body);
    table.rows.delete([...table.rows.keys()].find((key) => key.endsWith("request#/v1/impact#impact_read#none")));
    return { active: body.active_installations, requests: body.requests_total, captures: body.capture_checks_total,
      byRoute: Object.fromEntries(body.requests.map((item) => [`${item.route} ${item.outcome}`, item.count])) };
  }
  return { handle, install, get, impact, known, lines, repository, table };
}

test("a phone's requests count; the canary's same requests move no public figure", async () => {
  const service = setup();
  const phone = await service.install();
  const canary = await service.install();
  service.known.id = canary.id;

  await phone.register();
  assert.equal((await phone.post("/v1/vision/detect", detectBody)).statusCode, 200);
  const afterPhone = await service.impact();
  assert.deepEqual(afterPhone, { active: 1, requests: 2, captures: 1,
    byRoute: { "/v1/installations registered": 1, "/v1/vision/detect damaged": 1 } });

  await canary.register();
  const answer = await canary.post("/v1/vision/detect", detectBody);
  assert.equal(answer.statusCode, 200);
  assert.equal(JSON.parse(answer.body).assessment, "damaged", "the canary is answered exactly as a phone is");
  assert.deepEqual(await service.impact(), afterPhone, "requests, capture checks and active installations are unchanged");
  // Nothing was written for it at all: no request row, no per-install row, no capture row.
  assert.ok(![...service.table.rows.keys()].some((key) => key.includes(canary.id)));

  await phone.post("/v1/vision/detect", detectBody);
  assert.deepEqual(await service.impact(), { active: 1, requests: 3, captures: 2,
    byRoute: { "/v1/installations registered": 1, "/v1/vision/detect damaged": 2 } });
});

test("the canary's request line says canary: true, and a phone's line has no such field", async () => {
  const service = setup();
  const phone = await service.install();
  const canary = await service.install();
  service.known.id = canary.id;
  await phone.register();
  await phone.post("/v1/vision/detect", detectBody);
  await canary.register();
  const answer = await canary.post("/v1/vision/detect", detectBody);
  const requests = service.lines.filter((line) => line.event === "http_request");
  assert.deepEqual(requests.map((line) => [line.route, line.canary]), [
    ["/v1/installations", undefined], ["/v1/vision/detect", undefined],
    ["/v1/installations", true], ["/v1/vision/detect", true],
  ]);
  // Absent, not null or false: the health queries keep a line by `not ispresent(canary)`.
  assert.ok(!("canary" in requests[0]) && !("canary" in requests[1]));
  // The canary can see that it was recognised.
  assert.equal(answer.headers["x-canary"], "true");
  assert.equal((await phone.post("/v1/vision/detect", detectBody)).headers["x-canary"], undefined);
});

test("the canary still takes quota, and is refused at a cap like anyone", async () => {
  let taken = 0;
  const service = setup({ quota: async () => { taken += 1; return taken > 1 ? { ok: false, code: "daily_vision_limit", limit: 1 } : { ok: true, limit: 1 }; } });
  const canary = await service.install();
  service.known.id = canary.id;
  await canary.register();
  assert.equal((await canary.post("/v1/vision/detect", detectBody)).statusCode, 200);
  const refused = await canary.post("/v1/vision/detect", detectBody);
  assert.equal(refused.statusCode, 503);
  assert.equal(JSON.parse(refused.body).error, "daily_vision_limit");
  assert.equal(taken, 2);
});

test("a request that only claims the canary's id is refused and counted like any other", async () => {
  const service = setup();
  const canary = await service.install();
  const stranger = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  service.known.id = canary.id;
  await canary.register();
  const forged = await canary.post("/v1/vision/detect", detectBody, { signWith: stranger.privateKey });
  assert.equal(forged.statusCode, 401);
  const line = service.lines.at(-1);
  assert.ok(!("canary" in line));
  assert.equal((await service.impact()).requests, 1, "the refused request is in the figures");
  // And a replayed idempotency key is answered from the first answer, as for a phone.
});

test("the canary's reads of health, the map and the impact figures are not counted either", async () => {
  const service = setup();
  const canary = await service.install();
  service.known.id = canary.id;
  await service.get("/v1/health");
  await service.get("/v1/health", { "X-Install-ID": canary.id });
  const marked = service.lines.filter((line) => line.event === "http_request");
  assert.deepEqual(marked.map((line) => line.canary), [undefined, true]);
  assert.deepEqual((await service.impact()).byRoute, { "/v1/health healthy": 1 });
  // The header means nothing on a signed route: there the signature decides.
});

test("until the service knows the canary's id, every install counts", async () => {
  const service = setup();
  const canary = await service.install();
  await canary.register();
  await canary.post("/v1/vision/detect", detectBody);
  assert.deepEqual([(await service.impact()).active, service.lines.some((line) => "canary" in line)], [1, false]);
  // Once known, its earlier rows stop counting as an active installation at once.
  service.known.id = canary.id;
  assert.equal((await service.impact()).active, 0);
});

// ------------------------------------------------------------------ learning the id
test("the canary's id is read off the request path, kept ten minutes, and survives a failed read", async () => {
  let clock = 0;
  let stored = "a".repeat(32);
  let reads = 0;
  const errors = [];
  const canary = createCanaryInstall({ now: () => clock, logger: { error: (line) => errors.push(JSON.parse(line)) },
    read: async () => { reads += 1; if (stored instanceof Error) throw stored; return stored; } });
  assert.equal(canary.id(), null, "nobody is the canary before the first read");
  await canary.refresh();
  assert.equal(canary.id(), "a".repeat(32));
  clock = 599_000;
  await canary.refresh();
  assert.equal(reads, 1, "asked once in ten minutes, however often the warm event comes");
  clock = 600_000;
  stored = new Error("AccessDeniedException");
  await canary.refresh();
  assert.equal(canary.id(), "a".repeat(32), "a failed read keeps the last id");
  assert.deepEqual(errors.map((line) => line.event), ["canary_install_unreadable"]);
  clock = 1_200_000;
  stored = "b".repeat(32);
  await canary.refresh();
  assert.equal(canary.id(), "b".repeat(32));
  // Not published yet, or not an install id: nobody is the canary.
  for (const value of [null, "", "not-an-id", "A".repeat(31)]) {
    const none = createCanaryInstall({ read: async () => value, logger: { error() {} } });
    await none.refresh();
    assert.equal(none.id(), null);
  }
});
