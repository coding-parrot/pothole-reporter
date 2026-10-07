import { randomInt } from "node:crypto";

import { InvokeCommand, LambdaClient } from "@aws-sdk/client-lambda";
import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";

import {
  DETECT_PROMPT,
  DETECT_PROMPT_VERSION,
  DETECT_SCHEMA,
  DETECT_SCHEMA_VERSION,
  LLM_CONTRACT,
  MODEL_CONFIG,
  RUNTIME_CONFIG,
} from "../../../llm/generated/contract.mjs";
import { HttpError } from "./errors.mjs";

// insufficient_quota is what OpenAI actually sends when an account is out of credit.
const EXHAUSTION_CODES = new Set([
  "insufficient_quota",
  "billing_hard_limit_reached",
  "credit_balance_exhausted",
  "organization_spend_limit_exceeded",
  "project_spend_limit_exceeded",
  "organization_usage_limit_exceeded",
]);
const YOLO_CAP_CODES = new Set([
  "monthly_request_cap_exceeded",
  "monthly_estimated_budget_cap_exceeded",
]);
const promptConfig = LLM_CONTRACT.prompts.detection;
// Lambda and API Gateway both stop at 29 s. An OpenAI call that outlives the function is
// killed with no response, no log line and a lease left IN_PROGRESS, so the call must
// give up first and leave this much time to answer, release and log.
const LAMBDA_RESPONSE_RESERVE_MS = 3_000;
const MIN_UPSTREAM_MS = 1_000;
// openai_then_yolo is the order deployed today: gpt-5-mini judges every frame and the
// YOLO leg only covers OpenAI exhaustion. yolo_then_openai puts the fast detector first
// on drive frames and keeps OpenAI first for manual photos. openai_with_shadow_screen is
// how a new screen earns that place: gpt-5-mini still answers every frame, and the screen
// runs beside it on drive frames so the request log can say what it would have decided.
export const PROVIDER_MODES = Object.freeze([
  "openai", "yolo", "openai_then_yolo", "yolo_then_openai", "openai_with_shadow_screen",
]);
const SHADOW_MODE = "openai_with_shadow_screen";
// A drive frame screened by YOLO still has to reach gpt-5-mini when it is flagged, so
// the screen may take at most this much of the Lambda's 29 s. A warm ONNX call is well
// under a second; a cold container is a few seconds; anything longer is not a screen.
const YOLO_SCREEN_TIMEOUT_MS = 10_000;
// In shadow mode the screen decides nothing, so it may not cost the phone anything
// either: once gpt-5-mini has answered, a screen that is still running gets this long
// to land in the log and is then cancelled.
const SHADOW_SCREEN_GRACE_MS = 50;
// yolo_then_openai never shows gpt-5-mini a frame the screen clears, so a pothole the
// screen misses is missed by everyone and counted by no one. The first screen flagged 94%
// of damaged frames in testing and 76% on real phones. This share of the cleared drive
// frames is therefore still sent to gpt-5-mini, and the phone gets gpt-5-mini's answer
// for them: an audited frame cannot be a miss, and the request log keeps saying what the
// screen is missing. The stack parameter is ScreenAuditRate.
export const DEFAULT_SCREEN_AUDIT_RATE = 0.1;

// The rate as configured: a number from 0 to 1. Unset is the default. A value that
// cannot be read audits every cleared frame, because the other reading of a typo is no
// audit at all: this way the mistake costs gpt-5-mini calls (what production pays today)
// and never a pothole.
export function auditRate(value) {
  if (value === undefined || value === null || String(value).trim() === "") return DEFAULT_SCREEN_AUDIT_RATE;
  const rate = typeof value === "number" ? value : Number(String(value).trim());
  return Number.isFinite(rate) && rate >= 0 && rate <= 1 ? rate : 1;
}

// A number in [0, 1) from the operating system's generator, drawn after the screen has
// answered. Nothing a client sends (the image, its request, the time) moves it, so no
// client can tell beforehand which frames gpt-5-mini will also see.
const drawForAudit = () => randomInt(2 ** 32) / 2 ** 32;

