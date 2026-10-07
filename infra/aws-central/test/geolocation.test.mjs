import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { setTimeout as sleep } from "node:timers/promises";

import { createGeolocator } from "../service/geolocation.mjs";

// Since 7 Oct 2026 a Karnataka lookup is answered from the packaged copy of the KGIS
// layers (data/karnataka-ownership.bin) and the request path never calls KGIS. The live
// lookup survives behind `liveKgis: true` for tools/verify-local-ownership.mjs, which
// compares the two. The first half of this file is the default; the second half keeps
// the live path honest.

const hanging = (url, { signal }) => new Promise((resolve, reject) => {
  signal.addEventListener("abort", () => reject(signal.reason));
});

const quiet = { error() {}, log() {} };
const NO_LOCAL_GEOMETRY = "/nonexistent/karnataka-ownership.bin";
const live = (options) => createGeolocator({ liveKgis: true, ...options });
// The live lookup with no local bundle at all: what production was before 6 Oct 2026.
const withoutSnapshot = (options) => live({
  ...options, localGeometryPath: NO_LOCAL_GEOMETRY, logger: quiet,
});

const BODIES = JSON.parse(readFileSync(
  new URL("../../../data/karnataka-bodies.json", import.meta.url), "utf8")).bodies;

// A geolocator in the default configuration that counts what it would have sent to KGIS.
function counted(options = {}) {
  const calls = { kgis: 0 };
  const geolocator = createGeolocator({
    fetchImpl: async (url) => {
      if (String(url).includes("kgis.ksrsac.in")) calls.kgis += 1;
      throw new Error("no network in this test");
    },
    ...options,
  });
  return { geolocator, calls };
}

// Every answer below was put to KGIS itself on 7 Oct 2026 (town layer 1, highway layers
// 289, 290 and 291 with a 5 m buffer, panchayat layer) and is what KGIS said.

test("a state highway inside a town is a state highway, answered locally", async () => {
  const { geolocator, calls } = counted();
  // Munavalli Bailhongal Road where it runs through Bailahongal (CMC, LGD 251827). The
  // town polygon contains this point; calling it municipal would send the complaint to
  // the wrong authority.
  const result = await geolocator.resolve({ lat: 15.81887, lng: 74.8627 });
  assert.equal(result.road_ownership, "state_highway");
  assert.equal(result.highway_name, "Munavalli Bailhongal Road");
  assert.equal(result.lgd, null);
  assert.equal(result.town, null);
  assert.equal(result.source, "unresolved");
  assert.equal(result.lookup.kgis, "snapshot");
  assert.equal(result.lookup.local, "state_highway_polygon");
  assert.equal(calls.kgis, 0);
});

test("a district highway is a district highway, in a town or out of one", async () => {
  const { geolocator, calls } = counted();
  const inTown = await geolocator.resolve({ lat: 14.44149, lng: 75.47875 });
  assert.equal(inTown.road_ownership, "district_highway", "inside Rattihalli (TP)");
  assert.equal(inTown.highway_name, "Tavaragi - Tumminakatti Road");
  assert.equal(inTown.lgd, null);
  assert.equal(inTown.lookup.local, "district_highway_polygon");
  const country = await geolocator.resolve({ lat: 12.77097, lng: 77.33556 });
  assert.equal(country.road_ownership, "district_highway");
  assert.equal(country.highway_name, "Mayaganahalli to Sugganahalli via Dharapura");
  assert.equal(country.rural_body, null, "KGIS is not asked for the panchayat of a highway");
  // KGIS publishes most of its highway polygons with no name, or a blank one.
  const unnamed = await geolocator.resolve({ lat: 12.84257, lng: 75.91024 });
  assert.equal(unnamed.road_ownership, "state_highway");
  assert.equal(unnamed.highway_name, null);
  assert.equal(calls.kgis, 0);
});

