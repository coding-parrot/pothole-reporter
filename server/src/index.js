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
// Byte-identical to the app's DETECT_PROMPT in static/standalone.js. A user on the
// hosted service and a user with their own key must get the same verdict on the same
// photo; two prompts means two accuracies in one product.
const DETECT_PROMPT = `You are inspecting a road photo taken in Bengaluru for a civic complaint app.

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
// ---------- contracts ----------
// Moved off the phone. The app used to bundle 9.5 MB of contracts and run this match
// itself; here the data can be refreshed without an app release, and the shortlist is a
// SQL lookup on the body that owns the road rather than a scan.

const TENDER_STOP = new Set(["road", "roads", "street", "cross", "main", "layout", "bengaluru", "bangalore",
    "karnataka", "india", "ward", "city", "corporation", "south", "north", "east",
    "west", "central", "urban", "sector", "stage", "block", "phase"]);

// Only the officer receiving the letter can enforce their own body's works. A
// Commissioner has no standing over a state PWD, panchayat or irrigation contract, so
// those are not candidates at all. Bengaluru's five corporations inherited BBMP's works,
// which the award records still file under BBMP zones, so they share that legacy pool.
const BLR_BODIES = new Set(["305850", "305851", "305852", "305853", "305854"]);

const TENDER_SCHEMA = {
  type: "object", additionalProperties: false,
  required: ["match_index", "confidence"],
  properties: {
    match_index: { type: ["integer", "null"] },
    confidence: { type: "number" },
  },
};

async function handleTender(request, env) {
  const url = new URL(request.url);
  const address = (url.searchParams.get("address") || "").trim();
  const lgd = (url.searchParams.get("lgd") || "").trim();
  if (!address || !lgd) return json({ tender: null, reason: "need address and lgd" });

  const codes = BLR_BODIES.has(lgd) ? [lgd, "BLR"] : [lgd];
  const { results: pool } = await env.DB.prepare(
    `SELECT tn, title, loc, published, contractor FROM tenders
      WHERE body_lgd IN (${codes.map((_, i) => "?" + (i + 1)).join(",")})`
  ).bind(...codes).all();
  if (!pool || !pool.length) return json({ tender: null, reason: "no contracts for this body" });

  const tokens = new Set();
  for (const part of address.split(",").slice(0, 4)) {
    for (const w of part.trim().toLowerCase().replace(/[()]/g, " ").split(/\s+/)) {
      if (w.length > 2 && !TENDER_STOP.has(w)) tokens.add(w);
    }
  }
  if (!tokens.size) return json({ tender: null, reason: "no usable words in the address" });

  // Scored on the work description alone: loc is the body's own name, identical in every
  // one of its rows, so it cannot distinguish one of its roads from another.
  const hays = pool.map((t) => (t.title || "").toLowerCase());

  // The body's own name is not evidence about which of its roads this is, and it appears
  // in some titles too, so counting alone will not remove it.
  const bodyWords = new Set();
  for (const w of (pool[0].loc || "").toLowerCase().split(/[^a-z]+/)) {
    if (w.length > 2) bodyWords.add(w);
  }
  for (const w of bodyWords) tokens.delete(w);
  if (!tokens.size) return json({ tender: null, reason: "only the town's own name matched" });
  // Weight a word by how rare it is inside this body's own contracts. A word in none of
  // them is no evidence; a word in most of them (the town's own name) does not tell one
  // road from another. Only the words in between say which stretch this is.
  const idf = new Map();
  for (const tok of tokens) {
    let df = 0;
    for (const hay of hays) if (hay.includes(tok)) df++;
    // The "more than half" cut only means something once there are enough contracts to
    // count: in a town with three, every matching word exceeds half and the town could
    // never match anything. A word in every contract still carries no information.
    if (df === 0) continue;
    if (pool.length > 1 && df === pool.length) continue;
    if (pool.length >= 8 && df > pool.length * 0.5) continue;
    idf.set(tok, Math.log((pool.length + 1) / (df + 0.5)));
  }
  if (!idf.size) return json({ tender: null, reason: "nothing in the address narrows this town" });

  const scored = [];
  for (let i = 0; i < pool.length; i++) {
    let score = 0;
    for (const [tok, w] of idf) if (hays[i].includes(tok)) score += w;
    if (score > 0) scored.push([score, pool[i]]);
  }
  scored.sort((a, b) => b[0] - a[0]);
  const candidates = scored.slice(0, 25).map((x) => x[1]);
  if (!candidates.length) return json({ tender: null, reason: "no candidate contracts" });

  const listing = candidates.map((t, i) =>
    `${i}: ${(t.title || "").slice(0, 150)} | ${t.loc} | contractor: ${t.contractor || "not named"} | published: ${t.published}`
  ).join("\n");
  const prompt = `You match a pothole's location to road-work contracts awarded by the
