import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

import { HIGHWAY_BUFFER_METRES, LOCAL_GEOMETRY_FORMAT, LOCAL_GEOMETRY_PATH, classifyLocally } from "../service/geolocation.mjs";
import {
  CHUNK_EDGES, HIGHWAY_LAYERS, OWNERSHIP_LAYERS, openOwnershipBundle, polygonAttributes, polygonRings, polygonsAt,
} from "../service/local-ownership.mjs";
import { metresPerDegree } from "../service/spatial.mjs";
import { KGIS_LAYERS } from "../tools/kgis-layers.mjs";
import { tidyRing } from "../tools/ownership-bundle-writer.mjs";

// data/karnataka-ownership.bin is what the service answers every Karnataka road
// ownership lookup from. It is a copy of registers: the KGIS town, highway and panchayat
// layers and the app's pinned state boundary. This suite is what tells a stale or edited
// bundle from a fresh one (every hash it records has to match what it holds and what the
// repo pins today) and what holds the reader to its budget.

const root = new URL("../../../", import.meta.url);
const read = (path) => readFileSync(new URL(path, root));
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
const bundleBytes = read("data/karnataka-ownership.bin");
const bundle = openOwnershipBundle(bundleBytes);
const layer = (name) => bundle.layers[bundle.layerIndex[name]];
const bytesOf = (view) => Buffer.from(view.buffer, view.byteOffset, view.byteLength);
// [west, south, east, north] of a polygon, in the bundle's units.
function boxOf(polygon) {
  const box = [Infinity, Infinity, -Infinity, -Infinity];
  for (const ring of polygonRings(bundle, polygon)) {
    for (let index = 0; index < ring.length; index += 2) {
      if (ring[index] < box[0]) box[0] = ring[index];
      if (ring[index + 1] < box[1]) box[1] = ring[index + 1];
      if (ring[index] > box[2]) box[2] = ring[index];
      if (ring[index + 1] > box[3]) box[3] = ring[index + 1];
    }
  }
  return box;
}

test("the service reads the bundle the deploy packages", () => {
  assert.equal(LOCAL_GEOMETRY_PATH.pathname, new URL("data/karnataka-ownership.bin", root).pathname);
  assert.equal(bundle.header.format, LOCAL_GEOMETRY_FORMAT);
  assert.deepEqual(bundle.layers.map((entry) => entry.name), OWNERSHIP_LAYERS);
  const deploy = read("infra/aws-central/deploy.sh").toString();
  assert.match(deploy, /cp data\/karnataka-ownership\.bin "\$TMP_DIR\/package\/data\/"/);
  assert.doesNotMatch(deploy, /karnataka-local-geometry/, "the 6 Oct 2026 fallback file is gone");
});

test("every KGIS layer is whole: as many polygons as KGIS counted, from the layer the live lookup read", () => {
  // Counted by KGIS (returnCountOnly) when each layer was read. A refresh that changes
  // one of these changes this line too, on purpose.
  const expected = { national_highway: 93, state_highway: 7_613, district_highway: 15_192, gram_panchayat: 7_581 };
  for (const [name, count] of Object.entries(expected)) {
    const entry = layer(name);
    assert.equal(entry.source, KGIS_LAYERS[name].url, name);
    assert.equal(entry.layer_id, KGIS_LAYERS[name].layer_id, name);
    assert.equal(entry.kgis_count, count, `${name}: KGIS's own count`);
    assert.equal(entry.count, count, `${name}: features read`);
    assert.equal(entry.polygons, count, `${name}: polygons stored`);
    assert.match(entry.retrieved_at, /^20\d\d-\d\d-\d\d$/, name);
    assert.match(entry.raw_sha256, /^[0-9a-f]{64}$/, `${name}: hash of the download`);
    assert.ok(entry.names.length > 0, `${name}: KGIS names some of its polygons`);
    assert.equal(entry.polygons - entry.unnamed, [...bundle.polyAttr.subarray(entry.first_polygon, entry.first_polygon + entry.polygons)]
      .filter((attribute) => attribute !== 0xffffffff).length, `${name}: named polygons`);
  }
  // The highway layers publish no edit date; the panchayat layer does.
  assert.match(layer("gram_panchayat").source_last_edited, /^20\d\d-\d\d-\d\d$/);
  // The live lookup's own endpoints, so the copy cannot drift to another layer unnoticed.
  const service = read("infra/aws-central/service/geolocation.mjs").toString();
  for (const name of Object.keys(expected)) {
    assert.ok(service.includes(`"${KGIS_LAYERS[name].url}/query"`), `${name} is the layer geolocation.mjs queried`);
  }
});

