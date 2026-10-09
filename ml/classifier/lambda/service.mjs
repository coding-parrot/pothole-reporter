// The drive-frame screen: one image in, one damaged/undamaged verdict out, in the
// request and response shape infra/aws-yolo/service/handler.py defines and
// infra/aws-central/service/detectors.mjs (yolo, yoloPayload) already speaks. Nothing in
// this file touches the model; the scorer is passed in, so the contract is testable
// without native code. Request bodies and images are never logged.

import { createHash, randomUUID, timingSafeEqual } from "node:crypto";

const REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DATA_URL = /^data:(image\/(?:jpeg|jpg|png|webp));base64,([A-Za-z0-9+/=_-]+)$/i;
const CAPTURE_MODES = new Set(["manual", "drive"]);
const TASK = "road_damage_detection";
const PROMPT_VERSION = "road-damage-v5";
const SCHEMA_VERSION = 4;
// Fixed text: a classifier has a score, not an observation. In yolo_then_openai the
// damaged text is never shown (gpt-5-mini's verdict replaces it); the undamaged one is.
const DESCRIPTIONS = {
  damaged: "The road screen scored this frame as likely road damage.",
  undamaged: "The road screen found no road damage in this frame.",
};

export class ServiceError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function readConfig(env = process.env) {
  const hash = String(env.API_KEY_SHA256 || "").trim().toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(hash)) {
    throw new ServiceError(503, "service_not_configured",
      "The screen credential hash is not configured.");
  }
  const integer = (name, fallback) => {
    const value = Number(env[name] ?? fallback);
    if (!Number.isInteger(value) || value < 1) {
      throw new ServiceError(503, "service_not_configured", `${name} must be a positive integer.`);
    }
    return value;
  };
  let threshold = null;
  if (env.SCREEN_THRESHOLD !== undefined && env.SCREEN_THRESHOLD !== "") {
    threshold = Number(env.SCREEN_THRESHOLD);
    if (!(threshold > 0 && threshold < 1)) {
      throw new ServiceError(503, "service_not_configured",
        "SCREEN_THRESHOLD must be between 0 and 1.");
    }
  }
  return {
    apiKeySha256: hash,
    maxJsonBodyBytes: integer("MAX_JSON_BODY_BYTES", 5_500_000),
    maxImageBytes: integer("MAX_IMAGE_BYTES", 3_500_000),
    // Null means: use the threshold the model was released with (model.json).
    threshold,
  };
}

const lowerHeaders = (event) => Object.fromEntries(
  Object.entries(event?.headers || {})
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => [String(key).toLowerCase(), String(value)]),
);

function authenticate(headers, config) {
  let token = (headers["x-yolo-api-key"] || "").trim();
  if (!token && (headers.authorization || "").startsWith("Bearer ")) {
    token = headers.authorization.slice(7);
  }
  if (!token || token.length > 2048) {
    throw new ServiceError(401, "unauthorized", "The gateway credential is invalid.");
  }
  const supplied = createHash("sha256").update(token, "utf8").digest();
  if (!timingSafeEqual(supplied, Buffer.from(config.apiKeySha256, "hex"))) {
    throw new ServiceError(401, "unauthorized", "The gateway credential is invalid.");
  }
}

function magicMatches(mime, content) {
  if (mime === "image/jpeg") return content.subarray(0, 3).equals(Buffer.from([0xff, 0xd8, 0xff]));
  if (mime === "image/png") return content.subarray(0, 8).equals(Buffer.from("\x89PNG\r\n\x1a\n", "latin1"));
  if (mime === "image/webp") {
    return content.length >= 12 && content.toString("latin1", 0, 4) === "RIFF"
      && content.toString("latin1", 8, 12) === "WEBP";
  }
  return false;
}

function decodeImage(value, config) {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).length !== 1 || typeof value.data_url !== "string") {
    throw new ServiceError(400, "bad_image", "images[0] must contain only a data_url.");
  }
  const match = DATA_URL.exec(value.data_url);
  if (!match) {
    throw new ServiceError(400, "bad_image", "images[0] must be a base64 JPEG, PNG or WebP data URL.");
  }
  const mime = match[1].toLowerCase().replace("image/jpg", "image/jpeg");
  const content = Buffer.from(match[2].replace(/-/g, "+").replace(/_/g, "/"), "base64");
  if (!content.length || content.length > config.maxImageBytes) {
    throw new ServiceError(413, "image_too_large", "images[0] is empty or exceeds the per-image limit.");
  }
  if (!magicMatches(mime, content)) {
    throw new ServiceError(400, "bad_image", "images[0] content does not match its media type.");
  }
  return content;
}

// detectors.mjs invokes the function directly with routeKey "POST /v1/detect" and no
// requestContext.http; API Gateway and a function URL send requestContext.http.
function methodAndPath(event) {
  const http = event?.requestContext?.http || {};
  const [routeMethod, routePath] = String(event?.routeKey || "").split(" ");
  return {
    method: String(http.method || event?.httpMethod || routeMethod || "").toUpperCase(),
    path: String(http.path || event?.rawPath || event?.path || routePath || ""),
  };
}

