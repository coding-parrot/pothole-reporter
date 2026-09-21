import assert from "node:assert/strict";
import test from "node:test";

import { createGeolocator } from "../service/geolocation.mjs";

// KGIS sometimes stalls on its query endpoints while its root still answers. Each
// report then waited out every lookup in turn. After one timeout the geolocator stops
// asking for a while and returns road ownership unknown at once.

const hanging = (url, { signal }) => new Promise((resolve, reject) => {
  signal.addEventListener("abort", () => reject(signal.reason));
});

test("a KGIS timeout opens a breaker so the next lookup does not wait", async () => {
  let calls = 0;
  const geolocator = createGeolocator({
    fetchImpl: (...args) => { calls += 1; return hanging(...args); },
    kgisTimeoutMs: 50,
  });
  const first = await geolocator.resolve({ lat: 12.9716, lng: 77.5946 });
  assert.equal(first.lookup.kgis, "unavailable");
  const callsAfterFirst = calls;
  const started = Date.now();
  const second = await geolocator.resolve({ lat: 15.3647, lng: 75.124 });
  assert.ok(Date.now() - started < 50, `took ${Date.now() - started} ms`);
  assert.equal(second.lookup.kgis, "unavailable");
  assert.equal(second.road_ownership, "unknown");
  assert.equal(calls, callsAfterFirst, "no KGIS call while the breaker is open");
});

test("the default KGIS timeout is 3 s", () => {
  assert.equal(createGeolocator().kgisTimeoutMs, 3_000);
});

test("nearby points share a cache entry but keep their own coordinates", async () => {
  let calls = 0;
  const geolocator = createGeolocator({
    fetchImpl: async (url) => {
      calls += 1;
      const features = url.includes("Admin_Dynamic_New")
        ? [{ attributes: { KGISTownName: "Bengaluru", LGD_TownCode: 802 } }] : [];
      return new Response(JSON.stringify({ features }));
    },
  });
  await geolocator.resolve({ lat: 12.97161, lng: 77.59461, addressHint: "MG Road" });
  const before = calls;
  const near = await geolocator.resolve({ lat: 12.97164, lng: 77.59463, addressHint: "MG Road" });
  assert.equal(calls, before);
  assert.equal(near.road_ownership, "municipal");
  assert.equal(near.lat, 12.97164);
  assert.equal(near.lng, 77.59463);
});

// KGIS answers recorded on 21 Sep 2026, keyed by the smallest buffer at which each
// highway polygon starts to match. The highway layers are land cover, not centre lines.
const recordedHighways = [
  { lat: 12.9756, lng: 77.605, layer: "MapServer/289", from: 20,
    name: "MAHATMA GANDHI ROAD" },
  { lat: 15.3647, lng: 75.124, layer: "MapServer/290", from: 10, name: null },
  { lat: 13.00271, lng: 77.58406, layer: "MapServer/289", from: 5,
    name: "BELLARY ROAD NH 7" },
];
const recordedTowns = {
  "12.9756": "GBA - Central",
  "15.3647": "HUBLI DHARWAD",
  "13.00271": "GBA - West",
};

function recordedKgis(url) {
  const query = new URL(url).searchParams;
  const { x, y } = JSON.parse(query.get("geometry"));
  const distance = Number(query.get("distance") || 0);
  let features = [];
  if (url.includes("Admin_Dynamic_New")) {
    features = [{ attributes: { KGISTownName: recordedTowns[String(y)], LGD_TownCode: 1 } }];
  } else {
    const hit = recordedHighways.find((item) => item.lat === y && item.lng === x
      && url.includes(item.layer) && distance >= item.from);
    if (hit) features = [{ attributes: { Name: hit.name } }];
  }
  return Promise.resolve(new Response(JSON.stringify({ features })));
}

test("city arterials are not highways, and a national highway still is", async () => {
  const geolocator = createGeolocator({ fetchImpl: recordedKgis });
  const at = (lat, lng) => geolocator.resolve({ lat, lng, addressHint: "hint" });
  const mgRoad = await at(12.9756, 77.605);
  assert.equal(mgRoad.road_ownership, "municipal");
  assert.equal(mgRoad.town, "GBA - Central");
  assert.equal((await at(15.3647, 75.124)).road_ownership, "municipal");
  const bellary = await at(13.00271, 77.58406);
  assert.equal(bellary.road_ownership, "national_highway");
  assert.equal(bellary.highway_name, "BELLARY ROAD NH 7");
});
