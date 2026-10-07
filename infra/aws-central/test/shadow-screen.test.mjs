import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { PROVIDER_MODES, createDetector } from "../service/detectors.mjs";
import {
  SHADOW_QUERY, reportShadowScreen, shadowScreenCurve,
} from "../service/health/rules.mjs";
import { createReport } from "../service/health/report.mjs";
import { judgeWindow } from "../service/health/window.mjs";
import { HEALTHY_WINDOW, scriptedQuery } from "./health-support.mjs";
import { detectBody, harness, secretFrom, undamaged, upstream } from "./support.mjs";

// openai_with_shadow_screen proves the fast screen on live drive frames before it is
// allowed to decide anything. gpt-5-mini answers every frame exactly as it does today;
// the screen runs beside it, and the request log records what the screen would have
// said. These cases pin the one promise that makes shadow mode safe to deploy: whatever
// the screen does (answers, disagrees, fails, hangs), the phone gets today's answer in
// today's time.

process.env.SHARED_SECRET_ARN = "arn:aws:secretsmanager:test";

const template = readFileSync(new URL("../template.yaml", import.meta.url), "utf8");
const keys = { openai_api_key: "sk-test-not-a-real-key", yolo_api_key: "screen-test-token" };
const driveBody = { ...detectBody, capture_mode: "drive", capture_source: "drive_live" };
const screenDamaged = {
  image_quality: "acceptable",
  assessment: "damaged",
  damage_type: "pothole_cavity",
  size: null,
  description: "The screen scored this frame above its threshold.",
};
const screenUndamaged = { ...undamaged, description: "The screen scored this frame below its threshold." };
const openaiDamaged = {
  image_quality: "acceptable",
  assessment: "damaged",
  damage_type: "surface_breakup",
  size: "large",
  description: "Broken asphalt across the lane.",
};
const openaiRejected = {
  image_quality: "rejected",
  assessment: "undamaged",
  damage_type: null,
  size: null,
  description: "Too dark to judge.",
};

// The screen Lambda as detectors.mjs invokes it: the envelope the screen handler
// returns, with the raw score beside the verdict.
function screenLambda(answer, { score = 0.5, statusCode = 200, functionError = null } = {}) {
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
            ? { request_id: "screen-req-1", verdict: answer, model: "road-screen-v1", score, threshold: 0.2 }
            : { request_id: "screen-req-1", ...answer }),
        })),
      };
    },
  };
}

const envelope = (verdict, score) => ({
  Payload: Buffer.from(JSON.stringify({
    statusCode: 200,
    headers: { "x-request-id": "screen-req-1" },
    body: JSON.stringify({ request_id: "screen-req-1", verdict, model: "road-screen-v1", score }),
  })),
});

function openai(verdict, status = 200) {
  const calls = [];
  const respond = upstream(status, status === 200 ? { output_text: JSON.stringify(verdict) } : verdict);
  const fetchImpl = async (...args) => { calls.push(args); return respond(...args); };
  return { calls, fetchImpl };
}

async function detectWith({ mode = "openai_with_shadow_screen", screen, openai: ai, body = driveBody,
  secret = keys, functionName = "pothole-reporter-central-screen", postOptions, ...rest } = {}) {
  const h = await harness({
    detector: createDetector({
      providerMode: mode,
      secretProvider: secretFrom(secret),
      yoloFunctionName: functionName,
      lambdaClient: screen,
      fetchImpl: ai.fetchImpl,
      ...rest,
    }),
  });
  const started = Date.now();
  const result = await h.post("/v1/vision/detect", body, postOptions);
  const line = h.lines.log.map((entry) => JSON.parse(entry))
    .find((entry) => entry.event === "http_request" && entry.route === "/v1/vision/detect");
  return {
    status: result.statusCode,
    body: JSON.parse(result.body),
    calls: h.repository.calls,
    log: line,
    errors: h.lines.error,
    ms: Date.now() - started,
  };
}

