import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import test from "node:test";

import { createDetector } from "../service/detectors.mjs";
import { detectBody, harness, secretFrom, undamaged, upstream } from "./support.mjs";

// Request metrics are published by the public /v1/impact route. A request that
// succeeded must not be counted as internal_error, and a path nobody routes must not
// become a published row of its own.

process.env.SHARED_SECRET_ARN = "arn:aws:secretsmanager:test";

const recorded = (h) => h.repository.calls.requests.map((item) => `${item.route} ${item.outcome}`);

test("a CORS preflight is recorded as a preflight", async () => {
  const h = await harness();
  const result = await h.handle({
    rawPath: "/v1/vision/detect",
    requestContext: { http: { method: "OPTIONS" } },
  });
  assert.equal(result.statusCode, 204);
  assert.deepEqual(recorded(h), ["/v1/vision/detect preflight"]);
});

test("an idempotent replay is recorded as a replay", async () => {
  const h = await harness({
    detector: createDetector({
      secretProvider: secretFrom({ openai_api_key: "sk-test-not-a-real-key" }),
      fetchImpl: upstream(200, { output_text: JSON.stringify(undamaged) }),
    }),
  });
  const key = randomUUID();
  assert.equal((await h.post("/v1/vision/detect", detectBody, { key })).statusCode, 200);
  const replay = await h.post("/v1/vision/detect", detectBody, { key });
  assert.equal(replay.statusCode, 200);
  assert.deepEqual(recorded(h), [
    "/v1/vision/detect undamaged",
    "/v1/vision/detect idempotent_replay",
  ]);
});

test("unrouted paths share one metric row instead of publishing the path", async () => {
  const h = await harness();
  for (const [method, path] of [
    ["GET", "/wp-login.php"], ["GET", "/v1/nope"], ["OPTIONS", "/robots.txt"],
  ]) {
    await h.handle({ rawPath: path, requestContext: { http: { method } } });
  }
  assert.deepEqual(recorded(h), [
    "unmatched not_found", "unmatched not_found", "unmatched preflight",
  ]);
});
