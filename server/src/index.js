import {
  LLM_CONTRACT,
  DETECT_PROMPT,
  DETECT_PROMPT_VERSION,
  DETECT_SCHEMA,
  DETECT_SCHEMA_VERSION,
  TENDER_INSTRUCTIONS,
  TENDER_SCHEMA,
  MODEL_CONFIG,
  RUNTIME_CONFIG,
  IMAGING_CONFIG,
  TENDER_CONFIG,
} from "../../llm/generated/contract.mjs";

const DETECT_PROMPT_CONFIG = LLM_CONTRACT.prompts.detection;
const TENDER_PROMPT_CONFIG = LLM_CONTRACT.prompts.tender;

const MAX_SIGNATURE_AGE_MS = 5 * 60_000;
const MAX_JSON_BODY_BYTES = 17_000_000;
const MAX_IMAGE_BYTES = 3_500_000;
const MAX_DETECT_TOTAL_BYTES = 8_000_000;
const MAX_YOLO_DETECT_TOTAL_BYTES = 4_000_000;
const OPENAI_URL = RUNTIME_CONFIG.responsesUrl;
const SHARED_DETECTOR_PROVIDERS = new Set([
  "openai",
  "http_yolo",
  "openai_then_http_yolo",
]);
// These are the non-retryable billing/credit/usage codes documented by OpenAI.
// A plain 429, rate_limit_exceeded, slow_down, insufficient_quota type, timeout,
// authentication error, or 5xx is deliberately not an exhaustion signal.
const OPENAI_EXHAUSTION_CODES = new Set([
  "credit_balance_exhausted",
  "organization_spend_limit_exceeded",
  "project_spend_limit_exceeded",
  "organization_usage_limit_exceeded",
]);
// These are emitted by the project-owned AWS gateway only after its atomic
// DynamoDB monthly admission check. No other 429 code opens the circuit.
const YOLO_MONTHLY_CAP_CODES = new Set([
  "monthly_request_cap_exceeded",
  "monthly_estimated_budget_cap_exceeded",
]);
const DEFAULT_YOLO_MODEL = "pothole-yolo";
const DEFAULT_YOLO_AWS_REGION = "ap-south-1";
const AWS_SIGV4_ALGORITHM = "AWS4-HMAC-SHA256";
const AWS_SIGV4_SERVICE = "execute-api";
// In fallback mode these calls can happen sequentially. Keep their combined
// ceiling below the 100-second shared-client deadline, with network/Worker
// overhead left outside the upstream budgets.
const MAX_OPENAI_UPSTREAM_TIMEOUT_MS = RUNTIME_CONFIG.timeoutsMs.serverOpenAIMax;
const MAX_YOLO_UPSTREAM_TIMEOUT_MS = RUNTIME_CONFIG.timeoutsMs.serverYoloMax;
const KGIS_TOWN_URL =
  "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/Admin_Dynamic_New/MapServer/1/query";
const KGIS_NH_URL =
  "https://kgis.ksrsac.in/kgismaps/rest/services/State_Basemap/State_Basemap_Dynamic/MapServer/289/query";
const KGIS_SH_URL =
  "https://kgis.ksrsac.in/kgismaps/rest/services/State_Basemap/State_Basemap_Dynamic/MapServer/290/query";
const KGIS_DH_URL =
  "https://kgis.ksrsac.in/kgismaps/rest/services/State_Basemap/State_Basemap_Dynamic/MapServer/291/query";
const KGIS_GP_URL =
  "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/GP_Boundary/MapServer/0/query";
const DEFAULT_NH_PROXIMITY_METRES = 20;
const PUBLIC_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse";
const ALLOWED_MODELS = new Set(MODEL_CONFIG.allowedModels);
const ALLOWED_IMAGE_DETAILS = new Set(MODEL_CONFIG.allowedImageDetails);
const ORIGINAL_DETAIL_MODELS = new Set(MODEL_CONFIG.originalDetailModels);
const ALLOWED_LANGUAGES = new Set(MODEL_CONFIG.allowedLanguages);
const ALLOWED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const CAPTURE_SOURCES = new Set([
  "manual",
  "drive_live",
  "drive_vod",
  "imported_video",
]);
const LOCATION_SOURCES = new Set([
  "device_gps",
  "gpx_timestamp",
  "current_position_confirmed",
  "none",
]);
const DRIVE_CAPTURE_SOURCES = new Set(["drive_live", "drive_vod", "imported_video"]);
const schemaStrings = (field) => new Set(
  DETECT_SCHEMA.properties[field].enum.filter((value) => typeof value === "string"));
const DAMAGE_TYPES = schemaStrings("damage_type");
const SIZES = schemaStrings("size");
const KNOWN_ROUTES = new Set([
  "/", "/map", "/v1/health", "/v1/map", "/v1/impact",
  "/v1/installations", "/v1/activity", "/v1/vision/detect",
  "/v1/tenders/resolve", "/v1/potholes/report",
]);
const EARTH_RADIUS_M = 6_371_000;
const LOCATION_CACHE_TTL_MS = 5 * 60_000;
const LOCATION_CACHE_MAX = 256;
const DEFAULT_IDEMPOTENCY_CLAIM_TTL_MS = 3 * 60_000;
const TENDER_IDEMPOTENCY_TTL_MS = 24 * 60 * 60_000;
// Ownership policy is part of a tender result. Namespace the D1 replay key so a
// deployment that adds a stricter NH/SH/DH gate can never replay a municipal or
// contractor answer cached by an older policy, even while old clients keep using
// the same caller-visible Idempotency-Key.
const TENDER_IDEMPOTENCY_ROUTE = "/v1/tenders/resolve@ownership-v2";
// Browser and WorkManager outboxes may survive a multi-day civic-service outage.
// The receipt is single-observation, image, verdict and location bound, so a 30-day
// retry window preserves delivery without authorizing a second map point.
const DETECTION_RECEIPT_TTL_MS = 30 * 24 * 60 * 60_000;
const CONSUMED_RECEIPT_RETENTION_MS = 180 * 24 * 60 * 60_000;
const PUBLIC_NOMINATIM_INTERVAL_MS = 1_000;
const BOUNDED_IDEMPOTENCY_ROUTES = new Set([
  TENDER_IDEMPOTENCY_ROUTE,
  "/v1/vision/detect",
]);
// Personal-key detections are client-attested, so the server accepts only
// explicitly supported contracts. Shared detections with a receipt are instead
// validated against the immutable contract version stored in that receipt; this
// lets a queued observation survive a later prompt deployment.
const SUPPORTED_PERSONAL_DETECTOR_CONTRACTS = new Set([
  `${DETECT_PROMPT_VERSION}:${DETECT_SCHEMA_VERSION}`,
]);
const locationCache = new Map();

function idempotencyStorageRoute(pathname) {
  return pathname === "/v1/tenders/resolve" ? TENDER_IDEMPOTENCY_ROUTE : pathname;
}

class HttpError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const nowMs = () => Date.now();
const isoDay = (value = nowMs()) => new Date(value).toISOString().slice(0, 10);
const isoMonth = (value = nowMs()) => new Date(value).toISOString().slice(0, 7);
const isoMinute = (value = nowMs()) => new Date(value).toISOString().slice(0, 16);
const finiteNumber = (value, fallback = NaN) => {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : fallback;
};
// Coordinates cross a public JSON boundary and must be JSON numbers. Number(null),
// Number("") and Number(true) are finite, but accepting those coercions would put a
// caller's malformed report at (0,0) or (1,1) on the public map.
const externalCoordinate = (value) =>
  typeof value === "number" && Number.isFinite(value) ? value : NaN;
const boundedString = (value, max) =>
  typeof value === "string" ? value.trim().slice(0, max) : "";
const validLatLng = (lat, lng) =>
  Number.isFinite(lat) && Number.isFinite(lng)
  && lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180;
const NON_MUNICIPAL_ROAD_OWNERSHIPS = new Set([
  "national_highway", "state_highway", "district_highway",
  "rural", "outside_state",
]);

function captureProvenance(
  body,
  captureMode,
  hasCoordinates,
  { allowLocationLabelWithoutCoordinates = false } = {},
) {
  const defaultCaptureSource = captureMode === "drive" ? "drive_live" : "manual";
  const captureSource = Object.prototype.hasOwnProperty.call(body, "capture_source")
    ? boundedString(body.capture_source, 32) : defaultCaptureSource;
  if (!CAPTURE_SOURCES.has(captureSource)) {
    throw new HttpError(400, "bad_capture_source",
      "capture_source must be manual, drive_live, drive_vod or imported_video.");
  }
  if (captureMode === "manual" && captureSource !== "manual") {
    throw new HttpError(400, "bad_capture_source",
      "Manual capture_mode requires capture_source manual.");
  }
  if (captureMode === "drive" && !DRIVE_CAPTURE_SOURCES.has(captureSource)) {
    throw new HttpError(400, "bad_capture_source",
      "Drive capture_mode requires drive_live, drive_vod or imported_video.");
  }

  const defaultLocationSource = hasCoordinates ? "device_gps" : "none";
  const locationSource = Object.prototype.hasOwnProperty.call(body, "location_source")
    ? boundedString(body.location_source, 40) : defaultLocationSource;
  if (!LOCATION_SOURCES.has(locationSource)) {
    throw new HttpError(400, "bad_location_source",
      "location_source must be device_gps, gpx_timestamp, current_position_confirmed or none.");
  }
  if (!allowLocationLabelWithoutCoordinates
      && ((hasCoordinates && locationSource === "none")
        || (!hasCoordinates && locationSource !== "none"))) {
    throw new HttpError(400, "bad_location_source",
      "location_source must be none without coordinates and non-none when coordinates are supplied.");
  }
  if (["gpx_timestamp", "current_position_confirmed"].includes(locationSource)
      && captureSource !== "imported_video") {
    throw new HttpError(400, "bad_location_source",
      "GPX and confirmed-current-position provenance is valid only for imported_video.");
  }
  return { captureSource, locationSource };
}

// Only server-side KGIS resolution can change the public ownership projection.
// Nonmunicipal results deliberately have source="unresolved" because they do not
// expose a municipal LGD, so completeness must be checked from the lookup evidence.
function authoritativeRoadOwnership(jurisdiction) {
  if (!jurisdiction || typeof jurisdiction !== "object") return null;
  const ownership = jurisdiction.road_ownership;
  if (ownership === "municipal") {
    return jurisdiction.source === "kgis" && boundedString(jurisdiction.lgd, 64)
      ? ownership : null;
  }
  const lookup = jurisdiction.lookup;
  if (!NON_MUNICIPAL_ROAD_OWNERSHIPS.has(ownership)
      || !lookup || lookup.kgis !== "available") return null;
  if (["rural", "outside_state"].includes(ownership)
      && lookup.kgis_gp !== "available") return null;
  return ownership;
}

function corsHeaders() {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers":
      "content-type,x-install-id,x-timestamp,x-signature,idempotency-key",
    "access-control-expose-headers": "x-request-id",
  };
}

function jsonResponse(payload, status, requestId, extraHeaders = {}) {
  return new Response(JSON.stringify({ request_id: requestId, ...payload }), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-request-id": requestId,
      ...corsHeaders(),
      ...extraHeaders,
    },
  });
}

function errorResponse(error, requestId) {
  const known = error instanceof HttpError;
  const status = known ? error.status : 500;
  const code = known ? error.code : "internal_error";
  const message = known
    ? error.message
    : "The service could not complete this request.";
  return jsonResponse({
    error: code,
    message,
    ...(known && error.details ? { details: error.details } : {}),
  }, status, requestId);
}

function parseYoloExecuteApiEndpoint(value, expectedRegion = DEFAULT_YOLO_AWS_REGION) {
  const endpoint = boundedString(value, 2_048);
  try {
    const url = new URL(endpoint);
    const host = /^([a-z0-9]+)\.execute-api\.([a-z0-9-]+)\.amazonaws\.com$/
      .exec(url.hostname);
    if (url.protocol !== "https:" || url.username || url.password || url.port
        || url.search || url.hash || url.pathname !== "/v1/detect" || !host) {
      return { endpoint, configured: false, error: "invalid_execute_api_endpoint" };
    }
    if (host[2] !== expectedRegion) {
      return { endpoint, configured: false, error: "endpoint_region_mismatch" };
    }
    return {
      endpoint: url.href,
      configured: true,
      error: null,
      api_id: host[1],
      region: host[2],
    };
  } catch {
    return { endpoint, configured: false, error: "invalid_execute_api_endpoint" };
  }
}

function httpYoloDetectorStatus(env) {
  const awsRegion = boundedString(env.YOLO_AWS_REGION, 32).toLowerCase()
    || DEFAULT_YOLO_AWS_REGION;
  const endpoint = parseYoloExecuteApiEndpoint(env.YOLO_API_URL, awsRegion);
  const bearerConfigured = Boolean(boundedString(env.YOLO_API_KEY, 2_048));
  const accessKeyId = boundedString(env.YOLO_AWS_ACCESS_KEY_ID, 128);
  const secretAccessKey = boundedString(env.YOLO_AWS_SECRET_ACCESS_KEY, 2_048);
  const awsCredentialsConfigured = /^[A-Za-z0-9]{16,128}$/.test(accessKeyId)
    && secretAccessKey.length >= 16;
  let error = endpoint.error;
  if (!error && !awsCredentialsConfigured) error = "missing_aws_credentials";
  else if (!error && !bearerConfigured) error = "missing_bearer_key";
  return {
    provider: "http_yolo",
    model: boundedString(env.YOLO_MODEL, 80) || DEFAULT_YOLO_MODEL,
    endpoint: endpoint.endpoint,
    configured: endpoint.configured && awsCredentialsConfigured && bearerConfigured,
    bearer_configured: bearerConfigured,
    aws_credentials_configured: awsCredentialsConfigured,
    aws_region: awsRegion,
    error,
  };
}

// The mobile contract deliberately exposes only `shared_server`. This selector is
// an internal seam: operators can replace the paid vision backend without forcing
// clients or already stored detector provenance through a migration.
function sharedDetectorStatus(env) {
  const configuredName = boundedString(env.SHARED_DETECTOR_PROVIDER, 32)
    .toLowerCase() || "openai";
  if (!SHARED_DETECTOR_PROVIDERS.has(configuredName)) {
    return {
      provider: configuredName,
      model: null,
      configured: false,
      error: "unsupported_provider",
    };
  }
  if (configuredName === "http_yolo") {
    const primary = httpYoloDetectorStatus(env);
    return {
      ...primary,
      primary_provider: primary.provider,
      primary_configured: primary.configured,
      fallback_provider: null,
      fallback_configured: false,
      fallback_model: null,
    };
  }
  if (configuredName === "openai_then_http_yolo") {
    // Every YOLO mode requires the same IAM-signed HTTPS gateway plus the
    // defense-in-depth project token, so a configuration error fails closed.
    const fallback = httpYoloDetectorStatus(env);
    const primaryConfigured = Boolean(env.OPENAI_API_KEY);
    return {
      provider: configuredName,
      model: primaryConfigured ? MODEL_CONFIG.defaultModel : fallback.model,
      // A complete YOLO gateway is the required safety boundary for chained
      // mode. OpenAI is preferred when configured, but an absent key means its
      // credits are unavailable and must not disable the configured fallback.
      configured: fallback.configured,
      error: fallback.error,
      primary_provider: "openai",
      primary_configured: primaryConfigured,
      fallback_provider: fallback.provider,
      fallback_configured: fallback.configured,
      fallback_model: fallback.model,
      fallback,
    };
  }
  return {
    provider: configuredName,
    model: MODEL_CONFIG.defaultModel,
    configured: Boolean(env.OPENAI_API_KEY),
    error: env.OPENAI_API_KEY ? null : "missing_api_key",
    primary_provider: "openai",
    primary_configured: Boolean(env.OPENAI_API_KEY),
    fallback_provider: null,
    fallback_configured: false,
    fallback_model: null,
  };
}

function requireSharedDetector(env) {
  const status = sharedDetectorStatus(env);
  if (!status.configured) {
    throw new HttpError(503, "shared_vision_not_configured",
      "The shared vision detector is not configured. Use your own OpenAI key.", {
        provider: status.provider,
      });
  }
  return status;
}

function bytesToHex(value) {
  return [...new Uint8Array(value)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function sha256Hex(value) {
  const bytes = typeof value === "string"
    ? new TextEncoder().encode(value)
    : value instanceof Uint8Array ? value : new Uint8Array(value);
  return bytesToHex(await crypto.subtle.digest("SHA-256", bytes));
}

async function hmacSha256(key, value) {
  const bytes = typeof key === "string" ? new TextEncoder().encode(key) : key;
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    bytes,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return new Uint8Array(await crypto.subtle.sign(
    "HMAC",
    cryptoKey,
    new TextEncoder().encode(value),
  ));
}

function canonicalAwsHeaderValue(value) {
  return String(value).trim().replace(/\s+/g, " ");
}

async function awsSigV4Headers({
  endpoint,
  region,
  accessKeyId,
  secretAccessKey,
  sessionToken = "",
  yoloApiKey,
  requestId,
  body,
  now = new Date(),
}) {
  const url = new URL(endpoint);
  const amzDate = now.toISOString().replace(/[:-]|\.\d{3}/g, "");
  const date = amzDate.slice(0, 8);
  const payloadHash = await sha256Hex(body);
  const signed = {
    "content-type": "application/json",
    host: url.host,
    "x-amz-content-sha256": payloadHash,
    "x-amz-date": amzDate,
    "x-request-id": requestId,
    "x-yolo-api-key": yoloApiKey,
  };
  if (sessionToken) signed["x-amz-security-token"] = sessionToken;
  const signedHeaderNames = Object.keys(signed).sort();
  const canonicalHeaders = signedHeaderNames
    .map((name) => `${name}:${canonicalAwsHeaderValue(signed[name])}`)
    .join("\n") + "\n";
  const signedHeaders = signedHeaderNames.join(";");
  const canonicalRequest = [
    "POST",
    url.pathname,
    "",
    canonicalHeaders,
    signedHeaders,
    payloadHash,
  ].join("\n");
  const credentialScope = `${date}/${region}/${AWS_SIGV4_SERVICE}/aws4_request`;
  const stringToSign = [
    AWS_SIGV4_ALGORITHM,
    amzDate,
    credentialScope,
    await sha256Hex(canonicalRequest),
  ].join("\n");
  const dateKey = await hmacSha256(`AWS4${secretAccessKey}`, date);
  const regionKey = await hmacSha256(dateKey, region);
  const serviceKey = await hmacSha256(regionKey, AWS_SIGV4_SERVICE);
  const signingKey = await hmacSha256(serviceKey, "aws4_request");
  const signature = bytesToHex(await hmacSha256(signingKey, stringToSign));
  const headers = { ...signed };
  delete headers.host;
  headers.authorization = `${AWS_SIGV4_ALGORITHM} `
    + `Credential=${accessKeyId}/${credentialScope}, `
    + `SignedHeaders=${signedHeaders}, Signature=${signature}`;
  return headers;
}

function base64ToBytes(value) {
  try {
    const normalized = String(value || "")
      .replace(/-/g, "+")
      .replace(/_/g, "/")
      .replace(/\s/g, "");
    const padded = normalized + "=".repeat((4 - normalized.length % 4) % 4);
    const binary = atob(padded);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index++) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  } catch {
    throw new HttpError(400, "bad_base64", "A base64 value could not be decoded.");
  }
}

function readDerLength(bytes, cursor) {
  const first = bytes[cursor.index++];
  if (first === undefined) throw new Error("missing DER length");
  if ((first & 0x80) === 0) return first;
  const count = first & 0x7f;
  if (count < 1 || count > 2) throw new Error("unsupported DER length");
  let length = 0;
  for (let index = 0; index < count; index++) {
    const next = bytes[cursor.index++];
    if (next === undefined) throw new Error("truncated DER length");
    length = length * 256 + next;
  }
  return length;
}

// Browser WebCrypto emits IEEE-P1363 r||s while Android SHA256withECDSA commonly
// emits ASN.1 DER. Cloudflare WebCrypto verifies P1363, so accept and normalize both.
function normalizeP256Signature(bytes) {
  if (bytes.length === 64) return bytes;
  try {
    const cursor = { index: 0 };
    if (bytes[cursor.index++] !== 0x30) throw new Error("not a sequence");
    const sequenceLength = readDerLength(bytes, cursor);
    if (cursor.index + sequenceLength !== bytes.length) throw new Error("bad sequence length");
    const values = [];
    for (let part = 0; part < 2; part++) {
      if (bytes[cursor.index++] !== 0x02) throw new Error("not an integer");
      const length = readDerLength(bytes, cursor);
      let value = bytes.slice(cursor.index, cursor.index + length);
      cursor.index += length;
      while (value.length > 32 && value[0] === 0) value = value.slice(1);
      if (!value.length || value.length > 32) throw new Error("bad integer");
      const padded = new Uint8Array(32);
      padded.set(value, 32 - value.length);
      values.push(padded);
    }
    if (cursor.index !== bytes.length) throw new Error("trailing data");
    const raw = new Uint8Array(64);
    raw.set(values[0], 0);
    raw.set(values[1], 32);
    return raw;
  } catch {
    throw new HttpError(401, "bad_signature", "This request signature is not valid.");
  }
}

function imageMagicMatches(mime, bytes) {
  if (mime === "image/jpeg") {
    return bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
  }
  if (mime === "image/png") {
    return bytes.length >= 8
      && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e
      && bytes[3] === 0x47 && bytes[4] === 0x0d && bytes[5] === 0x0a
      && bytes[6] === 0x1a && bytes[7] === 0x0a;
  }
  if (mime === "image/webp") {
    return bytes.length >= 12
      && String.fromCharCode(...bytes.slice(0, 4)) === "RIFF"
      && String.fromCharCode(...bytes.slice(8, 12)) === "WEBP";
  }
  return false;
}

function validateImage(input, label) {
  const dataUrl = typeof input === "string" ? input : input && input.data_url;
  if (typeof dataUrl !== "string") {
    throw new HttpError(400, "bad_image", `${label} must contain a data_url.`);
  }
  const match = /^data:(image\/(?:jpeg|jpg|png|webp));base64,([A-Za-z0-9+/=_-]+)$/i.exec(dataUrl);
  if (!match) {
    throw new HttpError(400, "bad_image", `${label} must be a base64 JPEG, PNG or WebP data URL.`);
  }
  const mime = match[1].toLowerCase() === "image/jpg"
    ? "image/jpeg" : match[1].toLowerCase();
  if (!ALLOWED_IMAGE_TYPES.has(mime)) {
    throw new HttpError(415, "unsupported_image", `${label} has an unsupported image type.`);
  }
  const bytes = base64ToBytes(match[2]);
  if (!bytes.length || bytes.length > MAX_IMAGE_BYTES) {
    throw new HttpError(413, "image_too_large",
      `${label} must be non-empty and no larger than 3.5 MB.`);
  }
  if (!imageMagicMatches(mime, bytes)) {
    throw new HttpError(400, "bad_image", `${label} content does not match its media type.`);
  }
  return {
    dataUrl,
    mime,
    bytes: bytes.length,
    decoded: bytes,
  };
}

function rejectOversizedDeclaredBody(request) {
  const declared = request.headers.get("content-length");
  if (declared == null || declared === "") return;
  if (!/^\d+$/.test(declared)) {
    throw new HttpError(400, "bad_content_length", "Content-Length must be a byte count.");
  }
  if (BigInt(declared) > BigInt(MAX_JSON_BODY_BYTES)) {
    throw new HttpError(413, "request_too_large",
      "The JSON request body may be no larger than 17 MB.");
  }
}

async function readJsonBodyBytes(request, context) {
  if (context.rawBody) return context.rawBody;
  rejectOversizedDeclaredBody(request);
  if (!request.body) {
    context.rawBody = new Uint8Array(0);
    return context.rawBody;
  }

  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const chunk = value instanceof Uint8Array ? value : new Uint8Array(value);
    total += chunk.byteLength;
    if (total > MAX_JSON_BODY_BYTES) {
      try { await reader.cancel("request body exceeds limit"); } catch { /* best effort */ }
      throw new HttpError(413, "request_too_large",
        "The JSON request body may be no larger than 17 MB.");
    }
    chunks.push(chunk);
  }

  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  context.rawBody = bytes;
  return bytes;
}