// What production answers today: openai_then_yolo with no YOLO function named.
const today = (ai, extra = {}) => detectWith({
  mode: "openai_then_yolo", functionName: "", screen: screenLambda(screenDamaged), openai: ai, ...extra,
});

// Everything the phone receives, without the request id and the receipt keyed on it.
function answer(result) {
  const { request_id: id, detection_receipt: receipt, detection_receipt_expires_at: expires,
    ...rest } = result.body;
  return { status: result.status, body: rest, hasReceipt: Boolean(receipt), hasId: Boolean(id) };
}

test("the mode list adds the shadow screen after the four existing orders", () => {
  assert.deepEqual([...PROVIDER_MODES], [
    "openai", "yolo", "openai_then_yolo", "yolo_then_openai", "openai_with_shadow_screen",
  ]);
});

for (const [label, screenVerdict, score, openaiVerdict, agrees] of [
  ["both call the frame damaged", screenDamaged, 0.93, openaiDamaged, true],
  ["both call the frame undamaged", screenUndamaged, 0.02, undamaged, true],
  ["the screen misses damage OpenAI sees", screenUndamaged, 0.04, openaiDamaged, false],
  ["the screen flags a frame OpenAI clears", screenDamaged, 0.71, undamaged, false],
  ["the screen flags a frame OpenAI rejects for quality", screenDamaged, 0.66, openaiRejected, false],
]) {
  test(`${label}: the phone gets today's answer and the log records the screen`, async () => {
    const body = { ...driveBody, client_observation_id: "obs-shadow" };
    const screen = screenLambda(screenVerdict, { score });
    const ai = openai(openaiVerdict);
    const shadow = await detectWith({ screen, openai: ai, body });
    const baseline = await today(openai(openaiVerdict), { body });
    assert.equal(shadow.status, 200, JSON.stringify(shadow.body));
    assert.deepEqual(answer(shadow), answer(baseline));
    assert.equal(shadow.body.detector.backend_provider, "openai");
    assert.equal(shadow.body.detector.screened_by, undefined);
    assert.equal(shadow.body.detector.fallback_from, undefined);
    assert.equal(ai.calls.length, 1);
    assert.equal(screen.calls.length, 1);
    assert.equal(shadow.log.outcome, openaiVerdict.assessment);
    assert.equal(shadow.log.detector_provider, "openai");
    assert.equal(shadow.log.screen_score, score);
    assert.equal(shadow.log.screen_assessment, screenVerdict.assessment);
    assert.equal(shadow.log.screen_agrees, agrees);
    assert.equal(shadow.log.screen_error, null);
    assert.equal(shadow.log.screen_model, "road-screen-v1");
    assert.equal(typeof shadow.log.screen_ms, "number");
    assert.ok(shadow.log.screen_ms >= 0);
    assert.equal(shadow.log.yolo_request_id, "screen-req-1");
    // Shadow mode screens nothing: the fields of yolo_then_openai stay empty.
    assert.equal(shadow.log.detector_screened_by, null);
    assert.equal(shadow.log.detector_screen_confirmed, null);
    assert.equal(shadow.log.detector_fallback_reason, null);
    assert.equal(shadow.calls.take, 1);
    assert.equal(shadow.calls.refund, 0);
    assert.deepEqual(shadow.errors, []);
  });
}