test("each layer's recorded hash is the hash of what the bundle holds", () => {
  for (const entry of bundle.layers) {
    const end = entry.first_polygon + entry.polygons;
    const hash = createHash("sha256");
    hash.update(bytesOf(bundle.polyRings.subarray(entry.first_polygon, end)));
    hash.update(bytesOf(bundle.ringVertices.subarray(entry.first_ring, entry.first_ring + entry.rings)));
    hash.update(bytesOf(bundle.coords.subarray(entry.coords.offset, entry.coords.offset + entry.coords.length)));
    hash.update(bytesOf(bundle.polyAttr.subarray(entry.first_polygon, end)));
    hash.update(bytesOf(bundle.polyObjectId.subarray(entry.first_polygon, end)));
    hash.update(JSON.stringify(entry.names || entry.towns || null));
    assert.equal(hash.digest("hex"), entry.content_sha256,
      `${entry.name}: rebuild with node infra/aws-central/tools/build-karnataka-geometry.mjs`);
  }
});

test("the town polygons are the snapshot's, and the snapshot is the one on disk", () => {
  const towns = layer("town");
  const snapshotBytes = read("data/karnataka-town-polygons.json");
  assert.equal(towns.snapshot_sha256, sha256(snapshotBytes), "rebuild: node infra/aws-central/tools/build-karnataka-geometry.mjs");
  const snapshot = JSON.parse(snapshotBytes);
  assert.equal(snapshot.format, "pothole-kgis-town-polygons");
  assert.equal(snapshot.source, "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/Admin_Dynamic_New/MapServer/1");
  assert.equal(snapshot.spatial_reference, 4326);
  // docs/SOURCES.md: the layer holds exactly 319 polygons, one per urban local body.
  assert.equal(snapshot.count, 319);
  assert.equal(towns.count, 319);
  assert.equal(towns.polygons, 319);
  assert.equal(towns.towns.length, 319);
  assert.equal(towns.with_lgd, 318, "every body but ELCITA has an LGD code");
  const codes = towns.towns.map((town) => town.lgd).filter((lgd) => lgd !== null);
  assert.equal(new Set(codes).size, 318, "no LGD code twice");
  snapshot.towns.forEach((town, index) => {
    const polygon = towns.first_polygon + index;
    const record = polygonAttributes(bundle, polygon);
    assert.equal(record.name, town.name);
    assert.equal(record.lgd, town.lgd);
    assert.equal(record.objectid, town.objectid);
    assert.equal(polygonRings(bundle, polygon).length, town.rings.length, town.name);
  });
});

test("every town in the roster and every body the app can write to has a polygon", () => {
  const polygons = new Map(layer("town").towns.map((town) => [String(town.lgd), town]));
  const roster = JSON.parse(read("data/karnataka-towns.json")).towns;
  for (const town of roster) {
    if (town.lgd == null) continue;
    assert.equal(polygons.get(String(town.lgd))?.name, town.name, `LGD ${town.lgd}`);
  }
  const bodies = JSON.parse(read("data/karnataka-bodies.json")).bodies;
  for (const lgd of Object.keys(bodies)) assert.ok(polygons.has(lgd), `body ${lgd} has no polygon`);
});