async function parseJsonBody(request, context) {
  await readJsonBodyBytes(request, context);
  if (!context.rawBody.length) {
    throw new HttpError(400, "bad_request", "Send a JSON request body.");
  }
  try {
    const parsed = JSON.parse(new TextDecoder().decode(context.rawBody));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new HttpError(400, "bad_json", "The request body must be a JSON object.");
    }
    return parsed;
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(400, "bad_json", "The request body is not valid JSON.");
  }
}

async function importInstallationKey(publicKey, format) {
  try {
    return await crypto.subtle.importKey(
      format,
      base64ToBytes(publicKey),
      { name: "ECDSA", namedCurve: "P-256" },
      false,
      ["verify"],
    );
  } catch {
    throw new HttpError(400, "bad_public_key", "The P-256 public key could not be imported.");
  }
}

async function authenticate(request, env, context) {
  const installId = boundedString(request.headers.get("x-install-id"), 64);
  const timestamp = boundedString(request.headers.get("x-timestamp"), 32);
  const signatureText = boundedString(request.headers.get("x-signature"), 512);
  if (!installId || !timestamp || !signatureText) {
    throw new HttpError(401, "unsigned_request",
      "X-Install-ID, X-Timestamp and X-Signature are required.");
  }
  if (!/^[a-f0-9]{32}$/i.test(installId)) {
    throw new HttpError(401, "unknown_installation", "This app installation is not registered.");
  }
  const sentAt = Number(timestamp);
  if (!Number.isFinite(sentAt) || Math.abs(nowMs() - sentAt) > MAX_SIGNATURE_AGE_MS) {
    throw new HttpError(401, "stale_request", "The signed request is too old. Try again.");
  }

  const installation = await env.DB.prepare(
    `SELECT id, public_key, public_key_format, revoked_at
       FROM installations WHERE id = ?1`
  ).bind(installId).first();
  if (!installation || installation.revoked_at) {
    throw new HttpError(401, "unknown_installation", "This app installation is not registered.");
  }
  await readJsonBodyBytes(request, context);
  context.bodyHash = await sha256Hex(context.rawBody);
  context.idempotencyKey = boundedString(request.headers.get("idempotency-key"), 180);
  const canonical = [
    request.method.toUpperCase(),
    new URL(request.url).pathname,
    timestamp,
    context.idempotencyKey,
    context.bodyHash,
  ].join("\n");
  const publicKey = await importInstallationKey(
    installation.public_key,
    installation.public_key_format || "raw",
  );
  const signature = normalizeP256Signature(base64ToBytes(signatureText));
  const verified = await crypto.subtle.verify(
    { name: "ECDSA", hash: "SHA-256" },
    publicKey,
    signature,
    new TextEncoder().encode(canonical),
  );
  if (!verified) {
    throw new HttpError(401, "bad_signature", "This request signature is not valid.");
  }
  context.installId = installId;
  context.signatureHash = await sha256Hex(signature);
  await env.DB.prepare(
    `UPDATE installations SET last_seen_at = ?1 WHERE id = ?2`
  ).bind(nowMs(), installId).run();
  return installation;
}

async function rejectReplay(env, context) {
  if (!env.DEVICES || !context.signatureHash) return;
  const key = `replay:${context.installId}:${context.signatureHash}`;
  if (await env.DEVICES.get(key)) {
    throw new HttpError(409, "replayed_request", "This signed request was already used.");
  }
  await env.DEVICES.put(key, "1", { expirationTtl: 600 });
}

function requireIdempotency(context) {
  if (!context.idempotencyKey) {
    throw new HttpError(400, "idempotency_key_required",
      "Send an Idempotency-Key header for this operation.");
  }
}

function idempotencyClaimTtlMs(env) {
  return Math.min(10 * 60_000, Math.max(DEFAULT_IDEMPOTENCY_CLAIM_TTL_MS,
    finiteNumber(env.IDEMPOTENCY_CLAIM_TTL_MS,
      DEFAULT_IDEMPOTENCY_CLAIM_TTL_MS)));
}

async function idempotentResult(env, context) {
  requireIdempotency(context);
  if (BOUNDED_IDEMPOTENCY_ROUTES.has(context.idempotencyRoute)) {
    // Tender responses contain a reverse-geocoded address, while detection
    // responses contain a short-lived report receipt. Keep both only long enough
    // for safe client retries.
    await env.DB.prepare(
      `DELETE FROM idempotency_keys
        WHERE install_id=?1 AND route=?2 AND idempotency_key=?3 AND created_at<?4`
    ).bind(
      context.installId,
      context.idempotencyRoute,
      context.idempotencyKey,
      nowMs() - TENDER_IDEMPOTENCY_TTL_MS,
    ).run();
  }
  let row = await env.DB.prepare(
    `SELECT request_hash, status_code, response_json
       FROM idempotency_keys
      WHERE install_id = ?1 AND route = ?2 AND idempotency_key = ?3`
  ).bind(context.installId, context.idempotencyRoute, context.idempotencyKey).first();
  if (row) return storedIdempotentResponse(row, context);

  const claimedAt = nowMs();
  // The configured OpenAI timeout can be 90 seconds and tender matching also
  // performs geolocation. Never allow an operator setting to shorten the lease
  // below the safe three-minute baseline and duplicate a paid request in flight.
  const claimTtl = idempotencyClaimTtlMs(env);
  const claimToken = crypto.randomUUID();
  const claimed = await env.DB.prepare(
    `INSERT INTO idempotency_claims
       (install_id,route,idempotency_key,request_hash,claim_token,claimed_at)
     SELECT ?1,?2,?3,?4,?5,?6
      WHERE NOT EXISTS (
        SELECT 1 FROM idempotency_keys
         WHERE install_id=?1 AND route=?2 AND idempotency_key=?3
      )
     ON CONFLICT(install_id,route,idempotency_key) DO UPDATE SET
       request_hash=excluded.request_hash,
       claim_token=excluded.claim_token,
       claimed_at=excluded.claimed_at
     WHERE idempotency_claims.request_hash=excluded.request_hash
       AND idempotency_claims.claimed_at < ?7
     RETURNING claim_token`
  ).bind(
    context.installId,
    context.idempotencyRoute,
    context.idempotencyKey,
    context.bodyHash,
    claimToken,
    claimedAt,
    claimedAt - claimTtl,
  ).first();
  if (claimed && claimed.claim_token === claimToken) {
    context.idempotencyClaimToken = claimToken;
    return null;
  }

  // The owner may have finalized between our initial read and failed claim.
  row = await env.DB.prepare(
    `SELECT request_hash, status_code, response_json
       FROM idempotency_keys
      WHERE install_id = ?1 AND route = ?2 AND idempotency_key = ?3`
  ).bind(context.installId, context.idempotencyRoute, context.idempotencyKey).first();
  if (row) return storedIdempotentResponse(row, context);
  const active = await env.DB.prepare(
    `SELECT request_hash, claimed_at FROM idempotency_claims
      WHERE install_id = ?1 AND route = ?2 AND idempotency_key = ?3`
  ).bind(context.installId, context.idempotencyRoute, context.idempotencyKey).first();
  if (active && active.request_hash !== context.bodyHash) {
    throw new HttpError(409, "idempotency_conflict",
      "That Idempotency-Key is being used for a different request.");
  }
  throw new HttpError(425, "idempotency_in_progress",
    "The operation with this Idempotency-Key is still in progress. Retry it shortly.", {
      retryable: true,
    });
}

function storedIdempotentResponse(row, context) {
  if (row.request_hash !== context.bodyHash) {
    throw new HttpError(409, "idempotency_conflict",
      "That Idempotency-Key was already used for a different request.");
  }
  let payload;
  try {
    payload = JSON.parse(row.response_json);
  } catch {
    throw new HttpError(500, "idempotency_corrupt", "A stored operation result is unreadable.");
  }
  delete payload.request_id;
  context.outcome = "idempotent_replay";
  return jsonResponse({ ...payload, idempotent_replay: true },
    row.status_code, context.requestId);
}

async function rememberIdempotency(
  env,
  context,
  payload,
  statusCode = 200,
  beforeFinalizeStatements = [],
) {
  if (!context.idempotencyClaimToken) {
    throw new HttpError(500, "idempotency_claim_lost",
      "The operation's idempotency claim was lost before completion.");
  }
  const idempotencyIndex = beforeFinalizeStatements.length;
  const results = await env.DB.batch([
    ...beforeFinalizeStatements,
    env.DB.prepare(
      `INSERT INTO idempotency_keys
         (install_id, route, idempotency_key, request_hash, status_code, response_json, created_at)
       SELECT ?1,?2,?3,?4,?5,?6,?7
         FROM idempotency_claims
        WHERE install_id=?1 AND route=?2 AND idempotency_key=?3 AND claim_token=?8
       ON CONFLICT(install_id,route,idempotency_key) DO NOTHING`
    ).bind(
      context.installId,
      context.idempotencyRoute,
      context.idempotencyKey,
      context.bodyHash,
      statusCode,
      JSON.stringify(payload),
      nowMs(),
      context.idempotencyClaimToken,
    ),
    env.DB.prepare(
      `DELETE FROM idempotency_claims
        WHERE install_id=?1 AND route=?2 AND idempotency_key=?3 AND claim_token=?4`
    ).bind(
      context.installId,
      context.idempotencyRoute,
      context.idempotencyKey,
      context.idempotencyClaimToken,
    ),
  ]);
  if (Number(results[idempotencyIndex].meta
      && results[idempotencyIndex].meta.changes || 0) !== 1) {
    throw new HttpError(425, "idempotency_claim_lost",
      "The operation's idempotency lease expired. Retry with the same key.", {
        retryable: true,
      });
  }
  context.idempotencyClaimToken = null;
}

async function releaseIdempotencyClaim(env, context) {
  if (!context.idempotencyClaimToken) return;
  const token = context.idempotencyClaimToken;
  context.idempotencyClaimToken = null;
  await env.DB.prepare(
    `DELETE FROM idempotency_claims
      WHERE install_id=?1 AND route=?2 AND idempotency_key=?3 AND claim_token=?4`
  ).bind(
    context.installId,
    context.idempotencyRoute,
    context.idempotencyKey,
    token,
  ).run();
}

async function incrementCounter(env, scope, counterKey, limit) {
  if (!Number.isFinite(limit)) return { ok: true, used: null, limit: null };
  // Zero is an operator kill switch, not "unlimited". All configured limits have
  // positive defaults, so a negative value also fails closed instead of silently
  // opening an unbounded shared-credit path after a typo.
  if (limit <= 0) return { ok: false, used: 0, limit };
  const row = await env.DB.prepare(
    `INSERT INTO usage_counters(scope,counter_key,used,updated_at)
     VALUES (?1,?2,1,?3)
     ON CONFLICT(scope,counter_key) DO UPDATE SET
       used = usage_counters.used + 1,
       updated_at = excluded.updated_at
     WHERE usage_counters.used < ?4
     RETURNING used`
  ).bind(scope, counterKey, nowMs(), limit).first();
  return row
    ? { ok: true, used: Number(row.used), limit }
    : { ok: false, used: limit, limit };
}

async function rollbackCounters(env, acquired) {
  if (!acquired.length) return;
  // D1 batch() is transactional. A decrement represents this rejected admission,
  // not a particular caller, so concurrent successful admissions remain counted.
  // Keeping zero rows is harmless and avoids a delete/update race.
  await env.DB.batch(acquired.map(({ scope, counterKey }) => env.DB.prepare(
    `UPDATE usage_counters
        SET used = CASE WHEN used > 0 THEN used - 1 ELSE 0 END,
            updated_at = ?3
      WHERE scope=?1 AND counter_key=?2`
  ).bind(scope, counterKey, nowMs())));
}

async function takeVisionQuota(env, installId) {
  const dailyLimit = finiteNumber(env.DAILY_VISION_CAP, 200);
  const globalMinuteLimit = finiteNumber(env.GLOBAL_VISION_MINUTE_CAP, 120);
  const globalDailyLimit = finiteNumber(env.GLOBAL_VISION_DAILY_CAP, 5_000);
  const monthlyLimit = finiteNumber(env.MONTHLY_VISION_CAP, 50_000);
  const acquired = [];
  const take = async (scope, counterKey, limit, errorFactory) => {
    const result = await incrementCounter(env, scope, counterKey, limit);
    if (!result.ok) throw errorFactory(result);
    if (result.limit != null) acquired.push({ scope, counterKey });
    return result;
  };
  try {
    // Check project-wide admission before charging an installation. If any later
    // gate refuses the operation, roll back every counter already acquired so a
    // retry during shared overload cannot consume the caller's daily allowance.
    const globalMinute = await take("global_minute", isoMinute(), globalMinuteLimit,
      () => new HttpError(429, "shared_rate_limit",
        "The shared vision service is at its global per-minute limit. Try shortly.", {
          retryable: true,
          retry_after_seconds: 60,
        }));
    const globalDay = await take("global_day", isoDay(), globalDailyLimit,
      () => new HttpError(503, "shared_daily_budget_reached",
        "The shared vision service has reached today's global limit. Use your own key or try tomorrow.", {
          retryable: true,
        }));
    const monthly = await take("global_month", isoMonth(), monthlyLimit,
      () => new HttpError(503, "shared_budget_reached",
        "The shared vision service has reached its monthly limit. Use your own OpenAI key or try later.", {
          retryable: true,
        }));
    const daily = await take("install_day", `${installId}:${isoDay()}`, dailyLimit,
      (result) => new HttpError(429, "daily_vision_limit",
        `This installation has used today's ${result.limit} shared vision checks.`, {
          retryable: true,
        }));
    return { daily, globalMinute, globalDay, monthly };
  } catch (error) {
    try {
      await rollbackCounters(env, acquired);
    } catch (rollbackError) {
      console.error(JSON.stringify({
        event: "vision_quota_rollback_failed",
        scopes: acquired.map(({ scope }) => scope),
        error: String(rollbackError && rollbackError.message || rollbackError),
      }));
    }
    throw error;
  }
}

async function pruneExpiredTenderReplays(env) {
  const result = await env.DB.prepare(
    `DELETE FROM idempotency_keys
      WHERE route IN ('/v1/tenders/resolve','/v1/tenders/resolve@ownership-v2',
                      '/v1/vision/detect') AND created_at<?1`
  ).bind(nowMs() - TENDER_IDEMPOTENCY_TTL_MS).run();
  return Number(result.meta && result.meta.changes || 0);
}

async function pruneExpiredDetectionReceipts(env) {
  const result = await env.DB.prepare(
    `DELETE FROM shared_detection_receipts
      WHERE (consumed_at IS NULL AND expires_at<?1)
         OR (consumed_at IS NOT NULL AND consumed_at<?2)`
  ).bind(nowMs(), nowMs() - CONSUMED_RECEIPT_RETENTION_MS).run();
  return Number(result.meta && result.meta.changes || 0);
}

async function recordMetrics(env, context, response, elapsedMs) {
  if (!env.DB || context.routeName === "OPTIONS") return;
  const day = isoDay();
  const outcome = context.outcome
    || (response.status < 400 ? "success" : `http_${Math.floor(response.status / 100)}xx`);
  try {
    await env.DB.prepare(
      `INSERT INTO request_metrics_daily(day,route,outcome,vision_mode,request_count)
       VALUES (?1,?2,?3,?4,1)
       ON CONFLICT(day,route,outcome,vision_mode)
       DO UPDATE SET request_count = request_metrics_daily.request_count + 1`
    ).bind(day, context.routeName, outcome, context.visionMode || "none").run();
    if (context.installId) {
      await env.DB.prepare(
        `INSERT INTO installation_activity_daily(day,install_id,request_count,last_seen_at)
         VALUES (?1,?2,1,?3)
         ON CONFLICT(day,install_id) DO UPDATE SET
           request_count = installation_activity_daily.request_count + 1,
           last_seen_at = excluded.last_seen_at`
      ).bind(day, context.installId, nowMs()).run();
    }
  } catch (error) {
    console.error(JSON.stringify({
      event: "metrics_write_failed",
      request_id: context.requestId,
      error: String(error && error.message || error),
    }));
  }
  console.log(JSON.stringify({
    event: "http_request",
    request_id: context.requestId,
    route: context.routeName,
    method: context.method,
    status: response.status,
    outcome,
    vision_mode: context.visionMode || "none",
    duration_ms: elapsedMs,
    pothole_id: context.potholeId || null,
    detector_provider: context.detectorProvider || null,
    detector_request_id: context.detectorRequestId || null,
    openai_request_id: context.openaiRequestId || null,
    detection_openai_request_id: context.detectionOpenAIRequestId || null,
    tender_openai_request_id: context.tenderOpenAIRequestId || null,
    yolo_request_id: context.yoloRequestId || null,
    openai_error_code: context.openaiErrorCode || null,
    yolo_error_code: context.yoloErrorCode || null,
    detector_fallback_reason: context.detectorFallbackReason || null,
    capture_source: context.captureSource || null,
    location_source: context.locationSource || null,
  }));
}

