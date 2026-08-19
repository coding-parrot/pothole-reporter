// Pothole Reporter detection service.
//
// Exists so a citizen can report a pothole without owning an OpenAI account. That
// means this endpoint spends the operator's money on behalf of anonymous callers,
// so most of this file is about refusing to do that for the wrong people.
//
// Four gates, cheapest first, because the point is to reject before spending:
//   1. shape      - size, dimensions, content type
//   2. identity   - per-device signature, replay window
//   3. integrity  - Play Integrity verdict (when configured)
//   4. budget     - per-device daily quota and a global monthly ceiling
// Only then does it look at the image, and even then a cheap road check runs before
// the expensive detection.

const MAX_IMAGE_BYTES = 3_500_000;      // a 2000px JPEG is ~850 KB; this is generous
const MAX_SIGNATURE_AGE_MS = 5 * 60_000;
const DEFAULT_DAILY_QUOTA = 200;        // images per device per day
const SCREEN_MODEL = "gpt-5-nano";
const DETECT_MODEL = "gpt-5-mini";

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });

// Every refusal says why in a form the app can show a human, and never leaks
// whether a device id exists, what the global spend is, or anything about the key.
const refuse = (code, message, status = 400) => json({ error: code, message }, status);

const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
const sha256 = async (bytes) => hex(await crypto.subtle.digest("SHA-256", bytes));

function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// ---------------------------------------------------------------- identity
// Each install generates an ECDSA P-256 keypair and registers the public half. The
// signature does not prove the caller is honest, only that it is the same caller as
// before, which is what makes a per-device quota mean anything.
async function verifySignature(env, deviceId, timestamp, imageHash, signatureB64) {
  const stored = await env.DEVICES.get(`device:${deviceId}`);
  if (!stored) return "unknown_device";

  const age = Math.abs(Date.now() - Number(timestamp));
  if (!Number.isFinite(age) || age > MAX_SIGNATURE_AGE_MS) return "stale_request";

  // A signature replayed inside the window would otherwise be a free extra image.
  const seen = await env.DEVICES.get(`sig:${signatureB64.slice(0, 43)}`);
  if (seen) return "replayed_request";

  const key = await crypto.subtle.importKey(
    "raw", b64ToBytes(JSON.parse(stored).publicKey),
    { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"],
  );
  const payload = new TextEncoder().encode(`${deviceId}.${timestamp}.${imageHash}`);
  const ok = await crypto.subtle.verify(
    { name: "ECDSA", hash: "SHA-256" }, key, b64ToBytes(signatureB64), payload,
  );
  if (!ok) return "bad_signature";

  await env.DEVICES.put(`sig:${signatureB64.slice(0, 43)}`, "1", { expirationTtl: 600 });
  return null;
}

// ---------------------------------------------------------------- integrity
// Play Integrity is what actually separates "your app on a real phone" from "a script
// with a keypair". It is optional here on purpose: it only returns a useful app
// verdict once the app ships through Play, and the service has to work before that.
// When unconfigured, the daily quota drops so an unattested caller cannot cost much.
async function checkIntegrity(env, token) {
  if (!env.PLAY_INTEGRITY_PROJECT || !env.PLAY_INTEGRITY_TOKEN) {
    return { enforced: false, verdict: "not_configured" };
  }
  if (!token) return { enforced: true, verdict: "missing_token", ok: false };
  try {
    const res = await fetch(
      `https://playintegrity.googleapis.com/v1/${env.PLAY_INTEGRITY_PROJECT}:decodeIntegrityToken`,
      { method: "POST",
        headers: { "content-type": "application/json",
                   authorization: `Bearer ${env.PLAY_INTEGRITY_TOKEN}` },
        body: JSON.stringify({ integrityToken: token }) },
    );
    if (!res.ok) return { enforced: true, verdict: "verify_failed", ok: false };
    const body = await res.json();
    const p = body?.tokenPayloadExternal ?? {};
    const device = p.deviceIntegrity?.deviceRecognitionVerdict ?? [];
    const app = p.appIntegrity?.appRecognitionVerdict;
    return {
      enforced: true,
      verdict: `${app}/${device.join("+") || "none"}`,
      ok: device.includes("MEETS_DEVICE_INTEGRITY") && app === "PLAY_RECOGNIZED",
    };
  } catch (e) {
    return { enforced: true, verdict: "verify_error", ok: false };
  }
}

// ---------------------------------------------------------------- budget
const dayKey = () => new Date().toISOString().slice(0, 10);
const monthKey = () => new Date().toISOString().slice(0, 7);

async function takeQuota(env, deviceId, attested) {
  // An unattested caller gets a fraction of the allowance: enough to try the app,
  // not enough to be worth farming.
  const limit = attested
    ? Number(env.DAILY_QUOTA || DEFAULT_DAILY_QUOTA)
    : Math.max(10, Math.floor(Number(env.DAILY_QUOTA || DEFAULT_DAILY_QUOTA) / 10));

  const key = `quota:${deviceId}:${dayKey()}`;
  const used = Number((await env.DEVICES.get(key)) || 0);
  if (used >= limit) return { ok: false, used, limit };
  await env.DEVICES.put(key, String(used + 1), { expirationTtl: 172800 });
  return { ok: true, used: used + 1, limit };
}

// The ceiling that means a viral week cannot produce a surprise invoice. Counted in
// requests rather than dollars because the worker cannot see the bill; keep it in
// step with the model prices in the README.
async function takeGlobalBudget(env) {
  const cap = Number(env.MONTHLY_IMAGE_CAP || 0);
  if (!cap) return { ok: true };
  const key = `spend:${monthKey()}`;
  const used = Number((await env.DEVICES.get(key)) || 0);
  if (used >= cap) return { ok: false, used, cap };
  await env.DEVICES.put(key, String(used + 1), { expirationTtl: 3456000 });
  return { ok: true, used: used + 1, cap };
}

// ---------------------------------------------------------------- OpenAI
async function openai(env, body) {
  const res = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: { "content-type": "application/json",
               authorization: `Bearer ${env.OPENAI_API_KEY}` },
    body: JSON.stringify({ reasoning: { effort: "minimal" }, ...body }),
  });
  if (!res.ok) throw new Error(`upstream_${res.status}`);
  const data = await res.json();
  const msg = (data.output || []).find((o) => o.type === "message");
  const text = msg?.content?.find((c) => c.type === "output_text");
  if (!text?.text) throw new Error("upstream_empty");
  return JSON.parse(text.text);
}

