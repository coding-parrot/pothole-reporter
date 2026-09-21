import { generateKeyPairSync, randomUUID, sign } from "node:crypto";
import { readFileSync } from "node:fs";

import { DETECT_PROMPT_VERSION } from "../../../llm/generated/contract.mjs";
import { canonicalRequest, installationPublicKey } from "../service/auth.mjs";
import { createService } from "../service/core.mjs";
import { createSecretProvider } from "../service/detectors.mjs";

// Shared by the detect, label and error suites. Not a test file itself: npm test only
// picks up *.test.mjs.

const photo = readFileSync(new URL("../../../docs/example-pothole.jpg", import.meta.url))
  .toString("base64");

export const detectBody = {
  prompt_version: DETECT_PROMPT_VERSION,
  capture_mode: "manual",
  images: [`data:image/jpeg;base64,${photo}`],
};

export const undamaged = {
  image_quality: "acceptable",
  assessment: "undamaged",
  damage_type: null,
  size: null,
  description: "Smooth asphalt.",
};

export function memoryRepository() {
  const installations = new Map();
  const idempotency = new Map();
  const replays = new Set();
  const calls = { take: 0, refund: 0, requests: [] };
  return {
    calls,
    dedupeRadiusMetres: 30,
    async registerInstallation(value) { installations.set(value.id, value); },
    async getInstallation(id) { return installations.get(id) || null; },
    async touchInstallation() {},
    async claimReplay(key) {
      if (replays.has(key)) return false;
      replays.add(key);
      return true;
    },
    async claimIdempotency({ id, requestHash, owner }) {
      const existing = idempotency.get(id);
      if (existing) return existing;
      idempotency.set(id, { status: "IN_PROGRESS", request_hash: requestHash, owner });
      return { status: "CLAIMED" };
    },
    async completeIdempotency({ id, requestHash, statusCode, payload }) {
      idempotency.set(id, {
        status: "COMPLETED",
        request_hash: requestHash,
        status_code: statusCode,
        response_json: JSON.stringify(payload),
      });
    },
    async releaseIdempotency(id) { idempotency.delete(id); },
    async takeVisionQuota() { calls.take += 1; return { ok: true, limit: 50 }; },
    async refundVisionQuota() { calls.refund += 1; },
    async recordRequest(value) { calls.requests.push(value); },
    async recordCapture() {},
    async putReceipt() {},
  };
}

export function secretFrom(value) {
  return createSecretProvider({
    secretArn: "arn:aws:secretsmanager:test",
    client: {
      async send() {
        if (value instanceof Error) throw value;
        return { SecretString: JSON.stringify(value) };
      },
    },
  });
}

export function upstream(status, body) {
  return async () => new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export async function harness({ repository = memoryRepository(), detector = {},
  geolocator = {} } = {}) {
  const lines = { log: [], error: [] };
  const handle = createService({
    repository,
    detector,
    geolocator,
    logger: {
      log: (line) => lines.log.push(line),
      error: (line) => lines.error.push(line),
    },
  });
  const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const publicDer = publicKey.export({ type: "spki", format: "der" }).toString("base64");
  const installId = installationPublicKey(publicDer).installId;
  await handle({
    rawPath: "/v1/installations",
    requestContext: { http: { method: "POST" } },
    body: JSON.stringify({ public_key: publicDer }),
  });
  if (repository.calls) repository.calls.requests.length = 0;

  const post = (path, value, { key = randomUUID(), sentAt = Date.now(), awsContext } = {}) => {
    const body = JSON.stringify(value);
    const timestamp = String(sentAt);
    const signature = sign("sha256", Buffer.from(canonicalRequest({
      method: "POST", path, timestamp, idempotencyKey: key, body,
    })), { key: privateKey, dsaEncoding: "ieee-p1363" }).toString("base64");
    return handle({
      rawPath: path,
      requestContext: { http: { method: "POST" }, requestId: randomUUID() },
      headers: {
        "X-Install-ID": installId,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
        "Idempotency-Key": key,
      },
      body,
    }, awsContext);
  };
  return { handle, post, repository, lines, installId };
}
