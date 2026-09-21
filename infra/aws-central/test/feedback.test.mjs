import assert from "node:assert/strict";
import { generateKeyPairSync, randomUUID, sign } from "node:crypto";
import test from "node:test";

import { canonicalRequest, installationPublicKey } from "../service/auth.mjs";
import { createService } from "../service/core.mjs";

function memoryRepository({ feedbackPerInstallDay = 10 } = {}) {
  const installations = new Map();
  const idempotency = new Map();
  const replays = new Set();
  const usage = new Map();
  const feedback = [];
  return {
    feedback,
    dedupeRadiusMetres: 30,
    async registerInstallation(value) { installations.set(value.id, value); },
    async getInstallation(id) { return installations.get(id) || null; },
    async touchInstallation() {},
    async claimReplay(key) {
      if (replays.has(key)) return false;
      replays.add(key);
      return true;
    },
    async claimIdempotency({ id, requestHash, owner }) {
      const existing = idempotency.get(id);
      if (existing) return existing;
      idempotency.set(id, { status: "PENDING", request_hash: requestHash, owner });
      return { status: "CLAIMED" };
    },
    async completeIdempotency({ id, requestHash, statusCode, payload }) {
      idempotency.set(id, {
        status: "COMPLETED",
        request_hash: requestHash,
        status_code: statusCode,
        response_json: JSON.stringify(payload),
      });
    },
    async releaseIdempotency(id) { idempotency.delete(id); },
    async recordRequest() {},
    async takeFeedbackQuota(installId) {
      const key = `${installId}#${new Date().toISOString().slice(0, 10)}`;
      const used = usage.get(key) || 0;
      if (used >= feedbackPerInstallDay) return { ok: false, limit: feedbackPerInstallDay };
      usage.set(key, used + 1);
      return { ok: true, limit: feedbackPerInstallDay };
    },
    async putFeedback(item) { feedback.push(item); },
  };
}

const unused = {
  status: () => ({ openai_configured: false, yolo_configured: false, mode: "none" }),
};

function harness(options) {
  const repository = memoryRepository(options);
  const handle = createService({
    repository,
    detector: unused,
    geolocator: unused,
    logger: { log() {}, error() {} },
  });
  const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const publicDer = publicKey.export({ type: "spki", format: "der" }).toString("base64");
  const installId = installationPublicKey(publicDer).installId;

  const register = () => handle({
    rawPath: "/v1/installations",
    requestContext: { http: { method: "POST" } },
    body: JSON.stringify({ public_key: publicDer }),
  });

  const post = (path, value, { signed = true, sentAt = Date.now() } = {}) => {
    const body = JSON.stringify(value);
    const timestamp = String(sentAt);
    const idempotencyKey = randomUUID();
    const signature = sign("sha256", Buffer.from(canonicalRequest({
      method: "POST", path, timestamp, idempotencyKey, body,
    })), { key: privateKey, dsaEncoding: "ieee-p1363" }).toString("base64");
    return handle({
      rawPath: path,
      requestContext: { http: { method: "POST" } },
      headers: signed ? {
        "X-Install-ID": installId,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
        "Idempotency-Key": idempotencyKey,
      } : { "Idempotency-Key": idempotencyKey },
      body,
    });
  };
  return { repository, register, post, installId };
}

const valid = {
  rating: 4,
  text: "Drive mode missed two potholes near Silk Board.",
  test_mode: "bike",
  app_version: "1.39.0",
  device: "Pixel 7",
  email: "tester@example.com",
};

test("signed feedback is stored against the installation", async () => {
  const h = harness();
  await h.register();
  const result = await h.post("/v1/feedback", valid);
  assert.equal(result.statusCode, 201, result.body);
  assert.equal(h.repository.feedback.length, 1);
  const [item] = h.repository.feedback;
  assert.equal(item.install_id, h.installId);
  assert.equal(item.rating, 4);
  assert.equal(item.text, valid.text);
  assert.equal(item.test_mode, "bike");
  assert.equal(item.app_version, "1.39.0");
  assert.equal(item.device, "Pixel 7");
  assert.equal(item.email, "tester@example.com");
  assert.ok(Number.isInteger(item.created_at));
});

test("email is optional and a malformed email is dropped, not stored", async () => {
  const h = harness();
  await h.register();
  const { email, ...withoutEmail } = valid;
  assert.equal((await h.post("/v1/feedback", withoutEmail)).statusCode, 201);
  assert.equal((await h.post("/v1/feedback", { ...valid, email: "not an email" })).statusCode, 201);
  assert.deepEqual(h.repository.feedback.map((item) => item.email), [null, null]);
});

test("unsigned feedback is rejected", async () => {
  const h = harness();
  await h.register();
  const result = await h.post("/v1/feedback", valid, { signed: false });
  assert.equal(result.statusCode, 401);
  assert.equal(h.repository.feedback.length, 0);
});

test("feedback needs a 1 to 5 rating or some text, and a known test mode", async () => {
  const h = harness();
  await h.register();
  for (const body of [
    { ...valid, rating: 0 },
    { ...valid, rating: 6 },
    { ...valid, rating: 3.5 },
    { ...valid, test_mode: "boat" },
    { test_mode: "car", app_version: "1.39.0" },
  ]) {
    const result = await h.post("/v1/feedback", body);
    assert.equal(result.statusCode, 400, JSON.stringify(body));
  }
  assert.equal(h.repository.feedback.length, 0);
  assert.equal((await h.post("/v1/feedback", { text: "Crashed on start" })).statusCode, 201);
  assert.equal((await h.post("/v1/feedback", { rating: 2 })).statusCode, 201);
});

test("feedback text is capped at 2000 characters", async () => {
  const h = harness();
  await h.register();
  assert.equal((await h.post("/v1/feedback", { ...valid, text: "x".repeat(5_000) })).statusCode, 201);
  assert.equal(h.repository.feedback[0].text.length, 2_000);
});

test("an installation may send at most 10 feedback entries a day", async () => {
  const h = harness();
  await h.register();
  for (let index = 0; index < 10; index += 1) {
    assert.equal((await h.post("/v1/feedback", valid)).statusCode, 201);
  }
  const blocked = await h.post("/v1/feedback", valid);
  assert.equal(blocked.statusCode, 429);
  assert.equal(JSON.parse(blocked.body).error, "feedback_limit_reached");
  assert.equal(h.repository.feedback.length, 10);
});

test("a stale signature tells the app the server's time so it can re-sign", async () => {
  const h = harness();
  await h.register();
  const result = await h.post("/v1/feedback", valid, { sentAt: Date.now() - 6 * 60_000 });
  assert.equal(result.statusCode, 401);
  const body = JSON.parse(result.body);
  assert.equal(body.error, "stale_request");
  assert.equal(body.details?.retryable, true);
  assert.ok(Math.abs(body.details?.server_time - Date.now()) < 1_000, JSON.stringify(body));
});