test("the state boundary is the pinned in-ka-state-routing pack", () => {
  const state = layer("state");
  const manifests = readdirSync(new URL("static/", root))
    .map((name) => name.match(/^pack-manifest-v(\d+)\.(\d+)\.json$/)).filter(Boolean)
    .sort((left, right) => Number(left[1]) - Number(right[1]) || Number(left[2]) - Number(right[2]));
  const newest = manifests[manifests.length - 1][0];
  assert.equal(state.manifest, newest);
  const resource = JSON.parse(read(`static/${newest}`)).resources["in-ka-state-routing"];
  assert.equal(state.pack_sha256, resource.sha256);
  assert.equal(sha256(read(`docs/${resource.path}`)), resource.sha256);
  assert.equal(state.osm_relation_id, 2019939);
  assert.equal(state.polygons, 1);
  // Karnataka's extent: lat 11.59 to 18.46, lng 74.04 to 78.59.
  assert.deepEqual(boxOf(state.first_polygon).map((value) => Math.round((value / bundle.scale) * 100)), [7405, 1159, 7859, 1848]);
});

test("every ring is closed and every edge is short enough for the reader's exact arithmetic", () => {
  let rings = 0;
  for (let polygon = 0; polygon < bundle.polyRings.length; polygon += 1) {
    for (const ring of polygonRings(bundle, polygon)) {
      rings += 1;
      if (ring.length < 4 || ring[0] !== ring[ring.length - 2] || ring[1] !== ring[ring.length - 1]) {
        assert.fail(`polygon ${polygon} has an open ring`);
      }
      for (let index = 2; index < ring.length; index += 2) {
        const span = Math.max(Math.abs(ring[index] - ring[index - 2]), Math.abs(ring[index + 1] - ring[index - 1]));
        if (span >= 2 ** 17) assert.fail(`polygon ${polygon} has an edge of ${span} units`);
      }
    }
  }
  assert.equal(rings, bundle.ringVertices.length);
  assert.equal(bundle.header.chunk_edges, CHUNK_EDGES);
  assert.ok(bundle.header.max_buffer_metres >= HIGHWAY_BUFFER_METRES);
});

// Storing a ring may drop a vertex only when the ring is the same set of points without it.
test("a stored ring loses repeated and exactly collinear vertices and nothing else", () => {
  const square = [[0, 0], [5, 0], [5, 0], [10, 0], [10, 10], [4, 4], [0, 10], [0, 0]];
  assert.deepEqual(tidyRing(square), [[0, 0], [10, 0], [10, 10], [4, 4], [0, 10], [0, 0]]);
  // A spike doubles back along the same line: its tip is a corner, not a point on an edge.
  assert.deepEqual(tidyRing([[0, 0], [10, 0], [4, 0], [4, 5], [0, 0]]), [[0, 0], [10, 0], [4, 0], [4, 5], [0, 0]]);
  // An unclosed ring is closed; a sliver that fell onto one grid point stays as that point.
  assert.deepEqual(tidyRing([[0, 0], [3, 0], [3, 3]]), [[0, 0], [3, 0], [3, 3], [0, 0]]);
  assert.deepEqual(tidyRing([[7, 7], [7, 7], [7, 7]]), [[7, 7], [7, 7]]);
  assert.deepEqual(tidyRing([[1, 1], [2, 2], [1, 1]]), [[1, 1], [2, 2], [1, 1]]);
});