function parseRequest(event, headers, config) {
  const { method, path } = methodAndPath(event);
  if (method !== "POST" || !["/v1/detect", ""].includes(path)) {
    throw new ServiceError(404, "not_found", "This route does not exist.");
  }
  const contentType = (headers["content-type"] || "").split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "application/json") {
    throw new ServiceError(415, "unsupported_media_type", "Content-Type must be application/json.");
  }
  if (typeof event.body !== "string" || !event.body) {
    throw new ServiceError(400, "bad_request", "Send a JSON request body.");
  }
  const raw = event.isBase64Encoded ? Buffer.from(event.body, "base64") : Buffer.from(event.body, "utf8");
  if (raw.length > config.maxJsonBodyBytes) {
    throw new ServiceError(413, "request_too_large", "The JSON request body is too large.");
  }
  let body;
  try {
    body = JSON.parse(raw.toString("utf8"));
  } catch {
    throw new ServiceError(400, "bad_json", "The request body is not valid JSON.");
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new ServiceError(400, "bad_json", "The request body must be a JSON object.");
  }
  if (body.version !== 1 || body.task !== TASK) {
    throw new ServiceError(409, "contract_mismatch", "This screen requires contract version 1.");
  }
  if (body.schema_version !== SCHEMA_VERSION || body.prompt_version !== PROMPT_VERSION) {
    throw new ServiceError(409, "schema_version_mismatch",
      `This screen requires ${PROMPT_VERSION} schema ${SCHEMA_VERSION}.`);
  }
  if (!CAPTURE_MODES.has(String(body.capture_mode))) {
    throw new ServiceError(400, "bad_capture_mode", "capture_mode must be manual or drive.");
  }
  const requestId = String(body.request_id || "").trim();
  if (!REQUEST_ID.test(requestId)) {
    throw new ServiceError(400, "bad_request_id", "request_id is invalid.");
  }
  const headerId = (headers["x-request-id"] || "").trim();
  if (headerId && headerId !== requestId) {
    throw new ServiceError(400, "request_id_mismatch", "Header and body request IDs differ.");
  }
  if (!Array.isArray(body.images) || body.images.length !== 1) {
    throw new ServiceError(400, "bad_image_count", "Send exactly one image.");
  }
  return { requestId, image: decodeImage(body.images[0], config) };
}

function respond(status, requestId, payload) {
  return {
    statusCode: status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-request-id": requestId,
    },
    body: JSON.stringify({ request_id: requestId, ...payload }),
    isBase64Encoded: false,
  };
}

// scorer: { meta: { model_version, threshold }, score(buffer) -> { score, decodeMs, inferMs, boxes? } }.
// It may be a promise (the model loads during Lambda init) or a function returning one.
export function createHandler({ config = null, scorer, log = console.log } = {}) {
  return async function handle(event, lambdaContext = {}) {
    const started = performance.now();
    const headers = lowerHeaders(event);
    const headerId = (headers["x-request-id"] || "").trim();
    let requestId = REQUEST_ID.test(headerId) ? headerId
      : REQUEST_ID.test(String(lambdaContext.awsRequestId || "")) ? lambdaContext.awsRequestId
        : randomUUID();
    const record = { event: "screen_request_complete", outcome: "internal_error", status: 500 };
    try {
      const active = config || readConfig();
      authenticate(headers, active);
      const request = parseRequest(event, headers, active);
      requestId = request.requestId;
      const model = await (typeof scorer === "function" ? scorer() : scorer);
      const threshold = active.threshold ?? model.meta.threshold;
      let result;
      try {
        result = await model.score(request.image);
      } catch (error) {
        if (error?.code === "bad_image") {
          throw new ServiceError(400, "bad_image", "The image could not be decoded safely.");
        }
        throw error;
      }
      if (!(result.score >= 0 && result.score <= 1)) throw new Error("the model returned no score");
      const damaged = result.score >= threshold;
      const verdict = {
        image_quality: "acceptable",
        assessment: damaged ? "damaged" : "undamaged",
        // The YOLO contract's one damage type. gpt-5-mini names the real subtype.
        damage_type: damaged ? "pothole_cavity" : null,
        size: null,
        description: damaged ? DESCRIPTIONS.damaged : DESCRIPTIONS.undamaged,
      };
      Object.assign(record, {
        status: 200, outcome: verdict.assessment, score: Number(result.score.toFixed(5)),
        threshold, model_version: model.meta.model_version,
        decode_ms: Math.round(result.decodeMs), infer_ms: Math.round(result.inferMs),
        ...(Array.isArray(result.boxes) ? { boxes: result.boxes.length } : {}),
      });
      return respond(200, requestId, {
        verdict,
        model: model.meta.model_version,
        // Beside the verdict, not inside it: the verdict keeps the five contract fields.
        score: record.score,
        threshold,
        // A detector also says where: boxes as fractions of the frame, best first. A
        // classifier has none and the field is absent.
        ...(Array.isArray(result.boxes) ? { boxes: result.boxes } : {}),
      });
    } catch (error) {
      if (error instanceof ServiceError) {
        Object.assign(record, { status: error.status, outcome: error.code });
        return respond(error.status, requestId, { error: error.code, message: error.message });
      }
      // The type is useful and cannot carry image or body data.
      Object.assign(record, { status: 503, outcome: "inference_unavailable",
        error_type: String(error?.name || "Error").slice(0, 80) });
      return respond(503, requestId, {
        error: "inference_unavailable",
        message: "The road screen is temporarily unavailable.",
      });
    } finally {
      log(JSON.stringify({ ...record, request_id: requestId,
        aws_request_id: lambdaContext.awsRequestId || null,
        latency_ms: Math.round(performance.now() - started) }));
    }
  };
}
