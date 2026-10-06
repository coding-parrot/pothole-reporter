import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { setTimeout as sleep } from "node:timers/promises";

import { createGeolocator } from "../service/geolocation.mjs";

// KGIS sometimes stalls on its query endpoints while its root still answers. Each
// report then waited out every lookup in turn. After one timeout the geolocator stops
// asking for a while and answers from its own snapshot of the KGIS polygons at once.

const hanging = (url, { signal }) => new Promise((resolve, reject) => {
  signal.addEventListener("abort", () => reject(signal.reason));
});

const quiet = { error() {}, log() {} };
const NO_LOCAL_GEOMETRY = "/nonexistent/karnataka-local-geometry.json";
// A geolocator with no snapshot at all: what production was before 6 Oct 2026.
const withoutSnapshot = (options) => createGeolocator({
  ...options, localGeometryPath: NO_LOCAL_GEOMETRY, logger: quiet,
});

const BODIES = JSON.parse(readFileSync(
  new URL("../../../data/karnataka-bodies.json", import.meta.url), "utf8")).bodies;

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
  const geolocator = createGeolocator({
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
    const result=await createGeolocator({fetchImpl}).resolve({lat:12.9716,lng:77.5946});
    assert.equal(result.lookup.kgis,'unavailable');
    assert.equal(result.lookup.local,'municipal_polygon');
    assert.equal(result.source,'kgis_snapshot', 'the answer is the snapshot, never the malformed body');
    assert.equal(result.road_ownership,'municipal');
  }
});

test('a malformed GIS answer cannot change the verdict outside Karnataka', async()=>{
  for (const payload of [{}, {status:'temporarily unavailable'}, {features:{}}, {features:[{}]}]) {
    const locator=createGeolocator({fetchImpl:async()=>new Response(JSON.stringify(payload))});
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

test("KGIS down: a Bengaluru point is municipal from the snapshot, with its LGD", async () => {
  let kgisCalls = 0;
  const geolocator = createGeolocator({
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

test("KGIS down: a point on Bellary Road is a national highway from the OSM lines", async () => {
  const geolocator = createGeolocator({ fetchImpl: async () => { throw new Error("down"); } });
  const bellary = await geolocator.resolve({ lat: 13.00271, lng: 77.58406 });
  assert.equal(bellary.road_ownership, "national_highway");
  // OpenStreetMap carries the current number; KGIS names it "BELLARY ROAD NH 7".
  assert.equal(bellary.highway_name, "NH-44");
  assert.equal(bellary.lookup.local, "national_highway_geometry");
  assert.equal(bellary.lgd, null);
  // MG Road is a city arterial. The KGIS land-cover layer misfiles it as a National
  // Highway at 20 m; the OSM centre lines do not carry that mistake.
  const mgRoad = await geolocator.resolve({ lat: 12.9756, lng: 77.605 });
  assert.equal(mgRoad.road_ownership, "municipal");
  assert.equal(mgRoad.town, "GBA - Central");
});

test("KGIS down: open country inside Karnataka is rural, never municipal", async () => {
  const geolocator = createGeolocator({ fetchImpl: async () => { throw new Error("down"); } });
  // Fields in Tumakuru district, in no urban local body.
  const country = await geolocator.resolve({ lat: 13.45, lng: 77.05 });
  assert.equal(country.road_ownership, "rural");
  assert.equal(country.rural_body, null, "the snapshot holds no panchayat polygons");
  assert.equal(country.lgd, null);
  assert.equal(country.source, "unresolved");
  assert.equal(country.lookup.local, "state_polygon");
  // Hosur, Tamil Nadu, 6 km over the line: inside the envelope, outside the state polygon.
  const hosur = await geolocator.resolve({ lat: 12.7409, lng: 77.8253 });
  assert.equal(hosur.road_ownership, "outside_state");
  assert.equal(hosur.lookup.local, "outside_state_polygon");
  // Electronic City's industrial township is in the KGIS layer with no LGD code, and
  // KGIS itself answers unknown for it. The snapshot says no more than the register.
  const elcita = await geolocator.resolve({ lat: 12.8452, lng: 77.6602 });
  assert.equal(elcita.road_ownership, "unknown");
  assert.equal(elcita.lookup.local, "town_without_lgd");
});

test("KGIS up: its answer wins and the snapshot is not consulted", async () => {
  const geolocator = createGeolocator({
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
test("a panchayat layer outage is answered by the state boundary", async () => {
  const geolocator = createGeolocator({
    fetchImpl: async (url) => {
      if (url.includes("GP_Boundary")) throw new Error("GP layer down");
      return new Response(JSON.stringify({ features: [] }));
    },
  });
  const result = await geolocator.resolve({ lat: 13.45, lng: 77.05 });
  assert.equal(result.lookup.kgis, "available");
  assert.equal(result.lookup.kgis_gp, "not_needed_or_unavailable");
  assert.equal(result.road_ownership, "rural");
  assert.equal(result.lookup.local, "state_polygon");
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

// Seen live on 5 Oct 2026: two installs asked about the same 11 m cell within five
// minutes and the second got the first one's address, and so the first one's tender
// match. A hint typed by one phone is that phone's claim, not a fact about the place.
test("one caller's address hint is never served to the next caller in the cell", async () => {
  const geolocator = createGeolocator({
    fetchImpl: async (url) => new Response(JSON.stringify({ features:
      url.includes("Admin_Dynamic_New")
        ? [{ attributes: { KGISTownName: "Mysuru", LGD_TownCode: 252045 } }] : [] })),
  });
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
  const geolocator = createGeolocator({ fetchImpl: recordedKgis });
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

test("a snapshot that is not the expected bundle is treated as absent", async () => {
  const geolocator = createGeolocator({
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

test("inside Karnataka KGIS is still asked first, and without the snapshot the verdict fails closed", async () => {
  // The other half of the property. If this ever passes by short-circuiting, the envelope
  // has grown over the state it was meant to exclude nothing from.
  for (const [place, [lat, lng, lgd]] of Object.entries({
    "Bengaluru": [12.9716, 77.5946, "305851"],
    "Hubballi": [15.3647, 75.1240, "251893"],
    "Mangaluru": [12.9141, 74.8560, "252021"],
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
      const result = await createGeolocator({ fetchImpl }).resolve({ lat, lng });
      if (name !== "empty") {
        assert.equal(result.lookup.kgis, "unavailable", `${place} with KGIS ${name}`);
        assert.equal(result.source, "kgis_snapshot", `${place} with KGIS ${name}`);
        assert.equal(result.lgd, lgd, `${place} with KGIS ${name}`);
      }
    }
  }
});

test("a coordinate just outside the Karnataka border is still put to KGIS", async () => {
  let kgisCalls = 0;
  const geolocator = createGeolocator({
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