// The grid is an index, not a second opinion: a lookup through it has to give exactly
// what testing the point against every polygon gives.
test("a lookup through the grid finds what a scan of every polygon finds", () => {
  const scale = bundle.scale;
  const count = bundle.polyRings.length;
  const boxes = new Float64Array(count * 4);
  const decoded = new Map();
  for (let polygon = 0; polygon < count; polygon += 1) boxes.set(boxOf(polygon), polygon * 4);
  const scan = (lat, lng, buffer) => {
    const px = lng * scale;
    const py = lat * scale;
    const perDegree = metresPerDegree(lat);
    const mx = perDegree.lng / scale;
    const my = perDegree.lat / scale;
    const found = [];
    for (let polygon = 0; polygon < count; polygon += 1) {
      const buffered = bundle.buffered[bundle.polyLayer[polygon]];
      const padX = buffered ? (buffer + 1) / mx : 0;
      const padY = buffered ? (buffer + 1) / my : 0;
      if (px < boxes[polygon * 4] - padX || px > boxes[polygon * 4 + 2] + padX
          || py < boxes[polygon * 4 + 1] - padY || py > boxes[polygon * 4 + 3] + padY) continue;
      if (!decoded.has(polygon)) decoded.set(polygon, polygonRings(bundle, polygon));
      let inside = false;
      let nearest = Infinity;
      for (const ring of decoded.get(polygon)) {
        for (let index = 2; index < ring.length; index += 2) {
          const xi = ring[index];
          const yi = ring[index + 1];
          const xj = ring[index - 2];
          const yj = ring[index - 1];
          if ((yi > py) !== (yj > py) && px < xi + ((py - yi) * (xj - xi)) / (yj - yi)) inside = !inside;
          const ux = (xj - px) * mx;
          const uy = (yj - py) * my;
          const vx = (xi - xj) * mx;
          const vy = (yi - yj) * my;
          const length = vx * vx + vy * vy;
          const turn = length ? Math.max(0, Math.min(1, -(ux * vx + uy * vy) / length)) : 0;
          nearest = Math.min(nearest, (ux + turn * vx) ** 2 + (uy + turn * vy) ** 2);
        }
      }
      if (inside) found.push(`${polygon}:0`);
      else if (buffered && nearest <= buffer * buffer) found.push(`${polygon}:${Math.sqrt(nearest).toFixed(3)}`);
    }
    return found.sort().join(" ");
  };
  let state = 20261007;
  const random = () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296;
  };
  const points = [];
  for (let index = 0; index < 80; index += 1) points.push([11.6 + random() * 6.8, 74.1 + random() * 4.4]);
  // Where an index goes wrong: next to vertices, a few metres from edges, on cell lines.
  const first = layer("national_highway").first_polygon;
  const highways = HIGHWAY_LAYERS.reduce((sum, name) => sum + layer(name).polygons, 0);
  for (let index = 0; index < 180; index += 1) {
    const polygon = first + Math.floor(random() * highways);
    const rings = polygonRings(bundle, polygon);
    const ring = rings[Math.floor(random() * rings.length)];
    const [x, y] = ring.subarray(2 * Math.floor(random() * (ring.length / 2)));
    const jitter = [0.02, 20, 200][index % 3];
    points.push([(y + (random() - 0.5) * jitter) / scale, (x + (random() - 0.5) * jitter) / scale]);
  }
  for (let index = 0; index < 40; index += 1) {
    points.push([Math.round((12 + random() * 5) * 100) / 100 + (index % 2 ? 0 : (random() - 0.5) * 1e-6),
      Math.round((75 + random() * 3) * 100) / 100 + (index % 3 ? 0 : (random() - 0.5) * 1e-6)]);
  }
  for (const [lat, lng] of points) {
    for (const buffer of [0, HIGHWAY_BUFFER_METRES, 40]) {
      const got = polygonsAt(bundle, lat, lng, buffer)
        .map((hit) => `${hit.polygon}:${hit.metres ? hit.metres.toFixed(3) : 0}`).sort().join(" ");
      assert.equal(got, scan(lat, lng, buffer), `${lat},${lng} at ${buffer} m`);
    }
  }
});