function captureMetricStatement(env, context, {
  captureSource,
  locationSource,
  visionMode,
  outcome,
}) {
  // Tie this aggregate to the still-owned idempotency lease. Cached retries return
  // before reaching this statement; a lost/replaced lease cannot increment a row.
  return env.DB.prepare(
    `INSERT INTO capture_metrics_daily
       (day,capture_source,location_source,vision_mode,outcome,request_count)
     SELECT ?1,?2,?3,?4,?5,1
      WHERE EXISTS (
        SELECT 1 FROM idempotency_claims
         WHERE install_id=?6 AND route=?7 AND idempotency_key=?8 AND claim_token=?9
      )
     ON CONFLICT(day,capture_source,location_source,vision_mode,outcome)
     DO UPDATE SET request_count = capture_metrics_daily.request_count + 1`
  ).bind(
    isoDay(),
    captureSource,
    locationSource,
    visionMode,
    outcome,
    context.installId,
    context.idempotencyRoute,
    context.idempotencyKey,
    context.idempotencyClaimToken,
  );
}

async function handleInstallation(request, env, context) {
  const body = await parseJsonBody(request, context);
  const encoded = boundedString(body.public_key, 512);
  if (!encoded) {
    throw new HttpError(400, "bad_public_key", "public_key is required.");
  }
  const bytes = base64ToBytes(encoded);
  let format;
  if (bytes.length === 65 && bytes[0] === 0x04) format = "raw";
  else if (bytes.length >= 80 && bytes.length <= 160 && bytes[0] === 0x30) format = "spki";
  else {
    throw new HttpError(400, "bad_public_key",
      "public_key must be a raw or SPKI-encoded P-256 public key.");
  }
  await importInstallationKey(encoded, format);
  const installId = (await sha256Hex(bytes)).slice(0, 32);
  const time = nowMs();
  await env.DB.prepare(
    `INSERT INTO installations
       (id,public_key,public_key_format,created_at,last_seen_at,integrity_state,revoked_at)
     VALUES (?1,?2,?3,?4,?4,NULL,NULL)
     ON CONFLICT(id) DO UPDATE SET last_seen_at = excluded.last_seen_at`
  ).bind(installId, encoded, format, time).run();
  context.installId = installId;
  context.outcome = "registered";
  return jsonResponse({ install_id: installId }, 201, context.requestId);
}

async function handleActivity(request, env, context) {
  const body = await parseJsonBody(request, context);
  const cached = await idempotentResult(env, context);
  if (cached) return cached;
  const allowedKeys = new Set([
    "event", "vision_provider", "capture_mode", "capture_source", "location_source",
  ]);
  if (!body || typeof body !== "object" || Array.isArray(body)
      || Object.keys(body).some((key) => !allowedKeys.has(key))) {
    throw new HttpError(400, "bad_activity",
      "Activity accepts only event, vision_provider, capture_mode and aggregate provenance labels.");
  }
  if (body.event !== "vision_check" || body.vision_provider !== "personal_openai"
      || !["manual", "drive"].includes(body.capture_mode)) {
    throw new HttpError(400, "bad_activity",
      "Activity must describe a personal_openai vision_check in manual or drive mode.");
  }
  const provenance = captureProvenance(body, body.capture_mode, false, {
    // This privacy-preserving endpoint deliberately accepts only the categorical
    // location label, never the coordinates themselves.
    allowLocationLabelWithoutCoordinates: true,
  });
  await rejectReplay(env, context);
  context.visionMode = "own_key";
  context.outcome = `vision_check_${body.capture_mode}`;
  context.captureSource = provenance.captureSource;
  context.locationSource = provenance.locationSource;
  const payload = { accepted: true, event: "vision_check" };
  await rememberIdempotency(env, context, payload, 202, [
    captureMetricStatement(env, context, {
      ...provenance,
      visionMode: "own_key",
      outcome: "vision_check",
    }),
  ]);
  return jsonResponse(payload, 202, context.requestId);
}

function selectedModel(body) {
  const model = boundedString(body && body.model, 64) || MODEL_CONFIG.defaultModel;
  if (!ALLOWED_MODELS.has(model)) {
    throw new HttpError(400, "unsupported_model",
      `Shared vision supports only ${MODEL_CONFIG.allowedModels.join(" and ")}.`);
  }
  return model;
}

function selectedLanguage(body) {
  const language = boundedString(body && (body.language || body.lang), 8)
    || MODEL_CONFIG.defaultLanguage;
  if (!ALLOWED_LANGUAGES.has(language)) {
    throw new HttpError(400, "unsupported_language",
      `Shared vision supports these description languages: ${MODEL_CONFIG.allowedLanguages.join(", ")}.`);
  }
  return language;
}

function selectedImageDetail(body, model = null) {
  const detail = boundedString(body && body.image_detail, 16)
    || MODEL_CONFIG.defaultImageDetail;
  if (!ALLOWED_IMAGE_DETAILS.has(detail)) {
    throw new HttpError(400, "unsupported_image_detail",
      `image_detail must be ${MODEL_CONFIG.allowedImageDetails.join(" or ")}.`);
  }
  if (detail === MODEL_CONFIG.originalImageDetail && !ORIGINAL_DETAIL_MODELS.has(model)) {
    throw new HttpError(400, "unsupported_image_detail",
      `original image detail is supported only with ${MODEL_CONFIG.originalDetailModels.join(" or ")}.`);
  }
  return detail;
}

const outputFormat = (name, schema) => ({
  format: {
    type: "json_schema",
    name,
    schema,
    strict: RUNTIME_CONFIG.strictStructuredOutputs,
  },
  verbosity: RUNTIME_CONFIG.textVerbosity,
});

function detectionPrompt(captureMode, language) {
  const layout = captureMode === "drive"
    ? DETECT_PROMPT_CONFIG.captureLayouts.drive
    : DETECT_PROMPT_CONFIG.captureLayouts.manual;
  return DETECT_PROMPT
    + layout
    + (DETECT_PROMPT_CONFIG.languageSuffixes[language] || "");
}

function tenderUserInput(address, candidates) {
  const limits = TENDER_CONFIG.stringLimits;
  const data = {
    reverse_geocoded_address: String(address || "").slice(0, limits.address),
    candidates: candidates.map((tender, index) => ({
      match_index: index,
      work_description: String(tender.title || "").slice(0, limits.workDescription),
      division_or_location: String(tender.location || "")
        .slice(0, limits.divisionOrLocation),
      contractor: String(tender.contractor || "not named").slice(0, limits.contractor),
      published: String(tender.published || "unknown").slice(0, limits.published),
    })),
  };
  return `${TENDER_PROMPT_CONFIG.dataEnvelope.begin}\n${JSON.stringify(data)}\n`
    + TENDER_PROMPT_CONFIG.dataEnvelope.end;
}

async function readOpenAIError(response) {
  try {
    const data = await response.json();
    const error = data && typeof data === "object" && !Array.isArray(data)
      && data.error && typeof data.error === "object" && !Array.isArray(data.error)
      ? data.error : {};
    return {
      code: boundedString(error.code, 128).toLowerCase() || null,
      type: boundedString(error.type, 128).toLowerCase() || null,
    };
  } catch {
    return { code: null, type: null };
  }
}

function retryAfterSeconds(response) {
  const raw = boundedString(response.headers.get("retry-after"), 128);
  if (!raw) return null;
  const seconds = Number(raw);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.ceil(seconds);
  const at = Date.parse(raw);
  if (!Number.isFinite(at)) return null;
  return Math.max(0, Math.ceil((at - nowMs()) / 1_000));
}

function secondsUntilNextUtcMonth(value = nowMs()) {
  const current = new Date(value);
  const next = Date.UTC(current.getUTCFullYear(), current.getUTCMonth() + 1, 1);
  return Math.max(1, Math.ceil((next - value) / 1_000));
}

const yoloCapCircuitKey = (model) => `yolo-cap:${encodeURIComponent(model)}`;

function yoloCapError(cap) {
  return new HttpError(503, "shared_yolo_cap_reached",
    "The shared YOLO detector has reached its monthly cap. Use your own OpenAI key or try after it resets.", {
      retryable: false,
      provider: "http_yolo",
      yolo_error_code: cap.code,
      retry_after_seconds: cap.retryAfterSeconds,
    });
}

async function readYoloMonthlyCap(response) {
  let data;
  try {
    data = await response.json();
  } catch {
    return null;
  }
  // The AWS adapter contract uses a top-level string error. Deliberately do not
  // infer a monthly cap from messages, nested fields, status alone, or unknown codes.
  const code = data && typeof data === "object" && !Array.isArray(data)
    && typeof data.error === "string" ? data.error.trim() : "";
  if (!YOLO_MONTHLY_CAP_CODES.has(code)) return null;
  const retryAfter = retryAfterSeconds(response);
  return {
    code,
    retryAfterSeconds: retryAfter === null
      ? secondsUntilNextUtcMonth() : retryAfter,
  };
}

async function activeYoloCapCircuit(env, model) {
  if (!env.DEVICES) return null;
  let raw;
  try {
    raw = await env.DEVICES.get(yoloCapCircuitKey(model));
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const stored = JSON.parse(raw);
    const capUntil = finiteNumber(stored.cap_until);
    if (!YOLO_MONTHLY_CAP_CODES.has(stored.code) || capUntil <= nowMs()) return null;
    return {
      code: stored.code,
      retryAfterSeconds: Math.max(1, Math.ceil((capUntil - nowMs()) / 1_000)),
    };
  } catch {
    return null;
  }
}

async function rememberYoloCapCircuit(env, model, cap) {
  if (!env.DEVICES) return;
  const retryAfterSeconds = Math.max(1, Math.ceil(cap.retryAfterSeconds));
  const capUntil = nowMs() + retryAfterSeconds * 1_000;
  try {
    await env.DEVICES.put(yoloCapCircuitKey(model), JSON.stringify({
      code: cap.code,
      cap_until: capUntil,
    }), {
      // Cloudflare KV requires at least 60 seconds. The timestamp inside the value
      // remains authoritative if AWS returns a shorter boundary interval.
      expirationTtl: Math.max(60, retryAfterSeconds),
    });
  } catch {
    // The AWS admission gate still enforces the hard cap. A KV outage must not
    // replace the precise non-retryable cap response with an internal error.
  }
}

function isOpenAIExhaustionError(error) {
  return error instanceof HttpError
    && error.code === "shared_credits_exhausted"
    && OPENAI_EXHAUSTION_CODES.has(error.details && error.details.openai_error_code);
}

