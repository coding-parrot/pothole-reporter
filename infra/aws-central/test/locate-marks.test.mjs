import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createDetector } from "../service/detectors.mjs";
import { detectBody, harness, secretFrom, undamaged, upstream } from "./support.mjs";

// The owner (9 Oct 2026): "mark the pothole in the image you take". gpt-5-mini says that a
// frame shows damage, never where. The locator is a box detector in its own Lambda; the
// service asks it at the moment it asks gpt-5-mini and, when gpt-5-mini calls the frame
// damaged, sends the locator's boxes with the answer as `marks`. The promise these cases
// pin: a mark is an extra. Whatever the locator does (nothing, an error, a hang), the
// phone gets today's answer in today's time, and a frame gpt-5-mini clears has no mark.

process.env.SHARED_SECRET_ARN = "arn:aws:secretsmanager:test";

const template = readFileSync(new URL("../template.yaml", import.meta.url), "utf8");
const keys = { openai_api_key: "sk-test-not-a-real-key", yolo_api_key: "screen-test-token" };
const driveBody = { ...detectBody, capture_mode: "drive", capture_source: "drive_live" };
const damaged = { image_quality: "acceptable", assessment: "damaged", damage_type: "pothole_cavity",
  size: "medium", description: "Open cavity at the left road edge." };
const screenFlag = { image_quality: "acceptable", assessment: "damaged", damage_type: "pothole_cavity",
  size: null, description: "The screen scored this frame above its threshold." };
const screenClear = { ...undamaged, description: "The screen scored this frame below its threshold." };
const BOXES = [
  { x: 0.42, y: 0.61, w: 0.12, h: 0.06, score: 0.81 },
  { x: 0.1, y: 0.7, w: 0.05, h: 0.03, score: 0.34 },
  { x: 0.8, y: 0.5, w: 0.04, h: 0.02, score: 0.12 },   // under the mark floor
];

const envelope = (body, statusCode = 200) => ({
  Payload: Buffer.from(JSON.stringify({ statusCode, headers: { "x-request-id": "lambda-req" },
    body: JSON.stringify({ request_id: "lambda-req", ...body }) })),
});

// One fake Lambda client for both functions, told apart by name as production's are.
function lambdas({ screen = null, locate = { boxes: BOXES } } = {}) {
  const calls = [];
  return {
    calls,
    named: (name) => calls.filter((call) => call === name).length,
    async send(command, options) {
      const name = command.input.FunctionName;
      calls.push(name);
      const answer = name.endsWith("-locate") ? locate : screen;
      if (typeof answer === "function") return answer(command, options);
      if (answer instanceof Error) throw answer;
      if (name.endsWith("-locate")) {
        return envelope({ verdict: screenFlag, model: "pothole-det-test", score: 0.81, threshold: 0.3, ...answer });
      }
      return envelope({ verdict: answer.verdict, model: "road-screen-test", score: answer.score, threshold: 0.05 });
    },
  };
}

function openai(verdict, { delayMs = 0 } = {}) {
  const calls = [];
  const respond = upstream(200, { output_text: JSON.stringify(verdict) });
  return { calls, fetchImpl: async (...args) => {
    calls.push(args);
    if (delayMs) await new Promise((resolve) => setTimeout(resolve, delayMs));
    return respond(...args);
  } };
}

async function detect({ mode = "openai_then_yolo", client = lambdas(), ai = openai(damaged), body = detectBody,
  locateFunctionName = "pothole-reporter-central-locate", ...rest } = {}) {
  const h = await harness({ detector: createDetector({
    providerMode: mode, secretProvider: secretFrom(keys), lambdaClient: client, fetchImpl: ai.fetchImpl,
    yoloFunctionName: mode === "openai_then_yolo" ? "" : "pothole-reporter-central-screen",
    locateFunctionName, auditDraw: () => 0.99, ...rest }) });
  const started = Date.now();
  const result = await h.post("/v1/vision/detect", body);
  const log = h.lines.log.map((entry) => JSON.parse(entry))
    .find((entry) => entry.event === "http_request" && entry.route === "/v1/vision/detect");
  return { status: result.statusCode, body: JSON.parse(result.body), log, client, ai, ms: Date.now() - started };
}

const withoutIds = ({ request_id: id, detection_receipt: receipt, detection_receipt_expires_at: expires, marks, ...rest }) => rest;

test("a photo gpt-5-mini calls damaged comes back with the locator's boxes as marks", async () => {
  const marked = await detect();
  assert.equal(marked.status, 200);
  assert.equal(marked.body.assessment, "damaged");
  // Boxes at or over the floor (0.3), best first, as fractions of the frame; no score.
  assert.deepEqual(marked.body.marks, [{ x: 0.42, y: 0.61, w: 0.12, h: 0.06 }, { x: 0.1, y: 0.7, w: 0.05, h: 0.03 }]);
  assert.equal(marked.client.named("pothole-reporter-central-locate"), 1);
  assert.equal(marked.log.locate_boxes, 2);
  assert.equal(marked.log.locate_model, "pothole-det-test");
  assert.equal(typeof marked.log.locate_ms, "number");
  assert.equal(marked.log.locate_error, null);

  // Everything else in the answer is what a service with no locator sends.
  const plain = await detect({ locateFunctionName: "" });
  assert.equal("marks" in plain.body, false);
  assert.equal(plain.client.calls.length, 0);
  assert.deepEqual(withoutIds(marked.body), withoutIds(plain.body));
  assert.equal(plain.log.locate_boxes, null);
});

