import assert from "node:assert/strict";
import test from "node:test";

import { createDetector, createSecretProvider } from "../service/detectors.mjs";
import { createService } from "../service/core.mjs";

// A secret ARN being wired to the function says nothing about what the secret holds.
// Health used to answer from the ARN alone, so it reported a configured shared
// detector while the secret was empty: the app believed the service was ready and
// every capture failed at the point of detection instead of saying so up front.

const unused = new Proxy({}, { get() { return async () => {}; } });

function healthWith(detector) {
  const handle = createService({
    repository: unused,
    detector,
    geolocator: unused,
    logger: { log() {}, error() {} },
  });
  return handle({ rawPath: "/v1/health", requestContext: { http: { method: "GET" } } });
}

function detectorWithSecret(secretValue) {
  const client = {
    async send() {
      if (secretValue instanceof Error) throw secretValue;
      return { SecretString: JSON.stringify(secretValue) };
    },
  };
  return createDetector({
    secretProvider: createSecretProvider({ secretArn: "arn:aws:secretsmanager:test", client }),
  });
}

test("health reports an empty secret as an unconfigured detector", async () => {
  process.env.SHARED_SECRET_ARN = "arn:aws:secretsmanager:test";
  const result = await healthWith(detectorWithSecret({}));
  const body = JSON.parse(result.body);
  assert.equal(body.shared_vision_configured, false);
  assert.equal(body.shared_vision_primary_configured, false);
});

test("health reports a populated OpenAI secret as configured", async () => {
  process.env.SHARED_SECRET_ARN = "arn:aws:secretsmanager:test";
  const result = await healthWith(detectorWithSecret({ openai_api_key: "sk-test-not-a-real-key" }));
  const body = JSON.parse(result.body);
  assert.equal(body.shared_vision_configured, true);
  assert.equal(body.shared_vision_primary_configured, true);
  // The value itself must never appear in a public response.
  assert.ok(!result.body.includes("sk-test-not-a-real-key"));
});

test("an unreadable secret fails closed", async () => {
  process.env.SHARED_SECRET_ARN = "arn:aws:secretsmanager:test";
  const result = await healthWith(detectorWithSecret(new Error("AccessDeniedException")));
  const body = JSON.parse(result.body);
  assert.equal(body.shared_vision_configured, false);
});

test("no secret ARN at all is reported as unconfigured", async () => {
  delete process.env.SHARED_SECRET_ARN;
  const result = await healthWith(createDetector({}));
  const body = JSON.parse(result.body);
  assert.equal(body.shared_vision_configured, false);
});