// The buffer is "within N metres of the polygon, on the ground", the rule KGIS applies to
// its own polygons. Walk out from a carriageway edge and the verdict has to flip at N.
test("a highway is matched within the buffer of its polygon and not beyond it", () => {
  const scale = bundle.scale;
  let state = 5;
  const random = () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296;
  };
  let walked = 0;
  for (const name of HIGHWAY_LAYERS) {
    const entry = layer(name);
    for (let sample = 0; sample < 60; sample += 1) {
      const polygon = entry.first_polygon + Math.floor(random() * entry.polygons);
      const ring = polygonRings(bundle, polygon)[0];
      const index = 2 * (1 + Math.floor(random() * (ring.length / 2 - 1)));
      const [ax, ay, bx, by] = ring.subarray(index - 2, index + 2);
      const lat = (ay + by) / 2 / scale;
      const perDegree = metresPerDegree(lat);
      const ex = ((bx - ax) / scale) * perDegree.lng;
      const ey = ((by - ay) / scale) * perDegree.lat;
      const length = Math.hypot(ex, ey);
      if (length < 1) continue;
      const at = (metres) => [lat + (metres * (ex / length)) / perDegree.lat,
        (ax + bx) / 2 / scale + (metres * (-ey / length)) / perDegree.lng];
      // Outward is the side where, 30 m out, no highway polygon is nearer than this edge:
      // then along the walk this polygon is as far away as the walk has gone, and no
      // other highway polygon is nearer.
      const side = [1, -1].find((sign) => !polygonsAt(bundle, ...at(sign * 30), 29.95).some((hit) => bundle.buffered[hit.layer]));
      if (!side) continue;
      for (const metres of [1, 3, 4.9]) {
        const hit = polygonsAt(bundle, ...at(side * metres), HIGHWAY_BUFFER_METRES).find((entry) => entry.polygon === polygon);
        assert.ok(hit && Math.abs(hit.metres - metres) < 0.01, `${name} polygon ${polygon} at ${metres} m: ${hit?.metres}`);
        assert.equal(polygonsAt(bundle, ...at(side * metres), 0).some((entry) => entry.polygon === polygon), false,
          "outside the polygon itself");
        // This class, unless a national or state highway shares the carriageway.
        const verdict = classifyLocally(bundle, ...at(side * metres)).road_ownership;
        assert.ok(HIGHWAY_LAYERS.includes(verdict) && HIGHWAY_LAYERS.indexOf(verdict) <= HIGHWAY_LAYERS.indexOf(name),
          `${name} at ${metres} m answered ${verdict}`);
      }
      for (const metres of [5.1, 7, 20]) {
        assert.equal(HIGHWAY_LAYERS.includes(classifyLocally(bundle, ...at(side * metres)).road_ownership), false, `${name} at ${metres} m`);
      }
      walked += 1;
    }
  }
  assert.ok(walked >= 60, `only ${walked} edges had a clear side`);
});

// Answers recorded from KGIS itself, so the copy can be checked against the register.
test("the polygons place recorded KGIS answers where KGIS placed them", () => {
  const townAt = (lat, lng) => {
    const hit = polygonsAt(bundle, lat, lng, 0).find((entry) => entry.layer === bundle.layerIndex.town);
    return hit ? polygonAttributes(bundle, hit.polygon) : undefined;
  };
  // docs/SOURCES.md, the documented curl: MYSURU, CC, LGD 252045.
  assert.equal(townAt(12.2958, 76.6394)?.lgd, 252045);
  // geolocation.test.mjs recordings of 21 Sep 2026.
  assert.equal(townAt(12.9756, 77.605)?.name, "GBA - Central");
  assert.equal(townAt(13.00271, 77.58406)?.name, "GBA - West");
  assert.equal(townAt(15.3647, 75.124)?.name, "HUBLI DHARWAD");
  assert.equal(townAt(23.181854, 72.652801), undefined, "Gandhinagar");
  // The same recordings, for the highway layers: the smallest buffer at which KGIS
  // matched each point.
  const highwaysAt = (lat, lng, buffer) => polygonsAt(bundle, lat, lng, buffer)
    .filter((hit) => bundle.buffered[hit.layer]).map((hit) => polygonAttributes(bundle, hit.polygon));
  assert.deepEqual(highwaysAt(12.9756, 77.605, 15), [], "MG Road is not within 15 m of a highway polygon");
  assert.deepEqual(highwaysAt(12.9756, 77.605, 20).map((entry) => [entry.layer, entry.objectid, entry.name]),
    [["national_highway", 3059, "MAHATMA GANDHI ROAD"]]);
  assert.deepEqual(highwaysAt(15.3647, 75.124, 5), []);
  assert.deepEqual(highwaysAt(15.3647, 75.124, 10).map((entry) => entry.layer), ["state_highway"]);
  assert.deepEqual(highwaysAt(13.00271, 77.58406, 0), [], "the land cover stops short of the carriageway");
  assert.deepEqual(highwaysAt(13.00271, 77.58406, 5).map((entry) => [entry.layer, entry.name]),
    [["national_highway", "BELLARY ROAD NH 7"]]);
});

