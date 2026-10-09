import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createHandler, readConfig } from "../service.mjs";

// The screen speaks the YOLO Lambda contract (infra/aws-yolo/service/handler.py) so the
// central service's yolo_then_openai and openai_with_shadow_screen modes work with it
// unchanged. These cases pin that contract with a stand-in scorer; test/contract.test.mjs
// drives it from the central service's own code.

const TOKEN = "screen-test-token";
const photo = readFileSync(new URL("../../../../docs/example-pothole.jpg", import.meta.url));
const dataUrl = `data:image/jpeg;base64,${photo.toString("base64")}`;
const config = readConfig({ API_KEY_SHA256: createHash("sha256").update(TOKEN).digest("hex") });
const VERDICT_KEYS = ["assessment", "damage_type", "description", "image_quality", "size"];

function scorerReturning(score, meta = { model_version: "road-screen-test", threshold: 0.2 }) {
  const calls = [];
  return {
    calls, meta,
    async score(buffer) {
      calls.push(buffer);
      if (score instanceof Error) throw score;
      return { score, decodeMs: 4, inferMs: 30 };
    },
  };
}

// Exactly what detectors.mjs yolo() puts in the InvokeCommand payload.
function centralEvent({ token = TOKEN, requestId = "req-1", body = {}, headers = {} } = {}) {
  return {
    version: "2.0",
    routeKey: "POST /v1/detect",
    rawPath: "/v1/detect",
    headers: { "x-request-id": requestId, "x-yolo-api-key": token,
      "content-type": "application/json", ...headers },
    body: JSON.stringify({
      version: 1, task: "road_damage_detection", request_id: requestId, model: "pothole-yolo",
      capture_mode: "drive", language: "en", prompt_version: "road-damage-v5",
      schema_version: 4, images: [{ data_url: dataUrl }], ...body,
    }),
    isBase64Encoded: false,
    requestContext: { requestId },
  };
}

async function call(event, { scorer = scorerReturning(0.9), handlerConfig = config } = {}) {
  const lines = [];
  const handle = createHandler({ config: handlerConfig, scorer, log: (line) => lines.push(line) });
  const result = await handle(event, { awsRequestId: "aws-1" });
  return { result, body: JSON.parse(result.body), log: JSON.parse(lines.at(-1)), lines, scorer };
}

test("a frame scored at or above the threshold is damaged, in the five-field verdict", async () => {
  for (const score of [0.2, 0.93]) {
    const { result, body, scorer } = await call(centralEvent(), { scorer: scorerReturning(score) });
    assert.equal(result.statusCode, 200, result.body);
    assert.equal(result.headers["x-request-id"], "req-1");
    assert.equal(result.headers["cache-control"], "no-store");
    assert.equal(result.isBase64Encoded, false);
    assert.equal(body.request_id, "req-1");
    assert.deepEqual(Object.keys(body.verdict).sort(), VERDICT_KEYS);
    assert.deepEqual({ ...body.verdict, description: "" }, {
      image_quality: "acceptable", assessment: "damaged", damage_type: "pothole_cavity",
      size: null, description: "",
    });
    assert.ok(body.verdict.description.length > 0);
    assert.equal(body.model, "road-screen-test");
    assert.equal(body.score, score);
    assert.equal(body.threshold, 0.2);
    assert.equal(scorer.calls.length, 1);
    assert.ok(scorer.calls[0].equals(photo), "the scorer gets the decoded image bytes");
  }
});

test("a frame scored below the threshold is undamaged with no type and no size", async () => {
  const { result, body } = await call(centralEvent(), { scorer: scorerReturning(0.19999) });
  assert.equal(result.statusCode, 200);
  assert.deepEqual({ ...body.verdict, description: "" }, {
    image_quality: "acceptable", assessment: "undamaged", damage_type: null, size: null,
    description: "",
  });
  assert.ok(body.verdict.description.length > 0);
  assert.equal(body.score, 0.19999);
});

