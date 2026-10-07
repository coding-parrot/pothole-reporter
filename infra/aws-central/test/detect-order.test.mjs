import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { PROVIDER_MODES, createDetector } from "../service/detectors.mjs";
import { detectBody, harness, secretFrom, undamaged, upstream } from "./support.mjs";

// Production sends every drive frame to gpt-5-mini: p50 1.8 s, p90 2.6 s, p99 7 s. A
// tester in a car rated that one star because the potholes were not detected in time.
// yolo_then_openai puts the fast detector first on drive frames and keeps gpt-5-mini for
// the frames it flags and for every manual photo. These cases pin the order, what each
// leg may decide alone, and that the deployed openai_then_yolo order is untouched.

process.env.SHARED_SECRET_ARN = "arn:aws:secretsmanager:test";

const template = readFileSync(new URL("../template.yaml", import.meta.url), "utf8");
const keys = { openai_api_key: "sk-test-not-a-real-key", yolo_api_key: "yolo-test-token" };
const driveBody = { ...detectBody, capture_mode: "drive", capture_source: "drive_live" };
const damaged = {
  image_quality: "acceptable",
  assessment: "damaged",
  damage_type: "pothole_cavity",
  size: null,
  description: "The pothole model detected a cavity on the road surface.",
};
const confirmedByOpenai = { ...damaged, size: "medium", description: "A deep pothole." };
const rejected = {
  image_quality: "rejected",
  assessment: "undamaged",
  damage_type: null,
  size: null,
  description: "Too dark to judge.",
};

