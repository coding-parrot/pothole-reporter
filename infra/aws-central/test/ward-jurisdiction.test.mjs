import assert from "node:assert/strict";
import test from "node:test";

import { createGeolocator, wardNameWithoutNumber } from "../service/geolocation.mjs";

// The ward a municipal point is in comes from the packaged copy of the KGIS ward layer.
// The request path makes no ward call to KGIS: it is slow, and it stalls.

const quiet = { error() {}, log() {} };
const kgisTown = (attributes) => async (url) => new Response(JSON.stringify({
  features: url.includes("Admin_Dynamic_New/MapServer/1/") ? [{ attributes }] : [],
}));

test("a ward's name is the register's without its leading number", () => {
  assert.equal(wardNameWithoutNumber("41 - Munnenkolalu"), "Munnenkolalu");
  assert.equal(wardNameWithoutNumber("10 - Cox Town"), "Cox Town");
  assert.equal(wardNameWithoutNumber("49 - Doddakannelli Ward"), "Doddakannelli Ward");
  assert.equal(wardNameWithoutNumber("Basaveshwara Badavane 4"), "Basaveshwara Badavane 4");
  assert.equal(wardNameWithoutNumber("  "), null);
  assert.equal(wardNameWithoutNumber(null), null);
});

test("KGIS up: the ward comes from the local snapshot and KGIS is never asked for it", async () => {
  const asked = [];
  const answer = kgisTown({ KGISTownName: "GBA - East", LGD_TownCode: 305852, KGISTownCode: "20G3" });
  const geolocator = createGeolocator({
    fetchImpl: (url) => { asked.push(url); return answer(url); },
    logger: quiet,
  });
  const result = await geolocator.resolve({ lat: 12.94572, lng: 77.71055, addressHint: "6th Cross Road" });
  assert.equal(result.road_ownership, "municipal");
  assert.equal(result.source, "kgis");
  assert.equal(result.ward_name, "Munnenkolalu");
  assert.equal(result.ward_no, "41");
  assert.equal(result.ward_code, "20G3041");
  assert.equal(result.ward_numbering, "kgis_current");
  assert.equal(result.lookup.ward, "resolved");
  assert.equal(asked.filter((url) => url.includes("Admin_Dynamic_New/MapServer/2")).length, 0,
    "no live ward query");
});

test("KGIS down: the ward is still named, from the same snapshot", async () => {
  const geolocator = createGeolocator({ fetchImpl: async () => { throw new Error("down"); }, logger: quiet });
  const cox = await geolocator.resolve({ lat: 12.99657, lng: 77.62034, addressHint: "Thambhuchetty Road" });
  assert.equal(cox.source, "kgis_snapshot");
  assert.equal(cox.ward_name, "Cox Town");
  // KGIS numbers Cox Town 10 (Greater Bengaluru numbering). Tender titles call it ward
  // 108 (the old BBMP numbering), which is why the number is labelled and never matched.
  assert.equal(cox.ward_no, "10");
  assert.equal(cox.ward_code, "20G1010");
  assert.equal(cox.ward_numbering, "kgis_current");
  assert.equal(cox.lookup.ward, "resolved");
});

test("a point that is not municipal has no ward", async () => {
  const geolocator = createGeolocator({ fetchImpl: async () => { throw new Error("down"); }, logger: quiet });
  const country = await geolocator.resolve({ lat: 13.45, lng: 77.05 });
  assert.equal(country.road_ownership, "rural");
  assert.equal(country.ward_name, null);
  assert.equal(country.ward_no, null);
  assert.equal(country.ward_code, null);
  assert.equal(country.ward_numbering, null);
  assert.equal(country.lookup.ward, "not_municipal");
  const gandhinagar = await geolocator.resolve({ lat: 23.181854, lng: 72.652801 });
  assert.equal(gandhinagar.road_ownership, "outside_state");
  assert.equal(gandhinagar.ward_name, null);
  assert.equal(gandhinagar.lookup.ward, "out_of_scope");
});

test("a town the ward layer does not cover answers no_ward", async () => {
  // Hootagalli CMC, beside Mysuru: in the town layer, with no polygon in the ward layer.
  const geolocator = createGeolocator({ fetchImpl: async () => { throw new Error("down"); }, logger: quiet });
  const result = await geolocator.resolve({ lat: 12.34259, lng: 76.59126, addressHint: "x" });
  assert.equal(result.road_ownership, "municipal");
  assert.equal(result.town, "HOOTAGALLI");
  assert.equal(result.ward_name, null);
  assert.equal(result.ward_no, null);
  assert.equal(result.lookup.ward, "no_ward");
});

test("a missing ward bundle costs the ward and nothing else", async () => {
  const errors = [];
  const geolocator = createGeolocator({
    fetchImpl: kgisTown({ KGISTownName: "GBA - East", LGD_TownCode: 305852, KGISTownCode: "20G3" }),
    wardGeometryPath: "/nonexistent/karnataka-ward-geometry.json",
    logger: { error: (line) => errors.push(JSON.parse(line)), log() {} },
  });
  const first = await geolocator.resolve({ lat: 12.94572, lng: 77.71055, addressHint: "6th Cross Road" });
  const second = await geolocator.resolve({ lat: 12.95906, lng: 77.7207, addressHint: "8th Main Road" });
  for (const result of [first, second]) {
    assert.equal(result.road_ownership, "municipal");
    assert.equal(result.lgd, "305852");
    assert.equal(result.ward_name, null);
    assert.equal(result.lookup.ward, "unavailable");
  }
  assert.deepEqual(errors.map((line) => line.event), ["ward_geometry_unavailable"], "logged once");
});

test("the geocoder's named places are kept, most specific first, for ward tender matching", async () => {
  const geolocator = createGeolocator({
    geocoderUrl: "https://geocoder.example/reverse",
    fetchImpl: async (url) => new Response(JSON.stringify(url.includes("geocoder.example")
      ? { address: {
        road: "8th Main Road", neighbourhood: "Thubarahalli Palya", quarter: "BEML Layout 6th Stage",
        suburb: "Kundalahalli", city_district: "Bengaluru East City Corporation", city: "Bengaluru",
        state: "Karnataka", postcode: "560066",
      } }
      : { features: url.includes("Admin_Dynamic_New/MapServer/1/")
        ? [{ attributes: { KGISTownName: "GBA - East", LGD_TownCode: 305852, KGISTownCode: "20G3" } }] : [] })),
    logger: quiet,
  });
  const result = await geolocator.resolve({ lat: 12.95906, lng: 77.7207 });
  assert.deepEqual(result.address_parts.localities, ["Thubarahalli Palya", "BEML Layout 6th Stage", "Kundalahalli"]);
  assert.equal(result.address_parts.suburb, "Kundalahalli", "the existing field is unchanged");
  assert.equal(result.ward_name, "Kundalahalli");
});