// The owner's budget, 7 Oct 2026: a lookup in single-digit milliseconds, a cold start
// that does not notice the file, and a package that stays deployable.
test("the bundle loads and answers within its budget", () => {
  const megabytes = bundleBytes.length / 1e6;
  assert.ok(megabytes < 48, `the bundle is ${megabytes.toFixed(1)} MB; the Lambda zip must stay under 50 MB`);
  const opens = [];
  for (let run = 0; run < 3; run += 1) {
    const heapBefore = process.memoryUsage().heapUsed;
    const started = performance.now();
    openOwnershipBundle(read("data/karnataka-ownership.bin"));
    opens.push(performance.now() - started);
    assert.ok((process.memoryUsage().heapUsed - heapBefore) / 1e6 < 150, "opening the bundle took over 150 MB of heap");
  }
  assert.ok(Math.min(...opens) < 150, `reading and opening took ${Math.min(...opens).toFixed(0)} ms`);
  let state = 99;
  const random = () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296;
  };
  const points = Array.from({ length: 20_000 }, () => [11.6 + random() * 6.8, 74.1 + random() * 4.4]);
  for (const [lat, lng] of points.slice(0, 2_000)) classifyLocally(bundle, lat, lng);
  const started = performance.now();
  for (const [lat, lng] of points) classifyLocally(bundle, lat, lng);
  const mean = (performance.now() - started) / points.length;
  assert.ok(mean < 0.1, `a warm lookup averaged ${(mean * 1000).toFixed(0)} microseconds`);
  // The worst case is a property of the file, not of luck: the cell that lists the most
  // edges. Timed as the best of several runs, so a garbage collection is not the result.
  const { grid } = bundle;
  let heaviest = { edges: 0, cell: 0 };
  for (let cell = 0; cell < grid.cols * grid.rows; cell += 1) {
    let edges = 0;
    for (let index = bundle.cellChunkStart[cell]; index < bundle.cellChunkStart[cell + 1]; index += 1) {
      const run = bundle.cellChunkRuns[index];
      for (let chunk = run >>> 8; chunk <= (run >>> 8) + (run & 0xff); chunk += 1) edges += bundle.chunkEdges[chunk];
    }
    if (edges > heaviest.edges) heaviest = { edges, cell };
  }
  const lat = (grid.y0 + (Math.floor(heaviest.cell / grid.cols) + 0.3) * grid.cell) / bundle.scale;
  const lng = (grid.x0 + ((heaviest.cell % grid.cols) + 0.7) * grid.cell) / bundle.scale;
  let best = Infinity;
  for (let run = 0; run < 30; run += 1) {
    const before = performance.now();
    classifyLocally(bundle, lat, lng);
    best = Math.min(best, performance.now() - before);
  }
  assert.ok(best < 1, `the heaviest cell (${heaviest.edges} edges, ${lat.toFixed(3)},${lng.toFixed(3)}) took ${best.toFixed(2)} ms`);
});
