import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { DETECT_PROMPT_VERSION, DETECT_SCHEMA, DETECT_SCHEMA_VERSION } from "../../../llm/generated/contract.mjs";
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

// ------------------------------------------------------------------ the request line
// One query has to serve shadow mode and this one, so the screen's fields keep their
// shadow names and meanings: screen_assessment, screen_score, screen_ms, screen_model,
// screen_error, and screen_agrees wherever gpt-5-mini also judged the frame. Added here:
// screen_audit_rate on every drive frame of this mode (it is what tells a live line from
// a shadow one), screen_audited on a cleared frame drawn for audit, screen_audit_error
// when that audit could not be made. gpt-5-mini's verdict needs no new field: outcome is
// its verdict exactly when detector_provider is "openai".
const openaiRejected = { image_quality: "rejected", assessment: "undamaged", damage_type: null, size: null, description: "Too dark to judge." };
const screenFields = (log) => Object.fromEntries(["outcome", "detector_provider", "screen_assessment", "screen_score", "screen_error",
  "screen_agrees", "screen_audited", "screen_audit_error", "screen_audit_rate"].map((field) => [field, log[field]]));

for (const [label, options, expected] of [
  ["a cleared frame nobody audited logs the screen and no gpt-5-mini verdict",
    { screen: screenLambda(screenUndamaged, { score: 0.02 }), openai: openai(openaiDamaged), auditDraw: NO_AUDIT },
    { outcome: "undamaged", detector_provider: "yolo", screen_assessment: "undamaged", screen_score: 0.02, screen_error: null,
      screen_agrees: null, screen_audited: null, screen_audit_error: null, screen_audit_rate: 0.1 }],
  ["an audited frame gpt-5-mini also calls undamaged",
    { screen: screenLambda(screenUndamaged, { score: 0.03 }), openai: openai(undamaged), auditDraw: AUDIT },
    { outcome: "undamaged", detector_provider: "openai", screen_assessment: "undamaged", screen_score: 0.03, screen_error: null,
      screen_agrees: true, screen_audited: true, screen_audit_error: null, screen_audit_rate: 0.1 }],
  ["an audited frame gpt-5-mini rejects for quality counts as agreed, as in shadow mode",
    { screen: screenLambda(screenUndamaged, { score: 0.03 }), openai: openai(openaiRejected), auditDraw: AUDIT },
    { outcome: "undamaged", detector_provider: "openai", screen_assessment: "undamaged", screen_score: 0.03, screen_error: null,
      screen_agrees: true, screen_audited: true, screen_audit_error: null, screen_audit_rate: 0.1 }],
  ["an audited frame gpt-5-mini calls damaged is a miss the audit caught",
    { screen: screenLambda(screenUndamaged, { score: 0.04 }), openai: openai(openaiDamaged), auditDraw: AUDIT, screenAuditRate: 0.25 },
    { outcome: "damaged", detector_provider: "openai", screen_assessment: "undamaged", screen_score: 0.04, screen_error: null,
      screen_agrees: false, screen_audited: true, screen_audit_error: null, screen_audit_rate: 0.25 }],
  ["a flagged frame gpt-5-mini confirms",
    { screen: screenLambda(screenDamaged, { score: 0.91 }), openai: openai(openaiDamaged), auditDraw: AUDIT },
    { outcome: "damaged", detector_provider: "openai", screen_assessment: "damaged", screen_score: 0.91, screen_error: null,
      screen_agrees: true, screen_audited: null, screen_audit_error: null, screen_audit_rate: 0.1 }],
  ["a flagged frame gpt-5-mini overrules",
    { screen: screenLambda(screenDamaged, { score: 0.61 }), openai: openai(undamaged), auditDraw: AUDIT },
    { outcome: "undamaged", detector_provider: "openai", screen_assessment: "damaged", screen_score: 0.61, screen_error: null,
      screen_agrees: false, screen_audited: null, screen_audit_error: null, screen_audit_rate: 0.1 }],
  ["a flagged frame left standing by exhausted credit has no gpt-5-mini verdict",
    { screen: screenLambda(screenDamaged, { score: 0.61 }), openai: openai({ error: { code: "insufficient_quota" } }, 429), auditDraw: AUDIT },
    { outcome: "damaged", detector_provider: "yolo", screen_assessment: "damaged", screen_score: 0.61, screen_error: null,
      screen_agrees: null, screen_audited: null, screen_audit_error: null, screen_audit_rate: 0.1 }],
  ["a screen that fails says why, and gpt-5-mini answers",
    { screen: screenLambda(screenUndamaged, { functionError: "Unhandled" }), openai: openai(openaiDamaged), auditDraw: AUDIT },
    { outcome: "damaged", detector_provider: "openai", screen_assessment: null, screen_score: null, screen_error: "shared_vision_unavailable",
      screen_agrees: null, screen_audited: null, screen_audit_error: null, screen_audit_rate: 0.1 }],
  ["an audit that could not be made says so and logs no agreement",
    { screen: screenLambda(screenUndamaged, { score: 0.02 }), openai: openai({ error: { type: "server_error" } }, 500), auditDraw: AUDIT },
    { outcome: "undamaged", detector_provider: "yolo", screen_assessment: "undamaged", screen_score: 0.02, screen_error: null,
      screen_agrees: null, screen_audited: true, screen_audit_error: "shared_vision_unavailable", screen_audit_rate: 0.1 }],
]) {
  test(`the request line: ${label}`, async () => {
    const result = await detectWith(options);
    assert.equal(result.status, 200, JSON.stringify(result.body));
    assert.deepEqual(screenFields(result.log), expected);
    assert.equal(typeof result.log.screen_ms, "number");
    assert.ok(result.log.screen_ms >= 0);
    if (expected.screen_assessment) assert.equal(result.log.screen_model, "road-screen-v2");
    // Never a line that shows a gpt-5-mini verdict the screen's answer could be taken for.
    assert.equal(result.log.screen_agrees !== null, expected.detector_provider === "openai" && expected.screen_assessment !== null);
  });
}

