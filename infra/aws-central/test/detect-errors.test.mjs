import assert from "node:assert/strict";
import test from "node:test";

import { createDetector } from "../service/detectors.mjs";
import {
  detectBody, harness, memoryRepository, secretFrom, undamaged, upstream,
} from "./support.mjs";

// Each case is an upstream failure the tester cannot fix by resending the frame. The
// app decides whether to stop a drive from the code alone, so the code has to name the
// real cause, and a detection that never ran must not cost the tester a quota unit.

process.env.SHARED_SECRET_ARN = "arn:aws:secretsmanager:test";

async function detectWith(detectorOptions, postOptions) {
  const h = await harness({ detector: createDetector(detectorOptions) });
  const started = Date.now();
  const result = await h.post("/v1/vision/detect", detectBody, postOptions);
  return {
    status: result.statusCode,
    body: JSON.parse(result.body),
    calls: h.repository.calls,
    ms: Date.now() - started,
  };
}

const key = { openai_api_key: "sk-test-not-a-real-key" };

test("a secret with no current value is an unconfigured detector, not an internal error", async () => {
  const missing = Object.assign(
    new Error("Secrets Manager can't find the specified secret value for staging label: AWSCURRENT"),
    { name: "ResourceNotFoundException" },
  );
  const result = await detectWith({ secretProvider: secretFrom(missing) });
  assert.equal(result.status, 503);
  assert.equal(result.body.error, "shared_openai_not_configured");
  assert.equal(result.calls.refund, 1);
});

for (const code of ["insufficient_quota", "billing_hard_limit_reached"]) {
  test(`OpenAI 429 ${code} is exhausted credit, not a rate limit`, async () => {
    const result = await detectWith({
      secretProvider: secretFrom(key),
      fetchImpl: upstream(429, { error: { code, type: code } }),
    });
    assert.equal(result.status, 503);
    assert.equal(result.body.error, "shared_credits_exhausted");
    assert.equal(result.calls.refund, 1);
  });
}

test("exhausted credit with no YOLO fallback reports the OpenAI reason", async () => {
  const result = await detectWith({
    secretProvider: secretFrom(key),
    fetchImpl: upstream(429, { error: { code: "organization_spend_limit_exceeded" } }),
  });
  assert.equal(result.status, 503);
  assert.equal(result.body.error, "shared_credits_exhausted");
});

for (const status of [401, 403]) {
  test(`OpenAI ${status} is a server credential problem, not an image problem`, async () => {
    const result = await detectWith({
      secretProvider: secretFrom(key),
      fetchImpl: upstream(status, { error: { code: "invalid_api_key", type: "invalid_request_error" } }),
    });
    assert.equal(result.status, 503);
    assert.equal(result.body.error, "shared_vision_not_configured");
    assert.equal(result.calls.refund, 1);
  });
}

test("an OpenAI 400 is still an image rejection and still costs the unit", async () => {
  const result = await detectWith({
    secretProvider: secretFrom(key),
    fetchImpl: upstream(400, { error: { code: "invalid_image", type: "invalid_request_error" } }),
  });
  assert.equal(result.status, 422);
  assert.equal(result.body.error, "vision_request_rejected");
  assert.equal(result.calls.refund, 0);
});

for (const [label, fetchImpl, code] of [
  ["OpenAI 500", upstream(500, { error: { type: "server_error" } }), "shared_vision_unavailable"],
  ["OpenAI 429 rate limit", upstream(429, { error: { code: "rate_limit_exceeded" } }), "shared_rate_limit"],
  ["malformed structured output", upstream(200, { output_text: "{not json" }), "bad_upstream_response"],
  ["a contradictory verdict", upstream(200, {
    output_text: JSON.stringify({ ...undamaged, assessment: "damaged" }),
  }), "bad_upstream_response"],
]) {
  test(`${label} refunds the quota unit it took`, async () => {
    const result = await detectWith({ secretProvider: secretFrom(key), fetchImpl });
    assert.equal(result.body.error, code);
    assert.equal(result.calls.take, 1);
    assert.equal(result.calls.refund, 1);
  });
}

test("a successful detection is not refunded", async () => {
  const result = await detectWith({
    secretProvider: secretFrom(key),
    fetchImpl: upstream(200, { output_text: JSON.stringify(undamaged) }),
  });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(result.calls.take, 1);
  assert.equal(result.calls.refund, 0);
});

test("a hanging OpenAI call gives up inside the Lambda's remaining time", async () => {
  const hanging = (url, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener("abort", () => reject(signal.reason));
  });
  const result = await detectWith(
    { secretProvider: secretFrom(key), fetchImpl: hanging },
    { awsContext: { awsRequestId: "test", getRemainingTimeInMillis: () => 4_000 } },
  );
  assert.equal(result.status, 503);
  assert.equal(result.body.error, "shared_vision_unavailable");
  assert.equal(result.body.details?.retryable, true);
  assert.ok(result.ms < 2_500, `took ${result.ms} ms`);
  assert.equal(result.calls.refund, 1);
});

test("a quota refund that fails does not hide the detector's error", async () => {
  const repository = memoryRepository();
  repository.refundVisionQuota = async () => { throw new Error("throttled"); };
  const h = await harness({
    repository,
    detector: createDetector({
      secretProvider: secretFrom(key),
      fetchImpl: upstream(500, { error: { type: "server_error" } }),
    }),
  });
  const result = await h.post("/v1/vision/detect", detectBody);
  assert.equal(JSON.parse(result.body).error, "shared_vision_unavailable");
});
