import { createHash } from "node:crypto";

import {
  DETECT_PROMPT_VERSION,
  DETECT_SCHEMA,
  DETECT_SCHEMA_VERSION,
  MODEL_CONFIG,
} from "../../../llm/generated/contract.mjs";
import {
  installationPublicKey,
  newRequestId,
  sha256Hex,
  verifyInstallationSignature,
} from "./auth.mjs";
import { HttpError, asHttpError } from "./errors.mjs";
import { matchTender } from "./tenders.mjs";
import {
  metresBetween,
  nearbyCells,
  roundedPublicCoordinate,
  spatialCell,
  validLatLng,
} from "./spatial.mjs";

const MAX_BODY_BYTES = 8_000_000;
const MAX_IMAGE_BYTES = 3_500_000;
const SIGNATURE_AGE_MS = 5 * 60_000;
const RECEIPT_AGE_MS = 30 * 24 * 60 * 60_000;
const IDEMPOTENCY_AGE_MS = 30 * 24 * 60 * 60_000;
const DAMAGE_TYPES = new Set(DETECT_SCHEMA.properties.damage_type.enum.filter(Boolean));
const SIZES = new Set(DETECT_SCHEMA.properties.size.enum.filter(Boolean));
const CAPTURE_SOURCES = new Set(["manual", "drive_live", "drive_vod", "imported_video"]);
const FEEDBACK_TEST_MODES = new Set(["bike", "car", "walk", "other"]);
const FEEDBACK_TEXT_MAX = 2_000;
const LOCATION_SOURCES = new Set([
  "device_gps", "gpx_timestamp", "current_position_confirmed", "none",
]);

const bounded = (value, maximum) => typeof value === "string"
  ? value.trim().slice(0, maximum) : "";
const number = (value) => typeof value === "number" && Number.isFinite(value)
  ? value : NaN;
const today = (value = Date.now()) => new Date(value).toISOString().slice(0, 10);
const headers = (event) => Object.fromEntries(Object.entries(event.headers || {})
  .map(([key, value]) => [key.toLowerCase(), String(value)]));

function jsonBody(event) {
  let raw = event.body || "";
  if (event.isBase64Encoded) raw = Buffer.from(raw, "base64").toString("utf8");
  const bytes = Buffer.byteLength(raw);
  if (bytes > MAX_BODY_BYTES) {
    throw new HttpError(413, "request_too_large", "The JSON body may not exceed 8 MB.");
  }
  let value;
  try { value = JSON.parse(raw); } catch {
    throw new HttpError(400, "bad_json", "The request body is not valid JSON.");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new HttpError(400, "bad_json", "The request body must be a JSON object.");
  }
  return { value, raw };
}

function response(statusCode, payload, requestId, cacheControl = "no-store") {
  return {
    statusCode,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": cacheControl,
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET,POST,OPTIONS",
      "access-control-allow-headers": "content-type,x-install-id,x-timestamp,x-signature,idempotency-key",
      "access-control-expose-headers": "x-request-id",
      "x-content-type-options": "nosniff",
      "x-request-id": requestId,
    },
    body: JSON.stringify({ request_id: requestId, ...payload }),
    isBase64Encoded: false,
  };
}

function route(event) {
  const method = String(event.requestContext?.http?.method || event.httpMethod || "GET").toUpperCase();
  const path = event.rawPath || event.path || "/";
  return { method, path };
}

function query(event) {
  return event.queryStringParameters || {};
}

