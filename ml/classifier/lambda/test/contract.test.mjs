import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { createDetector } from "../../../../infra/aws-central/service/detectors.mjs";
import { detectBody, harness, secretFrom, undamaged } from "../../../../infra/aws-central/test/support.mjs";
import { createHandler, readConfig } from "../service.mjs";

// The central service's real detector code invoking the screen's real handler. Only the
// model (a fixed score) and the two network hops (the Lambda invoke, the OpenAI call)
// are stand-ins, so a contract drift on either side fails here.

process.env.SHARED_SECRET_ARN = "arn:aws:secretsmanager:test";

const TOKEN = "screen-contract-token";
const keys = { openai_api_key: "sk-test-not-a-real-key", yolo_api_key: TOKEN };
const driveBody = { ...detectBody, capture_mode: "drive", capture_source: "drive_live" };
const openaiDamaged = {
  image_quality: "acceptable", assessment: "damaged", damage_type: "surface_breakup",
  size: "large", description: "Broken asphalt across the lane.",
};

function screenBehindInvoke(score, token = TOKEN) {
  const handle = createHandler({
    config: readConfig({ API_KEY_SHA256: createHash("sha256").update(token).digest("hex") }),
    scorer: { meta: { model_version: "road-screen-test", threshold: 0.2 },
      async score() { return { score, decodeMs: 3, inferMs: 25 }; } },
    log: () => {},
  });
  const invocations = [];
  return {
    invocations,
    async send(command) {
      invocations.push(command.input.FunctionName);
      const event = JSON.parse(Buffer.from(command.input.Payload).toString("utf8"));
      return { Payload: Buffer.from(JSON.stringify(await handle(event, {}))) };
    },
  };
}

function openai(verdict) {
  const calls = [];
  return {
    calls,
    fetchImpl: async (...args) => {
      calls.push(args);
      return new Response(JSON.stringify({ output_text: JSON.stringify(verdict) }),
        { status: 200, headers: { "content-type": "application/json" } });
    },
  };
}

async function detect(mode, screen, ai) {
  const h = await harness({
    detector: createDetector({
      providerMode: mode,
      secretProvider: secretFrom(keys),
      yoloFunctionName: "pothole-reporter-central-screen",
      lambdaClient: screen,
      fetchImpl: ai.fetchImpl,
    }),
  });
  const result = await h.post("/v1/vision/detect", driveBody);
  const log = h.lines.log.map((line) => JSON.parse(line))
    .find((line) => line.event === "http_request" && line.route === "/v1/vision/detect");
  return { status: result.statusCode, body: JSON.parse(result.body), log };
}

test("yolo_then_openai: a frame the screen clears is answered by the screen alone", async () => {
  const screen = screenBehindInvoke(0.03);
  const ai = openai(openaiDamaged);
  const result = await detect("yolo_then_openai", screen, ai);
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.deepEqual(screen.invocations, ["pothole-reporter-central-screen"]);
  assert.equal(ai.calls.length, 0);
  assert.equal(result.body.assessment, "undamaged");
  assert.equal(result.body.damage_type, null);
  assert.equal(result.body.detector.backend_provider, "yolo");
  assert.equal(result.body.detector.model, "road-screen-test");
  assert.equal(result.body.detector.fallback_from, undefined);
});

test("yolo_then_openai: a frame the screen flags goes to gpt-5-mini, whose verdict is returned", async () => {
  const ai = openai(openaiDamaged);
  const result = await detect("yolo_then_openai", screenBehindInvoke(0.8), ai);
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(ai.calls.length, 1);
  assert.equal(result.body.damage_type, "surface_breakup");
  assert.equal(result.body.detector.backend_provider, "openai");
  assert.equal(result.body.detector.screened_by, "yolo");
  assert.equal(result.body.detector.screen_model, "road-screen-test");
  assert.equal(result.log.detector_screen_confirmed, true);
});

test("yolo_then_openai: a screen holding another key falls through to gpt-5-mini", async () => {
  const ai = openai(openaiDamaged);
  const result = await detect("yolo_then_openai", screenBehindInvoke(0.01, "another-key"), ai);
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(ai.calls.length, 1);
  assert.equal(result.body.assessment, "damaged");
  assert.equal(result.body.detector.fallback_from, "yolo");
});

test("openai_with_shadow_screen: gpt-5-mini answers and the log carries the screen's score", async () => {
  const ai = openai(undamaged);
  const result = await detect("openai_with_shadow_screen", screenBehindInvoke(0.4567), ai);
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(ai.calls.length, 1);
  assert.equal(result.body.assessment, "undamaged");
  assert.equal(result.body.detector.backend_provider, "openai");
  assert.equal(result.log.screen_score, 0.4567);
  assert.equal(result.log.screen_assessment, "damaged");
  assert.equal(result.log.screen_agrees, false);
  assert.equal(result.log.screen_model, "road-screen-test");
  assert.equal(result.log.screen_error, null);
});
