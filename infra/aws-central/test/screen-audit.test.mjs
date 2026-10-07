import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { DEFAULT_SCREEN_AUDIT_RATE, auditRate, createDetector } from "../service/detectors.mjs";
import { detectBody, harness, secretFrom, undamaged, upstream } from "./support.mjs";

// yolo_then_openai answers a drive frame the screen clears without asking gpt-5-mini, so
// from the day it is switched on the screen's misses are invisible: nobody judges a
// cleared frame. The first screen flagged 94% of damaged frames in testing and 76% on
// real phones, so the measuring cannot stop at the switch. A share of the cleared frames
// (ScreenAuditRate, 0.1) still goes to gpt-5-mini, whose answer the phone gets. These
// cases pin that sample: how it is drawn, what the phone receives, what it costs.

process.env.SHARED_SECRET_ARN = "arn:aws:secretsmanager:test";

const keys = { openai_api_key: "sk-test-not-a-real-key", yolo_api_key: "screen-test-token" };
const driveBody = { ...detectBody, capture_mode: "drive", capture_source: "drive_live" };
const screenDamaged = {
  image_quality: "acceptable", assessment: "damaged", damage_type: "pothole_cavity", size: null,
  description: "The road screen scored this frame as likely road damage.",
};
const screenUndamaged = { ...undamaged, description: "The road screen found no road damage in this frame." };
const openaiDamaged = {
  image_quality: "acceptable", assessment: "damaged", damage_type: "surface_breakup", size: "large",
  description: "Broken asphalt across the lane.",
};
const AUDIT = () => 0;
const NO_AUDIT = () => 0.999;

// The screen Lambda as detectors.mjs invokes it, with the raw score beside the verdict.
function screenLambda(answer, { score = 0.02, statusCode = 200, functionError = null } = {}) {
  const calls = [];
  return {
    calls,
    async send(command, options) {
      calls.push({ input: command.input, options });
      if (typeof answer === "function") return answer(command, options);
      return {
        FunctionError: functionError,
        Payload: Buffer.from(JSON.stringify({
          statusCode,
          headers: { "x-request-id": "screen-req-1" },
          body: JSON.stringify(statusCode === 200
            ? { request_id: "screen-req-1", verdict: answer, model: "road-screen-v2", score, threshold: 0.0549 }
            : { request_id: "screen-req-1", ...answer }),
        })),
      };
    },
  };
}

function openai(verdict, status = 200) {
  const calls = [];
  const respond = upstream(status, status === 200 ? { output_text: JSON.stringify(verdict) } : verdict);
  const fetchImpl = async (...args) => { calls.push(args); return respond(...args); };
  return { calls, fetchImpl };
}

const detectorWith = ({ screen, openai: ai, mode = "yolo_then_openai", ...rest }) => createDetector({
  providerMode: mode,
  secretProvider: secretFrom(keys),
  yoloFunctionName: "pothole-reporter-central-screen",
  lambdaClient: screen,
  fetchImpl: ai.fetchImpl,
  ...rest,
});

async function detectWith({ body = driveBody, postOptions, ...rest }) {
  const h = await harness({ detector: detectorWith(rest) });
  const result = await h.post("/v1/vision/detect", body, postOptions);
  const line = h.lines.log.map((entry) => JSON.parse(entry))
    .find((entry) => entry.event === "http_request" && entry.route === "/v1/vision/detect");
  return { status: result.statusCode, headers: result.headers, body: JSON.parse(result.body), calls: h.repository.calls, log: line };
}

// The detector alone, with no service around it: what a count over many frames needs.
const frame = { images: [{ dataUrl: driveBody.images[0] }], captureMode: "drive", language: "en" };
let serial = 0;
const judge = (detector, input = frame) => detector.detect(input, { requestId: `req-${serial += 1}` });

test("a cleared frame drawn for audit goes to gpt-5-mini, whose verdict is the answer, even when it says damaged", async () => {
  const screen = screenLambda(screenUndamaged);
  const ai = openai(openaiDamaged);
  const result = await detectWith({ screen, openai: ai, auditDraw: AUDIT,
    body: { ...driveBody, client_observation_id: "obs-audit" } });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(screen.calls.length, 1);
  assert.equal(ai.calls.length, 1);
  assert.equal(result.body.assessment, "damaged");
  assert.equal(result.body.damage_type, "surface_breakup");
  assert.equal(result.body.size, "large");
  assert.equal(result.body.description, "Broken asphalt across the lane.");
  assert.equal(result.body.detector.backend_provider, "openai");
  assert.equal(result.body.detector.model, "gpt-5-mini");
  assert.ok(result.body.detection_receipt, "a pothole the audit caught earns a receipt like any other");
});

test("a cleared frame not drawn for audit makes no OpenAI call", async () => {
  const screen = screenLambda(screenUndamaged);
  const ai = openai(openaiDamaged);
  const result = await detectWith({ screen, openai: ai, auditDraw: NO_AUDIT });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(ai.calls.length, 0);
  assert.equal(result.body.assessment, "undamaged");
  assert.equal(result.body.detector.backend_provider, "yolo");
});