test("the recorded city cases: MG Road is municipal, Bellary Road is a national highway", async () => {
  const { geolocator, calls } = counted();
  // The NH layer holds Bengaluru's MG Road (OBJECTID 3059), 20 m from this point. At
  // the 5 m buffer the live lookup uses it does not match, and it must not here.
  const mgRoad = await geolocator.resolve({ lat: 12.9756, lng: 77.605 });
  assert.equal(mgRoad.road_ownership, "municipal");
  assert.equal(mgRoad.town, "GBA - Central");
  assert.equal(mgRoad.lgd, "305851");
  assert.equal(mgRoad.source, "kgis_snapshot");
  assert.equal(mgRoad.lookup.local, "municipal_polygon");
  // The land-cover polygon stops short of this point on the carriageway; 5 m reaches it.
  const bellary = await geolocator.resolve({ lat: 13.00271, lng: 77.58406 });
  assert.equal(bellary.road_ownership, "national_highway");
  assert.equal(bellary.highway_name, "BELLARY ROAD NH 7");
  assert.equal(bellary.lgd, null);
  assert.equal(bellary.lookup.local, "national_highway_polygon");
  // Central Hubballi: a state highway polygon 10 m away, so still the corporation's.
  const hubballi = await geolocator.resolve({ lat: 15.3647, lng: 75.124 });
  assert.equal(hubballi.road_ownership, "municipal");
  assert.equal(hubballi.town, "HUBLI DHARWAD");
  assert.equal(calls.kgis, 0);
});

test("a point in no town is rural with its panchayat, and a point in no polygon is outside", async () => {
  const { geolocator, calls } = counted();
  // Fields in Tumakuru district.
  const country = await geolocator.resolve({ lat: 13.45, lng: 77.05 });
  assert.equal(country.road_ownership, "rural");
  assert.equal(country.rural_body, "THIMMARAJANAHALLI");
  assert.equal(country.lgd, null);
  assert.equal(country.source, "unresolved");
  assert.equal(country.lookup.local, "gp_polygon");
  // Hosur, Tamil Nadu, 6 km over the line: inside the envelope, in no KGIS polygon and
  // outside the state boundary.
  const hosur = await geolocator.resolve({ lat: 12.7409, lng: 77.8253 });
  assert.equal(hosur.road_ownership, "outside_state");
  assert.equal(hosur.lookup.kgis, "snapshot");
  assert.equal(hosur.lookup.local, "outside_state_polygon");
  // Electronic City's industrial township is in the town layer with no LGD code, and
  // KGIS itself answers unknown for it. The snapshot says no more than the register.
  const elcita = await geolocator.resolve({ lat: 12.8452, lng: 77.6602 });
  assert.equal(elcita.road_ownership, "unknown");
  assert.equal(elcita.lookup.local, "town_without_lgd");
  assert.equal(calls.kgis, 0);
});

// A point inside the state boundary is in Karnataka, whatever the panchayat layer calls
// it. The live lookup read "no panchayat name" as outside_state (it had no state polygon
// to check), so forest in 2.8% of the state's area was told it was outside Karnataka.
// With the boundary packaged, such a point is a rural road with no body named.
// lookup.local still tells the kinds apart.
test("a point inside Karnataka with no named panchayat is rural, never outside_state", async () => {
  const { geolocator, calls } = counted();
  // Forest in the Male Mahadeshwara hills and at Sandur: KGIS files each under a
  // panchayat polygon with a blank name.
  for (const [lat, lng] of [[12.0231, 77.6737], [15.0788, 76.6221]]) {
    const forest = await geolocator.resolve({ lat, lng });
    assert.equal(forest.road_ownership, "rural");
    assert.equal(forest.rural_body, null);
    assert.equal(forest.lookup.local, "gp_polygon_unnamed");
  }
  // The BRT hills and Nagarahole: inside the state boundary, in no panchayat polygon.
  for (const [lat, lng] of [[11.7788, 77.1261], [11.9787, 76.0694]]) {
    const gap = await geolocator.resolve({ lat, lng });
    assert.equal(gap.road_ownership, "rural");
    assert.equal(gap.rural_body, null);
    assert.equal(gap.lookup.local, "state_polygon_no_panchayat");
  }
  assert.equal(calls.kgis, 0);
});