for (const [label, make, reason] of [
  ["no screen function configured", () => ({ screen: screenLambda(screenDamaged), functionName: "" }),
    "shared_yolo_not_configured"],
  ["no yolo_api_key in the secret", () => ({ screen: screenLambda(screenDamaged),
    secret: { openai_api_key: keys.openai_api_key } }), "shared_yolo_not_configured"],
  ["a screen Lambda that errors", () => ({ screen: screenLambda(screenDamaged, { functionError: "Unhandled" }) }),
    "shared_vision_unavailable"],
  ["a screen Lambda answering 503", () => ({ screen: screenLambda({ error: "inference_unavailable" },
    { statusCode: 503 }) }), "shared_vision_unavailable"],
  ["a screen Lambda answering 401", () => ({ screen: screenLambda({ error: "unauthorized" },
    { statusCode: 401 }) }), "shared_vision_unavailable"],
  ["an invoke the SDK rejects", () => ({ screen: screenLambda(async () => {
    throw Object.assign(new Error("AccessDeniedException"), { name: "AccessDeniedException" });
  }) }), "shared_vision_unavailable"],
  ["a screen whose payload is not JSON", () => ({ screen: screenLambda(async () => ({
    Payload: Buffer.from("<html>502</html>"),
  })) }), "screen_failed"],
  ["a screen that answers no verdict", () => ({ screen: screenLambda(async () => envelope({}, 0.4)) }),
    "bad_screen_response"],
]) {
  test(`${label} changes nothing the phone sees and is logged as ${reason}`, async () => {
    const body = { ...driveBody, client_observation_id: "obs-shadow" };
    const ai = openai(openaiDamaged);
    const shadow = await detectWith({ openai: ai, body, ...make() });
    const baseline = await today(openai(openaiDamaged), { body });
    assert.equal(shadow.status, 200, JSON.stringify(shadow.body));
    assert.deepEqual(answer(shadow), answer(baseline));
    assert.equal(shadow.body.detector.fallback_from, undefined);
    assert.equal(ai.calls.length, 1);
    assert.equal(shadow.log.screen_error, reason);
    assert.equal(shadow.log.screen_assessment, null);
    assert.equal(shadow.log.screen_score, null);
    assert.equal(shadow.log.screen_agrees, null);
    assert.equal(shadow.log.detector_fallback_reason, null);
    assert.equal(shadow.calls.refund, 0);
    assert.deepEqual(shadow.errors, []);
  });
}

test("the screen and OpenAI are in flight together, neither waits for the other to start", async () => {
  // Each side answers only once the other has been called. Run one after the other and
  // this never finishes.
  let screenCalled;
  let openaiCalled;
  const screenStarted = new Promise((resolve) => { screenCalled = resolve; });
  const openaiStarted = new Promise((resolve) => { openaiCalled = resolve; });
  const screen = screenLambda(async () => {
    screenCalled();
    await openaiStarted;
    return envelope(screenDamaged, 0.9);
  });
  const fetchImpl = async () => {
    openaiCalled();
    await screenStarted;
    return new Response(JSON.stringify({ output_text: JSON.stringify(openaiDamaged) }),
      { status: 200, headers: { "content-type": "application/json" } });
  };
  const shadow = await detectWith({ screen, openai: { fetchImpl }, shadowGraceMs: 2_000 });
  assert.equal(shadow.status, 200, JSON.stringify(shadow.body));
  assert.equal(shadow.log.screen_assessment, "damaged");
  assert.equal(shadow.log.screen_agrees, true);
});

test("a hanging screen is abandoned once OpenAI has answered and the grace has passed", async () => {
  let aborted = false;
  const screen = screenLambda((command, { abortSignal }) => new Promise((resolve, reject) => {
    abortSignal.addEventListener("abort", () => { aborted = true; reject(abortSignal.reason); });
  }));
  const ai = openai(openaiDamaged);
  const body = { ...driveBody, client_observation_id: "obs-shadow" };
  // The screen's own budget is far away, so only the grace can end the wait.
  const shadow = await detectWith({ screen, openai: ai, body, yoloScreenTimeoutMs: 60_000,
    shadowGraceMs: 20 });
  const baseline = await today(openai(openaiDamaged), { body });
  assert.equal(shadow.status, 200, JSON.stringify(shadow.body));
  assert.deepEqual(answer(shadow), answer(baseline));
  assert.ok(shadow.ms < 5_000, `took ${shadow.ms} ms`);
  assert.equal(aborted, true, "the invoke is cancelled, not left running");
  assert.equal(shadow.log.screen_error, "screen_timeout");
  assert.equal(shadow.log.screen_assessment, null);
  assert.equal(shadow.log.screen_score, null);
  assert.equal(shadow.log.screen_agrees, null);
  assert.equal(typeof shadow.log.screen_ms, "number");
  assert.deepEqual(shadow.errors, []);
});

