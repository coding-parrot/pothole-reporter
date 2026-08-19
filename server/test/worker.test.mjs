// Runs the worker's real logic against an in-memory KV and a stubbed OpenAI, so the
// signature, quota and gating paths are exercised rather than described.
import assert from "node:assert";
import worker from "../src/index.js";
import { webcrypto } from "node:crypto";
if (!globalThis.crypto) globalThis.crypto = webcrypto;

const kv = new Map();
const DEVICES = {
  get: async (k) => (kv.has(k) ? kv.get(k) : null),
  put: async (k, v) => void kv.set(k, v),
};
let upstream = [];
const env = {
  DEVICES, OPENAI_API_KEY: "sk-test", DAILY_QUOTA: "3", MONTHLY_IMAGE_CAP: "5",
};

// a 1x1 JPEG is enough: the worker never decodes, it only measures and forwards
const JPEG = "data:image/jpeg;base64," + Buffer.from([
  0xff,0xd8,0xff,0xe0,0x00,0x10,0x4a,0x46,0x49,0x46,0x00,0x01,0xff,0xd9]).toString("base64");

globalThis.fetch = async (url, init) => {
  upstream.push(JSON.parse(init.body).model);
  const road = JSON.parse(init.body).model.includes("nano");
  return new Response(JSON.stringify({ output: [{ type: "message", content: [{
    type: "output_text",
    text: road ? JSON.stringify({ is_road_scene: roadAnswer })
               : JSON.stringify({ is_pothole: true, size: "large", confidence: 0.8,
                                  looks_like_speed_breaker: false, description: "seeded" }),
  }] }] }), { status: 200 });
};
let roadAnswer = true;

const call = (path, init) => worker.fetch(new Request("https://x" + path, init), env);

async function makeDevice() {
  const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" },
    true, ["sign", "verify"]);
  const raw = await crypto.subtle.exportKey("raw", pair.publicKey);
  const publicKey = Buffer.from(raw).toString("base64");
  const res = await call("/v1/register", { method: "POST", body: JSON.stringify({ publicKey }) });
  const { device_id } = await res.json();
  return { pair, device_id };
}

async function sign(dev, image, tsOverride) {
  const bytes = Buffer.from(image.slice(image.indexOf(",") + 1), "base64");
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const imageHash = Buffer.from(digest).toString("hex");
  const timestamp = String(tsOverride ?? Date.now());
  const sig = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, dev.pair.privateKey,
    new TextEncoder().encode(`${dev.device_id}.${timestamp}.${imageHash}`));
  return { "x-device-id": dev.device_id, "x-timestamp": timestamp,
           "x-signature": Buffer.from(sig).toString("base64"),
           "content-type": "application/json" };
}

const detect = (headers, image = JPEG) =>
  call("/v1/detect", { method: "POST", headers, body: JSON.stringify({ image }) });

let pass = 0;
const ok = (name) => { console.log("  ok  " + name); pass++; };

// --- identity -------------------------------------------------------------
const dev = await makeDevice();
assert.match(dev.device_id, /^[0-9a-f]{32}$/); ok("register derives a device id from the key");

let r = await detect({ "content-type": "application/json" });
assert.equal(r.status, 401);
assert.equal((await r.json()).error, "unsigned_request"); ok("unsigned request refused");

const h = await sign(dev, JPEG);
r = await detect({ ...h, "x-signature": Buffer.from("nope").toString("base64") });
assert.equal(r.status, 401); ok("forged signature refused");

r = await detect(await sign(dev, JPEG, Date.now() - 10 * 60_000));
assert.equal((await r.json()).error, "stale_request"); ok("stale request refused");

// --- happy path and replay ------------------------------------------------
const good = await sign(dev, JPEG);
r = await detect(good);
let body = await r.json();
assert.equal(r.status, 200);
assert.equal(body.is_pothole, true);
assert.deepEqual(upstream, ["gpt-5-nano", "gpt-5-mini"]); ok("cheap road gate runs before detection");

r = await detect(good);                       // exact same signature again
assert.equal((await r.json()).error, "replayed_request"); ok("replayed signature refused");

// --- image gating ---------------------------------------------------------
const big = "data:image/jpeg;base64," + "A".repeat(5_000_000);
r = await detect(await sign(dev, big), big);
assert.equal(r.status, 413); ok("oversized image refused before any model call");

roadAnswer = false;
upstream = [];
r = await detect(await sign(dev, JPEG));
assert.equal(r.status, 422);
assert.equal((await r.json()).error, "not_a_road");
assert.deepEqual(upstream, ["gpt-5-nano"]); ok("non-road image refused after the cheap call only");
roadAnswer = true;

// --- quotas ---------------------------------------------------------------
// DAILY_QUOTA is 3 and unattested devices get a tenth, floored at 10.
const spender = await makeDevice();
let lastErr = null;
for (let i = 0; i < 12; i++) {
  const res = await detect(await sign(spender, JPEG));
  if (res.status !== 200) { lastErr = (await res.json()).error; break; }
}
assert.ok(["daily_limit_reached", "service_budget_reached"].includes(lastErr),
  `expected a limit, got ${lastErr}`); ok("a device is cut off when its allowance runs out");

console.log(`\n${pass} checks passed`);