// A fake of the YOLO Lambda as detectors.mjs invokes it: the API Gateway v2 envelope
// the real handler.py returns, with the request it received kept for assertions.
function yoloLambda(answer, { statusCode = 200, functionError = null } = {}) {
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
          headers: { "x-request-id": "yolo-req-1" },
          body: JSON.stringify(statusCode === 200
            ? { request_id: "yolo-req-1", verdict: answer, model: "pothole-yolo-v1" }
            : { request_id: "yolo-req-1", ...answer }),
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

async function detectWith({ mode = "yolo_then_openai", yolo, openai: ai, body = driveBody,
  secret = keys, functionName = "pothole-reporter-central-yolo", postOptions, ...rest } = {}) {
  const h = await harness({
    detector: createDetector({
      providerMode: mode,
      secretProvider: secretFrom(secret),
      yoloFunctionName: functionName,
      lambdaClient: yolo,
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
    ms: Date.now() - started,
  };
}

test("the mode list names today's order and the screen-first order", () => {
  assert.deepEqual([...PROVIDER_MODES].slice(0, 4), ["openai", "yolo", "openai_then_yolo", "yolo_then_openai"]);
});

test("a drive frame YOLO calls undamaged never reaches OpenAI", async () => {
  const yolo = yoloLambda(undamaged);
  const ai = openai(confirmedByOpenai);
  const result = await detectWith({ yolo, openai: ai });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(ai.calls.length, 0);
  assert.equal(yolo.calls.length, 1);
  assert.equal(result.body.assessment, "undamaged");
  assert.equal(result.body.detector.backend_provider, "yolo");
  assert.equal(result.body.detector.model, "pothole-yolo-v1");
  assert.equal(result.body.detector.screened_by, undefined);
  assert.equal(result.body.detector.fallback_from, undefined);
  assert.equal(result.log.detector_provider, "yolo");
  assert.equal(result.log.detector_screened_by, null);
  assert.equal(result.calls.take, 1);
  assert.equal(result.calls.refund, 0);
});

test("a drive frame YOLO rejects for quality is answered by YOLO alone", async () => {
  const yolo = yoloLambda(rejected);
  const ai = openai(confirmedByOpenai);
  const result = await detectWith({ yolo, openai: ai });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(ai.calls.length, 0);
  assert.equal(result.body.image_quality, "rejected");
  assert.equal(result.body.detector.backend_provider, "yolo");
});

test("a drive frame YOLO flags goes to OpenAI, whose verdict is the answer", async () => {
  const yolo = yoloLambda(damaged);
  const ai = openai(confirmedByOpenai);
  const result = await detectWith({ yolo, openai: ai,
    body: { ...driveBody, client_observation_id: "obs-1" } });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(yolo.calls.length, 1);
  assert.equal(ai.calls.length, 1);
  assert.equal(result.body.assessment, "damaged");
  assert.equal(result.body.size, "medium");
  assert.equal(result.body.description, "A deep pothole.");
  assert.equal(result.body.detector.backend_provider, "openai");
  assert.equal(result.body.detector.model, "gpt-5-mini");
  assert.equal(result.body.detector.screened_by, "yolo");
  assert.equal(result.body.detector.screen_model, "pothole-yolo-v1");
  assert.equal(result.body.detector.fallback_from, undefined);
  assert.ok(result.body.detection_receipt, "a confirmed pothole still earns a receipt");
  assert.equal(result.log.detector_screened_by, "yolo");
  assert.equal(result.log.detector_screen_confirmed, true);
  assert.equal(result.log.yolo_request_id, "yolo-req-1");
});

test("OpenAI may overrule a YOLO flag: the frame is then undamaged", async () => {
  const yolo = yoloLambda(damaged);
  const ai = openai(undamaged);
  const result = await detectWith({ yolo, openai: ai,
    body: { ...driveBody, client_observation_id: "obs-2" } });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(result.body.assessment, "undamaged");
  assert.equal(result.body.damage_type, null);
  assert.equal(result.body.detector.backend_provider, "openai");
  assert.equal(result.body.detector.screened_by, "yolo");
  assert.equal(result.body.detection_receipt, undefined);
  assert.equal(result.log.detector_screen_confirmed, false);
});

test("the YOLO request carries the drive contract and the YOLO token, never the OpenAI key", async () => {
  const yolo = yoloLambda(undamaged);
  const result = await detectWith({ yolo, openai: openai(undamaged) });
  assert.equal(result.status, 200);
  const { input, options } = yolo.calls[0];
  assert.equal(input.FunctionName, "pothole-reporter-central-yolo");
  assert.equal(input.InvocationType, "RequestResponse");
  const envelope = JSON.parse(Buffer.from(input.Payload).toString("utf8"));
  assert.equal(envelope.rawPath, "/v1/detect");
  assert.equal(envelope.headers["x-yolo-api-key"], "yolo-test-token");
  const serialised = Buffer.from(input.Payload).toString("utf8");
  assert.ok(!serialised.includes("sk-test-not-a-real-key"));
  const payload = JSON.parse(envelope.body);
  assert.equal(payload.capture_mode, "drive");
  assert.equal(payload.images.length, 1);
  assert.equal(payload.prompt_version, detectBody.prompt_version);
  assert.ok(options?.abortSignal, "the invoke is bounded by the Lambda's remaining time");
});

for (const [label, make, reason] of [
  ["no YOLO function configured", () => ({ yolo: yoloLambda(damaged), functionName: "" }),
    "shared_yolo_not_configured"],
  ["no yolo_api_key in the secret", () => ({ yolo: yoloLambda(damaged),
    secret: { openai_api_key: keys.openai_api_key } }), "shared_yolo_not_configured"],
  ["a YOLO Lambda that errors", () => ({ yolo: yoloLambda(damaged, { functionError: "Unhandled" }) }),
    "shared_vision_unavailable"],
  ["a YOLO Lambda answering 503", () => ({ yolo: yoloLambda({ error: "inference_unavailable" },
    { statusCode: 503 }) }), "shared_vision_unavailable"],
  ["the YOLO monthly cap", () => ({ yolo: yoloLambda({ error: "monthly_request_cap_exceeded" },
    { statusCode: 429 }) }), "shared_yolo_cap_reached"],
  ["an invoke the SDK rejects", () => ({ yolo: yoloLambda(async () => {
    throw Object.assign(new Error("AccessDeniedException"), { name: "AccessDeniedException" });
  }) }), "shared_vision_unavailable"],
]) {
  test(`${label} falls through to OpenAI on a drive frame and says why`, async () => {
    const ai = openai(confirmedByOpenai);
    const result = await detectWith({ openai: ai, ...make() });
    assert.equal(result.status, 200, JSON.stringify(result.body));
    assert.equal(ai.calls.length, 1);
    assert.equal(result.body.assessment, "damaged");
    assert.equal(result.body.detector.backend_provider, "openai");
    assert.equal(result.body.detector.fallback_from, "yolo");
    assert.equal(result.body.detector.fallback_reason, reason);
    assert.equal(result.log.detector_fallback_reason, reason);
    assert.equal(result.calls.refund, 0);
  });
}

test("a hanging YOLO screen gives up and OpenAI still answers inside the Lambda's time", async () => {
  const yolo = yoloLambda((command, { abortSignal }) => new Promise((resolve, reject) => {
    abortSignal.addEventListener("abort", () => reject(abortSignal.reason));
  }));
  const ai = openai(undamaged);
  const result = await detectWith({ yolo, openai: ai, yoloScreenTimeoutMs: 300,
    postOptions: { awsContext: { awsRequestId: "test", getRemainingTimeInMillis: () => 20_000 } } });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.ok(result.ms < 2_000, `took ${result.ms} ms`);
  assert.equal(ai.calls.length, 1);
  assert.equal(result.body.detector.fallback_from, "yolo");
  assert.equal(result.body.detector.fallback_reason, "shared_vision_unavailable");
});

test("a YOLO screen is bounded by the Lambda's remaining time, not only its own budget", async () => {
  const yolo = yoloLambda((command, { abortSignal }) => new Promise((resolve, reject) => {
    abortSignal.addEventListener("abort", () => reject(abortSignal.reason));
  }));
  const ai = openai(undamaged);
  const result = await detectWith({ yolo, openai: ai,
    postOptions: { awsContext: { awsRequestId: "test", getRemainingTimeInMillis: () => 4_000 } } });
  assert.ok(result.ms < 2_500, `took ${result.ms} ms`);
  assert.equal(ai.calls.length, 1);
});

test("exhausted OpenAI credit leaves a YOLO flag standing as the verdict", async () => {
  const yolo = yoloLambda(damaged);
  const ai = openai({ error: { code: "insufficient_quota" } }, 429);
  const result = await detectWith({ yolo, openai: ai,
    body: { ...driveBody, client_observation_id: "obs-3" } });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(ai.calls.length, 1);
  assert.equal(result.body.assessment, "damaged");
  assert.equal(result.body.damage_type, "pothole_cavity");
  assert.equal(result.body.detector.backend_provider, "yolo");
  assert.equal(result.body.detector.fallback_from, "openai");
  assert.equal(result.body.detector.fallback_reason, "shared_credits_exhausted");
  assert.ok(result.body.detection_receipt);
  assert.equal(result.log.detector_screened_by, "yolo");
});

for (const [label, verdict, status, code] of [
  ["an OpenAI 500", { error: { type: "server_error" } }, 500, "shared_vision_unavailable"],
  ["an OpenAI rate limit", { error: { code: "rate_limit_exceeded" } }, 429, "shared_rate_limit"],
]) {
  test(`${label} on a flagged frame is still an error and refunds the unit`, async () => {
    const result = await detectWith({ yolo: yoloLambda(damaged), openai: openai(verdict, status) });
    assert.equal(result.body.error, code);
    assert.equal(result.calls.take, 1);
    assert.equal(result.calls.refund, 1);
  });
}

test("a manual photo goes to OpenAI first even with YOLO configured", async () => {
  const yolo = yoloLambda(damaged);
  const ai = openai(undamaged);
  const result = await detectWith({ yolo, openai: ai, body: detectBody });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(yolo.calls.length, 0);
  assert.equal(ai.calls.length, 1);
  assert.equal(result.body.assessment, "undamaged");
  assert.equal(result.body.detector.backend_provider, "openai");
  assert.equal(result.body.detector.screened_by, undefined);
});

test("a manual photo keeps YOLO as the exhaustion fallback only", async () => {
  const yolo = yoloLambda(damaged);
  const ai = openai({ error: { code: "insufficient_quota" } }, 429);
  const result = await detectWith({ yolo, openai: ai, body: detectBody });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(ai.calls.length, 1);
  assert.equal(yolo.calls.length, 1);
  assert.equal(result.body.detector.backend_provider, "yolo");
  assert.equal(result.body.detector.fallback_from, "openai");
});

test("today's openai_then_yolo order never screens a drive frame", async () => {
  const yolo = yoloLambda(undamaged);
  const ai = openai(confirmedByOpenai);
  const result = await detectWith({ mode: "openai_then_yolo", yolo, openai: ai });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(yolo.calls.length, 0);
  assert.equal(ai.calls.length, 1);
  assert.equal(result.body.assessment, "damaged");
  assert.equal(result.body.detector.backend_provider, "openai");
});

test("openai_then_yolo still bounds the fallback invoke by the Lambda's remaining time", async () => {
  const yolo = yoloLambda((command, { abortSignal }) => new Promise((resolve, reject) => {
    abortSignal.addEventListener("abort", () => reject(abortSignal.reason));
  }));
  const ai = openai({ error: { code: "insufficient_quota" } }, 429);
  const result = await detectWith({ mode: "openai_then_yolo", yolo, openai: ai,
    postOptions: { awsContext: { awsRequestId: "test", getRemainingTimeInMillis: () => 4_000 } } });
  assert.equal(result.status, 503);
  assert.equal(result.body.error, "shared_vision_unavailable");
  assert.ok(result.ms < 2_500, `took ${result.ms} ms`);
  assert.equal(result.calls.refund, 1);
});

test("an unknown provider mode is refused, not treated as any order", async () => {
  const result = await detectWith({ mode: "yolo_then_http", yolo: yoloLambda(undamaged),
    openai: openai(undamaged) });
  assert.equal(result.status, 503);
  assert.equal(result.body.error, "shared_vision_not_configured");
});

test("health names the drive screen only in yolo_then_openai and only with a YOLO key", async () => {
  async function health(mode, secret, functionName) {
    const h = await harness({
      detector: createDetector({
        providerMode: mode,
        secretProvider: secretFrom(secret),
        yoloFunctionName: functionName,
        lambdaClient: yoloLambda(undamaged),
        fetchImpl: openai(undamaged).fetchImpl,
      }),
    });
    const result = await h.handle({ rawPath: "/v1/health", requestContext: { http: { method: "GET" } } });
    return JSON.parse(result.body);
  }
  const screening = await health("yolo_then_openai", keys, "pothole-reporter-central-yolo");
  assert.equal(screening.shared_vision_provider_mode, "yolo_then_openai");
  assert.equal(screening.shared_vision_drive_screen_provider, "yolo");
  assert.equal(screening.shared_vision_drive_screen_configured, true);
  assert.equal(screening.shared_vision_fallback_configured, true);

  const noKey = await health("yolo_then_openai", { openai_api_key: keys.openai_api_key },
    "pothole-reporter-central-yolo");
  assert.equal(noKey.shared_vision_drive_screen_provider, "yolo");
  assert.equal(noKey.shared_vision_drive_screen_configured, false);
  assert.equal(noKey.shared_vision_configured, true);

  const today = await health("openai_then_yolo", keys, "");
  assert.equal(today.shared_vision_drive_screen_provider, null);
  assert.equal(today.shared_vision_drive_screen_configured, false);
  assert.equal(today.shared_vision_fallback_configured, false);
});

test("the stack deploys today's order unless the parameter is overridden", () => {
  const parameter = template.match(/SharedDetectorProvider:\s*\n\s*Type: String\s*\n\s*Default: (\S+)\s*\n\s*AllowedValues: \[([^\]]+)\]/);
  assert.ok(parameter, "SharedDetectorProvider parameter not found");
  assert.equal(parameter[1], "openai_then_yolo");
  assert.deepEqual(parameter[2].split(",").map((value) => value.trim()), [...PROVIDER_MODES]);
  assert.match(template, /SHARED_DETECTOR_PROVIDER: !Ref SharedDetectorProvider/);
  assert.match(template, /YoloFunctionName:\s*\n\s*Type: String\s*\n\s*Default: ''/);
  assert.match(template, /YOLO_FUNCTION_NAME: !If \[HasYoloFunction, !Ref YoloFunctionName, !Ref AWS::NoValue\]/);
});

test("naming the YOLO function grants invoke on exactly that function", () => {
  const start = template.indexOf("Sid: InvokeYoloDetector");
  assert.notEqual(start, -1, "InvokeYoloDetector statement not found");
  const statement = template.slice(start, template.indexOf("- !Ref AWS::NoValue", start));
  assert.match(statement, /Action: lambda:InvokeFunction/);
  assert.match(statement, /Resource: !Sub 'arn:\$\{AWS::Partition\}:lambda:\$\{AWS::Region\}:\$\{AWS::AccountId\}:function:\$\{YoloFunctionName\}'/);
  assert.match(template, /HasYoloFunction: !Not \[!Equals \[!Ref YoloFunctionName, ''\]\]/);
});
