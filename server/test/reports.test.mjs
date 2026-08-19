// Dedup, the city view and the contract lookup, against real SQLite and the real schema.
import assert from "node:assert";
import worker from "../src/index.js";
import { webcrypto } from "node:crypto";
import { makeD1 } from "./d1.mjs";
if (!globalThis.crypto) globalThis.crypto = webcrypto;

const kv = new Map();
const DEVICES = { get: async (k) => kv.get(k) ?? null, put: async (k, v) => void kv.set(k, v) };
let D1 = makeD1();
const env = { DEVICES, DB: D1, OPENAI_API_KEY: "sk-test",
              DEDUPE_METRES: "20", DEDUPE_DAYS: "120" };

let tenderPick = { match_index: 0, confidence: 0.9 };
globalThis.fetch = async (url, init) => new Response(JSON.stringify({
  output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify(tenderPick) }] }],
}), { status: 200 });

const call = (path, opts = {}) =>
  worker.fetch(new Request("https://x" + path, opts), env, { waitUntil() {} });

const report = (device, lat, lng, hash, extra = {}) => call("/v1/report", {
  method: "POST", headers: { "content-type": "application/json", "x-device-id": device },
  body: JSON.stringify({ lat, lng, image_hash: hash, size: "large", confidence: 0.8, ...extra }),
});

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("a first report is stored, not a duplicate", async () => {
  const r = await (await report("dev-a", 12.9115, 77.6427, "h1", { lgd: "305852", town: "BSCC" })).json();
  assert.equal(r.duplicate, false);
  assert.equal(r.report.seen_count, 1);
});

test("a second person at the same spot is a duplicate, and raises seen_count", async () => {
  const r = await (await report("dev-b", 12.91151, 77.64271, "h2", { lgd: "305852" })).json();
  assert.equal(r.duplicate, true, "should have matched the existing pothole");
  assert.equal(r.report.seen_count, 2, "two separate installs saw it");
  assert.ok(r.report.distance_m <= 20);
});

test("the same person reporting twice does not inflate the count", async () => {
  const r = await (await report("dev-b", 12.91152, 77.64272, "h3", { lgd: "305852" })).json();
  assert.equal(r.duplicate, true);
  assert.equal(r.report.seen_count, 2, "same device must not count twice");
});

test("a pothole further than the radius is its own report", async () => {
  // ~60 m north, well outside the 20 m radius
  const r = await (await report("dev-a", 12.91204, 77.6427, "h4", { lgd: "305852" })).json();
  assert.equal(r.duplicate, false, "60 m away is a different pothole");
});

test("the radius is not narrower east-west than north-south", async () => {
  // 15 m east at this latitude. A box that forgets cos(lat) would miss this.
  const dLng = 15 / (6371000 * Math.cos(12.9115 * Math.PI / 180)) * (180 / Math.PI);
  const r = await (await report("dev-c", 12.9115, 77.6427 + dLng, "h5", { lgd: "305852" })).json();
  assert.equal(r.duplicate, true, "15 m east must dedupe");
});

test("a resubmitted frame from the same device is not a new pothole", async () => {
  const before = (await (await call("/v1/city?lat=12.95&lng=77.70&radius=100")).json()).total;
  const r = await (await report("dev-a", 12.95, 77.70, "same-frame", { lgd: "305852" })).json();
  assert.equal(r.duplicate, false);
  // Sent again: location dedup catches it first, which is the right answer. What matters
  // is that it creates no second row and does not read as a second person.
  const again = await (await report("dev-a", 12.95, 77.70, "same-frame", { lgd: "305852" })).json();
  assert.equal(again.duplicate, true);
  assert.equal(again.report.seen_count, 1, "one install must not count as two people");
  const after = (await (await call("/v1/city?lat=12.95&lng=77.70&radius=100")).json()).total;
  assert.equal(after, before + 1, "a resubmission created a second row");
});

test("a report without coordinates is refused", async () => {
  const res = await report("dev-a", NaN, NaN, "h6");
  assert.equal(res.status, 400);
});

test("an unregistered caller cannot report", async () => {
  const res = await call("/v1/report", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ lat: 12.9, lng: 77.6, image_hash: "h7" }),
  });
  assert.equal(res.status, 401);
});

test("the city view returns the body's potholes and never a device id", async () => {
  const r = await (await call("/v1/city?lgd=305852")).json();
  assert.ok(r.total >= 3, `expected several, got ${r.total}`);
  assert.equal(r.by_size.large, r.total);
  assert.ok(r.reported_by_more_than_one >= 1);
  const blob = JSON.stringify(r);
  assert.ok(!blob.includes("dev-a") && !blob.includes("dev-b"),
            "a device id leaked into a public read");
});

test("the city view can fall back to a radius when there is no body code", async () => {
  const r = await (await call("/v1/city?lat=12.9115&lng=77.6427&radius=200")).json();
  assert.ok(r.total >= 1);
});

test("a contract is only matched from the body that owns the road", async () => {
  D1.db.exec(`INSERT INTO tenders (tn,title,loc,published,contractor,body_lgd) VALUES
    ('BBMP/1','Pothole filling in HSR Layout ward 221','BBMP South','13-09-2024','ACME','305852'),
    ('DMA/9','Pothole filling in Vidyanagar ward 48','DMA Hubballi','01-02-2025','OTHER','251893')`);
  const r = await (await call("/v1/tender?lgd=305852&address=" +
    encodeURIComponent("17th Main Road, HSR Layout, Bengaluru, 560102"))).json();
  assert.ok(r.tender, "expected a match inside the body");
  assert.equal(r.tender.tender_number, "BBMP/1", "matched a contract from another town");
});

test("a town whose contracts share no distinguishing word names nothing", async () => {
  const r = await (await call("/v1/tender?lgd=251893&address=" +
    encodeURIComponent("Krishnamurtipuram, Hubballi, 580020"))).json();
  assert.equal(r.tender, null, "named a contract on no evidence");
});

test("a body with no contracts names nothing", async () => {
  const r = await (await call("/v1/tender?lgd=999999&address=" +
    encodeURIComponent("Some Road, Somewhere"))).json();
  assert.equal(r.tender, null);
});

let failed = 0;
for (const [name, fn] of tests) {
  try { await fn(); console.log("  ok  " + name); }
  catch (e) { failed++; console.log("  FAIL " + name + "\n        " + e.message); }
}
console.log(failed ? `\n${failed} failed` : `\n${tests.length} checks passed`);
process.exit(failed ? 1 : 0);
