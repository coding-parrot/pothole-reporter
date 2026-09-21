import assert from "node:assert/strict";
import test from "node:test";

import { harness, memoryRepository } from "./support.mjs";

// An unexpected error reaches the app as internal_error, which is right for the app
// and useless for the operator. The log line has to carry the cause: an IAM denial
// once took an access simulation to find because the log said only internal_error.

test("an unexpected error logs its type and message", async () => {
  const repository = memoryRepository();
  repository.claimIdempotency = async () => {
    throw Object.assign(new Error(
      "User: arn:aws:sts::1:assumed-role/central is not authorized to perform: dynamodb:PutItem",
    ), { name: "AccessDeniedException" });
  };
  const h = await harness({ repository });
  const result = await h.post("/v1/feedback", { rating: 5, test_mode: "car" });
  assert.equal(result.statusCode, 500);
  assert.equal(JSON.parse(result.body).error, "internal_error");
  assert.ok(!result.body.includes("AccessDenied"), "the cause stays out of the response");
  const line = JSON.parse(h.lines.error.find((item) => item.includes("request_error")));
  assert.equal(line.error, "internal_error");
  assert.equal(line.error_type, "AccessDeniedException");
  assert.match(line.error_message, /not authorized/);
});

test("a known error logs no cause fields", async () => {
  const h = await harness();
  await h.handle({ rawPath: "/v1/nope", requestContext: { http: { method: "GET" } } });
  const line = JSON.parse(h.lines.error.find((item) => item.includes("request_error")));
  assert.equal(line.error, "not_found");
  assert.equal(line.error_type, undefined);
});