test("the grace in production is 50 ms", async () => {
  const source = readFileSync(new URL("../service/detectors.mjs", import.meta.url), "utf8");
  assert.match(source, /const SHADOW_SCREEN_GRACE_MS = 50;/);
  assert.match(source, /shadowGraceMs = SHADOW_SCREEN_GRACE_MS/);
});

test("a screen that answers after OpenAI but inside the grace is still recorded", async () => {
  let release;
  const openaiAnswered = new Promise((resolve) => { release = resolve; });
  const screen = screenLambda(async () => {
    await openaiAnswered;
    await new Promise((resolve) => setTimeout(resolve, 5));
    return envelope(screenUndamaged, 0.03);
  });
  const fetchImpl = async () => {
    const response = new Response(JSON.stringify({ output_text: JSON.stringify(openaiDamaged) }),
      { status: 200, headers: { "content-type": "application/json" } });
    release();
    return response;
  };
  const shadow = await detectWith({ screen, openai: { fetchImpl }, shadowGraceMs: 2_000 });
  assert.equal(shadow.status, 200, JSON.stringify(shadow.body));
  assert.equal(shadow.body.assessment, "damaged");
  assert.equal(shadow.log.screen_assessment, "undamaged");
  assert.equal(shadow.log.screen_agrees, false);
  assert.equal(shadow.log.screen_error, null);
});

for (const [label, verdict, status] of [
  ["an OpenAI 500", { error: { type: "server_error" } }, 500],
  ["an OpenAI rate limit", { error: { code: "rate_limit_exceeded" } }, 429],
  ["exhausted OpenAI credit", { error: { code: "insufficient_quota" } }, 429],
  ["a rejected OpenAI key", { error: { code: "invalid_api_key" } }, 401],
]) {
  test(`${label} is today's error in shadow mode: the screen never becomes the verdict`, async () => {
    const screen = screenLambda(screenDamaged, { score: 0.99 });
    const shadow = await detectWith({ screen, openai: openai(verdict, status) });
    const baseline = await today(openai(verdict, status));
    assert.deepEqual(answer(shadow), answer(baseline));
    assert.notEqual(shadow.status, 200);
    assert.equal(shadow.calls.refund, baseline.calls.refund);
    // What the screen said is still worth having beside the failure.
    assert.equal(shadow.log.screen_assessment, "damaged");
    assert.equal(shadow.log.screen_score, 0.99);
    assert.equal(shadow.log.screen_agrees, null);
  });
}

test("a manual photo is never sent to the shadow screen", async () => {
  const screen = screenLambda(screenDamaged);
  const ai = openai(undamaged);
  const shadow = await detectWith({ screen, openai: ai, body: detectBody });
  const baseline = await today(openai(undamaged), { body: detectBody });
  assert.equal(shadow.status, 200, JSON.stringify(shadow.body));
  assert.deepEqual(answer(shadow), answer(baseline));
  assert.equal(screen.calls.length, 0);
  assert.equal(ai.calls.length, 1);
  for (const field of ["screen_score", "screen_assessment", "screen_ms", "screen_agrees", "screen_error"]) {
    assert.equal(shadow.log[field], null, field);
  }
});