local body that owns this road. Every candidate below was awarded by that same body, so
the town is already correct and your only job is whether the work covers this stretch.
The pothole's reverse-geocoded address is:
${address}

Candidate contracts (index: work description | division | contractor | published):
${listing}

Pick the single contract whose work description covers this exact road stretch or
its immediate locality (same layout, ward or named road). Road names repeat across
localities within a town, so the locality or ward context must agree, not just the
road name. A ward-wide maintenance or pothole-filling contract for the pothole's own
locality or ward is a valid match. If no candidate clearly covers this location,
match_index must be null. confidence is your 0 to 1 confidence in the match.`;

  let m;
  try {
    // Picking one contract out of 25 near-identical descriptions, in a letter that names
    // a real company, is worth the reasoning budget a single photo verdict is not.
    m = await openai(env, {
      model: DETECT_MODEL, input: prompt,
      reasoning: { effort: "medium" },
      text: fmt("tender_match", TENDER_SCHEMA),
    });
  } catch (e) { return json({ tender: null, reason: "match unavailable" }); }

  if (!m || m.match_index === null || m.match_index < 0
      || m.match_index >= candidates.length || m.confidence < 0.6) {
    return json({ tender: null, reason: "no confident match" });
  }
  const t = candidates[m.match_index];

  // Award records carry no defect liability period, so this is inferred from how recent
  // the tender is and must stay worded as a possibility.
  let warranty = "recorded for this stretch", warranty_code = "record";
  const dm = /^(\d{2})-(\d{2})-(\d{4})/.exec(t.published || "");
  if (dm) {
    const age = (Date.now() - new Date(`${dm[3]}-${dm[2]}-${dm[1]}`).getTime()) / (365.25 * 24 * 3600 * 1000);
    if (age <= 1) { warranty = "within the defect liability period"; warranty_code = "dlp"; }
    else if (age <= 3) { warranty = "within the maintenance period"; warranty_code = "maint"; }
  }
  return json({
    tender: {
      tender_number: t.tn, contractor: t.contractor || null, title: t.title,
      published: t.published, warranty, warranty_code, confidence: m.confidence,
    },
  });
}

// ---------- reports, dedup and the city view ----------
// A pothole is a place, not a person. These endpoints hold road defects and the
// pseudonymous install that saw one. device_id is written but never read back out.

const EARTH_R = 6371000;
function metresBetween(lat1, lng1, lat2, lng2) {
  const rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad, dLng = (lng2 - lng1) * rad;
  const a = Math.sin(dLat / 2) ** 2
    + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_R * Math.asin(Math.sqrt(a));
}

// A degree of longitude shrinks with latitude, so the box has to be widened by
// 1/cos(lat) or the search is narrower east-west than it is north-south.
function boundingBox(lat, lng, metres) {
  const dLat = (metres / EARTH_R) * (180 / Math.PI);
  const dLng = dLat / Math.max(Math.cos(lat * Math.PI / 180), 1e-6);
  return [lat - dLat, lat + dLat, lng - dLng, lng + dLng];
}

const num = (v, fallback) => (Number.isFinite(parseFloat(v)) ? parseFloat(v) : fallback);
const validLatLng = (lat, lng) =>
  Number.isFinite(lat) && Number.isFinite(lng)
  && lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180;

// The nearest existing report within the dedupe radius and window, or null.
async function findExisting(env, lat, lng) {
  const metres = num(env.DEDUPE_METRES, 20);
  const days = num(env.DEDUPE_DAYS, 120);
  const since = Date.now() - days * 86400000;
  const [minLat, maxLat, minLng, maxLng] = boundingBox(lat, lng, metres);
  // The index makes this a range scan; the exact circle is applied in JS afterwards,
  // because a box is wider than a circle at the corners.
  const { results } = await env.DB.prepare(
    `SELECT id, lat, lng, size, confidence, created_at, seen_count
       FROM reports
      WHERE lat BETWEEN ?1 AND ?2 AND lng BETWEEN ?3 AND ?4 AND created_at >= ?5
      LIMIT 50`
  ).bind(minLat, maxLat, minLng, maxLng, since).all();
  let best = null, bestD = Infinity;
  for (const r of results || []) {
    const d = metresBetween(lat, lng, r.lat, r.lng);
    if (d <= metres && d < bestD) { best = r; bestD = d; }
  }
  return best ? { ...best, distance_m: Math.round(bestD) } : null;
}

async function handleReport(request, env) {
  const body = await request.json().catch(() => null);
  if (!body) return refuse("bad_request", "Send a JSON body.");
  const lat = num(body.lat, NaN), lng = num(body.lng, NaN);
  if (!validLatLng(lat, lng)) return refuse("bad_request", "A report needs valid coordinates.");
  if (!body.image_hash) return refuse("bad_request", "A report needs the image hash.");

  const deviceId = request.headers.get("x-device-id");
  if (!deviceId) return refuse("no_device", "This install is not registered.", 401);

  const existing = await findExisting(env, lat, lng);
  if (existing) {
    // Someone already reported this one. Record that a second install saw it, which is
    // what makes seen_count mean "how many people", and tell the app not to file again.
    const conf = await env.DB.prepare(
      `INSERT OR IGNORE INTO confirmations (report_id, device_id, created_at) VALUES (?1, ?2, ?3)`
    ).bind(existing.id, deviceId, Date.now()).run();
    if (conf.meta && conf.meta.changes) {
      await env.DB.prepare(`UPDATE reports SET seen_count = seen_count + 1 WHERE id = ?1`)
        .bind(existing.id).run();
      existing.seen_count += 1;
    }
    return json({
      duplicate: true,
      report: { id: existing.id, lat: existing.lat, lng: existing.lng, size: existing.size,
                first_reported: existing.created_at, seen_count: existing.seen_count,
                distance_m: existing.distance_m },
    });
  }

  const now = Date.now();
  const ins = await env.DB.prepare(
    `INSERT OR IGNORE INTO reports (lat, lng, size, confidence, image_hash, device_id, lgd, town, created_at)
     VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)`
  ).bind(lat, lng, body.size || null, num(body.confidence, null), String(body.image_hash),
         deviceId, body.lgd ? String(body.lgd) : null, body.town || null, now).run();

  if (!ins.meta || !ins.meta.changes) {
    // Same install, same frame: a retry, not a second pothole.
    const row = await env.DB.prepare(
      `SELECT id, seen_count FROM reports WHERE device_id = ?1 AND image_hash = ?2`
    ).bind(deviceId, String(body.image_hash)).first();
    return json({ duplicate: true, resubmitted: true, report: row || null });
  }
  // The install that first saw it counts as having seen it. Without this row, that same
  // install reporting the same pothole again would raise seen_count to two, and
  // seen_count is meant to mean "how many separate people".
  await env.DB.prepare(
    `INSERT OR IGNORE INTO confirmations (report_id, device_id, created_at) VALUES (?1, ?2, ?3)`
  ).bind(ins.meta.last_row_id, deviceId, now).run();
  return json({ duplicate: false, report: { id: ins.meta.last_row_id, seen_count: 1, first_reported: now } });
}

// Everything reported in the body the citizen is standing in. No device_id, ever.
async function handleCity(request, env) {
  const url = new URL(request.url);
  const lgd = url.searchParams.get("lgd");
  const lat = num(url.searchParams.get("lat"), NaN);
  const lng = num(url.searchParams.get("lng"), NaN);
  const days = Math.min(num(url.searchParams.get("days"), 180), 365);
  const since = Date.now() - days * 86400000;
  const limit = Math.min(num(url.searchParams.get("limit"), 500), 2000);

  let rows;
  if (lgd) {
    rows = await env.DB.prepare(
      `SELECT id, lat, lng, size, created_at, seen_count FROM reports
        WHERE lgd = ?1 AND created_at >= ?2 ORDER BY created_at DESC LIMIT ?3`
    ).bind(String(lgd), since, limit).all();
  } else if (validLatLng(lat, lng)) {
    // No body code (rural, or the GIS was unreachable): fall back to a radius.
    const radius = Math.min(num(url.searchParams.get("radius"), 5000), 25000);
    const [a, b, c, d] = boundingBox(lat, lng, radius);
    rows = await env.DB.prepare(
      `SELECT id, lat, lng, size, created_at, seen_count FROM reports
        WHERE lat BETWEEN ?1 AND ?2 AND lng BETWEEN ?3 AND ?4 AND created_at >= ?5
        ORDER BY created_at DESC LIMIT ?6`
    ).bind(a, b, c, d, since, limit).all();
  } else {
    return refuse("bad_request", "Pass lgd, or lat and lng.");
  }

  const results = rows.results || [];
  const counts = { small: 0, medium: 0, large: 0 };
  for (const r of results) if (counts[r.size] !== undefined) counts[r.size]++;
  return json({
    total: results.length,
    by_size: counts,
    reported_by_more_than_one: results.filter((r) => r.seen_count > 1).length,
    potholes: results,
  });
}

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
        "access-control-allow-methods": "GET,POST,OPTIONS",
      } });
    }
    let res;
    if (url.pathname === "/v1/health") {
      res = json({ ok: true, integrity: !!env.PLAY_INTEGRITY_PROJECT });
    } else if (url.pathname === "/v1/register" && request.method === "POST") {
      res = await handleRegister(request, env);
    } else if (url.pathname === "/v1/report" && request.method === "POST") {
      res = await handleReport(request, env);
    } else if (url.pathname === "/v1/city" && request.method === "GET") {
      res = await handleCity(request, env);
    } else if (url.pathname === "/v1/tender" && request.method === "GET") {
      res = await handleTender(request, env);
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