const fmt = (name, schema) => ({
  format: { type: "json_schema", name, schema, strict: true },
  verbosity: "low",
});

// Stops the endpoint becoming a free general-purpose vision API. A caller who sends
// holiday photos gets refused here, having spent a nano call rather than a mini one.
const ROAD_SCHEMA = {
  type: "object", additionalProperties: false, required: ["is_road_scene"],
  properties: { is_road_scene: { type: "boolean" } },
};
const ROAD_PROMPT =
  "Does this photograph show a road, street or paved surface, of the kind someone " +
  "would photograph to report road damage? Answer true for any outdoor road or " +
  "street scene including dashcam views. Answer false for photographs of people, " +
  "documents, screens, indoor scenes, or anything unrelated to a road.";

const ASSESS_SCHEMA = {
  type: "object", additionalProperties: false,
  required: ["is_pothole", "size", "confidence", "looks_like_speed_breaker", "description"],
  properties: {
    is_pothole: { type: "boolean" },
    size: { type: ["string", "null"], enum: ["small", "medium", "large", null] },
    confidence: { type: "number" },
    looks_like_speed_breaker: { type: "boolean" },
    description: { type: "string" },
  },
};
const DETECT_PROMPT = `You are inspecting a road photo for a civic complaint app.

Decide whether the photo clearly shows a pothole on a road surface.
- Classify size like pizzas: small (below 30 cm wide), medium (30 to 60 cm), large (above 60 cm or a cluster).
- Beware of speed breakers: from a distance they can look like potholes. Set looks_like_speed_breaker accordingly, and if it is actually a speed breaker, is_pothole must be false.
- Shadows, manhole covers, wet patches, and road repair scars are NOT potholes.
- confidence is your 0 to 1 confidence in the is_pothole verdict. Be conservative: this triggers a government complaint.
- description: one or two factual sentences usable in a complaint (surface condition, position on the road, hazard posed).
- Some images are dashcam frames from a moving vehicle: moderate motion blur, low light, or a boosted-brightness look are normal; judge the road surface itself.`;

const vision = (model, dataUrl, prompt, name, schema) => ({
  model,
  input: [{ role: "user", content: [
    { type: "input_image", image_url: dataUrl },
    { type: "input_text", text: prompt },
  ] }],
  text: fmt(name, schema),
});