test("the shadow request is the screen contract with the screen token, never the OpenAI key", async () => {
  const screen = screenLambda(screenUndamaged);
  const shadow = await detectWith({ screen, openai: openai(undamaged) });
  assert.equal(shadow.status, 200);
  const { input, options } = screen.calls[0];
  assert.equal(input.FunctionName, "pothole-reporter-central-screen");
  assert.equal(input.InvocationType, "RequestResponse");
  const serialised = Buffer.from(input.Payload).toString("utf8");
  assert.ok(!serialised.includes("sk-test-not-a-real-key"));
  const event = JSON.parse(serialised);
  assert.equal(event.rawPath, "/v1/detect");
  assert.equal(event.headers["x-yolo-api-key"], "screen-test-token");
  const payload = JSON.parse(event.body);
  assert.equal(payload.capture_mode, "drive");
  assert.equal(payload.images.length, 1);
  assert.ok(options?.abortSignal);
});

// The screen Lambda (and the YOLO Lambda) accept only ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$
// as a request id. API Gateway's ids end in "=", so on its first live frames
// (7 Oct 2026) every shadow call was refused with bad_request_id.
test("the request id sent to the screen is one the screen accepts", async () => {
  const screen = screenLambda(screenUndamaged);
  const shadow = await detectWith({ screen, openai: openai(undamaged) });
  assert.equal(shadow.status, 200);
  assert.match(shadow.body.request_id, /[=+/]/, "the harness must send a gateway-shaped id");
  const accepted = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
  const event = JSON.parse(Buffer.from(screen.calls[0].input.Payload).toString("utf8"));
  assert.match(event.headers["x-request-id"], accepted);
  assert.match(event.requestContext.requestId, accepted);
  const sent = JSON.parse(event.body).request_id;
  assert.match(sent, accepted);
  assert.equal(event.headers["x-request-id"], sent, "header and body must agree or the screen answers request_id_mismatch");
});

test("a screen without a score (the YOLO contract) still records its assessment", async () => {
  const screen = screenLambda(async () => envelope(screenDamaged, undefined));
  const shadow = await detectWith({ screen, openai: openai(openaiDamaged) });
  assert.equal(shadow.log.screen_assessment, "damaged");
  assert.equal(shadow.log.screen_score, null);
  assert.equal(shadow.log.screen_agrees, true);
});

// yolo_then_openai logs the same fields about the frames it screens: screen-audit.test.mjs.
test("the orders with no screen log no screen fields", async () => {
  for (const mode of ["openai", "openai_then_yolo"]) {
    const result = await detectWith({ mode, screen: screenLambda(screenDamaged, { score: 0.9 }),
      openai: openai(openaiDamaged) });
    assert.equal(result.status, 200, mode);
    for (const field of ["screen_score", "screen_assessment", "screen_ms", "screen_agrees", "screen_error"]) {
      assert.equal(result.log[field], null, `${mode} ${field}`);
    }
  }
});

test("health says the screen is a shadow: it neither screens nor backs OpenAI up", async () => {
  async function health(secret, functionName) {
    const h = await harness({
      detector: createDetector({
        providerMode: "openai_with_shadow_screen",
        secretProvider: secretFrom(secret),
        yoloFunctionName: functionName,
        lambdaClient: screenLambda(screenUndamaged),
        fetchImpl: openai(undamaged).fetchImpl,
      }),
    });
    const result = await h.handle({ rawPath: "/v1/health", requestContext: { http: { method: "GET" } } });
    return JSON.parse(result.body);
  }
  const ready = await health(keys, "pothole-reporter-central-screen");
  assert.equal(ready.shared_vision_provider_mode, "openai_with_shadow_screen");
  assert.equal(ready.shared_vision_configured, true);
  assert.equal(ready.shared_vision_primary_configured, true);
  assert.equal(ready.shared_vision_shadow_screen_configured, true);
  assert.equal(ready.shared_vision_drive_screen_provider, null);
  assert.equal(ready.shared_vision_drive_screen_configured, false);
  assert.equal(ready.shared_vision_fallback_configured, false);

  const noKey = await health({ openai_api_key: keys.openai_api_key }, "pothole-reporter-central-screen");
  assert.equal(noKey.shared_vision_shadow_screen_configured, false);
  assert.equal(noKey.shared_vision_configured, true);

  // A screen with no OpenAI key behind it detects nothing in this mode.
  const noOpenai = await health({ yolo_api_key: keys.yolo_api_key }, "pothole-reporter-central-screen");
  assert.equal(noOpenai.shared_vision_configured, false);
  assert.equal(noOpenai.shared_vision_shadow_screen_configured, true);
});