// Aborts after the given time, or as soon as the caller's own signal does.
function timeoutSignal(milliseconds, outer = null) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), milliseconds);
  const abandon = () => controller.abort(outer.reason);
  if (outer?.aborted) abandon();
  else outer?.addEventListener("abort", abandon, { once: true });
  return {
    signal: controller.signal,
    cancel: () => {
      clearTimeout(timer);
      outer?.removeEventListener("abort", abandon);
    },
  };
}

// The YOLO and screen Lambdas accept ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$ as a request id.
// API Gateway's ids are base64 ("E21ZGgNkBcwEJ5w="), so every other character becomes a
// dot: still unique enough to find the pair of log lines, and never refused.
const screenRequestId = (requestId) => {
  const safe = String(requestId || "").replace(/[^A-Za-z0-9._:-]/g, ".").slice(0, 128);
  return /^[A-Za-z0-9]/.test(safe) ? safe : `r${safe}`.slice(0, 128);
};

const flagsDamage = (verdict) => verdict?.image_quality === "acceptable"
  && verdict?.assessment === "damaged";

const hasAssessment = (verdict) => ["damaged", "undamaged"].includes(verdict?.assessment);

// What the screen said about a drive frame, for the request log: the same three fields
// whether the screen is being watched (shadow) or is deciding (yolo_then_openai). Says
// whether it flagged the frame.
function recordScreenVerdict(context, screen) {
  const flagged = flagsDamage(screen.verdict);
  context.screenAssessment = flagged ? "damaged" : "undamaged";
  context.screenScore = screen.score;
  context.screenModel = screen.model;
  return flagged;
}

// What the phone is told about a drive frame the screen answered alone. The screen's own
// sentences are fixed English and ten words long ("The road screen found no road damage
// in this frame."); the contract's description is at most eight words, and in Kannada
// when the request asks for it (the one language the prompt has a suffix for). The
// Kannada lines are the YOLO detector's own (infra/aws-yolo/service/detector.py).
const SCREEN_DESCRIPTIONS = Object.freeze({
  en: Object.freeze({
    damaged: "The road screen flagged likely road damage.",
    undamaged: "No road damage found in this frame.",
    rejected: "The image quality is insufficient for pothole detection.",
  }),
  kn: Object.freeze({
    damaged: "ಪಥೋಲ್ ಮಾದರಿಯು ರಸ್ತೆ ಮೇಲ್ಮೈಯಲ್ಲಿ ಗುಂಡಿಯನ್ನು ಪತ್ತೆಹಚ್ಚಿದೆ.",
    undamaged: "ಬಳಸಬಹುದಾದ ರಸ್ತೆ ಚಿತ್ರದಲ್ಲಿ ಪಥೋಲ್ ಪತ್ತೆಯಾಗಿಲ್ಲ.",
    rejected: "ಚಿತ್ರದ ಗುಣಮಟ್ಟ ಪಥೋಲ್ ಪರಿಶೀಲನೆಗೆ ಸಾಕಾಗಿಲ್ಲ.",
  }),
});

// The screen's verdict as the phone gets it: the same four decisions, with the
// description the contract allows.
function screenAnswer(screen, language) {
  const lines = SCREEN_DESCRIPTIONS[language] || SCREEN_DESCRIPTIONS.en;
  const kind = screen.verdict.image_quality === "acceptable" ? screen.verdict.assessment : "rejected";
  return { ...screen, verdict: { ...screen.verdict, description: lines[kind] } };
}

function outputFormat() {
  return {
    format: {
      type: "json_schema",
      name: promptConfig.schemaName,
      schema: DETECT_SCHEMA,
      strict: RUNTIME_CONFIG.strictStructuredOutputs,
    },
    verbosity: RUNTIME_CONFIG.textVerbosity,
  };
}

function detectionPrompt(captureMode, language) {
  return DETECT_PROMPT
    + promptConfig.captureLayouts[captureMode]
    + (promptConfig.languageSuffixes[language] || "");
}