function image(value) {
  const dataUrl = typeof value === "string" ? value : value?.data_url;
  const match = /^data:(image\/(?:jpeg|jpg|png|webp));base64,([A-Za-z0-9+/=_-]+)$/i.exec(dataUrl || "");
  if (!match) {
    throw new HttpError(400, "bad_image", "Send one base64 JPEG, PNG or WebP data URL.");
  }
  const mime = match[1].toLowerCase().replace("image/jpg", "image/jpeg");
  const bytes = Buffer.from(match[2].replace(/-/g, "+").replace(/_/g, "/"), "base64");
  if (!bytes.length || bytes.length > MAX_IMAGE_BYTES) {
    throw new HttpError(413, "image_too_large", "The image must be at most 3.5 MB.");
  }
  const validMagic = mime === "image/jpeg"
    ? bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff
    : mime === "image/png"
      ? bytes.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex"))
      : bytes.subarray(0, 4).toString() === "RIFF"
        && bytes.subarray(8, 12).toString() === "WEBP";
  if (!validMagic) throw new HttpError(400, "bad_image", "Image bytes do not match the media type.");
  return { dataUrl, bytes, mime };
}

function provenance(body, captureMode, hasLocation, allowLabelOnly = false) {
  const captureSource = bounded(body.capture_source, 32)
    || (captureMode === "drive" ? "drive_live" : "manual");
  const locationSource = bounded(body.location_source, 40)
    || (hasLocation ? "device_gps" : "none");
  if (!CAPTURE_SOURCES.has(captureSource)
      || (captureMode === "manual" && captureSource !== "manual")
      || (captureMode === "drive" && captureSource === "manual")) {
    throw new HttpError(400, "bad_capture_source", "capture_source does not match capture_mode.");
  }
  if (!LOCATION_SOURCES.has(locationSource)
      || (!allowLabelOnly && hasLocation === (locationSource === "none"))) {
    throw new HttpError(400, "bad_location_source", "location_source does not match the coordinates.");
  }
  return { captureSource, locationSource };
}

export function validateVerdict(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || !["acceptable", "rejected"].includes(value.image_quality)
      || !["damaged", "undamaged"].includes(value.assessment)
      || !(value.damage_type === null || DAMAGE_TYPES.has(value.damage_type))
      || !(value.size === null || SIZES.has(value.size))
      || typeof value.description !== "string") {
    throw new HttpError(502, "bad_upstream_response",
      `The detector did not return schema ${DETECT_SCHEMA_VERSION}.`);
  }
  const damaged = value.image_quality === "acceptable" && value.assessment === "damaged";
  if ((damaged && !value.damage_type)
      || (!damaged && (value.assessment !== "undamaged" || value.damage_type || value.size))) {
    throw new HttpError(502, "bad_upstream_response", "The detector returned contradictory fields.");
  }
  return {
    image_quality: value.image_quality,
    assessment: value.assessment,
    damage_type: value.damage_type,
    size: value.size,
    description: value.description.trim().slice(0, 1_000),
  };
}

function publicPothole(value) {
  return {
    id: Number(value.id),
    lat: roundedPublicCoordinate(Number(value.lat)),
    lng: roundedPublicCoordinate(Number(value.lng)),
    damage_type: value.damage_type,
    size: value.size || null,
    first_seen_at: Number(value.first_seen_at),
    last_seen_at: Number(value.last_seen_at),
    complaint_count: Number(value.complaint_count || 0),
    seen_count: Number(value.complaint_count || 0),
    observation_count: Number(value.observation_count || 0),
    verification: Number(value.server_verified_count || 0) > 0
      ? "server_verified_shared" : "client_attested",
    town: value.town || null,
    lgd: value.body_lgd || null,
  };
}

function numericPotholeId(seed = newRequestId()) {
  const value = BigInt(`0x${createHash("sha256").update(seed).digest("hex").slice(0, 13)}`);
  return Number(value % 8_000_000_000_000_000n) + 1;
}

function validDay(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value)
    && !Number.isNaN(Date.parse(`${value}T00:00:00Z`));
}