test("SCREEN_THRESHOLD moves the operating point without a new model", async () => {
  const moved = readConfig({ API_KEY_SHA256: config.apiKeySha256, SCREEN_THRESHOLD: "0.6" });
  const { body } = await call(centralEvent(), { scorer: scorerReturning(0.5), handlerConfig: moved });
  assert.equal(body.verdict.assessment, "undamaged");
  assert.equal(body.threshold, 0.6);
  for (const bad of ["0", "1", "1.5", "abc", "-0.1"]) {
    assert.throws(() => readConfig({ API_KEY_SHA256: config.apiKeySha256, SCREEN_THRESHOLD: bad }),
      /SCREEN_THRESHOLD/);
  }
});

test("a missing or wrong key is refused before the image is decoded or scored", async () => {
  for (const event of [
    centralEvent({ token: "wrong" }),
    centralEvent({ token: "" }),
    { ...centralEvent(), headers: { "content-type": "application/json" } },
  ]) {
    const { result, body, scorer } = await call(event);
    assert.equal(result.statusCode, 401);
    assert.equal(body.error, "unauthorized");
    assert.equal(scorer.calls.length, 0);
  }
  const bearer = centralEvent();
  delete bearer.headers["x-yolo-api-key"];
  bearer.headers.authorization = `Bearer ${TOKEN}`;
  assert.equal((await call(bearer)).result.statusCode, 200);
});

test("a function without a credential hash answers 503, never an open door", async () => {
  const lines = [];
  const before = process.env.API_KEY_SHA256;
  delete process.env.API_KEY_SHA256;
  try {
    const handle = createHandler({ scorer: scorerReturning(0.9), log: (line) => lines.push(line) });
    const result = await handle(centralEvent(), {});
    assert.equal(result.statusCode, 503);
    assert.equal(JSON.parse(result.body).error, "service_not_configured");
  } finally {
    if (before !== undefined) process.env.API_KEY_SHA256 = before;
  }
});

for (const [label, change, status, code] of [
  ["another contract version", { body: { version: 2 } }, 409, "contract_mismatch"],
  ["another task", { body: { task: "tender_match" } }, 409, "contract_mismatch"],
  ["another prompt version", { body: { prompt_version: "road-damage-v4" } }, 409, "schema_version_mismatch"],
  ["another schema version", { body: { schema_version: 3 } }, 409, "schema_version_mismatch"],
  ["an unknown capture mode", { body: { capture_mode: "video" } }, 400, "bad_capture_mode"],
  ["no image", { body: { images: [] } }, 400, "bad_image_count"],
  ["two images", { body: { images: [{ data_url: dataUrl }, { data_url: dataUrl }] } }, 400, "bad_image_count"],
  ["an image that is not a data URL", { body: { images: [{ data_url: "https://example.com/a.jpg" }] } }, 400, "bad_image"],
  ["an image with extra fields", { body: { images: [{ data_url: dataUrl, crop: [0, 0, 1, 1] }] } }, 400, "bad_image"],
  ["bytes that are not the stated type", { body: { images: [{ data_url: dataUrl.replace("image/jpeg", "image/png") }] } }, 400, "bad_image"],
  ["a request id that differs from the header", { headers: { "x-request-id": "other" } }, 400, "request_id_mismatch"],
  ["a wrong content type", { headers: { "content-type": "text/plain" } }, 415, "unsupported_media_type"],
]) {
  test(`${label} is refused with ${status} ${code} and never scored`, async () => {
    const { result, body, scorer, log } = await call(centralEvent(change));
    assert.equal(result.statusCode, status, result.body);
    assert.equal(body.error, code);
    assert.equal(scorer.calls.length, 0);
    assert.equal(log.outcome, code);
    assert.equal(log.status, status);
  });
}