test("the draw decides: under the rate the frame is audited, at or over it the frame is not", async () => {
  for (const [rate, draw, audited] of [[0.1, 0.0999, true], [0.1, 0.1, false], [0.1, 0.5, false],
    [0, 0, false], [1, 0.999999, true], [0.25, 0.2, true]]) {
    const ai = openai(undamaged);
    await judge(detectorWith({ screen: screenLambda(screenUndamaged), openai: ai, screenAuditRate: rate, auditDraw: () => draw }));
    assert.equal(ai.calls.length, audited ? 1 : 0, `rate ${rate}, draw ${draw}`);
  }
});

test("a scripted run of draws audits exactly the frames it names", async () => {
  const draws = [0.5, 0.05, 0.9, 0.099, 0.1, 0.0, 0.7, 0.31];
  const ai = openai(undamaged);
  const detector = detectorWith({ screen: screenLambda(screenUndamaged), openai: ai, screenAuditRate: 0.1,
    auditDraw: () => draws.shift() });
  const audited = [];
  for (let index = 0; index < 8; index += 1) {
    const before = ai.calls.length;
    await judge(detector);
    audited.push(ai.calls.length > before);
  }
  assert.deepEqual(audited, [false, true, false, true, false, true, false, false]);
});

test("with no draw injected, one cleared frame in ten is audited over many frames", async () => {
  const ai = openai(undamaged);
  const detector = detectorWith({ screen: screenLambda(screenUndamaged), openai: ai });
  const frames = 3000;
  for (let index = 0; index < frames; index += 1) await judge(detector);
  // 300 expected, standard deviation 16.4: five of them either side.
  assert.ok(ai.calls.length > 218 && ai.calls.length < 382, `${ai.calls.length} of ${frames} audited`);
});

test("the draw is made once for a cleared frame and never for a flagged one or a manual photo", async () => {
  let draws = 0;
  const draw = () => { draws += 1; return 0.999; };
  await judge(detectorWith({ screen: screenLambda(screenUndamaged), openai: openai(undamaged), auditDraw: draw }));
  assert.equal(draws, 1);
  await judge(detectorWith({ screen: screenLambda(screenDamaged, { score: 0.9 }), openai: openai(openaiDamaged), auditDraw: draw }));
  await judge(detectorWith({ screen: screenLambda(screenUndamaged), openai: openai(undamaged), auditDraw: draw }),
    { ...frame, captureMode: "manual" });
  assert.equal(draws, 1);
});

test("the rate is a number from 0 to 1: unset is the default, and a value that cannot be read audits every frame", () => {
  assert.equal(DEFAULT_SCREEN_AUDIT_RATE, 0.1);
  for (const unset of [undefined, null, "", "  "]) assert.equal(auditRate(unset), 0.1);
  for (const [given, rate] of [["0", 0], ["0.1", 0.1], ["1", 1], [0.25, 0.25], ["0.05", 0.05]]) {
    assert.equal(auditRate(given), rate);
  }
  // A typo must not switch the measuring off. Auditing everything costs what production
  // costs today and misses nothing.
  for (const unreadable of ["ten", "-0.1", "1.5", "10%", NaN, Infinity, "0.1.0"]) {
    assert.equal(auditRate(unreadable), 1, String(unreadable));
  }
});

test("a detector built with an unreadable rate audits every cleared frame", async () => {
  const ai = openai(undamaged);
  const detector = detectorWith({ screen: screenLambda(screenUndamaged), openai: ai, screenAuditRate: "lots" });
  for (let index = 0; index < 5; index += 1) await judge(detector);
  assert.equal(ai.calls.length, 5);
});

test("a screen that fails still falls through to gpt-5-mini, audit or no audit", async () => {
  for (const auditDraw of [AUDIT, NO_AUDIT]) {
    const ai = openai(openaiDamaged);
    const result = await detectWith({ screen: screenLambda(screenUndamaged, { functionError: "Unhandled" }), openai: ai, auditDraw });
    assert.equal(result.status, 200, JSON.stringify(result.body));
    assert.equal(ai.calls.length, 1);
    assert.equal(result.body.assessment, "damaged");
    assert.equal(result.body.detector.fallback_from, "yolo");
    assert.equal(result.body.detector.fallback_reason, "shared_vision_unavailable");
  }
});

// The audit is a measurement. When gpt-5-mini cannot be asked, the frame is answered as
// every unaudited cleared frame is, and the phone is not handed an error for it.
for (const [label, verdict, status, code] of [
  ["exhausted credit", { error: { code: "insufficient_quota" } }, 429, "shared_credits_exhausted"],
  ["a rate limit", { error: { code: "rate_limit_exceeded" } }, 429, "shared_rate_limit"],
  ["an OpenAI 500", { error: { type: "server_error" } }, 500, "shared_vision_unavailable"],
]) {
  test(`an audit that meets ${label} leaves the screen's answer standing and refunds nothing`, async () => {
    const ai = openai(verdict, status);
    const audited = await detectWith({ screen: screenLambda(screenUndamaged), openai: ai, auditDraw: AUDIT });
    const plain = await detectWith({ screen: screenLambda(screenUndamaged), openai: openai(undamaged), auditDraw: NO_AUDIT });
    assert.equal(audited.status, 200, JSON.stringify(audited.body));
    assert.equal(ai.calls.length, 1);
    const { request_id: first, ...auditedBody } = audited.body;
    const { request_id: second, ...plainBody } = plain.body;
    assert.deepEqual(auditedBody, plainBody);
    assert.equal(audited.calls.refund, 0);
    assert.equal(audited.log.screen_audit_error, code);
  });
}