test("a frame gpt-5-mini clears has no marks, whatever the locator saw", async () => {
  const cleared = await detect({ ai: openai(undamaged) });
  assert.equal(cleared.body.assessment, "undamaged");
  assert.equal("marks" in cleared.body, false);
  assert.equal(cleared.log.locate_boxes, null);
});

test("a locator with nothing over the floor leaves the answer unmarked", async () => {
  const none = await detect({ client: lambdas({ locate: { boxes: [BOXES[2]] } }) });
  assert.equal(none.body.assessment, "damaged");
  assert.equal("marks" in none.body, false);
  assert.equal(none.log.locate_boxes, 0);
});

for (const [label, locate, code] of [
  ["answers 503", () => envelope({ error: "inference_unavailable", message: "x" }, 503), "shared_vision_unavailable"],
  ["cannot be invoked", new Error("AccessDenied"), "shared_vision_unavailable"],
  ["answers with no boxes field (a classifier)", {}, null],
]) {
  test(`a locator that ${label} costs the phone nothing`, async () => {
    const result = await detect({ client: lambdas({ locate: typeof locate === "function" ? locate : locate instanceof Error ? locate : { boxes: undefined, ...locate } }) });
    assert.equal(result.status, 200);
    assert.equal(result.body.assessment, "damaged");
    assert.equal("marks" in result.body, false);
    assert.equal(result.log.locate_error, code);
  });
}

test("a locator that hangs is given up on after the grace, and the answer goes out unmarked", async () => {
  const hung = lambdas({ locate: (command, options) => new Promise((resolve, reject) => {
    options.abortSignal.addEventListener("abort", () => reject(new Error("aborted")));
  }) });
  const result = await detect({ client: hung, locateGraceMs: 40 });
  assert.equal(result.body.assessment, "damaged");
  assert.equal("marks" in result.body, false);
  assert.equal(result.log.locate_error, "locate_timeout");
  assert.ok(result.ms < 1500, `the answer took ${result.ms} ms`);
});

test("the locator is asked at the same moment as gpt-5-mini, not after it", async () => {
  const order = [];
  const client = lambdas({ locate: async () => { order.push("locate"); return envelope({ verdict: screenFlag, model: "m", score: 0.8, boxes: BOXES }); } });
  const ai = openai(damaged, { delayMs: 60 });
  const original = ai.fetchImpl;
  ai.fetchImpl = async (...args) => { const answer = await original(...args); order.push("openai answered"); return answer; };
  const result = await detect({ client, ai });
  assert.deepEqual(order, ["locate", "openai answered"]);
  assert.equal(result.body.marks.length, 2);
});

test("a drive frame the screen clears never reaches the locator", async () => {
  const client = lambdas({ screen: { verdict: screenClear, score: 0.01 } });
  const result = await detect({ mode: "yolo_then_openai", client, body: driveBody });
  assert.equal(result.body.assessment, "undamaged");
  assert.equal(result.client.named("pothole-reporter-central-locate"), 0);
  assert.equal(result.ai.calls.length, 0);
});

test("a drive frame the screen flags and gpt-5-mini confirms is marked", async () => {
  const client = lambdas({ screen: { verdict: screenFlag, score: 0.6 } });
  const result = await detect({ mode: "yolo_then_openai", client, body: driveBody });
  assert.equal(result.body.assessment, "damaged");
  assert.equal(result.body.marks.length, 2);
  assert.equal(result.client.named("pothole-reporter-central-screen"), 1);
  assert.equal(result.client.named("pothole-reporter-central-locate"), 1);
  assert.equal(result.body.detector.screened_by, "yolo");
});

test("boxes that are not boxes are dropped, and at most five marks go out", async () => {
  const many = Array.from({ length: 9 }, (_, i) => ({ x: 0.05 * i, y: 0.5, w: 0.04, h: 0.04, score: 0.9 - i * 0.01 }));
  const junk = [{ x: "a", y: 0, w: 1, h: 1, score: 0.9 }, { x: 0.5, y: 0.5, w: 0, h: 0.1, score: 0.9 },
    { x: 0.9, y: 0.9, w: 0.5, h: 0.5, score: 0.9 }, { x: -0.1, y: 0.2, w: 0.1, h: 0.1, score: 0.9 }, null, 7];
  const result = await detect({ client: lambdas({ locate: { boxes: [...junk, ...many] } }) });
  assert.equal(result.body.marks.length, 5);
  for (const mark of result.body.marks) {
    assert.deepEqual(Object.keys(mark), ["x", "y", "w", "h"]);
    assert.ok(mark.x >= 0 && mark.y >= 0 && mark.x + mark.w <= 1.0001 && mark.y + mark.h <= 1.0001);
  }
});

test("the stack names the locator, lets the function invoke it, and passes the name to it", () => {
  assert.match(template, /\n {2}LocateFunctionName:\n {4}Type: String\n {4}Default: ''/);
  assert.match(template, /HasLocateFunction: !Not \[!Equals \[!Ref LocateFunctionName, ''\]\]/);
  assert.match(template, /Sid: InvokePotholeLocator[\s\S]{0,200}function:\$\{LocateFunctionName\}/);
  assert.match(template, /LOCATE_FUNCTION_NAME: !If \[HasLocateFunction, !Ref LocateFunctionName, !Ref AWS::NoValue\]/);
  const handler = readFileSync(new URL("../service/handler.mjs", import.meta.url), "utf8");
  assert.match(handler, /locateFunctionName: process\.env\.LOCATE_FUNCTION_NAME \|\| ""/);
});