// Two polygons of one class within the buffer, which KGIS names differently. KGIS's own
// pick is the first row its spatial index returns (it listed the other polygon first at
// 18 of 28 such points on 7 Oct 2026, and the order changed with the query's output
// fields), so the choice here is a rule: the polygon the point is in, then a named one.
test("where two highway polygons of one class cover a point, the one it is in names the road", async () => {
  const { geolocator } = counted();
  // Inside district highway 5665 (no name), 0.9 m from 5625 ("Hosahalli").
  const inUnnamed = await geolocator.resolve({ lat: 15.409048275087615, lng: 75.51257940722155 });
  assert.equal(inUnnamed.road_ownership, "district_highway");
  assert.equal(inUnnamed.highway_name, null);
  // Inside state highway 1614, 0.6 m from 1615 ("Hesaraghatta road"): 1614's own name.
  const junction = await geolocator.resolve({ lat: 13.059415857992676, lng: 77.5068531217575 });
  assert.equal(junction.road_ownership, "state_highway");
  assert.equal(junction.highway_name, "Jalahalli watch factory road");
  // Inside two overlapping district highway polygons at once, 11498 (no name) and 11517:
  // the one with a name, though the other has the lower OBJECTID.
  const inBoth = await geolocator.resolve({ lat: 16.222333, lng: 77.385435 });
  assert.equal(inBoth.road_ownership, "district_highway");
  assert.equal(inBoth.highway_name, "Raichur to Burdipad");
});

test("in the default configuration nothing in Karnataka is ever put to KGIS", async () => {
  const { geolocator, calls } = counted();
  const points = {
    "Bengaluru": [12.9716, 77.5946, "305851"],
    "Hubballi": [15.3647, 75.1240, "251893"],
    "Mangaluru": [12.87, 74.88, "252021"],
    "Kalaburagi": [17.3297, 76.8343, "248127"],
    "Mysuru": [12.2958, 76.6394, "252045"],
  };
  for (const [place, [lat, lng, lgd]] of Object.entries(points)) {
    const result = await geolocator.resolve({ lat, lng });
    assert.equal(result.road_ownership, "municipal", place);
    assert.equal(result.lgd, lgd, place);
    assert.equal(result.source, "kgis_snapshot", place);
    assert.deepEqual(
      [result.lookup.kgis, result.lookup.kgis_town, result.lookup.kgis_highway, result.lookup.kgis_gp],
      ["snapshot", "snapshot", "snapshot", "snapshot"], place);
    assert.ok(BODIES[lgd]?.email, `${place}: the body the snapshot named is one the app can write to`);
  }
  // A grid across the whole envelope, towns, highways, country, sea and neighbours alike.
  for (let lat = 11.2; lat < 18.9; lat += 0.37) {
    for (let lng = 73.6; lng < 79.0; lng += 0.41) {
      const result = await geolocator.resolve({ lat, lng });
      assert.equal(result.lookup.kgis, "snapshot");
      assert.notEqual(result.road_ownership, "unknown", `${lat},${lng}`);
    }
  }
  assert.equal(calls.kgis, 0, "the request path made a KGIS request");
});

test("a package without the bundle fails closed, and still does not ask KGIS", async () => {
  const errors = [];
  const { geolocator, calls } = counted({
    localGeometryPath: NO_LOCAL_GEOMETRY,
    logger: { error: (line) => errors.push(JSON.parse(line)), log() {} },
  });
  for (const [lat, lng] of [[12.9716, 77.5946], [15.3647, 75.124]]) {
    const result = await geolocator.resolve({ lat, lng });
    assert.equal(result.road_ownership, "unknown");
    assert.equal(result.lookup.kgis, "snapshot");
    assert.equal(result.lookup.local, "unavailable");
  }
  assert.deepEqual(errors.map((line) => line.event), ["local_geometry_unavailable"], "logged once");
  assert.equal(calls.kgis, 0);
});

test("a file that is not the bundle is treated as absent", async () => {
  const { geolocator } = counted({
    localGeometryPath: new URL("../../../data/karnataka-towns.json", import.meta.url),
    logger: quiet,
  });
  const bengaluru = await geolocator.resolve({ lat: 12.9716, lng: 77.5946 });
  assert.equal(bengaluru.road_ownership, "unknown");
  assert.equal(bengaluru.lookup.local, "unavailable");
});