async function callOpenAI(env, context, body, purpose = "detection") {
  if (!env.OPENAI_API_KEY) {
    throw new HttpError(503, "shared_vision_not_configured",
      "The shared vision service is not configured. Use your own OpenAI key.");
  }
  let response;
  const controller = new AbortController();
  const timeoutMs = Math.min(MAX_OPENAI_UPSTREAM_TIMEOUT_MS, Math.max(
    RUNTIME_CONFIG.timeoutsMs.serverUpstreamMin,
    finiteNumber(env.OPENAI_TIMEOUT_MS, MAX_OPENAI_UPSTREAM_TIMEOUT_MS)));
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    response = await fetch(OPENAI_URL, {
      method: "POST",
      // Do not let an upstream redirect carry the project credential or image body
      // to another origin.
      redirect: "error",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${env.OPENAI_API_KEY}`,
        "x-client-request-id": context.requestId,
      },
      body: JSON.stringify({ ...body, store: RUNTIME_CONFIG.storeResponses }),
      signal: controller.signal,
    });
  } catch {
    throw new HttpError(503, "shared_vision_unavailable",
      "The shared vision service could not reach OpenAI. Try again later.");
  } finally {
    clearTimeout(timer);
  }
  context.openaiRequestId = response.headers.get("x-request-id") || null;
  if (purpose === "tender") {
    context.tenderOpenAIRequestId = context.openaiRequestId;
  } else {
    context.detectionOpenAIRequestId = context.openaiRequestId;
  }
  if (purpose === "detection" && context.detectorProvider === "openai") {
    context.detectorRequestId = context.openaiRequestId;
  }
  const upstreamError = response.ok
    ? { code: null, type: null }
    : await readOpenAIError(response);
  context.openaiErrorCode = upstreamError.code || upstreamError.type;
  if (response.status === 429 && OPENAI_EXHAUSTION_CODES.has(upstreamError.code)) {
    context.outcome = "shared_credits_exhausted";
    throw new HttpError(503, "shared_credits_exhausted",
      "The shared OpenAI account has exhausted its credits or enforced usage limit.", {
        retryable: false,
        openai_error_code: upstreamError.code,
      });
  }
  if (response.status === 429) {
    context.outcome = "shared_rate_limit";
    const retryAfter = retryAfterSeconds(response);
    throw new HttpError(429, "shared_rate_limit",
      "OpenAI is temporarily rate-limiting the shared vision service. Try again later.", {
        retryable: true,
        ...(retryAfter === null ? {} : { retry_after_seconds: retryAfter }),
        ...(upstreamError.code ? { openai_error_code: upstreamError.code } : {}),
      });
  }
  if (response.status === 401 || response.status === 403) {
    throw new HttpError(503, "shared_vision_not_configured",
      "The shared OpenAI account cannot use this model. Use your own key or try later.");
  }
  if (!response.ok) {
    throw new HttpError(response.status >= 500 ? 503 : 422,
      response.status >= 500 ? "shared_vision_unavailable" : "vision_request_rejected",
      response.status >= 500
        ? "OpenAI is temporarily unavailable."
        : "OpenAI could not analyse these images.");
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new HttpError(502, "bad_upstream_response",
      "OpenAI returned an unreadable response.");
  }
  const message = (data.output || []).find((item) => item.type === "message");
  const output = message && (message.content || [])
    .find((item) => item.type === "output_text");
  if (!output || typeof output.text !== "string") {
    throw new HttpError(502, "bad_upstream_response",
      "OpenAI returned no structured assessment.");
  }
  try {
    return JSON.parse(output.text);
  } catch {
    throw new HttpError(502, "bad_upstream_response",
      "OpenAI returned invalid structured output.");
  }
}

async function callHttpYolo(env, context, detector, input) {
  // Do not attribute a primary OpenAI request ID to the fallback backend when
  // the YOLO gateway fails before returning its own response headers.
  context.detectorRequestId = null;
  const activeCap = await activeYoloCapCircuit(env, detector.model);
  if (activeCap) {
    context.outcome = "shared_yolo_cap_reached";
    context.yoloErrorCode = activeCap.code;
    throw yoloCapError(activeCap);
  }
  const controller = new AbortController();
  const timeoutMs = Math.min(MAX_YOLO_UPSTREAM_TIMEOUT_MS, Math.max(
    RUNTIME_CONFIG.timeoutsMs.serverUpstreamMin,
    finiteNumber(env.YOLO_TIMEOUT_MS, MAX_YOLO_UPSTREAM_TIMEOUT_MS)));
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    const body = JSON.stringify({
      version: 1,
      task: DETECT_PROMPT_CONFIG.id,
      request_id: context.requestId,
      model: detector.model,
      capture_mode: input.captureMode,
      language: input.language,
      prompt_version: DETECT_PROMPT_VERSION,
      schema_version: DETECT_SCHEMA_VERSION,
      images: input.images.map((image) => ({
        data_url: image.dataUrl,
      })),
    });
    const headers = await awsSigV4Headers({
      endpoint: detector.endpoint,
      region: detector.aws_region,
      accessKeyId: boundedString(env.YOLO_AWS_ACCESS_KEY_ID, 128),
      secretAccessKey: boundedString(env.YOLO_AWS_SECRET_ACCESS_KEY, 2_048),
      sessionToken: boundedString(env.YOLO_AWS_SESSION_TOKEN, 8_192),
      yoloApiKey: boundedString(env.YOLO_API_KEY, 2_048),
      requestId: context.requestId,
      body,
    });
    response = await fetch(detector.endpoint, {
      method: "POST",
      // Redirects are rejected so AWS credentials, the defense-in-depth gateway
      // token, and selected road images cannot be forwarded to another origin.
      redirect: "error",
      headers,
      body,
      signal: controller.signal,
    });
  } catch {
    throw new HttpError(503, "shared_vision_unavailable",
      "The shared detector could not be reached. Try again later.", {
        retryable: true,
        provider: detector.provider,
      });
  } finally {
    clearTimeout(timer);
  }
  context.detectorRequestId = response.headers.get("x-request-id") || null;
  context.yoloRequestId = context.detectorRequestId;
  if (response.status === 401 || response.status === 403) {
    throw new HttpError(503, "shared_vision_not_configured",
      "The shared detector rejected its server credential. Use your own OpenAI key.", {
        provider: detector.provider,
      });
  }
  if (response.status === 429) {
    const monthlyCap = await readYoloMonthlyCap(response);
    if (monthlyCap) {
      await rememberYoloCapCircuit(env, detector.model, monthlyCap);
      context.outcome = "shared_yolo_cap_reached";
      context.yoloErrorCode = monthlyCap.code;
      throw yoloCapError(monthlyCap);
    }
    context.outcome = "shared_detector_rate_limited";
    throw new HttpError(503, "shared_vision_unavailable",
      "The shared detector is at capacity. Use your own key or try later.", {
        retryable: true,
        provider: detector.provider,
      });
  }
  if (!response.ok) {
    throw new HttpError(response.status >= 500 ? 503 : 502,
      "shared_vision_unavailable",
      "The shared detector could not complete this analysis.", {
        retryable: response.status >= 500,
        provider: detector.provider,
      });
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new HttpError(502, "bad_upstream_response",
      "The shared detector returned an unreadable response.");
  }
  const verdict = data && typeof data === "object"
    ? (data.verdict || data.result || data)
    : null;
  return {
    verdict,
    model: boundedString(data && data.model, 80) || detector.model,
  };
}

async function callSharedDetector(env, context, detector, input) {
  if (detector.provider === "http_yolo") {
    context.detectorProvider = "http_yolo";
    const result = await callHttpYolo(env, context, detector, input);
    return { ...result, provider: detector.provider };
  }
  const callPrimary = async () => {
    const model = selectedModel(input.body);
    context.detectorProvider = "openai";
    const verdict = await callOpenAI(env, context, {
      model,
      input: imageContent(
        input.images,
        input.prompt,
        DETECT_PROMPT_CONFIG.role,
        input.imageDetail,
      ),
      text: outputFormat(DETECT_PROMPT_CONFIG.schemaName, DETECT_SCHEMA),
      reasoning: {
        effort: MODEL_CONFIG.reasoningEffortByModel[model]
          || MODEL_CONFIG.defaultReasoningEffort,
      },
    });
    context.detectorRequestId = context.openaiRequestId;
    return { verdict, provider: "openai", model };
  };
  if (detector.provider === "openai") return callPrimary();
  if (!detector.primary_configured) {
    context.detectorProvider = "http_yolo";
    const result = await callHttpYolo(env, context, detector.fallback, input);
    // This is a direct configured-backend call, not a failed OpenAI request, so
    // do not emit fallback_from/fallback_reason provenance.
    return { ...result, provider: "http_yolo" };
  }

  try {
    return await callPrimary();
  } catch (error) {
    if (!isOpenAIExhaustionError(error)) throw error;
    context.detectorFallbackReason = error.details.openai_error_code;
    context.detectorProvider = "http_yolo";
    const result = await callHttpYolo(env, context, detector.fallback, input);
    return {
      ...result,
      provider: "http_yolo",
      fallbackFrom: "openai",
      fallbackReason: "openai_exhausted",
    };
  }
}

function validateDetectionVerdict(value) {
  const assessment = new Set(["damaged", "undamaged"]);
  const quality = new Set(["acceptable", "rejected"]);
  if (!value || typeof value !== "object" || Array.isArray(value)
      || !assessment.has(value.assessment)
      || !quality.has(value.image_quality)
      || !(value.damage_type === null || DAMAGE_TYPES.has(value.damage_type))
      || !(value.size === null || SIZES.has(value.size))
      || typeof value.description !== "string") {
    throw new HttpError(502, "bad_upstream_response",
      `The shared detector returned an assessment that does not match schema version ${DETECT_SCHEMA_VERSION}.`);
  }
  const damaged = value.image_quality === "acceptable"
    && value.assessment === "damaged";
  if ((damaged && value.damage_type === null)
      || (!damaged && (value.assessment !== "undamaged"
        || value.damage_type !== null || value.size !== null))) {
    throw new HttpError(502, "bad_upstream_response",
      "The shared detector returned contradictory quality, assessment or damage fields.");
  }
  // Project the exact public contract. An upstream request_id, error field, or
  // other arbitrary property must never override this service's response envelope.
  return {
    image_quality: value.image_quality,
    assessment: value.assessment,
    damage_type: value.damage_type,
    size: value.size,
    description: value.description.trim().slice(0, 1_000),
  };
}

function imageContent(
  images,
  prompt,
  role,
  imageDetail = MODEL_CONFIG.defaultImageDetail,
) {
  return [{
    role,
    content: [
      ...images.map((image) => ({
        type: "input_image",
        image_url: image.dataUrl,
        detail: imageDetail,
      })),
      { type: "input_text", text: prompt },
    ],
  }];
}

function prepareSharedDetection(body, env) {
  const imagesRaw = Array.isArray(body.images) ? body.images : [];
  if (imagesRaw.length !== 1) {
    throw new HttpError(400, "bad_image_count",
      "Shared detection requires exactly one image.");
  }
  const images = imagesRaw.map((image, index) =>
    validateImage(image, `images[${index}]`));
  const detector = requireSharedDetector(env);
  const totalBytes = images.reduce((sum, image) => sum + image.bytes, 0);
  const maxTotalBytes = detector.provider === "openai"
    ? MAX_DETECT_TOTAL_BYTES : MAX_YOLO_DETECT_TOTAL_BYTES;
  if (totalBytes > maxTotalBytes) {
    throw new HttpError(413, "images_too_large",
      `The detection image may be at most ${maxTotalBytes / 1_000_000} MB.`);
  }
  if (body.prompt_version !== DETECT_PROMPT_VERSION) {
    throw new HttpError(409, "prompt_version_mismatch",
      `This service requires ${DETECT_PROMPT_VERSION}.`);
  }
  const captureMode = body.capture_mode === "drive" ? "drive"
    : body.capture_mode === "manual" ? "manual" : null;
  if (!captureMode) {
    throw new HttpError(400, "bad_capture_mode",
      "capture_mode must be manual or drive.");
  }
  const language = selectedLanguage(body);
  // Preserve the existing client-selectable OpenAI model while keeping the YOLO
  // model entirely server-owned. The chained mode still validates its primary
  // model before claiming idempotency or quota.
  const usesOpenAI = detector.provider === "openai"
    || (detector.provider === "openai_then_http_yolo" && detector.primary_configured);
  const model = usesOpenAI ? selectedModel(body) : null;
  // Pure YOLO accepts the universal high/default setting but rejects OpenAI-only
  // original detail. callHttpYolo constructs an allowlisted payload and never
  // forwards this presentation hint to the server-owned detector.
  const imageDetail = selectedImageDetail(body, model);
  const clientObservationId = boundedString(body.client_observation_id, 180) || null;
  const hasLat = Object.prototype.hasOwnProperty.call(body, "lat");
  const hasLng = Object.prototype.hasOwnProperty.call(body, "lng");
  if (hasLat !== hasLng) {
    throw new HttpError(400, "bad_detection_location",
      "lat and lng must either both be supplied or both be omitted.");
  }
  const lat = hasLat ? externalCoordinate(body.lat) : null;
  const lng = hasLng ? externalCoordinate(body.lng) : null;
  if (hasLat && !validLatLng(lat, lng)) {
    throw new HttpError(400, "bad_detection_location",
      "Detection receipt coordinates must be valid lat and lng values.");
  }
  const provenance = captureProvenance(body, captureMode, hasLat);
  return {
    body,
    images,
    detector,
    captureMode,
    language,
    imageDetail,
    clientObservationId,
    lat,
    lng,
    ...provenance,
  };
}

async function runSharedDetection(env, context, prepared) {
  const quota = await takeVisionQuota(env, context.installId);
  const prompt = detectionPrompt(prepared.captureMode, prepared.language);
  const detection = await callSharedDetector(env, context, prepared.detector, {
    body: prepared.body,
    images: prepared.images,
    prompt,
    captureMode: prepared.captureMode,
    language: prepared.language,
    imageDetail: prepared.imageDetail,
  });
  const verdict = validateDetectionVerdict(detection.verdict);
  const payload = {
    ...verdict,
    detector: {
      provider: "shared_server",
      backend_provider: detection.provider,
      model: detection.model,
      prompt_version: DETECT_PROMPT_VERSION,
      schema_version: DETECT_SCHEMA_VERSION,
      evidence_count: prepared.images.length,
      ...(detection.fallbackFrom ? {
        fallback_from: detection.fallbackFrom,
        fallback_reason: detection.fallbackReason,
      } : {}),
    },
    quota: { used: quota.daily.used, limit: quota.daily.limit },
  };
  return { payload, verdict, detection };
}

async function handleVisionDetect(request, env, context) {
  context.visionMode = "shared_detect";
  const body = await parseJsonBody(request, context);
  const cached = await idempotentResult(env, context);
  if (cached) return cached;
  const prepared = prepareSharedDetection(body, env);
  context.captureSource = prepared.captureSource;
  context.locationSource = prepared.locationSource;
  await rejectReplay(env, context);
  const { payload, verdict, detection } = await runSharedDetection(env, context, prepared);
  const beforeFinalize = [];
  if (verdict.image_quality === "acceptable" && verdict.assessment === "damaged"
      && prepared.clientObservationId) {
    const issuedAt = nowMs();
    const imageHash = await sha256Hex(prepared.images[0].decoded);
    const receiptId = await sha256Hex([
      // A fresh paid re-analysis gets a fresh one-use receipt even when every
      // client field is identical. An idempotency replay still returns the cached
      // original response and therefore the same receipt.
      context.requestId,
      context.installId,
      prepared.clientObservationId,
      imageHash,
      DETECT_PROMPT_VERSION,
      DETECT_SCHEMA_VERSION,
      verdict.damage_type,
      verdict.size || "",
      detection.provider,
      detection.model || "",
      prepared.lat == null ? "" : prepared.lat,
      prepared.lng == null ? "" : prepared.lng,
    ].join("\n"));
    const expiresAt = issuedAt + DETECTION_RECEIPT_TTL_MS;
    payload.detection_receipt = receiptId;
    payload.detection_receipt_expires_at = expiresAt;
    beforeFinalize.push(env.DB.prepare(
      `INSERT INTO shared_detection_receipts
         (receipt_id,install_id,client_observation_id,image_hash,detection_lat,detection_lng,
          damage_type,size,backend_provider,detector_model,prompt_version,schema_version,
          issued_at,expires_at,consumed_at,consumed_request_id,consumed_client_observation_id)
       VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,NULL,NULL,NULL)
       ON CONFLICT(receipt_id) DO UPDATE SET
         detection_lat=excluded.detection_lat,
         detection_lng=excluded.detection_lng,
         damage_type=excluded.damage_type,
         size=excluded.size,
         backend_provider=excluded.backend_provider,
         detector_model=excluded.detector_model,
         issued_at=excluded.issued_at,
         expires_at=excluded.expires_at
       WHERE shared_detection_receipts.install_id=excluded.install_id
         AND shared_detection_receipts.client_observation_id=excluded.client_observation_id
         AND shared_detection_receipts.image_hash=excluded.image_hash
         AND shared_detection_receipts.prompt_version=excluded.prompt_version
         AND shared_detection_receipts.schema_version=excluded.schema_version
         AND shared_detection_receipts.consumed_at IS NULL`
    ).bind(
      receiptId,
      context.installId,
      prepared.clientObservationId,
      imageHash,
      prepared.lat,
      prepared.lng,
      verdict.damage_type,
      verdict.size,
      detection.provider,
      detection.model,
      DETECT_PROMPT_VERSION,
      DETECT_SCHEMA_VERSION,
      issuedAt,
      expiresAt,
    ));
  }
  const detectionOutcome = verdict.image_quality === "rejected"
    ? "image_rejected" : verdict.assessment;
  beforeFinalize.push(captureMetricStatement(env, context, {
    captureSource: prepared.captureSource,
    locationSource: prepared.locationSource,
    visionMode: "shared_detect",
    outcome: detectionOutcome,
  }));
  await rememberIdempotency(env, context, payload, 200, beforeFinalize);
  context.outcome = detectionOutcome;
  return jsonResponse(payload, 200, context.requestId);
}

function metresBetween(lat1, lng1, lat2, lng2) {
  const radians = Math.PI / 180;
  const deltaLat = (lat2 - lat1) * radians;
  const deltaLng = (lng2 - lng1) * radians;
  const value = Math.sin(deltaLat / 2) ** 2
    + Math.cos(lat1 * radians) * Math.cos(lat2 * radians)
      * Math.sin(deltaLng / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(value));
}

function boundingBox(lat, lng, metres) {
  const latitudeDelta = metres / EARTH_RADIUS_M * 180 / Math.PI;
  const longitudeDelta = latitudeDelta
    / Math.max(Math.cos(lat * Math.PI / 180), 1e-6);
  return [
    lat - latitudeDelta,
    lat + latitudeDelta,
    lng - longitudeDelta,
    lng + longitudeDelta,
  ];
}

function encodeGeohash(lat, lng, precision = 8) {
  const alphabet = "0123456789bcdefghjkmnpqrstuvwxyz";
  let latitude = [-90, 90];
  let longitude = [-180, 180];
  let hash = "";
  let bits = 0;
  let value = 0;
  let even = true;
  while (hash.length < precision) {
    const range = even ? longitude : latitude;
    const coordinate = even ? lng : lat;
    const middle = (range[0] + range[1]) / 2;
    if (coordinate >= middle) {
      value = value * 2 + 1;
      range[0] = middle;
    } else {
      value *= 2;
      range[1] = middle;
    }
    even = !even;
    bits++;
    if (bits === 5) {
      hash += alphabet[value];
      bits = 0;
      value = 0;
    }
  }
  return hash;
}

function normalizedObservedAt(value) {
  let observedAt = finiteNumber(value, nowMs());
  if (observedAt > 0 && observedAt < 10_000_000_000) observedAt *= 1000;
  if (observedAt > nowMs() + 5 * 60_000
      || observedAt < nowMs() - 2 * 365 * 86_400_000) {
    throw new HttpError(400, "bad_observed_at",
      "observed_at must be a recent Unix timestamp in milliseconds or seconds.");
  }
  return Math.round(observedAt);
}

function publicPothole(row) {
  return {
    id: Number(row.id),
    lat: Number(row.lat),
    lng: Number(row.lng),
    damage_type: row.damage_type,
    size: row.size || null,
    first_seen_at: Number(row.first_seen_at),
    last_seen_at: Number(row.last_seen_at),
    seen_count: Number(row.seen_count || 0),
    verified_shared_observers: Number(row.verified_shared_observers || 0),
    client_attested_observers: Number(row.client_attested_observers || 0),
    lgd: row.body_lgd || null,
    town: row.town || null,
  };
}

function compatibleDamage(candidate, existing) {
  if (candidate.damageType === existing.damage_type) return true;
  const localFamily = new Set(["pothole_cavity", "failed_patch"]);
  return localFamily.has(candidate.damageType) && localFamily.has(existing.damage_type);
}

function sizeConflicts(candidate, existing) {
  return candidate.size && existing.size
    && ((candidate.size === "small" && existing.size === "large")
      || (candidate.size === "large" && existing.size === "small"));
}

function sharedDetectionReceiptsRequired(env) {
  return String(env.REQUIRE_SHARED_DETECTION_RECEIPT || "true").toLowerCase()
    !== "false";
}

async function verifySharedDetectionReceipt(env, context, candidate) {
  if (candidate.detector.provider !== "shared_server") return null;
  if (!candidate.detectionReceiptId) {
    if (!sharedDetectionReceiptsRequired(env)) {
      if (candidate.detector.prompt_version !== DETECT_PROMPT_VERSION
          || candidate.detector.schema_version !== DETECT_SCHEMA_VERSION) {
        throw new HttpError(409, "detector_version_mismatch",
          `Receipt-free rollout reports require detector ${DETECT_PROMPT_VERSION} schema ${DETECT_SCHEMA_VERSION}.`);
      }
      return null;
    }
    throw new HttpError(400, "detection_receipt_required",
      "A server-issued detection_receipt is required for shared-server map reports.");
  }
  const receipt = await env.DB.prepare(
    `SELECT receipt_id,install_id,client_observation_id,image_hash,detection_lat,detection_lng,
            damage_type,size,backend_provider,detector_model,prompt_version,schema_version,
            expires_at,consumed_at,
            consumed_client_observation_id
       FROM shared_detection_receipts WHERE receipt_id=?1`
  ).bind(candidate.detectionReceiptId).first();
  if (!receipt || receipt.install_id !== context.installId) {
    throw new HttpError(403, "invalid_detection_receipt",
      "That detection receipt was not issued to this installation.");
  }
  if (Number(receipt.expires_at) < nowMs()) {
    throw new HttpError(409, "detection_receipt_expired",
      "That detection receipt has expired. Check the image again before reporting it.");
  }
  const expectedSize = receipt.size == null ? null : String(receipt.size);
  if (receipt.client_observation_id !== candidate.clientObservationId
      || String(receipt.image_hash).toLowerCase() !== candidate.imageHash
      || receipt.damage_type !== candidate.damageType
      || expectedSize !== candidate.size
      || receipt.prompt_version !== candidate.detector.prompt_version
      || Number(receipt.schema_version) !== candidate.detector.schema_version) {
    throw new HttpError(409, "detection_receipt_mismatch",
      "The report does not match the image and verdict authorized by its detection receipt.");
  }
  if (receipt.detection_lat == null || receipt.detection_lng == null
      || !validLatLng(Number(receipt.detection_lat), Number(receipt.detection_lng))) {
    throw new HttpError(409, "detection_receipt_missing_location",
      "The shared detection had no location, so it cannot be added to the public map.");
  }
  const tolerance = Math.min(10, Math.max(1,
    finiteNumber(env.RECEIPT_LOCATION_TOLERANCE_METRES, 3)));
  if (metresBetween(
    candidate.lat,
    candidate.lng,
    Number(receipt.detection_lat),
    Number(receipt.detection_lng),
  ) > tolerance) {
    throw new HttpError(409, "detection_receipt_location_mismatch",
      "The report location does not match the location bound to its detection receipt.");
  }
  if (receipt.consumed_at != null
      && receipt.consumed_client_observation_id !== candidate.clientObservationId) {
    throw new HttpError(409, "detection_receipt_consumed",
      "That detection receipt has already been used for another observation.");
  }
  // Shared-server provenance comes from the server receipt, never from the
  // caller's report metadata.
  candidate.detector.model = receipt.detector_model || null;
  candidate.detector.prompt_version = receipt.prompt_version;
  candidate.detector.schema_version = Number(receipt.schema_version);
  return receipt;
}

async function nearbyPothole(
  env,
  candidate,
  excludeId = null,
  days = null,
  beforeId = null,
) {
  const base = Math.max(1, finiteNumber(env.DEDUPE_METRES, 12));
  const maximum = Math.max(base, finiteNumber(env.DEDUPE_MAX_METRES, 20));
  const accuracy = Number.isFinite(candidate.gpsAccuracy)
    ? Math.max(0, candidate.gpsAccuracy) : base;
  const radius = Math.min(maximum, Math.max(base, accuracy));
  const [minLat, maxLat, minLng, maxLng] =
    boundingBox(candidate.lat, candidate.lng, radius);
  const horizonDays = days == null
    ? Math.max(1, finiteNumber(env.DEDUPE_DAYS, 120)) : days;
  const since = nowMs() - horizonDays * 86_400_000;
  const bound = [minLat, maxLat, minLng, maxLng, since];
  let sql = `SELECT id,lat,lng,body_lgd,town,damage_type,size,
                    first_seen_at,last_seen_at,seen_count
               FROM potholes
              WHERE lat BETWEEN ?1 AND ?2
                AND lng BETWEEN ?3 AND ?4
                AND last_seen_at >= ?5`;
  // Ordinary dedupe ignores abandoned empty canonicals. Post-insert race
  // reconciliation must include lower-ID zero-count rows because another request
  // may have inserted its canonical but not committed its first observation yet.
  if (beforeId == null) sql += " AND seen_count > 0";
  if (excludeId != null) {
    sql += ` AND id != ?${bound.length + 1}`;
    bound.push(excludeId);
  }
  if (beforeId != null) {
    sql += ` AND id < ?${bound.length + 1}`;
    bound.push(beforeId);
  }
  sql += beforeId == null
    ? " ORDER BY first_seen_at ASC, id ASC LIMIT 100"
    : " ORDER BY id ASC LIMIT 100";
  const { results = [] } = await env.DB.prepare(sql).bind(...bound).all();
  let best = null;
  let bestDistance = Infinity;
  for (const row of results) {
    if (!compatibleDamage(candidate, row) || sizeConflicts(candidate, row)) continue;
    const distance = metresBetween(candidate.lat, candidate.lng,
      Number(row.lat), Number(row.lng));
    if (beforeId != null && distance <= radius) {
      // Every in-flight reporter must converge on the same stable root. Choosing
      // the nearest lower row lets a three-way race form a chain through a row
      // another request is about to delete.
      return { row, distance, radius };
    }
    if (distance <= radius && distance < bestDistance) {
      best = row;
      bestDistance = distance;
    }
  }
  return best
    ? { row: best, distance: bestDistance, radius }
    : null;
}

function observationMatchesCandidate(stored, candidate, potholeId, toleranceMetres) {
  if (!stored || Number(stored.pothole_id) !== potholeId) return false;
  const storedSize = stored.size == null ? null : String(stored.size);
  const storedModel = stored.detector_model == null
    ? null : String(stored.detector_model);
  return stored.damage_type === candidate.damageType
    && storedSize === candidate.size
    && stored.image_hash === candidate.imageHash
    && stored.capture_source === candidate.captureSource
    && stored.location_source === candidate.locationSource
    && stored.detector_provider === candidate.detector.provider
    && storedModel === candidate.detector.model
    && stored.prompt_version === candidate.detector.prompt_version
    && Number(stored.schema_version) === candidate.detector.schema_version
    && validLatLng(Number(stored.lat), Number(stored.lng))
    && metresBetween(
      Number(stored.lat),
      Number(stored.lng),
      candidate.lat,
      candidate.lng,
    ) <= toleranceMetres;
}

async function commitReportObservation(
  env,
  context,
  candidate,
  potholeId,
  distance,
  jurisdiction,
) {
  const resolvedOwnership = authoritativeRoadOwnership(jurisdiction);
  const authoritativeOwnership = resolvedOwnership ? 1 : 0;
  const municipalOwnership = resolvedOwnership === "municipal" ? 1 : 0;
  const receipt = candidate.detectionReceipt || null;
  const locationTolerance = Math.min(10, Math.max(1,
    finiteNumber(env.RECEIPT_LOCATION_TOLERANCE_METRES, 3)));
  // Use one timestamp for every receipt predicate in this transaction. Two clock
  // reads could otherwise straddle expiry and insert a verified observation while
  // the receipt-consumption UPDATE matched zero rows.
  const commitAt = nowMs();
  // Reject a reused observation ID before touching any projection. The SQL
  // projection guards below repeat this boundary to cover two concurrent calls
  // that both completed this read before either inserted its observation.
  const before = await env.DB.prepare(
    `SELECT pothole_id,lat,lng,damage_type,size,image_hash,detector_provider,
            detector_model,prompt_version,schema_version,capture_source,location_source
       FROM observations
      WHERE install_id=?1 AND client_observation_id=?2`
  ).bind(context.installId, candidate.clientObservationId).first();
  if (before && !observationMatchesCandidate(
    before, candidate, potholeId, locationTolerance)) {
    throw new HttpError(409, "observation_id_conflict",
      "That client_observation_id already belongs to different evidence, location or provenance.");
  }
  const observationInsert = receipt
    ? env.DB.prepare(
      `INSERT OR IGNORE INTO observations
         (pothole_id,install_id,request_id,client_observation_id,observed_at,
          lat,lng,gps_accuracy_m,heading_deg,speed_mps,capture_source,location_source,
          damage_type,size,image_hash,
          detector_provider,verification_state,detector_model,prompt_version,schema_version,
          duplicate_distance_m)
       SELECT ?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,
              'server_verified_shared',?17,?18,?19,?20
        WHERE EXISTS (
          SELECT 1 FROM shared_detection_receipts r
           WHERE r.receipt_id=?21 AND r.install_id=?2
             AND r.client_observation_id=?4 AND r.image_hash=?15
             AND r.damage_type=?13
             AND (r.size=?14 OR (r.size IS NULL AND ?14 IS NULL))
             AND r.prompt_version=?18 AND r.schema_version=?19
             AND r.expires_at>=?22
             AND (r.consumed_at IS NULL OR r.consumed_client_observation_id=?4)
        )`
    ).bind(
      potholeId,
      context.installId,
      context.requestId,
      candidate.clientObservationId,
      candidate.observedAt,
      candidate.lat,
      candidate.lng,
      candidate.gpsAccuracy,
      candidate.heading,
      candidate.speed,
      candidate.captureSource,
      candidate.locationSource,
      candidate.damageType,
      candidate.size,
      candidate.imageHash,
      candidate.detector.provider,
      candidate.detector.model,
      candidate.detector.prompt_version,
      candidate.detector.schema_version,
      distance,
      candidate.detectionReceiptId,
      commitAt,
    )
    : env.DB.prepare(
      `INSERT OR IGNORE INTO observations
         (pothole_id,install_id,request_id,client_observation_id,observed_at,
          lat,lng,gps_accuracy_m,heading_deg,speed_mps,capture_source,location_source,
          damage_type,size,image_hash,
          detector_provider,verification_state,detector_model,prompt_version,schema_version,
          duplicate_distance_m)
       VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,
               'client_attested',?17,?18,?19,?20)`
    ).bind(
      potholeId,
      context.installId,
      context.requestId,
      candidate.clientObservationId,
      candidate.observedAt,
      candidate.lat,
      candidate.lng,
      candidate.gpsAccuracy,
      candidate.heading,
      candidate.speed,
      candidate.captureSource,
      candidate.locationSource,
      candidate.damageType,
      candidate.size,
      candidate.imageHash,
      candidate.detector.provider,
      candidate.detector.model,
      candidate.detector.prompt_version,
      candidate.detector.schema_version,
      distance,
    );
  // These statements are one logical report mutation. D1 batch() is
  // transactional: an observation can never survive without its observer/count
  // projections.
  // Every projection is derived from stored observations so a resubmitted client
  // ID also repairs data written by an older, partially transactional deployment.
  await env.DB.batch([
    observationInsert,
    ...(receipt ? [env.DB.prepare(
      `UPDATE shared_detection_receipts
          SET consumed_at=COALESCE(consumed_at,?1),
              consumed_request_id=COALESCE(consumed_request_id,?2),
              consumed_client_observation_id=COALESCE(consumed_client_observation_id,?3)
        WHERE receipt_id=?4 AND install_id=?5
          AND client_observation_id=?3 AND expires_at>=?1
          AND (consumed_at IS NULL OR consumed_client_observation_id=?3)
          AND EXISTS (
            SELECT 1 FROM observations o
             WHERE o.install_id=?5 AND o.client_observation_id=?3
               AND o.image_hash=?6 AND o.damage_type=?7
               AND (o.size=?8 OR (o.size IS NULL AND ?8 IS NULL))
               AND o.pothole_id=?9 AND o.lat=?10 AND o.lng=?11
               AND o.capture_source=?12 AND o.location_source=?13
               AND o.detector_provider='shared_server'
               AND o.verification_state='server_verified_shared'
               AND (o.detector_model=?14 OR (o.detector_model IS NULL AND ?14 IS NULL))
               AND o.prompt_version=?15 AND o.schema_version=?16
          )`
    ).bind(
      commitAt,
      context.requestId,
      candidate.clientObservationId,
      candidate.detectionReceiptId,
      context.installId,
      candidate.imageHash,
      candidate.damageType,
      candidate.size,
      potholeId,
      candidate.lat,
      candidate.lng,
      candidate.captureSource,
      candidate.locationSource,
      candidate.detector.model,
      candidate.detector.prompt_version,
      candidate.detector.schema_version,
    )] : []),
    env.DB.prepare(
      `INSERT INTO pothole_observers
         (pothole_id,install_id,first_seen_at,last_seen_at)
       SELECT o.pothole_id,o.install_id,MIN(o.observed_at),MAX(o.observed_at)
         FROM observations o
        WHERE o.pothole_id=?1
          AND EXISTS (
            SELECT 1 FROM observations accepted
             WHERE accepted.pothole_id=?1 AND accepted.install_id=?2
               AND accepted.client_observation_id=?3
               AND accepted.image_hash=?4 AND accepted.damage_type=?5
               AND (accepted.size=?6 OR (accepted.size IS NULL AND ?6 IS NULL))
               AND accepted.lat=?7 AND accepted.lng=?8
               AND accepted.capture_source=?9 AND accepted.location_source=?10
               AND accepted.detector_provider=?11
               AND (accepted.detector_model=?12
                 OR (accepted.detector_model IS NULL AND ?12 IS NULL))
               AND accepted.prompt_version=?13 AND accepted.schema_version=?14
          )
        GROUP BY o.pothole_id,o.install_id
       ON CONFLICT(pothole_id,install_id) DO UPDATE SET
         first_seen_at=MIN(pothole_observers.first_seen_at,excluded.first_seen_at),
         last_seen_at=MAX(pothole_observers.last_seen_at,excluded.last_seen_at)`
    ).bind(
      potholeId,
      context.installId,
      candidate.clientObservationId,
      candidate.imageHash,
      candidate.damageType,
      candidate.size,
      candidate.lat,
      candidate.lng,
      candidate.captureSource,
      candidate.locationSource,
      candidate.detector.provider,
      candidate.detector.model,
      candidate.detector.prompt_version,
      candidate.detector.schema_version,
    ),
    env.DB.prepare(
      `UPDATE potholes
          SET first_seen_at=COALESCE(
                (SELECT MIN(observed_at) FROM observations WHERE pothole_id=?1),
                first_seen_at),
              last_seen_at=COALESCE(
                (SELECT MAX(observed_at) FROM observations WHERE pothole_id=?1),
                last_seen_at),
              seen_count=(
                SELECT COUNT(*) FROM pothole_observers WHERE pothole_id=?1),
              body_lgd=CASE
                WHEN ?4=1 AND ?5=1 THEN ?6
                WHEN ?4=1 THEN NULL
                ELSE body_lgd END,
              town=CASE
                WHEN ?4=1 AND ?5=1 THEN ?7
                WHEN ?4=1 THEN NULL
                ELSE town END
        WHERE id=?1
          AND EXISTS (
            SELECT 1 FROM observations accepted
             WHERE accepted.pothole_id=?1 AND accepted.install_id=?2
               AND accepted.client_observation_id=?3
               AND accepted.image_hash=?8 AND accepted.damage_type=?9
               AND (accepted.size=?10 OR (accepted.size IS NULL AND ?10 IS NULL))
               AND accepted.lat=?11 AND accepted.lng=?12
               AND accepted.capture_source=?13 AND accepted.location_source=?14
               AND accepted.detector_provider=?15
               AND (accepted.detector_model=?16
                 OR (accepted.detector_model IS NULL AND ?16 IS NULL))
               AND accepted.prompt_version=?17 AND accepted.schema_version=?18
          )`
    ).bind(
      potholeId,
      context.installId,
      candidate.clientObservationId,
      authoritativeOwnership,
      municipalOwnership,
      jurisdiction.lgd,
      jurisdiction.town,
      candidate.imageHash,
      candidate.damageType,
      candidate.size,
      candidate.lat,
      candidate.lng,
      candidate.captureSource,
      candidate.locationSource,
      candidate.detector.provider,
      candidate.detector.model,
      candidate.detector.prompt_version,
      candidate.detector.schema_version,
    ),
  ]);

  const stored = await env.DB.prepare(
    `SELECT pothole_id,lat,lng,damage_type,size,image_hash,detector_provider,
            detector_model,prompt_version,schema_version,capture_source,location_source
       FROM observations
      WHERE install_id=?1 AND client_observation_id=?2`
  ).bind(context.installId, candidate.clientObservationId).first();
  if (!stored) {
    throw new HttpError(500, "report_write_failed", "The pothole observation could not be saved.");
  }
  if (!observationMatchesCandidate(stored, candidate, potholeId, locationTolerance)) {
    throw new HttpError(409, "observation_id_conflict",
      "That client_observation_id already belongs to different evidence, location or provenance.");
  }
  if (receipt) {
    const consumed = await env.DB.prepare(
      `SELECT consumed_at,consumed_request_id,consumed_client_observation_id
         FROM shared_detection_receipts
        WHERE receipt_id=?1 AND install_id=?2`
    ).bind(candidate.detectionReceiptId, context.installId).first();
    if (!consumed || !(Number(consumed.consumed_at) > 0)
        || !consumed.consumed_request_id
        || consumed.consumed_client_observation_id !== candidate.clientObservationId) {
      throw new HttpError(500, "receipt_consumption_failed",
        "The shared detection receipt could not be transactionally consumed.");
    }
  }
}

async function cleanupEmptyCanonical(env, potholeId, requestId) {
  await env.DB.prepare(
    `DELETE FROM potholes
      WHERE id=?1 AND created_request_id=?2 AND seen_count=0
        AND NOT EXISTS (SELECT 1 FROM observations WHERE pothole_id=?1)
        AND NOT EXISTS (SELECT 1 FROM pothole_observers WHERE pothole_id=?1)`
  ).bind(potholeId, requestId).run();
}

async function applyAuthoritativeJurisdiction(env, potholeId, jurisdiction) {
  const ownership = authoritativeRoadOwnership(jurisdiction);
  if (!ownership) return;
  if (ownership === "municipal") {
    await env.DB.prepare(
      `UPDATE potholes SET body_lgd = ?1, town = ?2 WHERE id = ?3`
    ).bind(jurisdiction.lgd, jurisdiction.town, potholeId).run();
    return;
  }
  await env.DB.prepare(
    "UPDATE potholes SET body_lgd = NULL, town = NULL WHERE id = ?1"
  ).bind(potholeId).run();
}

function reportCandidate(body) {
  const lat = externalCoordinate(body.lat);
  const lng = externalCoordinate(body.lng);
  if (!validLatLng(lat, lng)) {
    throw new HttpError(400, "bad_location", "A report needs valid lat and lng coordinates.");
  }
  const provenance = captureProvenance(body, null, true);
  const clientObservationId = boundedString(body.client_observation_id, 180);
  if (!clientObservationId) {
    throw new HttpError(400, "bad_observation_id", "client_observation_id is required.");
  }
  const damageType = boundedString(body.damage_type, 40);
  if (!DAMAGE_TYPES.has(damageType)) {
    throw new HttpError(400, "bad_damage_type", "damage_type is not a supported road-damage type.");
  }
  const size = body.size == null || body.size === "" ? null : boundedString(body.size, 16);
  if (size != null && !SIZES.has(size)) {
    throw new HttpError(400, "bad_size", "size must be small, medium, large or null.");
  }
  const imageHash = boundedString(body.image_hash, 128).toLowerCase();
  if (!/^[a-f0-9]{64}$/.test(imageHash)) {
    throw new HttpError(400, "bad_image_hash", "image_hash must be a SHA-256 hex digest.");
  }
  const gpsAccuracy = body.gps_accuracy_m == null
    ? null : finiteNumber(body.gps_accuracy_m);
  if (gpsAccuracy != null && (gpsAccuracy < 0 || gpsAccuracy > 1000)) {
    throw new HttpError(400, "bad_gps_accuracy", "gps_accuracy_m is outside a useful range.");
  }
  const heading = body.heading_deg == null ? null : finiteNumber(body.heading_deg);
  if (heading != null && (heading < 0 || heading >= 360)) {
    throw new HttpError(400, "bad_heading", "heading_deg must be from 0 up to 360.");
  }
  const speed = body.speed_mps == null ? null : finiteNumber(body.speed_mps);
  if (speed != null && (speed < 0 || speed > 150)) {
    throw new HttpError(400, "bad_speed", "speed_mps is outside a useful range.");
  }
  const detector = body.detector && typeof body.detector === "object"
    ? body.detector : {};
  const detectorProvider = boundedString(detector.provider, 40);
  const detectorPromptVersion = boundedString(detector.prompt_version, 80);
  const detectorSchemaVersion = Number.isInteger(detector.schema_version)
    ? detector.schema_version : null;
  const detectionReceiptId = boundedString(body.detection_receipt, 128).toLowerCase();
  if (detectionReceiptId && !/^[a-f0-9]{64}$/.test(detectionReceiptId)) {
    throw new HttpError(400, "bad_detection_receipt",
      "detection_receipt must be the server-issued 64-character identifier.");
  }
  if (!["shared_server", "personal_openai", "own_key"].includes(detectorProvider)) {
    throw new HttpError(400, "bad_detector_provider",
      "detector.provider must identify shared_server or personal_openai.");
  }
  if (!detectorPromptVersion || !Number.isInteger(detectorSchemaVersion)
      || detectorSchemaVersion < 1) {
    throw new HttpError(400, "bad_detector_contract",
      "detector.prompt_version and a positive integer schema_version are required.");
  }
  if (detectorProvider !== "shared_server"
      && !SUPPORTED_PERSONAL_DETECTOR_CONTRACTS.has(
        `${detectorPromptVersion}:${detectorSchemaVersion}`)) {
    throw new HttpError(409, "detector_version_mismatch",
      "That personal-key detector contract is not supported by this server.");
  }
  return {
    lat,
    lng,
    clientObservationId,
    observedAt: normalizedObservedAt(body.observed_at),
    gpsAccuracy,
    heading,
    speed,
    ...provenance,
    damageType,
    size,
    imageHash,
    detectionReceiptId: detectionReceiptId || null,
    detectionReceipt: null,
    detector: {
      provider: detectorProvider,
      model: boundedString(detector.model, 80) || null,
      prompt_version: detectorPromptVersion,
      schema_version: detectorSchemaVersion,
    },
    lgd: boundedString(body.lgd_hint, 64) || null,
    town: boundedString(body.town_hint, 160) || null,
  };
}

async function reportResult(env, context, row, duplicate, distance, extra = {}) {
  const current = await env.DB.prepare(
    `SELECT id,lat,lng,body_lgd,town,damage_type,size,
            first_seen_at,last_seen_at,seen_count,
            (SELECT COUNT(DISTINCT install_id) FROM observations
              WHERE pothole_id=potholes.id
                AND verification_state='server_verified_shared')
              AS verified_shared_observers,
            (SELECT COUNT(DISTINCT install_id) FROM observations
              WHERE pothole_id=potholes.id
                AND verification_state='client_attested')
              AS client_attested_observers
       FROM potholes WHERE id = ?1`
  ).bind(row.id).first();
  const payload = {
    duplicate,
    ...(extra.resubmitted ? { resubmitted: true } : {}),
    dedupe: duplicate
      ? {
          kind: extra.kind || "nearby",
          distance_m: Math.round((distance || 0) * 10) / 10,
        }
      : null,
    pothole: publicPothole(current),
  };
  const status = duplicate ? 200 : 201;
  context.potholeId = current.id;
  context.outcome = extra.resubmitted
    ? "resubmitted" : duplicate ? "duplicate" : "new_pothole";
  return { payload, status };
}

async function persistReportCandidate(env, context, candidate, jurisdiction) {
  const resubmitted = await env.DB.prepare(
    `SELECT p.id,p.lat,p.lng,p.body_lgd,p.town,p.damage_type,p.size,
            p.first_seen_at,p.last_seen_at,p.seen_count
       FROM observations o JOIN potholes p ON p.id = o.pothole_id
      WHERE o.install_id = ?1 AND o.client_observation_id = ?2`
  ).bind(context.installId, candidate.clientObservationId).first();
  if (resubmitted) {
    await commitReportObservation(
      env,
      context,
      candidate,
      Number(resubmitted.id),
      0,
      jurisdiction,
    );
    return reportResult(env, context, resubmitted, true, 0,
      { resubmitted: true, kind: "same_observation" });
  }

  // Status is deliberately irrelevant: any recent nearby observation of
  // compatible road damage joins the same canonical map point.
  const existing = await nearbyPothole(env, candidate);
  if (existing) {
    await commitReportObservation(
      env,
      context,
      candidate,
      Number(existing.row.id),
      existing.distance,
      jurisdiction,
    );
    return reportResult(env, context, existing.row, true, existing.distance);
  }

  const inserted = await env.DB.prepare(
    `INSERT INTO potholes
       (lat,lng,geohash,body_lgd,town,damage_type,size,
        first_seen_at,last_seen_at,seen_count,created_request_id)
     VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?8,0,?9)`
  ).bind(
    candidate.lat,
    candidate.lng,
    encodeGeohash(candidate.lat, candidate.lng),
    candidate.lgd,
    candidate.town,
    candidate.damageType,
    candidate.size,
    candidate.observedAt,
    context.requestId,
  ).run();
  const newId = Number(inserted.meta && inserted.meta.last_row_id);
  if (!(newId > 0)) {
    throw new HttpError(500, "report_write_failed", "The pothole could not be saved.");
  }

  try {
    // Reconcile a concurrent insert against lower IDs only. This gives both requests
    // the same winner even when their reads interleave: the first row never adopts a
    // later row, and a later row removes only itself.
    const raced = await nearbyPothole(env, candidate, null, null, newId);
    let potholeId = newId;
    let duplicate = false;
    let distance = 0;
    if (raced) {
      await env.DB.prepare(`DELETE FROM potholes WHERE id = ?1`).bind(newId).run();
      potholeId = Number(raced.row.id);
      duplicate = true;
      distance = raced.distance;
    }
    await commitReportObservation(
      env,
      context,
      candidate,
      potholeId,
      duplicate ? distance : null,
      jurisdiction,
    );
    return await reportResult(
      env,
      context,
      { id: potholeId },
      duplicate,
      distance,
    );
  } catch (error) {
    // Canonical creation precedes the lower-ID race check, so compensate a failed
    // first-observation transaction. The guards preserve a row another concurrent
    // reporter has already adopted; a later resubmission then recomputes projections.
    try {
      await cleanupEmptyCanonical(env, newId, context.requestId);
    } catch (cleanupError) {
      console.error(JSON.stringify({
        event: "empty_canonical_cleanup_failed",
        request_id: context.requestId,
        route: context.routeName,
        pothole_id: newId,
        error: String(cleanupError && cleanupError.message || cleanupError),
      }));
    }
    throw error;
  }
}

async function handlePotholeReport(request, env, context) {
  const body = await parseJsonBody(request, context);
  const cached = await idempotentResult(env, context);
  if (cached) return cached;
  const candidate = reportCandidate(body);
  context.captureSource = candidate.captureSource;
  context.locationSource = candidate.locationSource;
  await rejectReplay(env, context);
  candidate.detectionReceipt = await verifySharedDetectionReceipt(
    env, context, candidate);
  // Resolve the body here as well as in tender lookup. Native capture can report
  // before tender resolution finishes, and client hints must never become the map's
  // authoritative jurisdiction when KGIS is available.
  const jurisdiction = await resolveJurisdiction(
    env,
    {
      lgd_hint: candidate.lgd,
      town_hint: candidate.town,
      address_hint: body.address_hint,
    },
    candidate.lat,
    candidate.lng,
  );
  // Client hints can keep an outage-tolerant response useful, but public map
  // jurisdiction is authoritative only when KGIS verified municipal ownership.
  const municipalJurisdiction = jurisdiction.source === "kgis"
    && jurisdiction.road_ownership === "municipal";
  candidate.lgd = municipalJurisdiction ? jurisdiction.lgd : null;
  candidate.town = municipalJurisdiction ? jurisdiction.town : null;
  context.visionMode = ["personal_openai", "own_key"].includes(candidate.detector.provider)
    ? "own_key" : candidate.detector.provider === "shared_server"
      ? "shared_server" : "none";
  const result = await persistReportCandidate(env, context, candidate, jurisdiction);
  await rememberIdempotency(env, context, result.payload, result.status);
  return jsonResponse(result.payload, result.status, context.requestId);
}

async function fetchJsonWithTimeout(
  url,
  init = {},
  timeoutMs = 10_000,
  validate = () => true,
) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    if (!response.ok) return { available: false, data: null };
    const data = await response.json();
    return validate(data)
      ? { available: true, data }
      : { available: false, data: null };
  } catch {
    return { available: false, data: null };
  } finally {
    clearTimeout(timer);
  }
}

function validArcGisPayload(value) {
  return Boolean(value && typeof value === "object" && !value.error
    && Array.isArray(value.features));
}

function validGeocoderPayload(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value)
    && !value.error && boundedString(shortAddress(value), 500));
}

function kgisPointUrl(endpoint, lat, lng, outFields, distanceMetres = 0) {
  const geometry = encodeURIComponent(JSON.stringify({
    x: lng,
    y: lat,
    spatialReference: { wkid: 4326 },
  }));
  const distance = Number.isFinite(distanceMetres) && distanceMetres > 0
    ? `&distance=${encodeURIComponent(String(distanceMetres))}`
      + "&units=esriSRUnit_Meter"
    : "";
  return `${endpoint}?geometry=${geometry}`
    + "&geometryType=esriGeometryPoint&spatialRel=esriSpatialRelIntersects"
    + distance
    + `&outFields=${encodeURIComponent(outFields)}`
    + "&returnGeometry=false&f=json";
}

function reverseGeocoder(env, lat, lng) {
  const configured = boundedString(env.GEOCODER_REVERSE_URL, 2_048);
  const allowPublic = String(env.ALLOW_PUBLIC_NOMINATIM || "false").toLowerCase()
    === "true";
  const endpoint = configured || (allowPublic ? PUBLIC_NOMINATIM_URL : "");
  if (!endpoint) return null;
  try {
    const url = new URL(endpoint);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    if (url.hostname === "nominatim.openstreetmap.org" && !allowPublic) return null;
    url.searchParams.set("lat", String(lat));
    url.searchParams.set("lon", String(lng));
    url.searchParams.set("format", "jsonv2");
    url.searchParams.set("zoom", "17");
    url.searchParams.set("addressdetails", "1");
    const headers = {
      "user-agent": boundedString(env.GEOCODER_USER_AGENT, 300)
        || "PotholeReporter/1.13 (+https://github.com/coding-parrot/pothole-reporter)",
    };
    const token = boundedString(env.GEOCODER_BEARER_TOKEN, 2_048);
    if (configured && url.hostname !== "nominatim.openstreetmap.org" && token) {
      headers.authorization = `Bearer ${token}`;
    }
    return {
      url: url.href,
      headers,
      publicNominatim: url.hostname === "nominatim.openstreetmap.org",
    };
  } catch {
    return null;
  }
}

async function acquirePublicNominatimLease(env) {
  const time = nowMs();
  const row = await env.DB.prepare(
    `INSERT INTO external_rate_gates(name,next_allowed_at)
     VALUES ('public_nominatim',?1)
     ON CONFLICT(name) DO UPDATE SET next_allowed_at=excluded.next_allowed_at
       WHERE external_rate_gates.next_allowed_at<=?2
     RETURNING next_allowed_at`
  ).bind(time + PUBLIC_NOMINATIM_INTERVAL_MS, time).first();
  return Boolean(row);
}

function cachedLocation(lat, lng) {
  const key = `${lat.toFixed(5)},${lng.toFixed(5)}`;
  const cached = locationCache.get(key);
  if (!cached || cached.expiresAt <= nowMs()) {
    if (cached) locationCache.delete(key);
    return { key, value: null };
  }
  // Refresh insertion order so the bounded cache behaves like a small LRU.
  locationCache.delete(key);
  locationCache.set(key, cached);
  return { key, value: cached.value };
}

function rememberLocation(key, value) {
  locationCache.set(key, { value, expiresAt: nowMs() + LOCATION_CACHE_TTL_MS });
  while (locationCache.size > LOCATION_CACHE_MAX) {
    locationCache.delete(locationCache.keys().next().value);
  }
}

function shortAddress(value) {
  const address = value && value.address || {};
  const parts = [
    address.road || address.pedestrian || address.residential || address.footway,
    address.neighbourhood || address.hamlet,
    address.suburb || address.village,
    address.city || address.town || address.municipality,
    address.postcode,
  ].filter((part, index, all) => part && all.indexOf(part) === index);
  return parts.join(", ") || value && value.display_name || null;
}

async function resolveJurisdiction(env, body, lat, lng) {
  const cache = cachedLocation(lat, lng);
  let resolved = cache.value;
  if (!resolved) {
    const geocoder = reverseGeocoder(env, lat, lng);
    const geocoderAdmitted = geocoder && geocoder.publicNominatim
      ? await acquirePublicNominatimLease(env) : Boolean(geocoder);
    const highwayProximityMetres = Math.min(50, Math.max(5,
      finiteNumber(env.KGIS_NH_PROXIMITY_METRES, DEFAULT_NH_PROXIMITY_METRES)));
    const [townResult, nationalHighwayResult, stateHighwayResult,
      districtHighwayResult, geocodeResult] = await Promise.all([
      fetchJsonWithTimeout(kgisPointUrl(
        KGIS_TOWN_URL,
        lat,
        lng,
        "KGISTownName,Town_Type,KGISTownCode,LGD_TownCode",
      ), {}, 10_000, validArcGisPayload),
      fetchJsonWithTimeout(kgisPointUrl(
        KGIS_NH_URL,
        lat,
        lng,
        "Name",
        highwayProximityMetres,
      ), {}, 10_000, validArcGisPayload),
      fetchJsonWithTimeout(kgisPointUrl(
        KGIS_SH_URL,
        lat,
        lng,
        "Name",
        highwayProximityMetres,
      ), {}, 10_000, validArcGisPayload),
      fetchJsonWithTimeout(kgisPointUrl(
        KGIS_DH_URL,
        lat,
        lng,
        "Name",
        highwayProximityMetres,
      ), {}, 10_000, validArcGisPayload),
      geocoderAdmitted
        ? fetchJsonWithTimeout(
          geocoder.url,
          { headers: geocoder.headers, redirect: "error" },
          10_000,
          validGeocoderPayload,
        )
        : Promise.resolve({ available: false, data: null }),
    ]);
    const kgis = townResult.data;
    const geocode = geocodeResult.data;
    const highwayLayers = [
      { result: nationalHighwayResult, ownership: "national_highway" },
      { result: stateHighwayResult, ownership: "state_highway" },
      { result: districtHighwayResult, ownership: "district_highway" },
    ];
    const highwaysAvailable = highwayLayers.every(({ result }) => result.available);
    const highwayMatch = highwayLayers.find(({ result }) =>
      result.data && result.data.features[0]);
    const highwayFeature = highwayMatch
      && highwayMatch.result.data.features[0];
    const feature = kgis && kgis.features[0];
    const attributes = feature && feature.attributes || {};
    const authoritativeTownLgd = attributes.LGD_TownCode == null
      ? "" : boundedString(String(attributes.LGD_TownCode), 64);
    let roadOwnership = "unknown";
    let highwayName = null;
    let gpAvailable = false;
    let gpName = null;
    if (townResult.available && highwaysAvailable) {
      if (highwayFeature) {
        roadOwnership = highwayMatch.ownership;
        highwayName = boundedString(highwayFeature.attributes
          && highwayFeature.attributes.Name, 160) || null;
      } else if (feature && authoritativeTownLgd) {
        roadOwnership = "municipal";
      } else if (feature) {
        // A polygon without its authoritative LGD key cannot safely select a
        // tender partition. Do not substitute the caller's lgd_hint.
        roadOwnership = "unknown";
      } else {
        const gpResult = await fetchJsonWithTimeout(kgisPointUrl(
          KGIS_GP_URL,
          lat,
          lng,
          "KGISGPName",
        ), {}, 10_000, validArcGisPayload);
        gpAvailable = gpResult.available;
        const gpFeature = gpResult.data && gpResult.data.features[0];
        gpName = boundedString(gpFeature && gpFeature.attributes
          && gpFeature.attributes.KGISGPName, 160) || null;
        roadOwnership = !gpResult.available
          ? "unknown" : gpName ? "rural" : "outside_state";
      }
    }
    resolved = {
      lgd: authoritativeTownLgd,
      town: attributes.KGISTownName
        ? String(attributes.KGISTownName).trim() : "",
      address: shortAddress(geocode) || "",
      kgisAvailable: townResult.available && highwaysAvailable,
      townAvailable: townResult.available,
      highwayAvailable: highwaysAvailable,
      nationalHighwayAvailable: nationalHighwayResult.available,
      stateHighwayAvailable: stateHighwayResult.available,
      districtHighwayAvailable: districtHighwayResult.available,
      gpAvailable,
      geocoderAvailable: geocodeResult.available,
      geocoderProvider: geocoder && geocoder.publicNominatim
        ? "public_nominatim" : geocoder ? "operator_geocoder" : null,
      roadOwnership,
      highwayName,
      gpName,
    };
    // Cache only a complete answer. Caching one provider's transient failure next
    // to another provider's success made retries reuse the failure for five minutes.
    const ownershipComplete = resolved.kgisAvailable
      && resolved.roadOwnership !== "unknown"
      && (resolved.roadOwnership !== "rural" || resolved.gpAvailable);
    const addressComplete = resolved.roadOwnership !== "municipal"
      || resolved.geocoderAvailable;
    if (ownershipComplete && addressComplete) {
      rememberLocation(cache.key, resolved);
    }
  }
  const hintAddress = boundedString(body.address_hint, 500);
  // A Town polygon says where the point lies; it does not make that town the road
  // owner.  Never expose (or let a client persist) municipal identity for a highway,
  // rural road, outside-state point, or unresolved ownership.  Municipal resolution
  // already requires KGIS' authoritative LGD_TownCode, so client identity hints are
  // neither necessary nor safe here.
  const municipal = resolved.roadOwnership === "municipal";
  const publicLgd = municipal ? resolved.lgd || null : null;
  const publicTown = municipal ? resolved.town || null : null;
  return {
    lat,
    lng,
    address: resolved.address || hintAddress || null,
    lgd: publicLgd,
    town: publicTown,
    source: publicLgd ? "kgis" : "unresolved",
    address_source: resolved.address
      ? resolved.geocoderProvider || "geocoder"
      : hintAddress ? "client_hint" : "unresolved",
    lookup: {
      kgis: resolved.kgisAvailable ? "available" : "unavailable",
      kgis_town: resolved.townAvailable ? "available" : "unavailable",
      kgis_highway: resolved.highwayAvailable ? "available" : "unavailable",
      kgis_national_highway: resolved.nationalHighwayAvailable ? "available" : "unavailable",
      kgis_state_highway: resolved.stateHighwayAvailable ? "available" : "unavailable",
      kgis_district_highway: resolved.districtHighwayAvailable ? "available" : "unavailable",
      kgis_gp: resolved.gpAvailable ? "available" : "not_needed_or_unavailable",
      geocoder: resolved.geocoderAvailable ? "available"
        : hintAddress ? "skipped_client_hint" : "unavailable",
      // Retain the released name while the configured endpoint becomes
      // provider-neutral.
      nominatim: resolved.geocoderAvailable ? "available"
        : hintAddress ? "skipped_client_hint" : "unavailable",
    },
    road_ownership: resolved.roadOwnership || "unknown",
    highway_name: resolved.highwayName || null,
    rural_body: resolved.gpName || null,
  };
}

async function backfillObservedPotholeJurisdiction(
  env,
  context,
  lat,
  lng,
  jurisdiction,
) {
  if (!authoritativeRoadOwnership(jurisdiction)) return null;
  const radius = Math.max(1, finiteNumber(env.DEDUPE_MAX_METRES, 20));
  const [minLat, maxLat, minLng, maxLng] = boundingBox(lat, lng, radius);
  const { results = [] } = await env.DB.prepare(
    `SELECT p.id,p.lat,p.lng,p.last_seen_at
       FROM potholes p
       JOIN pothole_observers o ON o.pothole_id = p.id
      WHERE o.install_id = ?1
        AND p.lat BETWEEN ?2 AND ?3
        AND p.lng BETWEEN ?4 AND ?5
      ORDER BY p.last_seen_at DESC,p.id DESC
      LIMIT 50`
  ).bind(context.installId, minLat, maxLat, minLng, maxLng).all();
  let nearest = null;
  let nearestDistance = Infinity;
  for (const row of results) {
    const distance = metresBetween(lat, lng, Number(row.lat), Number(row.lng));
    if (distance <= radius && distance < nearestDistance) {
      nearest = row;
      nearestDistance = distance;
    }
  }
  if (!nearest) return null;
  await applyAuthoritativeJurisdiction(env, nearest.id, jurisdiction);
  return Number(nearest.id);
}

const TENDER_STOP = new Set([
  "road", "roads", "street", "cross", "main", "layout", "bengaluru", "bangalore",
  "karnataka", "india", "ward", "city", "corporation", "south", "north", "east",
  "west", "central", "urban", "sector", "stage", "block", "phase", "nagar",
  "nagara", "colony", "enclave", "extension", "extn", "area", "locality",
  "village", "town", "zone", "division", "circle", "junction", "lane",
  "old", "new", "service", "rd", "st",
]);
const BENGALURU_BODIES = new Set(["305850", "305851", "305852", "305853", "305854"]);

function addressTokens(address) {
  const tokens = new Set();
  for (const part of String(address || "").split(",").slice(0, 4)) {
    const words = part.trim().toLowerCase()
      .replace(/[()]/g, " ").split(/[^a-z0-9]+/).filter(Boolean);
    for (const word of words) {
      if (word.length > 2 && !TENDER_STOP.has(word)) tokens.add(word);
    }
    // Karnataka locality names commonly alternate between spaced and joined forms
    // ("Vasanth Nagar" / "Vasanthnagar"). Add only complete adjacent n-grams,
    // never arbitrary substrings, so that this recall aid cannot make "Nagar" match
    // an unrelated "Vijayanagar".
    for (let width = 2; width <= Math.min(3, words.length); width++) {
      for (let start = 0; start + width <= words.length; start++) {
        const phrase = words.slice(start, start + width);
        const compact = phrase.join("");
        if (compact.length >= 5 && phrase.some((word) => !TENDER_STOP.has(word))) {
          tokens.add(compact);
        }
      }
    }
  }
  return tokens;
}

function tenderDescriptionTokens(value) {
  const words = String(value || "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  const tokens = new Set(words.filter((word) => word.length > 2));
  for (let width = 2; width <= Math.min(3, words.length); width++) {
    for (let start = 0; start + width <= words.length; start++) {
      const phrase = words.slice(start, start + width);
      const compact = phrase.join("");
      if (compact.length >= 5 && phrase.some((word) => !TENDER_STOP.has(word))) {
        tokens.add(compact);
      }
    }
  }
  return tokens;
}

function tenderShortlist(address, pool, limit = TENDER_CONFIG.maxCandidates) {
  const tokens = addressTokens(address);
  if (!tokens.size || !pool.length) return [];
  // D1 does not promise row order and `location` is not consistently just a body
  // name. Remove only metadata words common to nearly the whole pool; using pool[0]
  // made a random first row erase a real locality such as Vijayanagar.
  const locationTokenFrequency = new Map();
  const locationTokens = [];
  for (const tender of pool) {
    const rowTokens = tenderDescriptionTokens(tender.location);
    locationTokens.push(rowTokens);
    for (const token of rowTokens) {
      locationTokenFrequency.set(token, (locationTokenFrequency.get(token) || 0) + 1);
    }
  }
  for (const [token, frequency] of locationTokenFrequency) {
    if ((pool.length > 1 && frequency === pool.length)
        || (pool.length >= 8 && frequency > pool.length * 0.8)) tokens.delete(token);
  }
  if (!tokens.size) return [];

  // Match complete location tokens only. Substring matching made a generic address
  // token such as "nagar" match an unrelated "Vijayanagar" tender and could name the
  // wrong contractor.
  const descriptionTokens = pool.map((tender, index) => new Set([
    ...tenderDescriptionTokens(tender.title),
    ...locationTokens[index],
  ]));
  const weights = new Map();
  for (const token of tokens) {
    let appearances = 0;
    for (const words of descriptionTokens) {
      if (words.has(token)) appearances++;
    }
    if (!appearances) continue;
    if (pool.length > 1 && appearances === pool.length) continue;
    if (pool.length >= 8 && appearances > pool.length * 0.5) continue;
    weights.set(token, Math.log((pool.length + 1) / (appearances + 0.5)));
  }
  if (!weights.size) return [];
  const scored = [];
  for (let index = 0; index < pool.length; index++) {
    let score = 0;
    for (const [token, weight] of weights) {
      if (descriptionTokens[index].has(token)) score += weight;
    }
    if (score > 0) scored.push({ score, tender: pool[index] });
  }
  const publishedStamp = (tender) => {
    const match = /^(\d{2})-(\d{2})-(\d{4})/.exec(String(tender.published || ""));
    return match ? Date.UTC(Number(match[3]), Number(match[2]) - 1, Number(match[1])) : 0;
  };
  scored.sort((left, right) =>
    right.score - left.score
    || publishedStamp(right.tender) - publishedStamp(left.tender)
    || String(left.tender.tender_number)
      .localeCompare(String(right.tender.tender_number)));
  const maximum = Number.isFinite(limit) ? Math.max(0, Math.trunc(limit)) : scored.length;
  return scored.slice(0, maximum).map((entry) => entry.tender);
}

// This fallback deliberately recognizes only explicit road-surface work phrases.
// It does not try to understand generic "improvement" tenders and it refuses to
// choose when more than one eligible location candidate remains. The narrow gate
// is preferable to naming an unrelated contractor when tender adjudication cannot
// use the model.
const UNAMBIGUOUS_ROAD_WORK = [
  /\b(?:pothole|pot\s+hole)s?\b(?:\W+\w+){0,6}\W+\b(?:fill\w*|filing|patch\w*|block\w*|clos\w*|cover\w*|seal\w*|repair\w*|maint\w*)\b/i,
  /\b(?:fill\w*|filing|patch\w*|block\w*|clos\w*|cover\w*|seal\w*|repair\w*|maint\w*)\b(?:\W+\w+){0,6}\W+\b(?:pothole|pot\s+hole)s?\b/i,
  /\b(?:cc|cement\s+concrete|concrete)\s+roads?\b/i,
  /\broads?\s+concret(?:e|ing)\b/i,
  /\b(?:interlocking\s+)?pavers?(?:\s+blocks?)?\s+roads?\b/i,
  /\broads?\s+(?:paving|interlock(?:ing)?(?:\s+(?:work|installation))?)\b/i,
  /\broad\s+patch(?:ing|\s+repair|\s+repairs)?\b/i,
];
const ACTIVE_SURFACE_WORK = /\b(?:re\s*[- ]?\s*asphalt(?:ed|ing)?|asphalting|resurfac\w*|re\s*[- ]?\s*carpet\w*|black\s*[- ]?\s*topp?(?:ed|ing)|tarring|paver\s+finish\s+asphalt|asphalt\s+work)\b/i;
// These operations may precede a long proper road name. Require the road noun and
// refuse to cross a non-surface asset while looking for it; a bare word "asphalt"
// in a supply/footpath tender is not evidence of carriageway responsibility.
const TIGHT_ROAD_SURFACE_WORK = [
  /\b(?:re\s*[- ]?\s*asphalt\w*|asphalt\w*|blacktopp?\w*|resurfac\w*|paver\s+finish\s+asphalt|widen\w*|strengthen\w*|upgrad\w*)\b(?:(?!\b(?:footpaths?|sidewalks?|walkways?|kerbs?|curbs?|drains?|drainage|culverts?|utilit(?:y|ies)|landscap\w*|buildings?|parks?|medians?|dividers?|lighting|lights?|signage|signboards?|markings?|furniture|plantation|bridges?|(?:crash\s+|safety\s+)?barriers?)\b)[^.;]){0,180}\b(?:road|roads|carriageway|pavement)\b/i,
  /\b(?:road|roads|carriageway|pavement)\b(?:(?!\b(?:footpaths?|sidewalks?|walkways?|kerbs?|curbs?|drains?|drainage|culverts?|utilit(?:y|ies)|landscap\w*|buildings?|parks?|medians?|dividers?|lighting|lights?|signage|signboards?|markings?|furniture|plantation|bridges?|(?:crash\s+|safety\s+)?barriers?)\b)[^.;]){0,80}\b(?:re\s*[- ]?\s*asphalt\w*|asphalt\w*|blacktopp?\w*|resurfac\w*|widen\w*|strengthen\w*|upgrad\w*)\b/i,
];
const GENERIC_ROAD_WORK = [
  /\b(?:repair|repairs|maintenance|resurfac\w*|re-?asphalt\w*|asphalt\w*|blacktopp?\w*|concreting|rehabilitation|restoration|reconstruction|construction|development|improvements?)\s+(?:of\s+|to\s+)?(?:the\s+)?(?:\w+[\s,/-]+){0,3}(?:road|roads|carriageway|pavement)\b/i,
  /\b(?:road|roads|carriageway|pavement)\s+(?:repair|repairs|maintenance|resurfac\w*|rehabilitation|restoration|reconstruction|construction|development|improvements?)\b/i,
];
const NON_ROAD_SCOPE_ASSET = /\b(?:excluded\s+asset|footpaths?|sidewalks?|walkways?|kerbs?|curbs?|drains?|drainage|culverts?|utilit(?:y|ies)|electric(?:al)?\s+poles?|landscap\w*|buildings?|parks?|medians?|dividers?|lighting|lights?|signage|signboards?|markings?|furniture|plantation|bridges?|(?:crash\s+|safety\s+)?barriers?|retaining\s+walls?|compound\s+walls?|deck\s+slabs?|covering\s+slabs?|bus\s+shelters?|pedestrian\s+subways?)\b/i;
const NON_ROAD_WORK_FIRST = /\b(?:repair|repairs|maintenance|construction|reconstruction|development|improvements?|concreting|shifting|relocation|re-?asphalt\w*|asphalt\w*|blacktopp?\w*|resurfac\w*|providing\s+(?:paver\s+finish\s+)?asphalt|laying\s+(?:paver\s+finish\s+)?asphalt)\s+(?:of\s+|to\s+)?(?:the\s+)?(?:(?:rcc|cc|cement\s+concrete|concrete|pipe|open|closed|pedestrian|storm\s*[- ]?\s*water)\s+){0,3}(?:excluded\s+asset|footpaths?|sidewalks?|walkways?|kerbs?|curbs?|drains?|drainage|culverts?|utilit(?:y|ies)|electric(?:al)?\s+poles?|landscap\w*|buildings?|parks?|medians?|dividers?|lighting|lights?|signage|signboards?|markings?|furniture|plantation|bridges?|(?:crash\s+|safety\s+)?barriers?|retaining\s+walls?|compound\s+walls?|deck\s+slabs?|covering\s+slabs?|bus\s+shelters?|pedestrian\s+subways?)\b/i;
const GENERIC_CIVIL_ONLY = /\b(?:general\s+improvements?|miscellaneous\s+civil\s+works?)\b/i;
const ENABLING_WIDENING_ONLY = /\b(?:facilitat\w*|obstruct\w*|shifting?|relocat\w*|land\s+acquisition|prepar\w*\s+(?:of\s+)?dpr)\b(?:(?![.;]).){0,120}\b(?:road\s+)?widen\w*\b/i;
const NON_SURFACE_ASSET_PHRASE = /\b(?:street\s*lights?|streetlights?|utility\s+(?:ducts?|lines?|cables?)|cycle\s*tracks?|traffic\s*signals?|pedestrian\s+underpass(?:es)?)\b/gi;
const NON_ROAD_ASSET_FIRST = new RegExp(
  `${NON_ROAD_SCOPE_ASSET.source}\\s+`
    + "(?:repair|repairs|maintenance|construction|reconstruction|development|"
    + "improvements?|installation|replacement|cleaning)\\b",
  "i",
);
// When a tender starts with footpath/drain work, a bare later word "road" is not
// enough to waive that exclusion: it could introduce a road median, light, sign or
// side drain. Require an explicit surface material or surface-work action.
const EXPLICIT_COMBINED_ROAD = /(?:\band\b|,)\s*(?:the\s+)?(?:(?:cc|cement\s+concrete|concrete|re-?asphalt\w*|asphalt\w*|blacktopp?\w*|paver\s+finish\s+asphalt)\s+(?:road|roads|carriageway|pavement)|(?:widen\w*|strengthen\w*|upgrad\w*|resurfac\w*|re-?asphalt\w*|asphalt\w*)\s+(?:of\s+|to\s+)?(?:the\s+)?(?:road|roads|carriageway|pavement)|(?:road|roads|carriageway|pavement)\s+(?:repair|repairs|maintenance|widen\w*|strengthen\w*|upgrad\w*|resurfac\w*|rehabilitation|restoration|reconstruction|development|improvements?|patching))\b/i;
const ROAD_NON_SURFACE_ASSET = /\broad(?:\s*[- ]?\s*side)?\s+(?:(?:storm\s+water\s+)?(?:drain(?:age)?|footpath|walkway|kerb|curb|culvert)s?|median|divider|lighting|lights?|signage|signboards?|markings?|furniture|plantation|landscap\w*|(?:crash\s+|safety\s+)?barriers?|bridge)\b/gi;
const SHARED_MIXED_ROAD_WORK = [
  /\b(?:repair|repairs|maintenance|construction|development|improvements?)\s+(?:of\s+|to\s+)?(?:the\s+)?(?:road|roads|carriageway|pavement)\s*(?:,|\band\b|&|\bwith\b)\s*(?:footpaths?|sidewalks?|walkways?|kerbs?|curbs?|drains?|drainage|culverts?|utilit(?:y|ies)|landscap\w*|medians?|dividers?|lighting|signage|markings?|bridges?)\b/i,
  /\b(?:repair|repairs|maintenance|construction|development|improvements?)\s+(?:of\s+|to\s+)?(?:the\s+)?(?:footpaths?|sidewalks?|walkways?|kerbs?|curbs?|drains?|drainage|culverts?|utilit(?:y|ies)|landscap\w*|medians?|dividers?|lighting|signage|markings?|bridges?)\s*(?:,|\band\b|&|\bwith\b)\s*(?:road|roads|carriageway|pavement)\b/i,
];

function strongRoadSurfaceEvidence(value) {
  if (ENABLING_WIDENING_ONLY.test(value)) return false;
  if (ACTIVE_SURFACE_WORK.test(value) && !NON_ROAD_SCOPE_ASSET.test(value)) return true;
  if (TIGHT_ROAD_SURFACE_WORK.some((pattern) => pattern.test(value))) return true;
  if (UNAMBIGUOUS_ROAD_WORK.some((pattern) => pattern.test(value))) return true;
  if (EXPLICIT_COMBINED_ROAD.test(value)) return true;
  return SHARED_MIXED_ROAD_WORK.some((pattern) => pattern.test(value));
}

function normalizeTenderScopeTitle(title) {
  return String(title || "")
    .replace(/\br\s*\.?\s*c\s*\.?\s*c\.?\b/gi, "rcc")
    .replace(/\bc\s*\.?\s*c\.?\b/gi, "cc")
    .replace(/\bfoot\s*[- ]+\s*path(s?)\b/gi, "footpath$1")
    .replace(/\b(?:draine|drainge)\b/gi, "drain")
    .replace(/\bdrainages\b/gi, "drainage")
    .replace(/\bdrain(?=from\b|near\b|in\b|cleaning\b)/gi, "drain ")
    .replace(/\s+/g, " ");
}

function roadSurfaceEvidence(value) {
  if (strongRoadSurfaceEvidence(value)) return true;
  return GENERIC_ROAD_WORK.some((pattern) => pattern.test(value));
}

function scrubNonSurfaceRoadAssets(title) {
  // Remove phrases where "road" modifies a non-surface asset before applying road
  // patterns. "Road-side drain" is drainage work, not evidence that the carriageway
  // itself is covered by the contract.
  return normalizeTenderScopeTitle(title)
    .replace(/\bconcrete\s+pavement\s+(?:to|for|on)\s+(?:the\s+)?footpaths?\b/gi,
      "excluded asset")
    .replace(NON_SURFACE_ASSET_PHRASE, "excluded asset")
    .replace(ROAD_NON_SURFACE_ASSET, "excluded asset")
    // Dotted initials are common inside road names ("A.R Dsouza road"). Treat the
    // dots as token separators so the bounded surface-action matcher does not mistake
    // an initial for the end of a sentence.
    .replace(/\./g, " ");
}

function hasExplicitRoadWorkScope(title) {
  const value = scrubNonSurfaceRoadAssets(title);
  if (GENERIC_CIVIL_ONLY.test(value) && !roadSurfaceEvidence(
    value.replace(GENERIC_CIVIL_ONLY, ""))) return false;
  // A tightly associated road-surface phrase wins even when the same package later
  // adds drains or footpaths. Real awards frequently list those clauses in either
  // order; the old one-directional "and road" exception dropped valid contracts.
  if (strongRoadSurfaceEvidence(value)) return true;
  if (NON_ROAD_ASSET_FIRST.test(value)) return false;
  if (NON_ROAD_WORK_FIRST.test(value) && !EXPLICIT_COMBINED_ROAD.test(value)) {
    return false;
  }
  return GENERIC_ROAD_WORK.some((pattern) => pattern.test(value));
}

function isClearlyNonRoadOnlyScope(title) {
  const raw = String(title || "");
  const value = scrubNonSurfaceRoadAssets(raw);
  if (GENERIC_CIVIL_ONLY.test(value) && !roadSurfaceEvidence(
    value.replace(GENERIC_CIVIL_ONLY, ""))) return true;
  // This is a negative-only safety boundary for model-selected candidates. Do not
  // require a title to fit the deterministic fallback's finite positive vocabulary:
  // real awards use many valid formulations (widening, asphalting, paver finish,
  // strengthening, and so on). Reject only when a known non-carriageway asset is
  // actually present and no explicit road-surface evidence survives after phrases
  // such as "road-side drain" and "road median" are scrubbed.
  const mentionsExcludedAsset = value !== raw || NON_ROAD_SCOPE_ASSET.test(value);
  if (!mentionsExcludedAsset) return false;
  if (strongRoadSurfaceEvidence(value)) return false;
  if (NON_ROAD_ASSET_FIRST.test(value)) return true;
  if (NON_ROAD_WORK_FIRST.test(value) && !EXPLICIT_COMBINED_ROAD.test(value)) {
    return true;
  }
  return !GENERIC_ROAD_WORK.some((pattern) => pattern.test(value));
}

const ROAD_IDENTITY_MARKERS = new Set([
  "road", "roads", "street", "cross", "lane", "main", "marg", "highway",
]);

function normalizedRoadWords(value) {
  return String(value || "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean)
    .map((word) => word === "rd" ? "road" : word === "st" ? "street" : word);
}

function addressRoadIdentity(address) {
  const words = normalizedRoadWords(String(address || "").split(",", 1)[0]);
  if (!words.some((word) => ROAD_IDENTITY_MARKERS.has(word))) return null;
  const distinctive = words.filter((word) => !ROAD_IDENTITY_MARKERS.has(word)
    && !TENDER_STOP.has(word));
  if (!distinctive.length) return null;
  const simpleNamedRoad = words.length === 2
    && words[1] === "road"
    && /^[a-z][a-z0-9]{2,}$/i.test(words[0])
    && !/^\d+(?:st|nd|rd|th)?$/i.test(words[0]);
  return {
    words,
    simpleCore: simpleNamedRoad ? words[0] : null,
  };
}

function candidateCoversRoadIdentity(title, identity) {
  if (!identity) return false;
  const words = normalizedRoadWords(title);
  for (let start = 0; start + identity.words.length <= words.length; start++) {
    if (identity.words.every((word, offset) => words[start + offset] === word)) {
      return true;
    }
  }
  if (!identity.simpleCore) return false;

  // Reverse geocoders sometimes return a short proper-name road while the tender
  // spells the same stretch as "<name> <landmark> road". Permit that narrow form,
  // but never let an administrative mention ("Manipala ward") jump to another road.
  for (let index = 0; index < words.length; index++) {
    if (words[index] !== identity.simpleCore) continue;
    const suffix = words.slice(index + 1, index + 4);
    const roadIndex = suffix.indexOf("road");
    if (roadIndex < 0) continue;
    const between = suffix.slice(0, roadIndex);
    if (!between.some((word) => ROAD_IDENTITY_MARKERS.has(word)
        || ["ward", "zone", "division", "layout", "area", "locality"].includes(word))) {
      return true;
    }
  }
  return false;
}

const ROAD_IDENTITY_NOISE = new Set([
  ...TENDER_STOP,
  "of", "to", "at", "in", "on", "near", "from", "between", "along", "under",
  "repair", "repairs", "maintenance", "maintaining", "construction", "reconstruction",
  "development", "improvement", "improvements", "resurfacing", "asphalting",
  "asphalt", "widening", "strengthening", "upgradation", "restoration",
  "providing", "laying", "work", "works", "annual", "package", "scheme",
]);

function candidateHasSpecificRoadIdentity(title) {
  if (areaWideRoadWork(title)) return false;
  const words = normalizedRoadWords(title);
  for (let index = 0; index < words.length; index++) {
    if (!ROAD_IDENTITY_MARKERS.has(words[index])) continue;
    if (words[index] === "roads"
        || words.slice(index + 1, index + 3)
          .some((word) => ["division", "zone", "circle", "ward"].includes(word))) {
      continue;
    }
    // Proper/numeric road identities normally precede the marker: "MG Road",
    // "12th Cross", "Outer Ring Road". Ignore work verbs and administrative
    // boilerplate so "maintenance of roads in Ward 5" remains area-level.
    for (let before = Math.max(0, index - 4); before < index; before++) {
      const word = words[before];
      if (!ROAD_IDENTITY_MARKERS.has(word) && !ROAD_IDENTITY_NOISE.has(word)) {
        return true;
      }
    }
    // Also recognize the common "Road No. 5" / "Road 5" ordering without
    // treating the locality after "road in" as a road name.
    const next = words[index + 1];
    const afterNumberWord = ["no", "number"].includes(next) ? words[index + 2] : next;
    if (/^\d+(?:st|nd|rd|th)?$/.test(afterNumberWord || "")) return true;
  }
  return false;
}

function modelSelectedRoadConflicts(address, candidate) {
  const identity = addressRoadIdentity(address);
  if (!identity || !candidate) return false;
  // Both fields are model-visible untrusted data. A generic title cannot launder
  // an explicitly different road placed in the division/location column, and a
  // correct title cannot make contradictory road metadata safe either.
  for (const value of [candidate.title, candidate.location]) {
    if (!candidateHasSpecificRoadIdentity(value)) continue;
    if (!candidateCoversRoadIdentity(value, identity) && !areaWideRoadWork(value)) {
      return true;
    }
  }
  return false;
}

const UNCONDITIONAL_AREA_WIDE_ROAD_WORK = [
  /\b(?:all|various|multiple|several)\s+(?:the\s+)?(?:road|roads|streets|carriageways)\b/i,
  /\b(?:road|roads|streets|carriageways)\s+(?:throughout|across)\b/i,
  /^\s*(?:annual\s+)?(?:road|roads|streets|carriageways)\s+(?:repair|repairs|maintenance)\b[^.;]{0,100}\b(?:throughout|across)\b/i,
  /\b(?:pothole|pot\s+hole)s?\s+(?:filling|repair|repairs|maintenance)(?:\s+works?)?\s+(?:throughout|across)\b/i,
  /\b(?:pothole|pot\s+hole)s?\s+(?:filling|repair|repairs|maintenance)\b(?:(?!\b(?:road|street|cross|lane|main|marg|highway)\b)[^.;]){0,100}\b(?:ward|zone|division)\b/i,
];

const SPECIFIC_ROAD_REFERENCE = /\b(?:road|rd|street|st|cross|lane|main|marg|highway)\b/i;

function areaWideRoadWork(title) {
  const value = String(title || "");
  if (UNCONDITIONAL_AREA_WIDE_ROAD_WORK.some((pattern) => pattern.test(value))) {
    return true;
  }
  const conditionalPatterns = [
    /\b(?:pothole|pot\s+hole)s?\s+(?:filling|repair|repairs|maintenance)(?:\s+works?)?\s+(?:in|within)\s+([^.;]{1,120})/i,
    /\b(?:roads|streets|carriageways)\s*(?:(?:,|and|&)\s*(?:drains?|footpaths?|culverts?)\s*)?(?:in|within)\s+([^.;]{1,120})/i,
  ];
  for (const pattern of conditionalPatterns) {
    const match = pattern.exec(value);
    if (match && !SPECIFIC_ROAD_REFERENCE.test(match[1])) return true;
  }
  const combinedAt = /\b(?:improvements?|development|maintenance|construction)\s+(?:of\s+|to\s+)?roads\s*(?:,|and|&)\s*(?:drains?|footpaths?|culverts?)\b[^.;]{0,30}\bat\s+([^.;]{1,120})/i.exec(value);
  return Boolean(combinedAt
    && !SPECIFIC_ROAD_REFERENCE.test(combinedAt[1])
    && /\b(?:ward|zone|division)\b/i.test(combinedAt[1]));
}

function deterministicCandidateCoversLocation(address, candidate) {
  const roadIdentity = addressRoadIdentity(address);
  if (candidateCoversRoadIdentity(candidate.title, roadIdentity)) return true;
  // tenderShortlist already established a non-generic locality/ward overlap. Only
  // a clearly area-wide road contract may rely on that overlap without also naming
  // the reported road; a different named stretch in the same locality is not enough.
  return areaWideRoadWork(candidate.title);
}

function deterministicTenderMatch(candidates, address) {
  const eligible = candidates.filter((candidate) =>
    hasExplicitRoadWorkScope(candidate.title)
      && deterministicCandidateCoversLocation(address, candidate));
  if (eligible.length !== 1) return null;
  return {
    candidate: eligible[0],
    confidence: 0.70,
    reason: "One location-matched candidate explicitly covers road-surface work; no competing eligible candidate remained.",
  };
}

function warrantyFor(_published) {
  // A publication date is not an award, completion, hand-over, or contract-specific
  // defect-liability date. Until those authoritative fields are imported, never imply
  // that the named bidder currently owes a repair.
  return {
    warranty: "current liability not established by the publication record",
    warranty_code: "unverified",
  };
}

function validateTenderSelection(address, candidates, match, threshold = TENDER_CONFIG.minimumConfidence) {
  if (!match || !Number.isInteger(match.match_index)
      || match.match_index < 0 || match.match_index >= candidates.length
      || !Number.isFinite(match.confidence) || match.confidence < threshold || match.confidence > 1) {
    return { candidate: null, reason: "no_confident_match" };
  }
  const selected = candidates[match.match_index];
  // A model can rank ambiguous locations, but cannot override scope or select
  // an explicitly different road. Candidate text is data, not authorization.
  if (isClearlyNonRoadOnlyScope(selected.title)) return { candidate: null, reason: "non_road_work_scope" };
  if (modelSelectedRoadConflicts(address, selected)) return { candidate: null, reason: "road_location_conflict" };
  return { candidate: selected, reason: null };
}

function publicTender(selected, confidence, reason, matchMethod) {
  return {
    tender_number: selected.tender_number,
    title: selected.title,
    location: selected.location || null,
    contractor: selected.contractor || null,
    published: selected.published || null,
    confidence,
    reason: boundedString(reason, TENDER_CONFIG.stringLimits.reason) || null,
    match_method: matchMethod,
    ...warrantyFor(selected.published),
    source_name: selected.source_name || null,
    source_url: selected.source_url || null,
  };
}

async function tenderResult(env, context, jurisdiction, options = {}) {
  if (!jurisdiction.lgd) return { tender: null, reason: "jurisdiction_unresolved" };
  if (!jurisdiction.address) return { tender: null, reason: "address_unresolved" };
  const codes = BENGALURU_BODIES.has(jurisdiction.lgd)
    ? [jurisdiction.lgd, "BLR"] : [jurisdiction.lgd];
  const placeholders = codes.map((_, index) => `?${index + 1}`).join(",");
  const { results = [] } = await env.DB.prepare(
    `SELECT tender_number,title,location,contractor,published,body_lgd,
            source_name,source_url
       FROM tenders WHERE body_lgd IN (${placeholders})`
  ).bind(...codes).all();
  if (!results.length) return { tender: null, reason: "no_tenders_for_jurisdiction" };
  const locationCandidates = tenderShortlist(jurisdiction.address, results, Infinity);
  if (!locationCandidates.length) return { tender: null, reason: "no_location_match" };
  const candidates = locationCandidates.slice(0, TENDER_CONFIG.maxCandidates);

  const deterministic = () => {
    const match = deterministicTenderMatch(locationCandidates, jurisdiction.address);
    return match ? {
      tender: publicTender(
        match.candidate,
        match.confidence,
        match.reason,
        "deterministic_location_scope",
      ),
      reason: null,
    } : { tender: null, reason: "no_confident_match" };
  };
  context.visionMode = "shared_tender";
  if (options.forceDeterministic) return deterministic();

  const prompt = tenderUserInput(jurisdiction.address, candidates);
  try {
    await takeVisionQuota(env, context.installId);
    const match = await callOpenAI(env, context, {
      model: TENDER_CONFIG.model,
      instructions: TENDER_INSTRUCTIONS,
      input: [{
        role: TENDER_PROMPT_CONFIG.dataRole,
        content: [{ type: "input_text", text: prompt }],
      }],
      text: outputFormat(TENDER_PROMPT_CONFIG.schemaName, TENDER_SCHEMA),
      reasoning: { effort: TENDER_CONFIG.reasoningEffort },
    }, "tender");
    const selection = validateTenderSelection(jurisdiction.address, candidates, match);
    if (!selection.candidate) return { tender: null, reason: selection.reason };
    const selected = selection.candidate;
    return {
      tender: publicTender(
        selected,
        match.confidence,
        match.reason,
        "model_adjudicated",
      ),
      reason: null,
    };
  } catch (error) {
    if (error instanceof HttpError
        && ["shared_credits_exhausted", "shared_budget_reached",
          "shared_daily_budget_reached", "shared_rate_limit", "daily_vision_limit",
          "shared_vision_not_configured", "shared_vision_unavailable"].includes(error.code)) {
      const deterministicEligible = [
        "shared_credits_exhausted",
        "shared_budget_reached",
        "shared_daily_budget_reached",
        "daily_vision_limit",
      ].includes(error.code);
      if (options.allowDeterministicFallback && deterministicEligible) {
        return deterministic();
      }
      throw new HttpError(503, error.code, error.message, {
        ...(error.details || {}),
        retryable: true,
      });
    }
    throw error;
  }
}

async function handleTenderResolve(request, env, context) {
  const body = await parseJsonBody(request, context);
  const cached = await idempotentResult(env, context);
  if (cached) return cached;
  const lat = externalCoordinate(body.lat);
  const lng = externalCoordinate(body.lng);
  if (!validLatLng(lat, lng)) {
    throw new HttpError(400, "bad_location",
      "Tender resolution needs valid lat and lng coordinates.");
  }
  await rejectReplay(env, context);
  const jurisdiction = await resolveJurisdiction(env, body, lat, lng);
  if (jurisdiction.road_ownership === "unknown") {
    throw new HttpError(503, "road_ownership_unavailable",
      "Road ownership could not be verified. Retry later.", {
        retryable: true,
        services: ["kgis_town", "kgis_highway", "geocoder"].filter((service) =>
          jurisdiction.lookup[service] !== "available"),
      });
  }
  if (["national_highway", "state_highway", "district_highway",
    "rural", "outside_state"].includes(
    jurisdiction.road_ownership)) {
    const enrichedPotholeId = await backfillObservedPotholeJurisdiction(
      env, context, lat, lng, jurisdiction);
    if (enrichedPotholeId != null) context.potholeId = enrichedPotholeId;
    const reason = jurisdiction.road_ownership;
    context.outcome = reason;
    const payload = { jurisdiction, tender: null, reason };
    await rememberIdempotency(env, context, payload);
    return jsonResponse(payload, 200, context.requestId);
  }
  const unavailable = [];
  if (!jurisdiction.lgd && jurisdiction.lookup.kgis === "unavailable") {
    unavailable.push("kgis");
  }
  if (jurisdiction.lookup.geocoder !== "available") {
    unavailable.push("geocoder");
  }
  if (unavailable.length) {
    throw new HttpError(503, "geolocation_unavailable",
      "Location services are temporarily unavailable. Retry later.", {
        retryable: true,
        services: unavailable,
      });
  }
  const enrichedPotholeId = await backfillObservedPotholeJurisdiction(
    env, context, lat, lng, jurisdiction);
  if (enrichedPotholeId != null) context.potholeId = enrichedPotholeId;
  const matched = await tenderResult(env, context, jurisdiction, {
    forceDeterministic: !env.OPENAI_API_KEY,
    allowDeterministicFallback: true,
  });
  context.outcome = matched.tender ? "tender_matched" : matched.reason;
  const payload = {
    jurisdiction,
    tender: matched.tender,
    reason: matched.reason,
  };
  await rememberIdempotency(env, context, payload);
  return jsonResponse(payload, 200, context.requestId);
}

async function handleMap(request, env, context) {
  const url = new URL(request.url);
  const sinceValue = url.searchParams.get("since");
  const since = sinceValue == null
    ? nowMs() - 180 * 86_400_000 : finiteNumber(sinceValue);
  if (!Number.isFinite(since) || since < 0) {
    throw new HttpError(400, "bad_since", "since must be a Unix timestamp in milliseconds.");
  }
  const limit = Math.min(2000, Math.max(1,
    Math.trunc(finiteNumber(url.searchParams.get("limit"), 1000))));
  const parameters = [since];
  // A canonical is created just before its first observation transaction. Keep a
  // Worker termination in that tiny gap from exposing or matching a zero-observer row.
  let where = "last_seen_at >= ?1 AND seen_count > 0";

  const bboxText = url.searchParams.get("bbox");
  if (bboxText) {
    const bbox = bboxText.split(",").map((part) => Number(part));
    if (bbox.length !== 4 || !bbox.every(Number.isFinite)
        || !validLatLng(bbox[1], bbox[0]) || !validLatLng(bbox[3], bbox[2])
        || bbox[0] >= bbox[2] || bbox[1] >= bbox[3]) {
      throw new HttpError(400, "bad_bbox",
        "bbox must be west,south,east,north in longitude/latitude degrees.");
    }
    parameters.push(bbox[1], bbox[3], bbox[0], bbox[2]);
    where += ` AND lat BETWEEN ?${parameters.length - 3} AND ?${parameters.length - 2}`
      + ` AND lng BETWEEN ?${parameters.length - 1} AND ?${parameters.length}`;
  }
  parameters.push(limit);
  const { results = [] } = await env.DB.prepare(
    `SELECT id,lat,lng,body_lgd,town,damage_type,size,
            first_seen_at,last_seen_at,seen_count,
            (SELECT COUNT(DISTINCT o.install_id) FROM observations o
              WHERE o.pothole_id=potholes.id
                AND o.verification_state='server_verified_shared')
              AS verified_shared_observers,
            (SELECT COUNT(DISTINCT o.install_id) FROM observations o
              WHERE o.pothole_id=potholes.id
                AND o.verification_state='client_attested')
              AS client_attested_observers
       FROM potholes WHERE ${where}
       ORDER BY last_seen_at DESC LIMIT ?${parameters.length}`
  ).bind(...parameters).all();
  context.outcome = "map_read";
  return jsonResponse({
    type: "FeatureCollection",
    total: results.length,
    features: results.map((row) => ({
      type: "Feature",
      geometry: {
        type: "Point",
        coordinates: [Number(row.lng), Number(row.lat)],
      },
      properties: {
        id: Number(row.id),
        damage_type: row.damage_type,
        size: row.size || null,
        first_seen_at: Number(row.first_seen_at),
        last_seen_at: Number(row.last_seen_at),
        seen_count: Number(row.seen_count || 0),
        verification: Number(row.verified_shared_observers || 0) > 0
          ? "server_verified_shared" : "client_attested",
        verified_shared_observers: Number(row.verified_shared_observers || 0),
        client_attested_observers: Number(row.client_attested_observers || 0),
        town: row.town || null,
        lgd: row.body_lgd || null,
      },
    })),
  }, 200, context.requestId, { "cache-control": "public, max-age=30" });
}

function validDay(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value)
    && !Number.isNaN(Date.parse(value + "T00:00:00Z"));
}

async function handleImpact(request, env, context) {
  const url = new URL(request.url);
  const defaultTo = isoDay();
  const defaultFrom = isoDay(nowMs() - 29 * 86_400_000);
  const from = url.searchParams.get("from") || defaultFrom;
  const to = url.searchParams.get("to") || defaultTo;
  if (!validDay(from) || !validDay(to) || from > to) {
    throw new HttpError(400, "bad_period",
      "from and to must be valid YYYY-MM-DD values with from no later than to.");
  }
  const fromMs = Date.parse(from + "T00:00:00Z");
  const toMs = Date.parse(to + "T23:59:59.999Z");
  const [
    requestRows,
    captureRows,
    activeRow,
    potholeRow,
    observationRow,
  ] = await Promise.all([
    env.DB.prepare(
      `SELECT route,outcome,vision_mode,SUM(request_count) AS count
         FROM request_metrics_daily
        WHERE day BETWEEN ?1 AND ?2
        GROUP BY route,outcome,vision_mode
        ORDER BY route,outcome,vision_mode`
    ).bind(from, to).all(),
    env.DB.prepare(
      `SELECT capture_source,location_source,vision_mode,outcome,
              SUM(request_count) AS count
         FROM capture_metrics_daily
        WHERE day BETWEEN ?1 AND ?2
        GROUP BY capture_source,location_source,vision_mode,outcome
        ORDER BY capture_source,location_source,vision_mode,outcome`
    ).bind(from, to).all(),
    env.DB.prepare(
      `SELECT COUNT(DISTINCT install_id) AS count
         FROM installation_activity_daily WHERE day BETWEEN ?1 AND ?2`
    ).bind(from, to).first(),
    env.DB.prepare(
      `SELECT COUNT(*) AS total
         FROM potholes WHERE seen_count > 0 AND first_seen_at BETWEEN ?1 AND ?2`
    ).bind(fromMs, toMs).first(),
    env.DB.prepare(
      `SELECT COUNT(*) AS total, COUNT(DISTINCT install_id) AS distinct_observers,
              SUM(CASE WHEN verification_state='server_verified_shared' THEN 1 ELSE 0 END)
                AS server_verified_shared,
              SUM(CASE WHEN verification_state='client_attested' THEN 1 ELSE 0 END)
                AS client_attested,
              COUNT(DISTINCT CASE WHEN verification_state='server_verified_shared'
                THEN install_id END) AS verified_distinct_observers
         FROM observations WHERE observed_at BETWEEN ?1 AND ?2`
    ).bind(fromMs, toMs).first(),
  ]);
  const requests = (requestRows.results || []).map((row) => ({
    route: row.route,
    outcome: row.outcome,
    vision_mode: row.vision_mode,
    count: Number(row.count || 0),
  }));
  const captureChecks = (captureRows.results || []).map((row) => ({
    capture_source: row.capture_source,
    location_source: row.location_source,
    vision_mode: row.vision_mode,
    outcome: row.outcome,
    count: Number(row.count || 0),
  }));
  const videoChecksTotal = captureChecks
    .filter((row) => DRIVE_CAPTURE_SOURCES.has(row.capture_source))
    .reduce((sum, row) => sum + row.count, 0);
  const importedVideoChecksTotal = captureChecks
    .filter((row) => row.capture_source === "imported_video")
    .reduce((sum, row) => sum + row.count, 0);
  context.outcome = "impact_read";
  return jsonResponse({
    period: { from, to },
    active_installations: Number(activeRow && activeRow.count || 0),
    requests_total: requests.reduce((sum, row) => sum + row.count, 0),
    requests,
    capture_checks_total: captureChecks.reduce((sum, row) => sum + row.count, 0),
    video_checks_total: videoChecksTotal,
    imported_video_checks_total: importedVideoChecksTotal,
    capture_checks: captureChecks,
    potholes: {
      total: Number(potholeRow && potholeRow.total || 0),
    },
    observations: {
      total: Number(observationRow && observationRow.total || 0),
      distinct_observers: Number(
        observationRow && observationRow.distinct_observers || 0),
      server_verified_shared: Number(
        observationRow && observationRow.server_verified_shared || 0),
      client_attested: Number(observationRow && observationRow.client_attested || 0),
      verified_distinct_observers: Number(
        observationRow && observationRow.verified_distinct_observers || 0),
    },
  }, 200, context.requestId, { "cache-control": "public, max-age=60" });
}

const MAP_HTML = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Pothole Reporter impact map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
  <style>
    :root { color-scheme: dark; font-family: system-ui,sans-serif; background:#0d0f12; color:#f5f5f5; }
    * { box-sizing:border-box; }
    body { margin:0; }
    header { padding:16px; display:flex; gap:20px; align-items:center; flex-wrap:wrap; }
    h1 { margin:0; font-size:20px; }
    #stats { display:flex; gap:12px; color:#b8bec8; font-size:14px; }
    #map { height:calc(100vh - 76px); min-height:480px; background:#171a20; }
    .legend { background:#15181eeb; padding:8px 10px; border-radius:8px; line-height:1.7; }
    .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }
    a { color:#8bc7ff; }
  </style>
</head>
<body>
  <header>
    <h1>Pothole Reporter impact map</h1>
    <div id="stats" aria-live="polite">Loading aggregate impact…</div>
  </header>
  <main id="map" aria-label="Reported road damage map"></main>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <script nonce="__CSP_NONCE__">
    const map = L.map("map").setView([12.97,77.59], 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom:19, attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).addTo(map);
    const layer = L.layerGroup().addTo(map);
    const legend = L.control({position:"bottomright"});
    legend.onAdd = function() {
      const div = L.DomUtil.create("div","legend");
      div.innerHTML = '<span class="dot" style="background:#ff7043"></span>Server-verified shared detection<br>'
        + '<span class="dot" style="background:#64b5f6"></span>Client-attested detection';
      return div;
    };
    legend.addTo(map);
    let loadNumber = 0;
    async function loadMap() {
      const mine = ++loadNumber;
      const b = map.getBounds();
      const bbox = [b.getWest(),b.getSouth(),b.getEast(),b.getNorth()].join(",");
      const response = await fetch("/v1/map?bbox=" + encodeURIComponent(bbox) + "&limit=2000");
      const data = await response.json();
      if (mine !== loadNumber || !response.ok) return;
      layer.clearLayers();
      for (const feature of data.features || []) {
        const p = feature.properties;
        const verified = p.verification === "server_verified_shared";
        const marker = L.circleMarker(
          [feature.geometry.coordinates[1],feature.geometry.coordinates[0]],
          {radius:Math.min(11,5+Math.log2(Math.max(1,p.seen_count))),
           color:verified ? "#ff7043" : "#64b5f6",fillOpacity:.8,weight:2}
        );
        marker.bindPopup("<b>" + p.damage_type.replaceAll("_"," ") + "</b><br>"
          + p.seen_count + " installation(s) observed it<br>"
          + p.verified_shared_observers + " server-verified · "
          + p.client_attested_observers + " client-attested");
        marker.addTo(layer);
      }
    }
    async function loadImpact() {
      const response = await fetch("/v1/impact");
      const data = await response.json();
      if (!response.ok) return;
      document.getElementById("stats").textContent =
        data.active_installations + " active installs · "
        + data.potholes.total + " new potholes · "
        + (data.video_checks_total || 0) + " video checks · "
        + (data.imported_video_checks_total || 0) + " imported-video checks";
    }
    map.on("moveend", loadMap);
    loadMap().catch(console.error);
    loadImpact().catch(console.error);
  </script>
</body>
</html>`;