// Before this, a screen answer with no assessment in it was passed on as the answer and
// refused by the service as a 502: the one way a broken screen could fail a drive frame,
// and (not being a 200) a failure the screen's own error count never saw.
test("a screen answer with no assessment in it is a screen error: gpt-5-mini answers and the line says why", async () => {
  const ai = openai(openaiDamaged);
  const result = await detectWith({ screen: screenLambda({ message: "ok" }), openai: ai, auditDraw: NO_AUDIT });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(ai.calls.length, 1);
  assert.equal(result.body.assessment, "damaged");
  assert.equal(result.body.detector.fallback_from, "yolo");
  assert.equal(result.body.detector.fallback_reason, "bad_screen_response");
  assert.equal(result.log.screen_error, "bad_screen_response");
  assert.equal(result.log.screen_assessment, null);
  assert.equal(result.calls.refund, 0);
});

test("a manual photo in this mode is not a screened frame and logs none of it", async () => {
  const result = await detectWith({ screen: screenLambda(screenUndamaged), openai: openai(undamaged), auditDraw: AUDIT, body: detectBody });
  for (const field of ["screen_assessment", "screen_score", "screen_ms", "screen_error", "screen_agrees", "screen_audited", "screen_audit_rate"]) {
    assert.equal(result.log[field], null, field);
  }
});

test("shadow mode logs no audit rate: that is how a query tells a shadow line from a live one", async () => {
  const shadow = await detectWith({ mode: "openai_with_shadow_screen", screen: screenLambda(screenUndamaged), openai: openai(undamaged), auditDraw: AUDIT });
  assert.equal(shadow.log.screen_assessment, "undamaged");
  assert.equal(shadow.log.screen_agrees, true);
  assert.equal(shadow.log.screen_audit_rate, null);
  assert.equal(shadow.log.screen_audited, null);
});

// ------------------------------------------------------------- what the phone is told
// The screen's own sentences are fixed English and ten words long ("The road screen found
// no road damage in this frame."). The contract's description is at most eight words, in
// Kannada when the request asks for it. A frame the screen answers alone is the common
// case in this mode, so its answer has to be one every installed app accepts.
const words = (text) => text.trim().split(/\s+/).length;
const KANNADA = /[ಀ-೿]/;
const withoutIds = ({ request_id: id, detection_receipt: receipt, detection_receipt_expires_at: expires, ...rest }) => rest;

test("the contract still asks for at most eight words", () => {
  assert.match(DETECT_SCHEMA.properties.description.description, /^At most eight words/);
  assert.equal(words(screenUndamaged.description), 10, "the screen's own sentence is the long one these cases start from");
});