export function createService({ repository, detector, geolocator, logger = console } = {}) {
  if (!repository || !detector || !geolocator) throw new Error("Service dependencies are required.");

  async function authenticate(event, context, path, method, raw) {
    const inputHeaders = headers(event);
    const installId = bounded(inputHeaders["x-install-id"], 64);
    const timestamp = bounded(inputHeaders["x-timestamp"], 32);
    const signature = bounded(inputHeaders["x-signature"], 512);
    const idempotencyKey = bounded(inputHeaders["idempotency-key"], 180);
    if (!/^[a-f0-9]{32}$/i.test(installId) || !timestamp || !signature) {
      throw new HttpError(401, "unsigned_request",
        "X-Install-ID, X-Timestamp and X-Signature are required.");
    }
    if (!idempotencyKey) {
      throw new HttpError(400, "idempotency_key_required", "Send an Idempotency-Key header.");
    }
    const sentAt = Number(timestamp);
    if (!Number.isFinite(sentAt) || Math.abs(Date.now() - sentAt) > SIGNATURE_AGE_MS) {
      throw new HttpError(401, "stale_request", "The signed request is too old.");
    }
    const installation = await repository.getInstallation(installId);
    if (!installation || installation.revoked_at) {
      throw new HttpError(401, "unknown_installation", "This installation is not registered.");
    }
    if (!verifyInstallationSignature({
      installation,
      signature,
      method,
      path,
      timestamp,
      idempotencyKey,
      body: raw,
    })) {
      throw new HttpError(401, "bad_signature", "This request signature is not valid.");
    }
    context.installId = installId;
    context.idempotencyId = `${installId}#${path}#${idempotencyKey}`;
    context.requestHash = sha256Hex(raw);
    const claimed = await repository.claimIdempotency({
      id: context.idempotencyId,
      requestHash: context.requestHash,
      owner: context.requestId,
      leaseExpiresAt: Date.now() + 180_000,
    });
    if (claimed.status === "COMPLETED") {
      if (claimed.request_hash !== context.requestHash) {
        throw new HttpError(409, "idempotency_conflict",
          "That Idempotency-Key was used for a different request.");
      }
      context.idempotentReplay = true;
      return {
        replay: response(
          Number(claimed.status_code),
          { ...JSON.parse(claimed.response_json), idempotent_replay: true },
          context.requestId,
        ),
      };
    }
    if (claimed.status !== "CLAIMED") {
      if (claimed.request_hash && claimed.request_hash !== context.requestHash) {
        throw new HttpError(409, "idempotency_conflict",
          "That Idempotency-Key is being used for a different request.");
      }
      throw new HttpError(425, "idempotency_in_progress",
        "This operation is still in progress; retry shortly.", { retryable: true });
    }
    context.idempotencyClaimed = true;
    const replayHash = sha256Hex(Buffer.from(signature, "base64"));
    if (!await repository.claimReplay(`${installId}#${replayHash}`, Date.now() + 600_000)) {
      throw new HttpError(409, "replayed_request", "This signed request was already used.");
    }
    await repository.touchInstallation(installId);
    return { replay: null };
  }

  async function complete(context, statusCode, payload) {
    await repository.completeIdempotency({
      id: context.idempotencyId,
      owner: context.requestId,
      requestHash: context.requestHash,
      statusCode,
      payload,
      expiresAt: Date.now() + IDEMPOTENCY_AGE_MS,
    });
    context.idempotencyClaimed = false;
    return response(statusCode, payload, context.requestId);
  }

  async function installation(event, context) {
    const { value } = jsonBody(event);
    let key;
    try { key = installationPublicKey(value.public_key); } catch (error) {
      throw new HttpError(400, error.code || "bad_public_key", error.message);
    }
    await repository.registerInstallation({
      id: key.installId,
      public_key: key.encoded,
      public_key_format: key.format,
    });
    context.installId = key.installId;
    context.outcome = "registered";
    return response(201, { install_id: key.installId }, context.requestId);
  }

  async function activity(body, context) {
    if (body.event !== "vision_check" || body.vision_provider !== "personal_openai"
        || !["manual", "drive"].includes(body.capture_mode)) {
      throw new HttpError(400, "bad_activity",
        "Activity must be a personal_openai vision_check in manual or drive mode.");
    }
    const source = provenance(body, body.capture_mode, false, true);
    context.visionMode = "own_key";
    context.outcome = `vision_check_${body.capture_mode}`;
    await repository.recordCapture({
      ...source,
      visionMode: "own_key",
      outcome: "vision_check",
    });
    return complete(context, 202, { accepted: true, event: "vision_check" });
  }

  async function detect(body, context) {
    context.visionMode = "shared_detect";
    if (body.prompt_version !== DETECT_PROMPT_VERSION) {
      throw new HttpError(409, "prompt_version_mismatch",
        `This server requires ${DETECT_PROMPT_VERSION}.`);
    }
    if (!Array.isArray(body.images) || body.images.length !== 1) {
      throw new HttpError(400, "bad_image_count", "Shared detection requires one image.");
    }
    const captureMode = ["manual", "drive"].includes(body.capture_mode)
      ? body.capture_mode : null;
    if (!captureMode) throw new HttpError(400, "bad_capture_mode", "capture_mode is invalid.");
    const selectedImage = image(body.images[0]);
    const language = MODEL_CONFIG.allowedLanguages.includes(body.language)
      ? body.language : MODEL_CONFIG.defaultLanguage;
    const model = MODEL_CONFIG.allowedModels.includes(body.model)
      ? body.model : MODEL_CONFIG.defaultModel;
    const imageDetail = MODEL_CONFIG.allowedImageDetails.includes(body.image_detail)
      ? body.image_detail : MODEL_CONFIG.defaultImageDetail;
    const hasLocation = Object.hasOwn(body, "lat") || Object.hasOwn(body, "lng");
    const lat = Object.hasOwn(body, "lat") ? number(body.lat) : null;
    const lng = Object.hasOwn(body, "lng") ? number(body.lng) : null;
    if (hasLocation && !validLatLng(lat, lng)) {
      throw new HttpError(400, "bad_detection_location", "lat and lng must both be valid.");
    }
    const source = provenance(body, captureMode, hasLocation);
    const quota = await repository.takeVisionQuota(context.installId);
    if (!quota.ok) {
      throw new HttpError(quota.code === "shared_rate_limit" ? 429 : 503,
        quota.code, "The configured shared-vision limit has been reached.", {
          retryable: quota.code === "shared_rate_limit",
          limit: quota.limit,
        });
    }
    const detection = await detector.detect({
      images: [{ dataUrl: selectedImage.dataUrl }],
      captureMode,
      language,
      model,
      imageDetail,
    }, context);
    context.detectorProvider = detection.provider;
    const verdict = validateVerdict(detection.verdict);
    const payload = {
      ...verdict,
      detector: {
        provider: "shared_server",
        backend_provider: detection.provider,
        model: detection.model,
        prompt_version: DETECT_PROMPT_VERSION,
        schema_version: DETECT_SCHEMA_VERSION,
        evidence_count: 1,
        ...(detection.fallbackFrom ? {
          fallback_from: detection.fallbackFrom,
          fallback_reason: detection.fallbackReason,
        } : {}),
      },
      quota: { used: null, limit: quota.limit },
    };
    const observationId = bounded(body.client_observation_id, 180);
    if (verdict.image_quality === "acceptable" && verdict.assessment === "damaged"
        && observationId) {
      const receipt = sha256Hex(`${context.requestId}\n${context.installId}\n${observationId}\n${sha256Hex(selectedImage.bytes)}`);
      const expiresAt = Date.now() + RECEIPT_AGE_MS;
      await repository.putReceipt({
        id: receipt,
        install_id: context.installId,
        client_observation_id: observationId,
        image_hash: sha256Hex(selectedImage.bytes),
        lat,
        lng,
        damage_type: verdict.damage_type,
        size: verdict.size,
        backend_provider: detection.provider,
        detector_model: detection.model,
        prompt_version: DETECT_PROMPT_VERSION,
        schema_version: DETECT_SCHEMA_VERSION,
        issued_at: Date.now(),
        expiresAt,
      });
      payload.detection_receipt = receipt;
      payload.detection_receipt_expires_at = expiresAt;
    }
    context.outcome = verdict.assessment;
    await repository.recordCapture({
      ...source,
      visionMode: "shared_detect",
      outcome: verdict.assessment,
    });
    return complete(context, 200, payload);
  }

  async function feedback(body, context) {
    const hasRating = Object.hasOwn(body, "rating") && body.rating !== null;
    const rating = hasRating ? number(body.rating) : null;
    const text = bounded(body.text, FEEDBACK_TEXT_MAX);
    const testMode = body.test_mode == null ? null : bounded(body.test_mode, 16);
    if ((hasRating && !(Number.isInteger(rating) && rating >= 1 && rating <= 5))
        || (!hasRating && !text)
        || (testMode !== null && !FEEDBACK_TEST_MODES.has(testMode))) {
      throw new HttpError(400, "bad_feedback",
        "Feedback needs a whole 1 to 5 rating or some text, and test_mode must be bike, car, walk or other.");
    }
    const quota = await repository.takeFeedbackQuota(context.installId);
    if (!quota.ok) {
      throw new HttpError(429, "feedback_limit_reached",
        "This installation has sent the maximum feedback for today.", { limit: quota.limit });
    }
    const email = bounded(body.email, 254);
    const createdAt = Date.now();
    await repository.putFeedback({
      install_id: context.installId,
      request_id: context.requestId,
      created_at: createdAt,
      rating,
      text,
      test_mode: testMode,
      app_version: bounded(body.app_version, 32) || null,
      device: bounded(body.device, 80) || null,
      email: /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ? email.toLowerCase() : null,
    });
    context.outcome = "feedback_received";
    return complete(context, 201, { accepted: true, created_at: createdAt });
  }

  async function resolveTender(body, context) {
    const lat = number(body.lat);
    const lng = number(body.lng);
    if (!validLatLng(lat, lng)) {
      throw new HttpError(400, "bad_location", "Tender resolution needs valid lat and lng.");
    }
    const jurisdiction = await geolocator.resolve({
      lat,
      lng,
      addressHint: bounded(body.address_hint, 500),
    });
    if (jurisdiction.road_ownership === "unknown") {
      throw new HttpError(503, "road_ownership_unavailable",
        "Road ownership could not be verified. Retry later.", { retryable: true });
    }
    if (jurisdiction.road_ownership !== "municipal") {
      context.outcome = jurisdiction.road_ownership;
      return complete(context, 200, {
        jurisdiction,
        tender: null,
        reason: jurisdiction.road_ownership,
      });
    }
    if (!jurisdiction.lgd) {
      throw new HttpError(503, "geolocation_unavailable",
        "The municipal body could not be resolved.", { retryable: true });
    }
    const tenders = await repository.queryTenders(jurisdiction.lgd);
    const matched = tenders.length
      ? matchTender(jurisdiction.address, tenders)
      : { tender: null, reason: "no_tenders_for_jurisdiction" };
    context.outcome = matched.tender ? "tender_matched" : matched.reason;
    return complete(context, 200, { jurisdiction, ...matched });
  }

  async function report(body, context) {
    const lat = number(body.lat);
    const lng = number(body.lng);
    const observationId = bounded(body.client_observation_id, 180);
    const observedAt = Math.trunc(number(body.observed_at));
    const damageType = bounded(body.damage_type, 64);
    const size = body.size == null ? null : bounded(body.size, 16);
    const imageHash = bounded(body.image_hash, 80).toLowerCase();
    if (!validLatLng(lat, lng) || !observationId || !Number.isFinite(observedAt)
        || !DAMAGE_TYPES.has(damageType) || !(size === null || SIZES.has(size))
        || !/^[a-f0-9]{64}$/.test(imageHash)) {
      throw new HttpError(400, "bad_report", "The pothole observation is incomplete or invalid.");
    }
    const captureMode = bounded(body.capture_source, 32) === "manual" ? "manual" : "drive";
    const source = provenance(body, captureMode, true);
    const provider = bounded(body.detector?.provider, 40);
    let verification = "client_attested";
    let receiptId = null;
    if (provider === "shared_server") {
      receiptId = bounded(body.detection_receipt, 128);
      const receipt = receiptId ? await repository.getReceipt(receiptId) : null;
      if (!receipt || receipt.install_id !== context.installId
          || receipt.client_observation_id !== observationId
          || receipt.image_hash !== imageHash
          || receipt.damage_type !== damageType
          || (receipt.size || null) !== size
          || Number(receipt.expires_at || 0) * 1_000 <= Date.now()
          || (receipt.lat != null && metresBetween(lat, lng, receipt.lat, receipt.lng) > 10)) {
        throw new HttpError(409, "invalid_detection_receipt",
          "The shared detection receipt does not match this observation.");
      }
      verification = "server_verified_shared";
    } else if (!["personal_openai", "own_key"].includes(provider)
        || body.detector?.prompt_version !== DETECT_PROMPT_VERSION
        || Number(body.detector?.schema_version) !== DETECT_SCHEMA_VERSION) {
      throw new HttpError(400, "bad_detector", "The detector provenance is invalid.");
    }
    const jurisdiction = await geolocator.resolve({
      lat,
      lng,
      addressHint: bounded(body.address_hint, 500),
    }).catch(() => ({ road_ownership: "unknown", source: "unresolved" }));
    const municipal = jurisdiction.source === "kgis"
      && jurisdiction.road_ownership === "municipal";
    const cells = nearbyCells(lat, lng, repository.dedupeRadiusMetres);
    if (!await repository.acquireLocationLocks(cells, context.requestId)) {
      throw new HttpError(425, "location_dedupe_in_progress",
        "A nearby report is being consolidated. Retry shortly.", { retryable: true });
    }
    let created = false;
    let duplicateDistance = 0;
    let pothole;
    try {
      const candidates = await repository.findNearby(cells);
      const nearest = candidates.map((candidate) => ({
        candidate,
        distance: metresBetween(lat, lng, Number(candidate.lat), Number(candidate.lng)),
      })).filter(({ distance }) => distance <= repository.dedupeRadiusMetres)
        .sort((left, right) => left.distance - right.distance)[0];
      if (nearest) {
        pothole = nearest.candidate;
        duplicateDistance = nearest.distance;
      } else {
        for (let attempt = 0; attempt < 3 && !pothole; attempt += 1) {
          const id = numericPotholeId(`${context.requestId}:${attempt}`);
          const candidate = {
            id,
            lat,
            lng,
            damage_type: damageType,
            size,
            first_seen_at: observedAt,
            last_seen_at: observedAt,
            complaint_count: 0,
            observation_count: 0,
            server_verified_count: 0,
            body_lgd: municipal ? jurisdiction.lgd : null,
            town: municipal ? jurisdiction.town : null,
            map_shard: String(id % 16).padStart(2, "0"),
            created_request_id: context.requestId,
          };
          if (await repository.createPothole(candidate, spatialCell(lat, lng))) {
            pothole = candidate;
            created = true;
          }
        }
        if (!pothole) throw new HttpError(500, "report_write_failed", "Could not allocate a report ID.");
      }
      await repository.attachObservation({
        potholeId: pothole.id,
        receiptId,
        observation: {
          install_id: context.installId,
          client_observation_id: observationId,
          request_id: context.requestId,
          observed_at: observedAt,
          lat,
          lng,
          gps_accuracy_m: Number.isFinite(number(body.gps_accuracy_m)) ? body.gps_accuracy_m : null,
          heading_deg: Number.isFinite(number(body.heading_deg)) ? body.heading_deg : null,
          speed_mps: Number.isFinite(number(body.speed_mps)) ? body.speed_mps : null,
          capture_source: source.captureSource,
          location_source: source.locationSource,
          damage_type: damageType,
          size,
          image_hash: imageHash,
          detector_provider: provider,
          verification_state: verification,
          detector_model: bounded(body.detector?.model, 80) || null,
          prompt_version: body.detector?.prompt_version || DETECT_PROMPT_VERSION,
          schema_version: Number(body.detector?.schema_version || DETECT_SCHEMA_VERSION),
          duplicate_distance_m: created ? null : duplicateDistance,
        },
      });
      pothole = await repository.getPothole(pothole.id);
    } finally {
      await repository.releaseLocationLocks(cells, context.requestId).catch(() => {});
    }
    context.potholeId = pothole.id;
    context.visionMode = provider === "shared_server" ? "shared_server" : "own_key";
    context.outcome = created ? "created" : "deduplicated";
    await repository.recordReport({ newPothole: created, verification });
    return complete(context, created ? 201 : 200, {
      pothole: publicPothole(pothole),
      duplicate: !created,
      dedupe: {
        radius_m: repository.dedupeRadiusMetres,
        distance_m: Math.round(duplicateDistance * 10) / 10,
      },
    });
  }

  async function publicMap(event, context) {
    const params = query(event);
    const since = params.since == null ? Date.now() - 180 * 86_400_000 : Number(params.since);
    const limit = Math.min(2_000, Math.max(1, Math.trunc(Number(params.limit) || 1_000)));
    if (!Number.isFinite(since) || since < 0) {
      throw new HttpError(400, "bad_since", "since must be a millisecond timestamp.");
    }
    let bbox = null;
    if (params.bbox) {
      bbox = params.bbox.split(",").map(Number);
      if (bbox.length !== 4 || !bbox.every(Number.isFinite)
          || !validLatLng(bbox[1], bbox[0]) || !validLatLng(bbox[3], bbox[2])
          || bbox[0] >= bbox[2] || bbox[1] >= bbox[3]) {
        throw new HttpError(400, "bad_bbox", "bbox must be west,south,east,north.");
      }
    }
    const potholes = await repository.listPotholes({ since, bbox, limit });
    context.outcome = "map_read";
    return response(200, {
      type: "FeatureCollection",
      total: potholes.length,
      features: potholes.map((item) => {
        const publicItem = publicPothole(item);
        const { lat, lng, ...properties } = publicItem;
        return {
          type: "Feature",
          geometry: { type: "Point", coordinates: [lng, lat] },
          properties,
        };
      }),
    }, context.requestId, "public, max-age=30");
  }

  async function impact(event, context) {
    const params = query(event);
    const to = params.to || today();
    const from = params.from || today(Date.now() - 29 * 86_400_000);
    if (!validDay(from) || !validDay(to) || from > to
        || Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`) > 90 * 86_400_000) {
      throw new HttpError(400, "bad_period", "Use a valid period of at most 90 days.");
    }
    const data = await repository.impact({ from, to });
    const requestsTotal = data.requests.reduce((sum, item) => sum + item.count, 0);
    const captureTotal = data.captures.reduce((sum, item) => sum + item.count, 0);
    context.outcome = "impact_read";
    return response(200, {
      period: { from, to },
      active_installations: data.activeInstallations,
      requests_total: requestsTotal,
      requests: data.requests,
      capture_checks_total: captureTotal,
      video_checks_total: data.captures.filter((item) => item.capture_source !== "manual")
        .reduce((sum, item) => sum + item.count, 0),
      imported_video_checks_total: data.captures
        .filter((item) => item.capture_source === "imported_video")
        .reduce((sum, item) => sum + item.count, 0),
      capture_checks: data.captures,
      potholes: { total: data.summary.new_potholes },
      observations: {
        total: data.summary.observations,
        server_verified_shared: data.summary.server_verified_shared,
        client_attested: data.summary.client_attested,
      },
    }, context.requestId, "public, max-age=60");
  }

  async function dispatch(event, context) {
    const target = route(event);
    context.route = target.path;
    context.method = target.method;
    if (target.method === "OPTIONS") return response(204, {}, context.requestId);
    if (target.method === "GET" && target.path === "/v1/health") {
      context.outcome = "healthy";
      // Readiness reads the secret. "Configured" has to mean a detection would be
      // attempted, not merely that a secret ARN is wired to this function.
      const ready = typeof detector.readiness === "function"
        ? await detector.readiness()
        : detector.status();
      return response(200, {
        ok: true,
        platform: "aws",
        shared_vision_configured: ready.openai_configured || ready.yolo_configured,
        shared_vision_provider: detector.status().mode,
        shared_vision_provider_mode: detector.status().mode,
        shared_vision_primary_provider: "openai",
        shared_vision_primary_configured: ready.openai_configured,
        shared_vision_fallback_provider: "yolo",
        shared_vision_fallback_configured: ready.yolo_configured,
        shared_vision_fallback_model: detector.status().yolo_model,
        detection_prompt_version: DETECT_PROMPT_VERSION,
        detection_schema_version: DETECT_SCHEMA_VERSION,
        shared_detection_receipts_required: true,
      }, context.requestId, "public, max-age=30");
    }
    if (target.method === "GET" && target.path === "/v1/map") return publicMap(event, context);
    if (target.method === "GET" && target.path === "/v1/impact") return impact(event, context);
    if (target.method === "POST" && target.path === "/v1/installations") {
      return installation(event, context);
    }
    if (target.method !== "POST" || ![
      "/v1/activity",
      "/v1/vision/detect",
      "/v1/tenders/resolve",
      "/v1/potholes/report",
      "/v1/feedback",
    ].includes(target.path)) {
      throw new HttpError(404, "not_found", "No such endpoint exists.");
    }
    const parsed = jsonBody(event);
    const authenticated = await authenticate(
      event, context, target.path, target.method, parsed.raw,
    );
    if (authenticated.replay) return authenticated.replay;
    if (target.path === "/v1/activity") return activity(parsed.value, context);
    if (target.path === "/v1/vision/detect") return detect(parsed.value, context);
    if (target.path === "/v1/tenders/resolve") return resolveTender(parsed.value, context);
    if (target.path === "/v1/feedback") return feedback(parsed.value, context);
    return report(parsed.value, context);
  }

  return async function handle(event, awsContext = {}) {
    const startedAt = Date.now();
    const context = {
      requestId: bounded(event.requestContext?.requestId, 128)
        || bounded(awsContext.awsRequestId, 128) || newRequestId(),
      route: route(event).path,
      method: route(event).method,
      outcome: "internal_error",
      visionMode: "none",
      installId: null,
      potholeId: null,
      detectorProvider: null,
      idempotencyClaimed: false,
    };
    let result;
    try {
      result = await dispatch(event, context);
    } catch (error) {
      const known = asHttpError(error);
      context.outcome = known.code;
      context.failed = true;
      if (context.idempotencyClaimed) {
        await repository.releaseIdempotency(context.idempotencyId, context.requestId).catch(() => {});
      }
      result = response(known.status, {
        error: known.code,
        message: known.message,
        ...(known.details ? { details: known.details } : {}),
      }, context.requestId);
      logger.error(JSON.stringify({
        event: "request_error",
        request_id: context.requestId,
        route: context.route,
        error: known.code,
      }));
    }
    await repository.recordRequest({
      route: context.route,
      outcome: context.outcome,
      visionMode: context.visionMode,
      installId: context.installId,
      failed: Boolean(context.failed),
    }).catch((error) => logger.error(JSON.stringify({
      event: "metrics_write_failed",
      request_id: context.requestId,
      error_type: error?.name || "Error",
    })));
    logger.log(JSON.stringify({
      event: "http_request",
      request_id: context.requestId,
      aws_request_id: bounded(awsContext.awsRequestId, 128) || null,
      route: context.route,
      method: context.method,
      status: result.statusCode,
      outcome: context.outcome,
      vision_mode: context.visionMode,
      duration_ms: Date.now() - startedAt,
      pothole_id: context.potholeId,
      detector_provider: context.detectorProvider,
      openai_request_id: context.openaiRequestId || null,
      yolo_request_id: context.yoloRequestId || null,
      detector_fallback_reason: context.detectorFallbackReason || null,
    }));
    return result;
  };
}