test("an image over the byte limit is refused with 413", async () => {
  const small = readConfig({ API_KEY_SHA256: config.apiKeySha256, MAX_IMAGE_BYTES: "1000" });
  const { result, body } = await call(centralEvent(), { handlerConfig: small });
  assert.equal(result.statusCode, 413);
  assert.equal(body.error, "image_too_large");
});

test("the route is POST /v1/detect, by API Gateway's fields or by routeKey", async () => {
  const gateway = { ...centralEvent(), routeKey: undefined,
    requestContext: { http: { method: "POST", path: "/v1/detect" } } };
  assert.equal((await call(gateway)).result.statusCode, 200);
  const get = { ...centralEvent(), routeKey: "GET /v1/detect" };
  assert.equal((await call(get)).result.statusCode, 404);
  const other = { ...centralEvent(), routeKey: "POST /v1/other", rawPath: "/v1/other" };
  assert.equal((await call(other)).result.statusCode, 404);
});

test("an undecodable image is a 400; a model failure is a 503 the caller can fall through on", async () => {
  const undecodable = await call(centralEvent(), {
    scorer: scorerReturning(Object.assign(new Error("undecodable image"), { code: "bad_image" })) });
  assert.equal(undecodable.result.statusCode, 400);
  assert.equal(undecodable.body.error, "bad_image");

  const broken = await call(centralEvent(), { scorer: scorerReturning(new RangeError("arena")) });
  assert.equal(broken.result.statusCode, 503);
  assert.equal(broken.body.error, "inference_unavailable");
  assert.equal(broken.log.error_type, "RangeError");

  const nan = await call(centralEvent(), { scorer: scorerReturning(Number.NaN) });
  assert.equal(nan.result.statusCode, 503);

  const neverLoaded = await call(centralEvent(), {
    scorer: () => Promise.reject(new Error("model.onnx does not match the sha256 in model.json")) });
  assert.equal(neverLoaded.result.statusCode, 503);
  assert.equal(neverLoaded.body.error, "inference_unavailable");
});

test("the log line has the score and timings and nothing of the image or the key", async () => {
  const { log, lines } = await call(centralEvent(), { scorer: scorerReturning(0.31234567) });
  assert.equal(log.event, "screen_request_complete");
  assert.equal(log.request_id, "req-1");
  assert.equal(log.aws_request_id, "aws-1");
  assert.equal(log.status, 200);
  assert.equal(log.outcome, "damaged");
  assert.equal(log.score, 0.31235);
  assert.equal(log.threshold, 0.2);
  assert.equal(log.model_version, "road-screen-test");
  assert.equal(log.decode_ms, 4);
  assert.equal(log.infer_ms, 30);
  assert.equal(typeof log.latency_ms, "number");
  const everything = lines.join("\n");
  assert.ok(!everything.includes(TOKEN));
  assert.ok(!everything.includes(photo.toString("base64").slice(0, 64)));
  assert.ok(everything.length < 600);
});

test("a detector's boxes travel beside the score; a classifier's answer has no boxes field", async () => {
  const boxes = [{ x: 0.45, y: 0.6, w: 0.1, h: 0.05, score: 0.81 }];
  const locator = { meta: { model_version: "pothole-det-test", threshold: 0.3 },
    async score() { return { score: 0.81, boxes, decodeMs: 5, inferMs: 90 }; } };
  const logged = [];
  const located = await createHandler({ config, scorer: locator, log: (line) => logged.push(JSON.parse(line)) })(centralEvent());
  const body = JSON.parse(located.body);
  assert.equal(located.statusCode, 200);
  assert.deepEqual(body.boxes, boxes);
  assert.equal(body.verdict.assessment, "damaged");
  assert.deepEqual(Object.keys(body.verdict).sort(), VERDICT_KEYS);
  assert.equal(logged[0].boxes, 1);

  const plain = await createHandler({ config, scorer: scorerReturning(0.9), log: () => {} })(centralEvent());
  assert.equal("boxes" in JSON.parse(plain.body), false);
});