for (const language of ["en", "kn", "mr", "bn", undefined]) {
  test(`a cleared frame is a complete, short answer in the contract's shape (language ${language})`, async () => {
    const result = await detectWith({ screen: screenLambda(screenUndamaged), openai: openai(openaiDamaged), auditDraw: NO_AUDIT,
      body: { ...driveBody, ...(language ? { language } : {}) } });
    assert.equal(result.status, 200, JSON.stringify(result.body));
    // The app's own check: every field the schema requires is there.
    for (const field of DETECT_SCHEMA.required) assert.ok(Object.hasOwn(result.body, field), field);
    for (const field of ["image_quality", "assessment", "damage_type", "size"]) {
      assert.ok(DETECT_SCHEMA.properties[field].enum.includes(result.body[field]), field);
    }
    assert.deepEqual([result.body.image_quality, result.body.assessment, result.body.damage_type, result.body.size],
      ["acceptable", "undamaged", null, null]);
    const { description } = result.body;
    assert.ok(description.trim().length > 0);
    assert.ok(words(description) <= 8, description);
    assert.notEqual(description, screenUndamaged.description);
    // Kannada is the one language the prompt asks gpt-5-mini to write in.
    assert.equal(KANNADA.test(description), language === "kn", description);
    assert.equal(result.body.detector.provider, "shared_server");
    assert.equal(result.body.detector.backend_provider, "yolo");
    assert.equal(result.body.detector.model, "road-screen-v2");
    assert.equal(result.body.detector.prompt_version, DETECT_PROMPT_VERSION);
    assert.equal(result.body.detector.schema_version, DETECT_SCHEMA_VERSION);
    assert.equal(result.body.detection_receipt, undefined);
  });
}

for (const language of ["en", "kn"]) {
  test(`a screen verdict left standing by exhausted credit is short too (language ${language})`, async () => {
    const result = await detectWith({ screen: screenLambda(screenDamaged, { score: 0.9 }),
      openai: openai({ error: { code: "insufficient_quota" } }, 429), auditDraw: NO_AUDIT, body: { ...driveBody, language } });
    assert.equal(result.body.assessment, "damaged");
    assert.ok(words(result.body.description) <= 8, result.body.description);
    assert.equal(KANNADA.test(result.body.description), language === "kn");
  });

  test(`a frame the screen rejects for quality is short too (language ${language})`, async () => {
    const result = await detectWith({ screen: screenLambda({ ...screenUndamaged, image_quality: "rejected" }),
      openai: openai(undamaged), auditDraw: NO_AUDIT, body: { ...driveBody, language } });
    assert.equal(result.body.image_quality, "rejected");
    assert.ok(words(result.body.description) <= 8, result.body.description);
    assert.equal(KANNADA.test(result.body.description), language === "kn");
  });
}

// gpt-5-mini sees a frame for one of two reasons: the screen flagged it, or the screen
// cleared it and it was drawn for audit. The phone is told gpt-5-mini answered and never
// which reason it was.
test("an audited frame's response is a flagged frame's response, field for field", async () => {
  for (const verdict of [undamaged, openaiDamaged]) {
    const body = { ...driveBody, client_observation_id: "obs-same" };
    const audited = await detectWith({ screen: screenLambda(screenUndamaged, { score: 0.02 }), openai: openai(verdict), auditDraw: AUDIT, body });
    const flagged = await detectWith({ screen: screenLambda(screenDamaged, { score: 0.9 }), openai: openai(verdict), auditDraw: AUDIT, body });
    assert.deepEqual(withoutIds(audited.body), withoutIds(flagged.body));
    assert.equal(Boolean(audited.body.detection_receipt), Boolean(flagged.body.detection_receipt));
    const { "x-request-id": first, ...auditedHeaders } = audited.headers;
    const { "x-request-id": second, ...flaggedHeaders } = flagged.headers;
    assert.deepEqual(auditedHeaders, flaggedHeaders);
  }
});

test("nothing the phone can read names the audit or its rate", async () => {
  for (const auditDraw of [AUDIT, NO_AUDIT]) {
    const result = await detectWith({ screen: screenLambda(screenUndamaged), openai: openai(undamaged), auditDraw });
    assert.doesNotMatch(JSON.stringify([result.body, result.headers]), /audit/i);
    assert.doesNotMatch(JSON.stringify(result.body), /0\.1\b/);
  }
  const h = await harness({ detector: detectorWith({ screen: screenLambda(screenUndamaged), openai: openai(undamaged) }) });
  const health = await h.handle({ rawPath: "/v1/health", requestContext: { http: { method: "GET" } } });
  assert.equal(health.statusCode, 200);
  assert.doesNotMatch(health.body, /audit/i);
});