test("every path costs one unit of the install's allowance, and none is refunded", async () => {
  const paths = {
    "cleared, not audited": { screen: screenLambda(screenUndamaged), openai: openai(undamaged), auditDraw: NO_AUDIT },
    "cleared, audited, undamaged": { screen: screenLambda(screenUndamaged), openai: openai(undamaged), auditDraw: AUDIT },
    "cleared, audited, damaged": { screen: screenLambda(screenUndamaged), openai: openai(openaiDamaged), auditDraw: AUDIT },
    "flagged": { screen: screenLambda(screenDamaged, { score: 0.9 }), openai: openai(openaiDamaged), auditDraw: AUDIT },
    "screen failed": { screen: screenLambda(screenUndamaged, { functionError: "Unhandled" }), openai: openai(undamaged), auditDraw: AUDIT },
    "manual photo": { screen: screenLambda(screenUndamaged), openai: openai(undamaged), auditDraw: AUDIT, body: detectBody },
  };
  for (const [label, options] of Object.entries(paths)) {
    const result = await detectWith(options);
    assert.equal(result.status, 200, label);
    assert.equal(result.calls.take, 1, label);
    assert.equal(result.calls.refund, 0, label);
  }
});

test("a flagged frame whose gpt-5-mini call fails is still an error and refunds the unit, as before the audit", async () => {
  const result = await detectWith({ screen: screenLambda(screenDamaged, { score: 0.9 }),
    openai: openai({ error: { type: "server_error" } }, 500), auditDraw: AUDIT });
  assert.equal(result.body.error, "shared_vision_unavailable");
  assert.equal(result.calls.take, 1);
  assert.equal(result.calls.refund, 1);
});

test("no other order draws for an audit", async () => {
  for (const mode of ["openai", "openai_then_yolo", "openai_with_shadow_screen"]) {
    let draws = 0;
    const ai = openai(undamaged);
    await judge(detectorWith({ mode, screen: screenLambda(screenUndamaged), openai: ai, auditDraw: () => { draws += 1; return 0; } }));
    assert.equal(draws, 0, mode);
    assert.equal(ai.calls.length, 1, mode);
  }
});

// ------------------------------------------------------------ the stack and the deploy
const template = readFileSync(new URL("../template.yaml", import.meta.url), "utf8");
const deploy = readFileSync(new URL("../deploy.sh", import.meta.url), "utf8");
const handler = readFileSync(new URL("../service/handler.mjs", import.meta.url), "utf8");

test("the stack takes ScreenAuditRate, a number from 0 to 1 that defaults to the service's own default", () => {
  const parameter = template.match(/\n  ScreenAuditRate:\n    Type: Number\n    Default: (\S+)\n    MinValue: (\S+)\n    MaxValue: (\S+)\n/);
  assert.ok(parameter, "ScreenAuditRate is not declared as a bounded number with a default");
  assert.deepEqual(parameter.slice(1).map(Number), [DEFAULT_SCREEN_AUDIT_RATE, 0, 1]);
  assert.match(template, /SCREEN_AUDIT_RATE: !Ref ScreenAuditRate/);
  assert.match(handler, /screenAuditRate: process\.env\.SCREEN_AUDIT_RATE,/);
});

// CloudFormation keeps a stack's old value for a parameter that is not passed again, so
// a rate decided in template.yaml reaches the stack only if deploy.sh passes it.
test("deploy.sh passes ScreenAuditRate from the template on every deploy", () => {
  const loop = deploy.match(/for name in ([^;]*); do/);
  assert.ok(loop, "the explicit parameter loop was not found");
  assert.ok(loop[1].split(/\s+/).includes("ScreenAuditRate"));
  // The script's own reader, run on the real template: what the loop will pass.
  const reader = deploy.match(/template_default\(\) \{\n[^}]*\n\}/);
  assert.ok(reader, "template_default was not found");
  const read = execFileSync("bash", ["-c", `${reader[0]}\ntemplate_default ScreenAuditRate`],
    { cwd: fileURLToPath(new URL("../../..", import.meta.url)), encoding: "utf8" }).trim();
  assert.equal(Number(read), DEFAULT_SCREEN_AUDIT_RATE);
});

// The template's default order is openai_then_yolo and production runs another. The loop
// passes template defaults, so the order must stay out of it: a routine deploy would
// otherwise change which model answers users.
test("a routine deploy does not choose the detection order or the screen function", () => {
  const names = deploy.match(/for name in ([^;]*); do/)[1].split(/\s+/);
  assert.ok(!names.includes("SharedDetectorProvider"));
  assert.ok(!names.includes("YoloFunctionName"));
});