function readOutputText(data) {
  if (typeof data?.output_text === "string") return data.output_text;
  const message = (data?.output || []).find((item) => item.type === "message");
  const output = (message?.content || []).find((item) => item.type === "output_text");
  return output?.text;
}

async function errorCode(response) {
  try {
    const data = await response.json();
    return {
      code: typeof data?.error?.code === "string" ? data.error.code : null,
      type: typeof data?.error?.type === "string" ? data.error.type : null,
    };
  } catch {
    return { code: null, type: null };
  }
}

export function createSecretProvider({ secretArn, client = new SecretsManagerClient({}) }) {
  let cached = null;
  let expiresAt = 0;
  return async () => {
    if (cached && Date.now() < expiresAt) return cached;
    if (!secretArn) return {};
    const result = await client.send(new GetSecretValueCommand({ SecretId: secretArn }));
    const raw = result.SecretString
      || (result.SecretBinary ? Buffer.from(result.SecretBinary).toString("utf8") : "");
    let value;
    try {
      value = JSON.parse(raw);
    } catch {
      value = { openai_api_key: raw };
    }
    cached = {
      openaiApiKey: String(value.openai_api_key || value.OPENAI_API_KEY || "").trim(),
      yoloApiKey: String(value.yolo_api_key || value.YOLO_API_KEY || "").trim(),
    };
    expiresAt = Date.now() + 300_000;
    return cached;
  };
}

function upstreamBudget(maximum, context) {
  const remaining = context.remainingTimeMs?.();
  if (!Number.isFinite(remaining)) return maximum;
  return Math.max(MIN_UPSTREAM_MS, Math.min(maximum, remaining - LAMBDA_RESPONSE_RESERVE_MS));
}

