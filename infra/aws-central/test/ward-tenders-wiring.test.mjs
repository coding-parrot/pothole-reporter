import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createCachedGeolocator } from "../service/geo-cache.mjs";
import { createGeolocator, wardAt } from "../service/geolocation.mjs";
import { loadBodyTenders } from "../tools/ward-tender-vocabulary.mjs";
import { harness, memoryRepository } from "./support.mjs";
import { CASES } from "./ward-tender-cases.mjs";

// Ward tenders were written beside a week-long location cache (geo-cache.mjs) that
// landed on main the same day. The two merged without a conflict in any line they share
// and were wrong together in two ways. These tests build the geolocator the way
// handler.mjs does, so the service is tested as it is wired in production.

const BENGALURU = loadBodyTenders("BLR").rows;
const WARDS = JSON.parse(readFileSync(new URL("../../../data/karnataka-ward-geometry.json", import.meta.url), "utf8"));
const quiet = { error() {}, log() {} };

function cellStore() {
  const cells = new Map();
  return { cells,
    async getGeoCell(cell) { return cells.get(cell) || null; },
    async putGeoCell(cell, value, expiresAt) { cells.set(cell, { value, expiresAt }); } };
}

// KGIS and the geocoder both answering live, which is the only kind of answer the cache keeps.
function liveGeolocator(points) {
  const byPoint = new Map(points.map((point) => [`${point.lat},${point.lng}`, point]));
  return createGeolocator({
    liveKgis: true,
    geocoderUrl: "https://geocoder.test/reverse",
    logger: quiet,
    fetchImpl: async (input) => {
      const url = new URL(input);
      if (url.hostname === "geocoder.test") {
        const point = byPoint.get(`${url.searchParams.get("lat")},${url.searchParams.get("lon")}`);
        return new Response(JSON.stringify({ address: point.address }));
      }
      const { x, y } = JSON.parse(url.searchParams.get("geometry"));
      const point = byPoint.get(`${y},${x}`);
      return new Response(JSON.stringify({ features: url.pathname.includes("Admin_Dynamic_New/MapServer/1/")
        ? [{ attributes: { KGISTownName: point.town, LGD_TownCode: point.lgd, KGISTownCode: point.townCode } }] : [] }));
    },
  });
}

// A point inside the West corporation's Vinayaka Layout ward (20G5025), found from the
// polygons so the test does not depend on a hand-copied coordinate.
function pointInWard(code) {
  const [, , , bbox] = WARDS.towns[code.slice(0, 4)].wards.find((ward) => ward[0] === code);
  for (let step = 1; step < 20; step += 1) {
    const lat = (bbox[1] + ((bbox[3] - bbox[1]) * step) / 20) / WARDS.coordinate_scale;
    const lng = (bbox[0] + ((bbox[2] - bbox[0]) * step) / 20) / WARDS.coordinate_scale;
    const fixed = { lat: Number(lat.toFixed(5)), lng: Number(lng.toFixed(5)) };
    if (wardAt(WARDS, fixed.lat, fixed.lng, code.slice(0, 4))?.code === code) return fixed;
  }
  throw new Error(`no point found in ${code}`);
}

test("through the cached geolocator, a namesake across town is still left out", async () => {
  const west = { ...pointInWard("20G5025"), town: "GBA - West", lgd: 305854, townCode: "20G5",
    address: { road: "3rd Cross Road", suburb: "Vinayaka Layout", city: "Bengaluru", state: "Karnataka" } };
  const repository = { ...memoryRepository(), ...cellStore() };
  repository.queryTenders = async () => BENGALURU;
  const geolocator = createCachedGeolocator({ geolocator: liveGeolocator([west]), repository });
  assert.equal(typeof geolocator.wardRoster, "function", "the wrapper passes the roster through");
  assert.equal((await geolocator.wardRoster("20G5025")).length, 369);
  const h = await harness({ geolocator, repository });
  const body = JSON.parse((await h.post("/v1/tenders/resolve", { lat: west.lat, lng: west.lng })).body);
  assert.equal(body.jurisdiction.ward_name, "Vinayaka Layout");
  assert.equal(body.jurisdiction.ward_code, "20G5025");
  // Two Bengaluru tenders name a Vinayaka layout in Doddanekundi ward 101, 20 km east.
  const doddanekundi = body.ward_tenders.filter((entry) => /doddanekundi/i.test(entry.title));
  assert.deepEqual(doddanekundi.map((entry) => entry.title), []);
});

test("a location stored before the ward release is not answered without its ward", async () => {
  const point = { ...CASES.munnekolala, town: "GBA - East", lgd: 305852, townCode: "20G3" };
  const repository = { ...memoryRepository(), ...cellStore() };
  repository.queryTenders = async () => BENGALURU;
  // What the cache held for this cell on 6 Oct 2026: a live answer with no ward in it.
  repository.cells.set(`GEO#v1#${point.lat.toFixed(4)},${point.lng.toFixed(4)}`, {
    expiresAt: Date.now() + 6 * 86_400_000,
    value: { lat: point.lat, lng: point.lng, address: point.street,
      address_parts: { road: "6th Cross Road", suburb: "Munnenkolalu", city: "Bengaluru", state: "Karnataka" },
      lgd: "305852", town: "GBA - East", source: "kgis", address_source: "operator_geocoder",
      road_ownership: "municipal", lookup: { kgis: "available", local: "not_needed", geocoder: "available" } },
  });
  const geolocator = createCachedGeolocator({ geolocator: liveGeolocator([point]), repository });
  const h = await harness({ geolocator, repository });
  const first = JSON.parse((await h.post("/v1/tenders/resolve", { lat: point.lat, lng: point.lng })).body);
  assert.equal(first.jurisdiction.ward_name, "Munnenkolalu");
  assert.equal(first.jurisdiction.lookup.ward, "resolved");
  for (const title of point.expected) assert.ok(first.ward_tenders.some((entry) => entry.title === title), title);
  // The answer stored now carries the ward, and a hit answers the same ward tenders.
  const second = JSON.parse((await h.post("/v1/tenders/resolve", { lat: point.lat, lng: point.lng })).body);
  assert.equal(second.jurisdiction.lookup.cache, "hit");
  assert.equal(second.jurisdiction.ward_name, "Munnenkolalu");
  assert.deepEqual(second.ward_tenders, first.ward_tenders);
  const logged = JSON.parse(h.lines.log.findLast((line) => line.includes('"http_request"')));
  assert.equal(logged.geo_cache, "hit");
  assert.equal(logged.ward_lookup, "resolved");
  assert.equal(logged.ward_tender_count, second.ward_tenders.length);
});