test("the stack accepts the shadow mode and still deploys today's order by default", () => {
  const parameter = template.match(/SharedDetectorProvider:\s*\n\s*Type: String\s*\n\s*Default: (\S+)\s*\n\s*AllowedValues: \[([^\]]+)\]/);
  assert.ok(parameter, "SharedDetectorProvider parameter not found");
  assert.equal(parameter[1], "openai_then_yolo");
  assert.ok(parameter[2].split(",").map((value) => value.trim()).includes("openai_with_shadow_screen"));
});

// ---------------------------------------------------------------- the health report
const rows = (...groups) => groups.map(([n, outcome, screenAssessment, screenError]) => ({
  n: String(n), outcome, screen_assessment: screenAssessment || undefined,
  screen_error: screenError || undefined,
}));

test("the shadow report gives live recall and the share of undamaged frames cleared", () => {
  const report = reportShadowScreen(rows(
    [294, "damaged", "damaged"], [6, "damaged", "undamaged"],
    [400, "undamaged", "undamaged"], [600, "undamaged", "damaged"],
    [12, "undamaged", null, "screen_timeout"], [3, "damaged", null, "shared_vision_unavailable"],
  ));
  assert.equal(report.damaged, 300);
  assert.equal(report.flagged, 294);
  assert.equal(report.recall, 0.98);
  assert.equal(report.undamaged, 1000);
  assert.equal(report.cleared, 400);
  assert.equal(report.clearedShare, 0.4);
  assert.equal(report.unanswered, 15);
  assert.match(report.detail, /294 of 300 .*98\.0%/);
  assert.match(report.detail, /400 of 1000 .*40\.0%/);
  assert.match(report.detail, /15 .*no screen answer/);
});

test("the shadow report never fails a run, even at zero recall or with no shadow traffic", () => {
  const blind = reportShadowScreen(rows([50, "damaged", "undamaged"], [50, "undamaged", "undamaged"]));
  assert.equal(blind.recall, 0);
  assert.equal(blind.broken, false);
  const none = reportShadowScreen([]);
  assert.equal(none.broken, false);
  assert.equal(none.damaged, 0);
  assert.equal(none.recall, null);
  assert.equal(none.clearedShare, null);
  assert.match(none.detail, /no drive frames were shadow screened/);
  // Rows a null-tolerant query may return for lines with neither field are ignored.
  assert.equal(reportShadowScreen(rows([900, "undamaged", null, null])).undamaged, 0);
});

test("the shadow query reads the fields the service logs", async () => {
  for (const field of ["screen_assessment", "screen_error", "outcome", "route", "status"]) {
    assert.ok(SHADOW_QUERY.includes(field), field);
  }
  const shadow = await detectWith({ screen: screenLambda(screenDamaged, { score: 0.8 }),
    openai: openai(openaiDamaged) });
  for (const field of ["screen_assessment", "screen_error", "screen_score", "screen_ms", "screen_agrees",
    "outcome", "route", "status"]) {
    assert.ok(Object.hasOwn(shadow.log, field), field);
  }
  const report = reportShadowScreen([{ n: "1", outcome: shadow.log.outcome,
    screen_assessment: shadow.log.screen_assessment }]);
  assert.equal(report.recall, 1);
});

// A screen that misses every pothole and never answers, over a window with enough
// traffic for every rule to be judged.
const blindScreen = HEALTHY_WINDOW.map((entry) => (entry.match === "by outcome, screen_assessment, screen_error, bucket" ? { ...entry, rows: [
  { n: "400", outcome: "damaged", screen_assessment: "undamaged", bucket: "0" },
  { n: "90", outcome: "undamaged", screen_error: "screen_timeout" },
] } : entry));