export function createDetector({
  providerMode = "openai_then_yolo",
  secretProvider,
  yoloMode = "lambda",
  yoloFunctionName = "",
  yoloUrl = "",
  yoloModel = "pothole-yolo",
  fetchImpl = fetch,
  lambdaClient = new LambdaClient({}),
  openaiTimeoutMs = RUNTIME_CONFIG.timeoutsMs.serverOpenAIMax,
  yoloTimeoutMs = RUNTIME_CONFIG.timeoutsMs.serverYoloMax,
  yoloScreenTimeoutMs = YOLO_SCREEN_TIMEOUT_MS,
  shadowGraceMs = SHADOW_SCREEN_GRACE_MS,
  screenAuditRate = DEFAULT_SCREEN_AUDIT_RATE,
  auditDraw = drawForAudit,
} = {}) {
  const secrets = secretProvider || (async () => ({}));
  const auditShare = auditRate(screenAuditRate);

  // A secret with no current version (as on 2026-09-19) is a missing credential, not a
  // server fault, and must read as one to the app instead of internal_error.
  async function readSecret(code, message, details) {
    try {
      return await secrets();
    } catch (error) {
      throw new HttpError(503, code, message, details, { cause: error });
    }
  }

  async function openai(input, context) {
    const secret = await readSecret("shared_openai_not_configured",
      "The shared OpenAI detector secret could not be read.", { fallback_allowed: true });
    if (!secret.openaiApiKey) {
      throw new HttpError(503, "shared_openai_not_configured",
        "The shared OpenAI detector is not configured.", { fallback_allowed: true });
    }
    const model = MODEL_CONFIG.allowedModels.includes(input.model)
      ? input.model : MODEL_CONFIG.defaultModel;
    const detail = MODEL_CONFIG.allowedImageDetails.includes(input.imageDetail)
      ? input.imageDetail : MODEL_CONFIG.defaultImageDetail;
    if (detail === MODEL_CONFIG.originalImageDetail
        && !MODEL_CONFIG.originalDetailModels.includes(model)) {
      throw new HttpError(400, "unsupported_image_detail",
        `original detail is not supported by ${model}.`);
    }
    const request = {
      model,
      input: [{
        role: promptConfig.role,
        content: [
          ...input.images.map((image) => ({
            type: "input_image",
            image_url: image.dataUrl,
            detail,
          })),
          { type: "input_text", text: detectionPrompt(input.captureMode, input.language) },
        ],
      }],
      text: outputFormat(),
      reasoning: {
        effort: MODEL_CONFIG.reasoningEffortByModel[model]
          || MODEL_CONFIG.defaultReasoningEffort,
      },
      store: false,
    };
    const timeout = timeoutSignal(upstreamBudget(openaiTimeoutMs, context));
    let response;
    try {
      response = await fetchImpl(RUNTIME_CONFIG.responsesUrl, {
        method: "POST",
        redirect: "error",
        headers: {
          authorization: `Bearer ${secret.openaiApiKey}`,
          "content-type": "application/json",
          "x-client-request-id": context.requestId,
        },
        body: JSON.stringify(request),
        signal: timeout.signal,
      });
    } catch (error) {
      timeout.cancel();
      throw new HttpError(503, "shared_vision_unavailable",
        "The shared OpenAI detector could not be reached.", { retryable: true },
        { cause: error });
    }
    context.openaiRequestId = response.headers.get("x-request-id") || null;
    if (!response.ok) {
      const upstream = await errorCode(response).finally(timeout.cancel);
      context.openaiErrorCode = upstream.code || upstream.type;
      if (response.status === 429 && (EXHAUSTION_CODES.has(upstream.code)
          || EXHAUSTION_CODES.has(upstream.type))) {
        throw new HttpError(503, "shared_credits_exhausted",
          "The shared OpenAI credit or spending limit has been reached.", {
            fallback_allowed: true,
            upstream_code: upstream.code,
          });
      }
      if (response.status === 429) {
        throw new HttpError(429, "shared_rate_limit",
          "The shared OpenAI detector is temporarily rate limited.");
      }
      // A revoked or wrong key is the operator's to fix. Calling it an image problem
      // kept a drive sending frames that could never succeed.
      if (response.status === 401 || response.status === 403) {
        throw new HttpError(503, "shared_vision_not_configured",
          "The shared OpenAI detector credential was rejected.", {
            fallback_allowed: true,
            upstream_code: upstream.code || upstream.type,
          });
      }
      throw new HttpError(response.status >= 500 ? 503 : 422,
        response.status >= 500 ? "shared_vision_unavailable" : "vision_request_rejected",
        "OpenAI could not analyse this image.");
    }
    // The timer stays armed until the body is read: headers alone do not finish a call.
    const data = await response.json().catch(() => null).finally(timeout.cancel);
    if (timeout.signal.aborted) {
      throw new HttpError(503, "shared_vision_unavailable",
        "The shared OpenAI detector did not answer in time.", { retryable: true });
    }
    const text = readOutputText(data);
    if (!text) {
      throw new HttpError(502, "bad_upstream_response",
        "OpenAI returned no structured assessment.");
    }
    try {
      return { verdict: JSON.parse(text), provider: "openai", model };
    } catch {
      throw new HttpError(502, "bad_upstream_response",
        "OpenAI returned invalid structured output.");
    }
  }

  function yoloPayload(input, context) {
    return {
      version: 1,
      task: promptConfig.id,
      request_id: screenRequestId(context.requestId),
      model: yoloModel,
      capture_mode: input.captureMode,
      language: input.language,
      prompt_version: DETECT_PROMPT_VERSION,
      schema_version: DETECT_SCHEMA_VERSION,
      images: input.images.map(({ dataUrl }) => ({ data_url: dataUrl })),
    };
  }

  async function yolo(input, context, budgetMs = yoloTimeoutMs, abandonSignal = null) {
    const secret = await readSecret("shared_yolo_not_configured",
      "The shared YOLO detector secret could not be read.");
    if (!secret.yoloApiKey) {
      throw new HttpError(503, "shared_yolo_not_configured",
        "The shared YOLO detector credential is not configured.");
    }
    const payload = yoloPayload(input, context);
    let status;
    let headers = {};
    let body;
    if (yoloMode === "lambda") {
      if (!yoloFunctionName) {
        throw new HttpError(503, "shared_yolo_not_configured",
          "The YOLO Lambda function is not configured.");
      }
      // The YOLO function's own timeout is longer than this function's remaining time,
      // so an invoke that is not bounded here can outlive the caller.
      const timeout = timeoutSignal(upstreamBudget(budgetMs, context), abandonSignal);
      let invoked;
      try {
        invoked = await lambdaClient.send(new InvokeCommand({
          FunctionName: yoloFunctionName,
          InvocationType: "RequestResponse",
          Payload: Buffer.from(JSON.stringify({
            version: "2.0",
            routeKey: "POST /v1/detect",
            rawPath: "/v1/detect",
            headers: {
              "x-request-id": screenRequestId(context.requestId),
              "x-yolo-api-key": secret.yoloApiKey,
              "content-type": "application/json",
            },
            body: JSON.stringify(payload),
            isBase64Encoded: false,
            requestContext: { requestId: screenRequestId(context.requestId) },
          })),
        }), { abortSignal: timeout.signal });
      } catch (error) {
        throw new HttpError(503, "shared_vision_unavailable",
          timeout.signal.aborted
            ? "The YOLO Lambda did not answer in time."
            : "The YOLO Lambda could not be invoked.",
          { retryable: true }, { cause: error });
      } finally {
        timeout.cancel();
      }
      if (invoked.FunctionError) {
        throw new HttpError(503, "shared_vision_unavailable",
          "The YOLO Lambda failed to complete inference.");
      }
      const envelope = JSON.parse(Buffer.from(invoked.Payload || []).toString("utf8"));
      status = Number(envelope.statusCode || 500);
      headers = envelope.headers || {};
      body = JSON.parse(envelope.body || "{}");
    } else if (yoloMode === "http") {
      if (!/^https:\/\//.test(yoloUrl)) {
        throw new HttpError(503, "shared_yolo_not_configured",
          "The YOLO HTTPS endpoint is not configured.");
      }
      const timeout = timeoutSignal(upstreamBudget(budgetMs, context), abandonSignal);
      let response;
      try {
        response = await fetchImpl(yoloUrl, {
          method: "POST",
          redirect: "error",
          headers: {
            "content-type": "application/json",
            "x-request-id": screenRequestId(context.requestId),
            "x-yolo-api-key": secret.yoloApiKey,
          },
          body: JSON.stringify(payload),
          signal: timeout.signal,
        });
      } catch {
        throw new HttpError(503, "shared_vision_unavailable",
          "The YOLO endpoint could not be reached.");
      } finally {
        timeout.cancel();
      }
      status = response.status;
      headers = Object.fromEntries(response.headers.entries());
      body = await response.json().catch(() => ({}));
    } else {
      throw new HttpError(503, "shared_yolo_not_configured",
        "YOLO_MODE must be lambda or http.");
    }
    context.yoloRequestId = headers["x-request-id"] || body.request_id || null;
    if (status === 429 && YOLO_CAP_CODES.has(body.error)) {
      throw new HttpError(503, "shared_yolo_cap_reached",
        "The configured YOLO monthly cap has been reached.", {
          upstream_code: body.error,
        });
    }
    if (status < 200 || status >= 300) {
      throw new HttpError(status >= 500 ? 503 : 502, "shared_vision_unavailable",
        "The YOLO detector could not complete this analysis.");
    }
    return {
      verdict: body.verdict || body.result || body,
      provider: "yolo",
      model: String(body.model || yoloModel).slice(0, 80),
      // The classifier screen reports the raw score its threshold was applied to. The
      // box detector does not; shadow mode logs whichever it gets.
      score: Number.isFinite(body.score) ? body.score : null,
    };
  }

  // Today's order. OpenAI judges; YOLO covers only the documented exhaustion errors.
  async function openaiThenYolo(input, context) {
    try {
      return await openai(input, context);
    } catch (error) {
      if (!(error instanceof HttpError)
          || !error.details?.fallback_allowed) throw error;
      context.detectorFallbackReason = error.code;
      try {
        const result = await yolo(input, context);
        return { ...result, fallbackFrom: "openai", fallbackReason: error.code };
      } catch (fallbackError) {
        // No fallback deployed is not news. The OpenAI reason is the one to report.
        if (fallbackError?.code === "shared_yolo_not_configured") throw error;
        throw fallbackError;
      }
    }
  }

  // Drive frames arrive several times a second and most show plain road. The fast
  // detector answers those itself; only a frame it flags as damaged costs a gpt-5-mini
  // call, and gpt-5-mini's verdict is the one the app gets. A YOLO leg that is absent,
  // capped, slow or broken falls through to OpenAI alone, so flipping the mode can never
  // make a drive worse than today's order; it is only ever faster.
  async function yoloThenOpenai(input, context) {
    const started = performance.now();
    // On every drive frame of this mode, whatever the screen then does: it is what tells
    // a live line from a shadow one in the request log.
    context.screenAuditRate = auditShare;
    let screen;
    try {
      screen = await yolo(input, context, yoloScreenTimeoutMs);
      // An answer with no assessment in it is a broken screen like any other. Passed on,
      // it was refused by the service as a 502 and the drive frame failed.
      if (!hasAssessment(screen.verdict)) {
        throw new HttpError(502, "bad_screen_response", "The screen returned no assessment.");
      }
    } catch (error) {
      if (!(error instanceof HttpError)) throw error;
      context.screenMs = Math.round(performance.now() - started);
      context.screenError = error.code;
      context.detectorFallbackReason = error.code;
      const result = await openai(input, context);
      return { ...result, fallbackFrom: "yolo", fallbackReason: error.code };
    }
    context.screenMs = Math.round(performance.now() - started);
    if (!recordScreenVerdict(context, screen)) return auditCleared(input, context, screen);
    context.detectorScreenedBy = "yolo";
    try {
      const confirmed = await openai(input, context);
      context.detectorScreenConfirmed = confirmed.verdict?.assessment === "damaged";
      context.screenAgrees = flagsDamage(confirmed.verdict);
      return { ...confirmed, screenedBy: "yolo", screenModel: screen.model };
    } catch (error) {
      // The same rule as openai_then_yolo: a missing or exhausted OpenAI credential
      // leaves the screen's verdict standing; a timeout, rate limit or 5xx stays an error
      // rather than silently changing which model judged the frame.
      if (!(error instanceof HttpError) || !error.details?.fallback_allowed) throw error;
      context.detectorFallbackReason = error.code;
      return { ...screenAnswer(screen, input.language), fallbackFrom: "openai", fallbackReason: error.code };
    }
  }

  // A frame the screen cleared. One draw decides whether gpt-5-mini sees it too. When it
  // does, the phone gets gpt-5-mini's answer, so a pothole the screen missed on an
  // audited frame is still reported. The audit is a measurement and may not cost the
  // phone anything but time: when gpt-5-mini cannot be asked (no credit, a rate limit, a
  // timeout, a 5xx) the frame is answered exactly as an unaudited one, and the log says
  // the audit was lost.
  async function auditCleared(input, context, screen) {
    const answer = screenAnswer(screen, input.language);
    if (!(auditDraw() < auditShare)) return answer;
    context.screenAudited = true;
    try {
      const judged = await openai(input, context);
      // A frame gpt-5-mini rejects for quality counts as undamaged, as in shadow mode.
      context.screenAgrees = !flagsDamage(judged.verdict);
      return { ...judged, screenedBy: "yolo", screenModel: screen.model };
    } catch (error) {
      if (!(error instanceof HttpError)) throw error;
      context.screenAuditError = error.code;
      return answer;
    }
  }

  // Shadow mode. The phone gets gpt-5-mini's answer, or gpt-5-mini's error, exactly as
  // in "openai": the screen is unproven, so it is not a fallback either. The screen is
  // called at the same moment on its own copy of the context and can only ever write
  // log fields. It is never awaited past the grace, and whatever it throws stops here.
  async function openaiWithShadowScreen(input, context) {
    const started = performance.now();
    const abandon = new AbortController();
    const screenContext = { requestId: context.requestId, remainingTimeMs: context.remainingTimeMs };
    const elapsed = () => Math.round(performance.now() - started);
    const watching = yolo(input, screenContext, yoloScreenTimeoutMs, abandon.signal).then(
      (screen) => ({ screen, ms: elapsed() }),
      (error) => ({ error, ms: elapsed() }),
    );
    let answer;
    let failure = null;
    try {
      answer = await openai(input, context);
    } catch (error) {
      failure = error;
    }
    let timer;
    const settled = await Promise.race([
      watching,
      new Promise((resolve) => { timer = setTimeout(resolve, shadowGraceMs, null); }),
    ]);
    clearTimeout(timer);
    if (!settled) abandon.abort();
    try {
      recordShadowScreen(context, settled, failure ? null : answer.verdict, elapsed());
      context.yoloRequestId = screenContext.yoloRequestId || null;
    } catch {
      context.screenError = "screen_failed";
    }
    if (failure) throw failure;
    return answer;
  }

  function recordShadowScreen(context, settled, openaiVerdict, waitedMs) {
    context.screenMs = settled ? settled.ms : waitedMs;
    if (!settled) {
      context.screenError = "screen_timeout";
      return;
    }
    if (settled.error) {
      context.screenError = settled.error instanceof HttpError ? settled.error.code : "screen_failed";
      return;
    }
    if (!hasAssessment(settled.screen.verdict)) {
      context.screenError = "bad_screen_response";
      return;
    }
    const flagged = recordScreenVerdict(context, settled.screen);
    // Agreement is about the decision the screen would take over: does this frame go
    // to gpt-5-mini or not. A frame gpt-5-mini rejects for quality counts as undamaged.
    if (openaiVerdict) context.screenAgrees = flagged === flagsDamage(openaiVerdict);
  }

  return {
    status() {
      return {
        mode: providerMode,
        // Shape only. Whether a key is actually present is readiness(), below.
        openai_configured: Boolean(process.env.SHARED_SECRET_ARN),
        yolo_configured: Boolean(process.env.SHARED_SECRET_ARN)
          && (yoloMode === "lambda" ? Boolean(yoloFunctionName) : /^https:\/\//.test(yoloUrl)),
        yolo_mode: yoloMode,
        yolo_model: yoloModel,
        drive_screen_provider: providerMode === "yolo_then_openai" ? "yolo" : null,
        // In shadow mode the YOLO leg is watched, not used: it neither screens a frame
        // nor answers when OpenAI cannot.
        shadow_screen_provider: providerMode === SHADOW_MODE ? "yolo" : null,
      };
    },
    // The presence of a secret ARN says nothing about the secret's contents. Health
    // reported "configured" against an empty secret, so the app believed the shared
    // detector was ready and every capture failed at the point of detection instead.
    // Read the secret and report what is actually usable. Never report the value.
    async readiness() {
      const shape = this.status();
      if (!shape.openai_configured && !shape.yolo_configured) {
        return { openai_configured: false, yolo_configured: false };
      }
      let secret;
      try {
        secret = await secrets();
      } catch {
        // Fail closed: an unreadable secret is not a configured detector.
        return { openai_configured: false, yolo_configured: false };
      }
      return {
        openai_configured: shape.openai_configured && Boolean(secret.openaiApiKey),
        yolo_configured: shape.yolo_configured && Boolean(secret.yoloApiKey),
      };
    },
    async detect(input, context) {
      if (providerMode === "openai") return openai(input, context);
      if (providerMode === "yolo") return yolo(input, context);
      if (providerMode === "openai_then_yolo") return openaiThenYolo(input, context);
      if (providerMode === "yolo_then_openai") {
        // A manual photo is one deliberate report, so accuracy outranks speed there:
        // OpenAI judges it and YOLO keeps its exhaustion-only role.
        return input.captureMode === "drive"
          ? yoloThenOpenai(input, context)
          : openaiThenYolo(input, context);
      }
      if (providerMode === SHADOW_MODE) {
        return input.captureMode === "drive"
          ? openaiWithShadowScreen(input, context)
          : openai(input, context);
      }
      throw new HttpError(503, "shared_vision_not_configured",
        "SHARED_DETECTOR_PROVIDER is invalid.");
    },
  };
}