function htmlResponse(html, requestId) {
  const nonce = crypto.randomUUID().replaceAll("-", "");
  return new Response(html.replaceAll("__CSP_NONCE__", nonce), {
    status: 200,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "public, max-age=300",
      "content-security-policy":
        `default-src 'self'; script-src 'self' https://unpkg.com 'nonce-${nonce}'; `
        + "style-src 'self' https://unpkg.com 'unsafe-inline'; "
        + "img-src 'self' data: https://*.tile.openstreetmap.org; "
        + "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none';",
      "referrer-policy": "no-referrer",
      "x-content-type-options": "nosniff",
      "x-request-id": requestId,
      ...corsHeaders(),
    },
  });
}

function routeName(pathname) {
  return KNOWN_ROUTES.has(pathname) ? pathname : "not_found";
}

async function dispatch(request, env, context) {
  const url = new URL(request.url);
  const pathname = url.pathname;
  if (request.method === "OPTIONS") {
    context.routeName = "OPTIONS";
    return new Response(null, {
      status: 204,
      headers: { "x-request-id": context.requestId, ...corsHeaders() },
    });
  }
  if (request.method === "GET" && (pathname === "/" || pathname === "/map")) {
    context.outcome = "dashboard";
    return htmlResponse(MAP_HTML, context.requestId);
  }
  if (request.method === "GET" && pathname === "/v1/health") {
    context.outcome = "healthy";
    const detector = sharedDetectorStatus(env);
    return jsonResponse({
      ok: true,
      // Keep the original boolean for all released clients.
      shared_vision_configured: detector.configured,
      shared_vision_provider: detector.provider,
      shared_vision_provider_mode: detector.provider,
      shared_vision_model: detector.model,
      shared_vision_primary_provider: detector.primary_provider,
      shared_vision_primary_configured: detector.primary_configured,
      shared_vision_fallback_provider: detector.fallback_provider,
      shared_vision_fallback_configured: detector.fallback_configured,
      shared_vision_fallback_model: detector.fallback_model,
      supported_shared_vision_providers: [...SHARED_DETECTOR_PROVIDERS],
      detection_prompt_version: DETECT_PROMPT_VERSION,
      detection_schema_version: DETECT_SCHEMA_VERSION,
      supported_personal_detector_contracts: [
        { prompt_version: DETECT_PROMPT_VERSION, schema_version: DETECT_SCHEMA_VERSION },
      ],
      shared_detection_receipts_required: sharedDetectionReceiptsRequired(env),
      operator_geocoder_configured: Boolean(reverseGeocoder({
        ...env,
        ALLOW_PUBLIC_NOMINATIM: "false",
      }, 0, 0)),
      public_nominatim_enabled: String(env.ALLOW_PUBLIC_NOMINATIM || "false")
        .toLowerCase() === "true",
    }, 200, context.requestId, { "cache-control": "public, max-age=30" });
  }
  if (request.method === "GET" && pathname === "/v1/map") {
    return handleMap(request, env, context);
  }
  if (request.method === "GET" && pathname === "/v1/impact") {
    return handleImpact(request, env, context);
  }
  if (request.method === "POST" && pathname === "/v1/installations") {
    return handleInstallation(request, env, context);
  }

  if (request.method === "POST") await authenticate(request, env, context);
  if (request.method === "POST" && pathname === "/v1/activity") {
    return handleActivity(request, env, context);
  }
  if (request.method === "POST" && pathname === "/v1/vision/detect") {
    return handleVisionDetect(request, env, context);
  }
  if (request.method === "POST" && pathname === "/v1/tenders/resolve") {
    return handleTenderResolve(request, env, context);
  }
  if (request.method === "POST" && pathname === "/v1/potholes/report") {
    return handlePotholeReport(request, env, context);
  }
  throw new HttpError(404, "not_found", "No such endpoint exists.");
}