// ---------------------------------------------------------------- routes
async function handleRegister(request, env) {
  const { publicKey } = await request.json().catch(() => ({}));
  if (typeof publicKey !== "string" || publicKey.length < 80 || publicKey.length > 200) {
    return refuse("bad_public_key", "That public key is not usable.");
  }
  // The device id is derived from the key, so a device cannot claim someone else's
  // id and cannot mint several ids from one key.
  const deviceId = (await sha256(b64ToBytes(publicKey))).slice(0, 32);
  await env.DEVICES.put(`device:${deviceId}`,
    JSON.stringify({ publicKey, registered_at: Date.now() }));
  return json({ device_id: deviceId });
}

async function handleDetect(request, env) {
  const deviceId = request.headers.get("x-device-id") || "";
  const timestamp = request.headers.get("x-timestamp") || "";
  const signature = request.headers.get("x-signature") || "";
  const integrityToken = request.headers.get("x-integrity-token") || "";
  if (!deviceId || !timestamp || !signature) {
    return refuse("unsigned_request", "This request was not signed.", 401);
  }

  const body = await request.json().catch(() => null);
  const image = body?.image;
  if (typeof image !== "string" || !image.startsWith("data:image/")) {
    return refuse("bad_image", "Send a JPEG or PNG data URL.");
  }
  const b64 = image.slice(image.indexOf(",") + 1);
  const bytes = b64ToBytes(b64);
  if (bytes.length > MAX_IMAGE_BYTES) {
    return refuse("image_too_large", "That image is too large. Send at most 3.5 MB.", 413);
  }

  const sigProblem = await verifySignature(env, deviceId, timestamp, await sha256(bytes), signature);
  if (sigProblem) {
    return refuse(sigProblem, "This request could not be verified. Reopen the app.", 401);
  }

  const integrity = await checkIntegrity(env, integrityToken);
  if (integrity.enforced && !integrity.ok) {
    return refuse("failed_integrity",
      "This copy of the app could not be verified. Install it from the official release.", 403);
  }

  const budget = await takeGlobalBudget(env);
  if (!budget.ok) {
    return refuse("service_budget_reached",
      "The free service has reached its limit for this month. Add your own OpenAI key in Settings to continue.", 503);
  }
  const quota = await takeQuota(env, deviceId, integrity.enforced && integrity.ok);
  if (!quota.ok) {
    return refuse("daily_limit_reached",
      `You have used today's ${quota.limit} free checks. They reset tomorrow.`, 429);
  }

  try {
    const road = await openai(env, vision(SCREEN_MODEL, image, ROAD_PROMPT, "road_check", ROAD_SCHEMA));
    if (!road.is_road_scene) {
      return refuse("not_a_road", "That photo does not look like a road, so it was not checked.", 422);
    }
    const lang = body?.lang === "kn" ? "kn" : "en";
    const prompt = DETECT_PROMPT + (lang === "kn"
      ? "\n- Write the description field in formal Kannada (ಕನ್ನಡ ಭಾಷೆಯಲ್ಲಿ ಬರೆಯಿರಿ)."
      : "");
    const verdict = await openai(env, vision(DETECT_MODEL, image, prompt, "assessment", ASSESS_SCHEMA));
    return json({ ...verdict, quota: { used: quota.used, limit: quota.limit } });
  } catch (e) {
    const upstream = String(e.message || "");
    if (upstream.startsWith("upstream_4")) {
      return refuse("rejected_upstream", "The image could not be analysed. Try another photo.", 422);
    }
    return refuse("upstream_unavailable", "The detection service is busy. Try again in a moment.", 503);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: {
        "access-control-allow-origin": "*",
        "access-control-allow-headers": "content-type,x-device-id,x-timestamp,x-signature,x-integrity-token",
        "access-control-allow-methods": "POST,OPTIONS",
      } });
    }
    let res;
    if (url.pathname === "/v1/health") {
      res = json({ ok: true, integrity: !!env.PLAY_INTEGRITY_PROJECT });
    } else if (url.pathname === "/v1/register" && request.method === "POST") {
      res = await handleRegister(request, env);
    } else if (url.pathname === "/v1/detect" && request.method === "POST") {
      res = await handleDetect(request, env);
    } else {
      res = refuse("not_found", "No such endpoint.", 404);
    }
    const out = new Response(res.body, res);
    out.headers.set("access-control-allow-origin", "*");
    return out;
  },
};
