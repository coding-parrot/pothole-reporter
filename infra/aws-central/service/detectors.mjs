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

const EXHAUSTION_CODES = new Set([
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

function timeoutSignal(milliseconds) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), milliseconds);
  return { signal: controller.signal, cancel: () => clearTimeout(timer) };
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
} = {}) {
  const secrets = secretProvider || (async () => ({}));

  async function openai(input, context) {
    const secret = await secrets();
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
    const timeout = timeoutSignal(openaiTimeoutMs);
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
    } catch {
      throw new HttpError(503, "shared_vision_unavailable",
        "The shared OpenAI detector could not be reached.");
    } finally {
      timeout.cancel();
    }
    context.openaiRequestId = response.headers.get("x-request-id") || null;
    if (!response.ok) {
      const upstream = await errorCode(response);
      context.openaiErrorCode = upstream.code || upstream.type;
      if (response.status === 429 && EXHAUSTION_CODES.has(upstream.code)) {
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
      throw new HttpError(response.status >= 500 ? 503 : 422,
        response.status >= 500 ? "shared_vision_unavailable" : "vision_request_rejected",
        "OpenAI could not analyse this image.");
    }
    const data = await response.json().catch(() => null);
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
      request_id: context.requestId,
      model: yoloModel,
      capture_mode: input.captureMode,
      language: input.language,
      prompt_version: DETECT_PROMPT_VERSION,
      schema_version: DETECT_SCHEMA_VERSION,
      images: input.images.map(({ dataUrl }) => ({ data_url: dataUrl })),
    };
  }

  async function yolo(input, context) {
    const secret = await secrets();
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
      const invoked = await lambdaClient.send(new InvokeCommand({
        FunctionName: yoloFunctionName,
        InvocationType: "RequestResponse",
        Payload: Buffer.from(JSON.stringify({
          version: "2.0",
          routeKey: "POST /v1/detect",
          rawPath: "/v1/detect",
          headers: {
            "x-request-id": context.requestId,
            "x-yolo-api-key": secret.yoloApiKey,
            "content-type": "application/json",
          },
          body: JSON.stringify(payload),
          isBase64Encoded: false,
          requestContext: { requestId: context.requestId },
        })),
      }));
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
      const timeout = timeoutSignal(yoloTimeoutMs);
      let response;
      try {
        response = await fetchImpl(yoloUrl, {
          method: "POST",
          redirect: "error",
          headers: {
            "content-type": "application/json",
            "x-request-id": context.requestId,
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
    };
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
      if (providerMode !== "openai_then_yolo") {
        throw new HttpError(503, "shared_vision_not_configured",
          "SHARED_DETECTOR_PROVIDER is invalid.");
      }
      try {
        return await openai(input, context);
      } catch (error) {
        if (!(error instanceof HttpError)
            || !error.details?.fallback_allowed) throw error;
        context.detectorFallbackReason = error.code;
        const result = await yolo(input, context);
        return { ...result, fallbackFrom: "openai", fallbackReason: error.code };
      }
    },
  };
}