test("the health window prints the shadow report and cannot fail on it", async () => {
  const query = scriptedQuery(blindScreen);
  const report = createReport();
  await judgeWindow({ query, hours: 6, logGroup: "/aws/lambda/test", report });
  const result = report.conclude();
  assert.ok(query.asked.some((asked) => asked.text === SHADOW_QUERY), "the shadow query is asked");
  const shadow = result.rules.find((rule) => rule.name === "shadow screen (report only)");
  assert.equal(shadow.state, "ok", "the shadow report must only report");
  assert.match(shadow.detail, /flagged 0 of 400 frames gpt-5-mini judged damaged \(live recall 0\.0%\); .*90 frames had no screen answer/);
  assert.equal(result.healthy, true);
});

test("the score curve reads off the threshold that keeps 98% of damaged frames", () => {
  // 200 damaged frames: 4 score in [0.10, 0.12), 196 in [0.60, 0.62).
  // 1000 undamaged: 300 in [0.02, 0.04), 400 in [0.10, 0.12), 300 in [0.70, 0.72).
  const scored = [
    { n: "4", outcome: "damaged", bucket: "5" }, { n: "196", outcome: "damaged", bucket: "30" },
    { n: "300", outcome: "undamaged", bucket: "1" }, { n: "400", outcome: "undamaged", bucket: "5" },
    { n: "300", outcome: "undamaged", bucket: "35" },
  ];
  const curve = shadowScreenCurve(scored);
  assert.equal(curve.threshold, 0.6, "196 of 200 is exactly 98%, so the threshold can sit at 0.60");
  assert.equal(curve.recall, 0.98);
  assert.equal(curve.cleared, 700);
  assert.equal(curve.clearedShare, 0.7);
  assert.equal(curve.broken, false);
  assert.match(curve.detail, /0\.60 .*98\.0% of 200 .*700 of 1000 .*70\.0%/);
  // One more miss than 2% allows and the threshold has to drop under the low bucket.
  const stricter = shadowScreenCurve(scored, { target: 0.99 });
  assert.equal(stricter.threshold, 0.1);
  assert.equal(stricter.recall, 1);
  assert.equal(stricter.cleared, 300);
});

test("the score curve waits for 100 damaged frames and never fails a run", () => {
  const few = shadowScreenCurve([{ n: "99", outcome: "damaged", bucket: "30" },
    { n: "5000", outcome: "undamaged", bucket: "1" }]);
  assert.equal(few.threshold, null);
  assert.equal(few.broken, false);
  assert.match(few.detail, /99 scored frames judged damaged; 100 are needed/);
  assert.equal(shadowScreenCurve([]).broken, false);
  // A screen that scores every damaged frame at zero: the only threshold is zero.
  const blind = shadowScreenCurve([{ n: "150", outcome: "damaged", bucket: "0" },
    { n: "150", outcome: "undamaged", bucket: "0" }]);
  assert.equal(blind.threshold, 0);
  assert.equal(blind.clearedShare, 0);
});

test("the score query buckets the logged score and the health window prints the curve", async () => {
  for (const field of ["screen_score", "outcome", "route", "status"]) {
    assert.ok(SHADOW_QUERY.includes(field), field);
  }
  const query = scriptedQuery(blindScreen);
  const report = createReport();
  await judgeWindow({ query, hours: 6, logGroup: "/aws/lambda/test", report });
  assert.equal(query.asked.filter((asked) => asked.text === SHADOW_QUERY).length, 1, "the report and the curve read one query");
  const curve = report.conclude().rules.find((rule) => rule.name === "shadow screen threshold for 98% live recall (report only)");
  assert.equal(curve.state, "ok", "the curve must only report");
  assert.match(curve.detail, /a threshold of 0\.00 would have flagged 100\.0% of 400 damaged frames and cleared 0 of 0 undamaged \(n\/a\)/);
});