test("nearby points share a cache entry but keep their own coordinates", async () => {
  const { geolocator } = counted();
  const first = await geolocator.resolve({ lat: 12.97161, lng: 77.59461, addressHint: "MG Road" });
  const near = await geolocator.resolve({ lat: 12.97164, lng: 77.59463, addressHint: "MG Road" });
  assert.equal(first.road_ownership, "municipal");
  assert.equal(near.road_ownership, "municipal");
  assert.equal(near.lat, 12.97164);
  assert.equal(near.lng, 77.59463);
});

// ---------------------------------------------------------------------------------
// The live lookup, behind `liveKgis: true`. KGIS sometimes stalls on its query endpoints
// while its root still answers; after one timeout the live geolocator stops asking for a
// while and answers from the same local bundle at once.

test("a KGIS timeout opens a breaker so the next lookup does not wait", async () => {
  let calls = 0;
  const geolocator = live({
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
  assert.equal(second.lookup.local, "municipal_polygon");
  assert.equal(second.road_ownership, "municipal");
  assert.equal(second.source, "kgis_snapshot");
  assert.equal(second.town, "HUBLI DHARWAD");
  assert.equal(calls, callsAfterFirst, "no KGIS call while the breaker is open");
});

test("the default KGIS timeout is 3 s and the breaker stays open for a minute", () => {
  assert.equal(createGeolocator().kgisTimeoutMs, 3_000);
  assert.equal(createGeolocator().kgisBreakerMs, 60_000);
});

// An open breaker is a pause, not a verdict on KGIS. Once it closes the next lookup
// asks KGIS again, and a live answer takes over from the snapshot.
test("after the breaker closes the next lookup asks KGIS again", async () => {
  let calls = 0;
  let stalled = true;
  const geolocator = live({
    fetchImpl: (url, options) => {
      calls += 1;
      if (stalled) return hanging(url, options);
      return Promise.resolve(new Response(JSON.stringify({ features:
        url.includes("Admin_Dynamic_New")
          ? [{ attributes: { KGISTownName: "Live town", LGD_TownCode: 999001 } }] : [] })));
    },
    kgisTimeoutMs: 20,
    kgisBreakerMs: 40,
  });
  const during = await geolocator.resolve({ lat: 12.9716, lng: 77.5946 });
  assert.equal(during.source, "kgis_snapshot");
  const opened = calls;
  await geolocator.resolve({ lat: 15.3647, lng: 75.124 });
  assert.equal(calls, opened, "the breaker is open");
  stalled = false;
  await sleep(60);
  const after = await geolocator.resolve({ lat: 12.9141, lng: 74.856 });
  assert.ok(calls > opened, "KGIS was asked again once the breaker closed");
  assert.equal(after.lookup.kgis, "available");
  assert.equal(after.lookup.local, "not_needed");
  assert.equal(after.source, "kgis");
  assert.equal(after.lgd, "999001");
});

// Inside Karnataka a broken KGIS answer is not evidence of anything. The snapshot
// answers instead, and says so. (This case used a Gandhinagar coordinate until
// 30 Sep 2026; outside Karnataka the answer no longer comes from KGIS at all, so it
// proved nothing.)
test('malformed GIS JSON never proves outside-state or municipal ownership', async()=>{
  for (const payload of [{}, {status:'temporarily unavailable'}, {features:{}}, {features:[{}]}]) {
    const fetchImpl=async()=>new Response(JSON.stringify(payload));
    const blind=await withoutSnapshot({fetchImpl}).resolve({lat:12.9716,lng:77.5946});
    assert.equal(blind.road_ownership,'unknown');
    assert.equal(blind.lookup.kgis,'unavailable');
    assert.equal(blind.lookup.local,'unavailable');
    const result=await live({fetchImpl}).resolve({lat:12.9716,lng:77.5946});
    assert.equal(result.lookup.kgis,'unavailable');
    assert.equal(result.lookup.local,'municipal_polygon');
    assert.equal(result.source,'kgis_snapshot', 'the answer is the snapshot, never the malformed body');
    assert.equal(result.road_ownership,'municipal');
  }
});

test('a malformed GIS answer cannot change the verdict outside Karnataka', async()=>{
  for (const payload of [{}, {status:'temporarily unavailable'}, {features:{}}, {features:[{}]}]) {
    const locator=live({fetchImpl:async()=>new Response(JSON.stringify(payload))});
    const result=await locator.resolve({lat:23.181854,lng:72.652801});
    assert.equal(result.road_ownership,'outside_state');
    assert.equal(result.lookup.kgis,'out_of_scope');
  }
});

test('malformed GIS failure is not cached across a successful retry', async()=>{
  let broken=true;
  const locator=withoutSnapshot({fetchImpl:async(url)=>new Response(JSON.stringify(broken
    ? {} : {features:url.includes('Admin_Dynamic_New')
      ? [{attributes:{KGISTownName:'Test town',LGD_TownCode:1}}] : []}))});
  const args={lat:12.97,lng:77.59,addressHint:'Test Road'};
  assert.equal((await locator.resolve(args)).road_ownership,'unknown');
  broken=false;
  assert.equal((await locator.resolve(args)).road_ownership,'municipal');
});

// The four cases the 6 Oct 2026 fallback has to get right. 227 of the 450 tender
// lookups in the previous 30 days were 503 road_ownership_unavailable, every one a
// Karnataka point KGIS could not answer for in time.

test("live KGIS down: a Bengaluru point is municipal from the snapshot, with its LGD", async () => {
  let kgisCalls = 0;
  const geolocator = live({
    fetchImpl: async (url) => {
      if (url.includes("kgis.ksrsac.in")) kgisCalls += 1;
      throw new Error("KGIS is down");
    },
  });
  // GBA Central's office, 12.99657,77.62034. The whole of what was Bengaluru is five
  // city corporations since 2025 and the snapshot carries all five.
  const result = await geolocator.resolve({ lat: 12.99657, lng: 77.62034, addressHint: "Cunningham Road" });
  assert.ok(kgisCalls > 0, "KGIS was still asked first");
  assert.equal(result.road_ownership, "municipal");
  assert.equal(result.lgd, "305851");
  assert.equal(result.town, "GBA - Central");
  assert.equal(result.source, "kgis_snapshot");
  assert.equal(result.lookup.kgis, "unavailable");
  assert.equal(result.lookup.local, "municipal_polygon");
  assert.ok(BODIES["305851"]?.email, "the body the snapshot named is one the app can write to");
});

test("live KGIS down: highways, country and the border are answered from the same bundle", async () => {
  const geolocator = live({ fetchImpl: async () => { throw new Error("down"); } });
  const bellary = await geolocator.resolve({ lat: 13.00271, lng: 77.58406 });
  assert.equal(bellary.road_ownership, "national_highway");
  assert.equal(bellary.highway_name, "BELLARY ROAD NH 7", "the name KGIS would have given");
  assert.equal(bellary.lookup.kgis, "unavailable");
  assert.equal(bellary.lookup.local, "national_highway_polygon");
  assert.equal(bellary.lgd, null);
  const mgRoad = await geolocator.resolve({ lat: 12.9756, lng: 77.605 });
  assert.equal(mgRoad.road_ownership, "municipal");
  assert.equal(mgRoad.town, "GBA - Central");
  // The case the 6 Oct 2026 fallback could not answer: it held no state highways, so it
  // called this stretch of one municipal.
  const stateHighway = await geolocator.resolve({ lat: 15.81887, lng: 74.8627 });
  assert.equal(stateHighway.road_ownership, "state_highway");
  assert.equal(stateHighway.lookup.local, "state_highway_polygon");
  const country = await geolocator.resolve({ lat: 13.45, lng: 77.05 });
  assert.equal(country.road_ownership, "rural");
  assert.equal(country.rural_body, "THIMMARAJANAHALLI");
  assert.equal(country.source, "unresolved");
  assert.equal(country.lookup.local, "gp_polygon");
  const hosur = await geolocator.resolve({ lat: 12.7409, lng: 77.8253 });
  assert.equal(hosur.road_ownership, "outside_state");
  assert.equal(hosur.lookup.local, "outside_state_polygon");
  const elcita = await geolocator.resolve({ lat: 12.8452, lng: 77.6602 });
  assert.equal(elcita.road_ownership, "unknown");
  assert.equal(elcita.lookup.local, "town_without_lgd");
});

test("live KGIS up: its answer wins and the snapshot is not consulted", async () => {
  const geolocator = live({
    fetchImpl: async (url) => new Response(JSON.stringify({ features:
      url.includes("Admin_Dynamic_New")
        ? [{ attributes: { KGISTownName: "Live town", LGD_TownCode: 424242 } }] : [] })),
  });
  const result = await geolocator.resolve({ lat: 12.99657, lng: 77.62034 });
  assert.equal(result.source, "kgis");
  assert.equal(result.lgd, "424242", "the live register's code, not the snapshot's 305851");
  assert.equal(result.town, "Live town");
  assert.equal(result.lookup.kgis, "available");
  assert.equal(result.lookup.local, "not_needed");
});

// KGIS's town and highway layers answered but the panchayat layer did not: before
// 6 Oct 2026 that was a 503 too.
test("a live panchayat layer outage is answered by the packaged panchayat polygons", async () => {
  const geolocator = live({
    fetchImpl: async (url) => {
      if (url.includes("GP_Boundary")) throw new Error("GP layer down");
      return new Response(JSON.stringify({ features: [] }));
    },
  });
  const result = await geolocator.resolve({ lat: 13.45, lng: 77.05 });
  assert.equal(result.lookup.kgis, "available");
  assert.equal(result.lookup.kgis_gp, "not_needed_or_unavailable");
  assert.equal(result.road_ownership, "rural");
  assert.equal(result.rural_body, "THIMMARAJANAHALLI");
  assert.equal(result.lookup.local, "gp_polygon");
});

test("live: nearby points share a cache entry and one round of KGIS calls", async () => {
  let calls = 0;
  const geolocator = live({
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

// Seen live on 5 Oct 2026: two installs asked about the same 11 m cell within five
// minutes and the second got the first one's address, and so the first one's tender
// match. A hint typed by one phone is that phone's claim, not a fact about the place.
test("one caller's address hint is never served to the next caller in the cell", async () => {
  const { geolocator } = counted();
  const first = await geolocator.resolve({ lat: 12.3051, lng: 76.6551, addressHint: "Nirvikalpa Road" });
  assert.equal(first.address, "Nirvikalpa Road");
  const second = await geolocator.resolve({ lat: 12.30512, lng: 76.65512, addressHint: "Some Other Lane" });
  assert.equal(second.address, "Some Other Lane");
  assert.equal(second.address_source, "client_hint");
  assert.equal(second.lgd, "252045");
  const third = await geolocator.resolve({ lat: 12.30511, lng: 76.65511 });
  assert.equal(third.address, null);
  assert.equal(third.address_source, "unresolved");
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
  const geolocator = live({ fetchImpl: recordedKgis });
  const at = (lat, lng) => geolocator.resolve({ lat, lng, addressHint: "hint" });
  const mgRoad = await at(12.9756, 77.605);
  assert.equal(mgRoad.road_ownership, "municipal");
  assert.equal(mgRoad.town, "GBA - Central");
  assert.equal(mgRoad.source, "kgis");
  assert.equal(mgRoad.lookup.local, "not_needed");
  assert.equal((await at(15.3647, 75.124)).road_ownership, "municipal");
  const bellary = await at(13.00271, 77.58406);
  assert.equal(bellary.road_ownership, "national_highway");
  assert.equal(bellary.highway_name, "BELLARY ROAD NH 7");
  assert.equal(bellary.lookup.local, "not_needed");
});

// Gandhinagar, Gujarat, reported from the field on 30 Sep 2026. KGIS is a Karnataka
// register: it has nothing to say about a Gujarat street whether it is up or down. Asking
// it anyway turned a transient KGIS blip into road_ownership "unknown", which the app
// showed as "could not check whether this road is a national, state, or district highway,
// try again when you have a signal" on a phone with full 5G. A point this far outside
// Karnataka must answer outside_state without a single KGIS request.
test("a point far outside Karnataka answers outside_state without calling KGIS", async () => {
  let kgisCalls = 0;
  const geolocator = createGeolocator({
    fetchImpl: async (url) => {
      if (url.includes("kgis.ksrsac.in")) kgisCalls += 1;
      throw new Error("KGIS is down");
    },
  });
  const result = await geolocator.resolve({ lat: 23.181854, lng: 72.652801 });
  assert.equal(result.road_ownership, "outside_state");
  assert.equal(kgisCalls, 0, "KGIS cannot answer for Gujarat, so it must not be asked");
});

test("a KGIS outage without the snapshot still refuses to guess ownership inside Karnataka", async () => {
  const geolocator = withoutSnapshot({ fetchImpl: async () => { throw new Error("down"); } });
  const bengaluru = await geolocator.resolve({ lat: 12.9716, lng: 77.5946 });
  assert.equal(bengaluru.road_ownership, "unknown");
  assert.equal(bengaluru.lookup.local, "unavailable");
});

test("live: a snapshot that is not the expected bundle is treated as absent", async () => {
  const geolocator = live({
    fetchImpl: async () => { throw new Error("down"); },
    localGeometryPath: new URL("../../../data/karnataka-towns.json", import.meta.url),
    logger: quiet,
  });
  const bengaluru = await geolocator.resolve({ lat: 12.9716, lng: 77.5946 });
  assert.equal(bengaluru.road_ownership, "unknown");
  assert.equal(bengaluru.lookup.local, "unavailable");
});

// The Gandhinagar report was one coordinate; the fault was the whole country. KGIS can
// only answer inside Karnataka, so for every point outside it the verdict is a fact about
// geography and must not move when KGIS does. This grid walks India state by state and
// asserts exactly that: outside Karnataka, KGIS is never asked, and the answer is the same
// whether KGIS is healthy, empty, slow or throwing.
const OUTSIDE_KARNATAKA = {
  "Gandhinagar, Gujarat": [23.181854, 72.652801],
  "Mumbai, Maharashtra": [19.0760, 72.8777],
  "Delhi": [28.6139, 77.2090],
  "Kolkata, West Bengal": [22.5726, 88.3639],
  "Chennai, Tamil Nadu": [13.0827, 80.2707],
  "Jaipur, Rajasthan": [26.9124, 75.7873],
  "Guwahati, Assam": [26.1445, 91.7362],
  "Bhopal, Madhya Pradesh": [23.2599, 77.4126],
  "Patna, Bihar": [25.5941, 85.1376],
  "Lucknow, Uttar Pradesh": [26.8467, 80.9462],
  "Srinagar, Jammu and Kashmir": [34.0837, 74.7973],
  "Thiruvananthapuram, Kerala": [8.5241, 76.9366],
  "Port Blair, Andaman and Nicobar": [11.6234, 92.7265],
  "Itanagar, Arunachal Pradesh": [27.0844, 93.6053],
};

const BEHAVIOURS = {
  throwing: async () => { throw new Error("KGIS is down"); },
  empty: async () => new Response(JSON.stringify({ features: [] })),
  malformed: async () => new Response(JSON.stringify({ status: "temporarily unavailable" })),
  erroring: async () => new Response("no", { status: 500 }),
};

test("outside Karnataka the verdict never depends on KGIS, anywhere in India", async () => {
  for (const [place, [lat, lng]] of Object.entries(OUTSIDE_KARNATAKA)) {
    const answers = new Set();
    for (const [name, behaviour] of Object.entries(BEHAVIOURS)) {
      let kgisCalls = 0;
      const geolocator = createGeolocator({
        fetchImpl: (url, ...rest) => {
          if (String(url).includes("kgis.ksrsac.in")) kgisCalls += 1;
          return behaviour(url, ...rest);
        },
      });
      const result = await geolocator.resolve({ lat, lng });
      assert.equal(kgisCalls, 0,
        `${place} put ${kgisCalls} request(s) to a Karnataka register (KGIS ${name})`);
      assert.equal(result.lookup.kgis, "out_of_scope", `${place} with KGIS ${name}`);
      answers.add(result.road_ownership);
    }
    assert.deepEqual([...answers], ["outside_state"],
      `${place} gave different answers depending on KGIS health: ${[...answers]}`);
  }
});

test("live: inside Karnataka KGIS is asked first, and without the snapshot the verdict fails closed", async () => {
  // The other half of the property. If this ever passes by short-circuiting, the envelope
  // has grown over the state it was meant to exclude nothing from.
  for (const [place, [lat, lng, lgd]] of Object.entries({
    "Bengaluru": [12.9716, 77.5946, "305851"],
    "Hubballi": [15.3647, 75.1240, "251893"],
    "Mangaluru": [12.87, 74.88, "252021"],
    "Kalaburagi": [17.3297, 76.8343, "248127"],
  })) {
    for (const [name, behaviour] of Object.entries(BEHAVIOURS)) {
      let kgisCalls = 0;
      const fetchImpl = (url, ...rest) => {
        if (String(url).includes("kgis.ksrsac.in")) kgisCalls += 1;
        return behaviour(url, ...rest);
      };
      const blind = await withoutSnapshot({ fetchImpl }).resolve({ lat, lng });
      assert.ok(kgisCalls > 0, `${place} did not consult KGIS (KGIS ${name})`);
      if (name !== "empty") {
        assert.equal(blind.road_ownership, "unknown",
          `${place} claimed an answer from a KGIS that was ${name}`);
      }
      const result = await live({ fetchImpl }).resolve({ lat, lng });
      if (name !== "empty") {
        assert.equal(result.lookup.kgis, "unavailable", `${place} with KGIS ${name}`);
        assert.equal(result.source, "kgis_snapshot", `${place} with KGIS ${name}`);
        assert.equal(result.lgd, lgd, `${place} with KGIS ${name}`);
      }
    }
  }
});

test("live: a coordinate just outside the Karnataka border is still put to KGIS", async () => {
  let kgisCalls = 0;
  const geolocator = live({
    fetchImpl: async (url) => {
      if (url.includes("kgis.ksrsac.in")) kgisCalls += 1;
      return new Response(JSON.stringify({ features: [] }));
    },
  });
  // Hosur, Tamil Nadu, 6 km from the Karnataka line: close enough that only KGIS
  // can settle it, so the envelope must not short-circuit it.
  await geolocator.resolve({ lat: 12.7409, lng: 77.8253 });
  assert.ok(kgisCalls > 0, "a border point must still be resolved against KGIS");
});

// 197 lookups on 6 Oct 2026 ended address_unresolved: the phone sent no street and the
// service had no geocoder configured, so tender matching had nothing to match on.
test("with a geocoder configured the service finds the street itself and identifies to it", async () => {
  const seen = [];
  const geolocator = createGeolocator({
    geocoderUrl: "https://nominatim.example/reverse",
    fetchImpl: async (url, init) => {
      seen.push({ url: String(url), headers: init?.headers || {} });
      if (String(url).startsWith("https://nominatim.example/")) {
        return new Response(JSON.stringify({ address: { road: "Nirvikalpa Road", suburb: "Kuvempunagar", city: "Mysuru" } }));
      }
      return new Response(JSON.stringify({ features: String(url).includes("Admin_Dynamic_New")
        ? [{ attributes: { KGISTownName: "Mysuru", LGD_TownCode: 252045 } }] : [] }));
    },
  });
  const result = await geolocator.resolve({ lat: 12.3051, lng: 76.6551 });
  assert.equal(result.address, "Nirvikalpa Road, Kuvempunagar, Mysuru");
  assert.equal(result.address_source, "operator_geocoder");
  const call = seen.find((item) => item.url.startsWith("https://nominatim.example/"));
  assert.match(call.url, /lat=12\.3051&lon=76\.6551&format=jsonv2&zoom=17&addressdetails=1/);
  assert.match(call.headers["user-agent"], /PotholeReporter.*contact@aiengg\.dev/);
});