export default {
  async fetch(request, env) {
    const startedAt = nowMs();
    const requestId = crypto.randomUUID();
    const pathname = new URL(request.url).pathname;
    const context = {
      requestId,
      routeName: routeName(pathname),
      idempotencyRoute: idempotencyStorageRoute(pathname),
      method: request.method.toUpperCase(),
      outcome: null,
      visionMode: "none",
      installId: null,
      potholeId: null,
      detectorProvider: null,
      detectorRequestId: null,
      openaiRequestId: null,
      detectionOpenAIRequestId: null,
      tenderOpenAIRequestId: null,
      yoloRequestId: null,
      openaiErrorCode: null,
      yoloErrorCode: null,
      detectorFallbackReason: null,
      captureSource: null,
      locationSource: null,
      rawBody: null,
      bodyHash: null,
      idempotencyKey: "",
      idempotencyClaimToken: null,
      signatureHash: null,
    };
    let response;
    try {
      response = await dispatch(request, env, context);
    } catch (error) {
      try {
        await releaseIdempotencyClaim(env, context);
      } catch (releaseError) {
        console.error(JSON.stringify({
          event: "idempotency_claim_release_failed",
          request_id: requestId,
          route: context.routeName,
          error: String(releaseError && releaseError.message || releaseError),
        }));
      }
      context.outcome = error instanceof HttpError ? error.code : "internal_error";
      console.error(JSON.stringify({
        event: "request_error",
        request_id: requestId,
        route: context.routeName,
        error: error instanceof HttpError ? error.code : String(error && error.message || error),
      }));
      response = errorResponse(error, requestId);
    }
    await recordMetrics(env, context, response, nowMs() - startedAt);
    return response;
  },
  async scheduled(_controller, env, executionContext) {
    executionContext.waitUntil(pruneExpiredTenderReplays(env).catch((error) => {
      console.error(JSON.stringify({
        event: "tender_replay_prune_failed",
        error: String(error && error.message || error),
      }));
    }));
    executionContext.waitUntil(pruneExpiredDetectionReceipts(env).catch((error) => {
      console.error(JSON.stringify({
        event: "detection_receipt_prune_failed",
        error: String(error && error.message || error),
      }));
    }));
  },
};

export const __test = {
  boundingBox,
  encodeGeohash,
  metresBetween,
  normalizeP256Signature,
  pruneExpiredTenderReplays,
  pruneExpiredDetectionReceipts,
  idempotencyClaimTtlMs,
  awsSigV4Headers,
  parseYoloExecuteApiEndpoint,
  sharedDetectorStatus,
  tenderShortlist,
  hasExplicitRoadWorkScope,
  isClearlyNonRoadOnlyScope,
  modelSelectedRoadConflicts,
  validateTenderSelection,
  deterministicTenderMatch,
  validateDetectionVerdict,
  sharedDetectionReceiptsRequired,
  validArcGisPayload,
  validGeocoderPayload,
  reverseGeocoder,
};
