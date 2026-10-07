import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import { decodeRun, pointInRings } from "../service/spatial.mjs";
import {
  CONTAINED_SHARE, INDEX_FORMAT, INDEX_PATH, MIN_RING_AREA, SCALE, SLIVER_SHARE, SNAPSHOT_FORMAT, SOURCES, STATE_BOX_PAD,
  WARDS_DIR, WORK_DIR, assembleRings, boxOfRings, buildIndex, buildSnapshot, committable, encodeRing, overlapPairs, parseKml,
  root, sourceById, stateBox, wardHolds,
} from "../tools/snapshot-india-wards.mjs";

// data/wards/<STATE>/<city>.json are ward polygons copied from open sources for cities
// outside Karnataka (tools/snapshot-india-wards.mjs). This suite is what tells a copied
// file from an edited one, and a usable polygon from a broken one.

const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
const index = JSON.parse(readFileSync(INDEX_PATH));
const snapshots = index.snapshots.map((entry) => {
  const bytes = readFileSync(path.join(root, entry.path));
  return { entry, bytes, snapshot: JSON.parse(bytes), source: sourceById(entry.id) };
});

test("the index is the one the tool builds from the files on disk", () => {
  assert.equal(index.format, INDEX_FORMAT);
  assert.equal(index.snapshot_format, SNAPSHOT_FORMAT);
  assert.deepEqual(index, JSON.parse(JSON.stringify(buildIndex())), "rebuild: node infra/aws-central/tools/snapshot-india-wards.mjs --index");
  assert.ok(index.count >= 20, `${index.count} snapshots`);
  assert.equal(index.count, index.snapshots.length);
  assert.equal(index.wards, snapshots.reduce((sum, { snapshot }) => sum + snapshot.count, 0));
});

test("every snapshot is the file the index hashed, and nothing else sits beside them", () => {
  for (const { entry, bytes, snapshot } of snapshots) {
    assert.equal(sha256(bytes), entry.sha256, `${entry.path} was edited after it was indexed`);
    assert.equal(bytes.length, entry.bytes, entry.path);
    assert.equal(snapshot.count, entry.count, entry.path);
    assert.equal(snapshot.provenance.raw_sha256, entry.raw_sha256, entry.path);
  }
  const indexed = new Set(index.snapshots.map((entry) => entry.path));
  for (const state of readdirSync(WARDS_DIR)) {
    const dir = path.join(WARDS_DIR, state);
    if (state.startsWith(".") || !statSync(dir).isDirectory()) continue;
    assert.match(state, /^[A-Z]{2}$/, `${state} is not a State code`);
    for (const file of readdirSync(dir)) {
      assert.ok(indexed.has(path.relative(root, path.join(dir, file))), `${state}/${file} is not in the index`);
    }
  }
});

test("the committed data stays under 60 MB", () => {
  const snapshotBytes = index.snapshots.reduce((sum, entry) => sum + entry.bytes, 0);
  assert.equal(index.bytes, snapshotBytes + (index.localities?.bytes || 0));
  assert.ok(index.bytes < 60_000_000, `${index.bytes} bytes of ward snapshots and localities`);
});

test("where the raw download is still on this machine, its hash is the one recorded", { skip: !existsSync(path.join(WORK_DIR, "raw")) }, () => {
  let checked = 0;
  for (const { entry, snapshot, source } of snapshots) {
    const raw = path.join(WORK_DIR, "raw", `${source.state}-${source.city}.${source.kind === "kml" ? "kml" : "json"}`);
    if (!existsSync(raw)) continue;
    const bytes = readFileSync(raw);
    assert.equal(sha256(bytes), snapshot.provenance.raw_sha256, `${entry.id}: the raw download changed; re-run --city ${entry.id} --offline`);
    assert.equal(bytes.length, snapshot.provenance.raw_bytes, entry.id);
    // The snapshot is exactly what the tool makes of that download.
    const rebuilt = buildSnapshot(source, bytes, {
      retrievedAt: snapshot.retrieved_at, sourceLastEdited: snapshot.source_last_edited, rawFrom: snapshot.provenance.retrieved_from,
    });
    assert.deepEqual(JSON.parse(JSON.stringify(rebuilt)), snapshot, `${entry.id}: the snapshot is not what the tool builds from its raw download`);
    checked += 1;
  }
  assert.ok(checked > 0, "no raw download found to check");
});

test("every snapshot says where it came from and under what licence", () => {
  for (const { entry, snapshot, source } of snapshots) {
    assert.ok(source, `${entry.id} is not in the registry`);
    assert.ok(committable(source), `${entry.id} is committed from a source the registry does not allow`);
    assert.equal(snapshot.format, SNAPSHOT_FORMAT);
    assert.equal(snapshot.schema_version, 1);
    assert.equal(snapshot.spatial_reference, 4326);
    assert.equal(snapshot.coordinate_scale, SCALE);
    assert.equal(snapshot.source, source.url, entry.id);
    assert.equal(snapshot.state_code, source.state);
    assert.equal(snapshot.town_code, `${source.state}-${source.city}`);
    assert.match(snapshot.retrieved_at, /^\d{4}-\d{2}-\d{2}$/);
    const { provenance } = snapshot;
    assert.equal(provenance.licence_status, "open", entry.id);
    assert.match(provenance.licence, /ODbL|CC BY|Public Domain/, `${entry.id}: ${provenance.licence}`);
    assert.doesNotMatch(provenance.licence, /NonCommercial|NC\b/i, entry.id);
    for (const key of ["source_url", "source_page", "publisher", "upstream", "licence_url", "attribution", "vintage"]) {
      assert.ok(typeof provenance[key] === "string" && provenance[key].length > 5, `${entry.id} has no ${key}`);
    }
    assert.match(provenance.raw_sha256, /^[0-9a-f]{64}$/);
    assert.ok(provenance.raw_bytes > 1_000);
    assert.ok(Array.isArray(provenance.caveats));
    // An attribution a licence requires has to name the publisher's work.
    if (/datameet/i.test(provenance.publisher)) assert.match(provenance.attribution, /DataMeet India community/);
    if (/OpenStreetMap/.test(provenance.publisher)) assert.match(provenance.attribution, /OpenStreetMap contributors/);
  }
});

test("no source with an unclear, proprietary or non-commercial licence can be committed", () => {
  for (const source of SOURCES) {
    if (source.licence_status === "open") continue;
    assert.equal(committable(source), false, source.id);
    assert.equal(existsSync(path.join(WARDS_DIR, source.state, `${source.city}.json`)), false, `${source.id} must not be under data/wards/${source.state}/`);
  }
  assert.ok(SOURCES.some((source) => source.licence_status === "proprietary"), "the Esri India layer is recorded as refused");
});

test("every ward has an identity, and every ring is closed, has four points and lies in its State", () => {
  for (const { entry, snapshot } of snapshots) {
    const [west, south, east, north] = stateBox(snapshot.state_code).map((value) => value * SCALE);
    const pad = STATE_BOX_PAD * SCALE;
    const codes = new Set();
    assert.equal(snapshot.wards.length, snapshot.count, entry.id);
    assert.equal(snapshot.wards.filter((ward) => ward.name).length, snapshot.named, entry.id);
    assert.equal(snapshot.wards.filter((ward) => ward.no).length, snapshot.numbered, entry.id);
    for (const ward of snapshot.wards) {
      assert.ok(!codes.has(ward.code), `${ward.code} twice`);
      codes.add(ward.code);
      assert.ok(ward.no !== null || ward.name !== null, `${ward.code} has neither number nor name`);
      if (ward.no !== null) assert.match(ward.no, /^[1-9]\d{0,2}$/, `${ward.code} number ${ward.no}`);
      assert.equal(ward.town_code, snapshot.town_code);
      assert.equal(ward.body, snapshot.body);
      assert.equal(ward.state, snapshot.state_code);
      assert.ok(ward.rings.length >= 1, `${ward.code} has no ring`);
      assert.deepEqual(ward.bbox, boxOfRings(ward.rings), `${ward.code} box`);
      assert.ok(ward.bbox[0] >= west - pad && ward.bbox[1] >= south - pad && ward.bbox[2] <= east + pad && ward.bbox[3] <= north + pad,
        `${ward.code} lies outside ${snapshot.state_code}`);
      for (const ring of ward.rings) {
        assert.ok(ring.length >= 8 && ring.length % 2 === 0, `${ward.code}: a ring of ${ring.length / 2} points`);
        assert.ok(ring.every(Number.isInteger), `${ward.code}: a coordinate is not an integer`);
        const points = decodeRun(ring);
        assert.deepEqual(points[points.length - 1], points[0], `${ward.code}: a ring is not closed`);
        for (let at = 1; at < points.length; at += 1) {
          assert.ok(points[at][0] !== points[at - 1][0] || points[at][1] !== points[at - 1][1], `${ward.code}: a repeated point`);
        }
        let doubled = 0;
        for (let at = 1; at < points.length; at += 1) doubled += points[at - 1][0] * points[at][1] - points[at][0] * points[at - 1][1];
        assert.ok(Math.abs(doubled) / 2 >= MIN_RING_AREA, `${ward.code}: a ring that encloses nothing`);
      }
    }
  }
});

test("no two wards of one body overlap by more than a sliver", () => {
  for (const { entry, snapshot } of snapshots) {
    const pairs = overlapPairs(snapshot.wards);
    const worst = pairs[0] || { share: 0 };
    assert.ok(worst.share <= SLIVER_SHARE,
      `${entry.id}: ${(worst.share * 100).toFixed(1)}% of ${worst.a} also lies in ${worst.b}`);
    assert.equal(snapshot.provenance.overlap.worst_pair_share, Number(worst.share.toFixed(4)), `${entry.id}: recorded overlap`);
    assert.equal(snapshot.provenance.overlap.pairs_over_1_percent, pairs.filter((pair) => pair.share > 0.01).length, entry.id);
    // Every ward can be found: some point of its box is in it, and the service's own
    // point test says so too.
    for (const ward of snapshot.wards) {
      const [x0, y0, x1, y1] = ward.bbox;
      let found = null;
      for (let i = 0; i < 40 && !found; i += 1) {
        for (let j = 0; j < 40 && !found; j += 1) {
          const x = x0 + ((i + 0.5) / 40) * (x1 - x0);
          const y = y0 + ((j + 0.5) / 40) * (y1 - y0);
          if (wardHolds(ward, x, y)) found = [x, y];
        }
      }
      assert.ok(found, `${ward.code} holds no point of its own box`);
      assert.equal(pointInRings(found[0], found[1], ward.rings), true, `${ward.code}: the tool and the service disagree`);
      assert.equal(pointInRings(x0 - 5, y0 - 5, ward.rings), wardHolds(ward, x0 - 5, y0 - 5));
    }
  }
});

// Each point is the OpenStreetMap place node that carries the ward's own name (Geofabrik
// India extract of 6 Oct 2026). The sources were drawn by other hands, so a node inside
// the ward of its name says the file is georeferenced where the city is.
test("named wards sit where their city has them", () => {
  const wardAt = (id, lat, lng) => {
    const { snapshot } = snapshots.find(({ entry }) => entry.id === id);
    const x = lng * SCALE;
    const y = lat * SCALE;
    return snapshot.wards.filter((ward) => x >= ward.bbox[0] && x <= ward.bbox[2] && y >= ward.bbox[1] && y <= ward.bbox[3]
      && pointInRings(x, y, ward.rings)).map((ward) => ward.name || ward.no);
  };
  // Kanpur's file is Web Mercator with no CRS declared: this one misses by kilometres if
  // the conversion is wrong.
  assert.deepEqual(wardAt("UP/kanpur", 26.4066, 80.3240), ["Naubasta East"], "the place node called Naubasta");
  // The file draws zones 5 and 6 as one polygon each, named Panki and Naramau. They are
  // left out: Vijay Nagar and the place called Panki are in no ward, not in a wrong one.
  assert.deepEqual(wardAt("UP/kanpur", 26.4623, 80.2940), [], "Vijay Nagar");
  assert.deepEqual(wardAt("UP/kanpur", 26.4659, 80.2456), [], "Panki");
  assert.deepEqual(wardAt("UP/lucknow", 26.8676, 80.9550), ["Nishat Ganj"]);
  assert.deepEqual(wardAt("GJ/ahmedabad", 23.0360, 72.5643), ["NAVRANGPURA"]);
  assert.deepEqual(wardAt("GJ/vadodara", 22.2826, 73.2147), ["Pratap Nagar"]);
  assert.deepEqual(wardAt("DL/delhi-2017", 28.5603, 77.1628), ["VASANT VIHAR"]);
  assert.deepEqual(wardAt("TG/hyderabad", 17.4462, 78.4630), ["Begumpet"]);
  assert.deepEqual(wardAt("KL/kochi", 9.9676, 76.2422), ["Fort Kochi"]);
  assert.deepEqual(wardAt("SK/gangtok", 27.2942, 88.5896), ["Ranipool"]);
  assert.deepEqual(wardAt("UP/kanpur", 12.9716, 77.5946), [], "Bengaluru is in no Kanpur ward");
});

test("a ring is rounded, closed and kept only if it encloses something", () => {
  const counters = {};
  // 0.00001 degrees is one unit: this square is 100 units a side.
  assert.deepEqual(encodeRing([[77, 12], [77.001, 12], [77.001, 12.001], [77, 12.001], [77, 12]], counters),
    [7_700_000, 1_200_000, 100, 0, 0, 100, -100, 0, 0, -100]);
  assert.deepEqual(counters, {});
  // Left open by the source: closed, and counted.
  assert.deepEqual(encodeRing([[77, 12], [77.001, 12], [77.001, 12.001], [77, 12.001]], counters),
    [7_700_000, 1_200_000, 100, 0, 0, 100, -100, 0, 0, -100]);
  assert.equal(counters.closed, 1);
  // Points that round together are one point.
  assert.deepEqual(encodeRing([[77, 12], [77.000001, 12.000001], [77.001, 12], [77.001, 12.001], [77, 12]], {}),
    [7_700_000, 1_200_000, 100, 0, 0, 100, -100, -100]);
  assert.equal(encodeRing([[77, 12], [77.001, 12], [77, 12]], counters), null);
  assert.equal(counters.dropped, 1);
  // Four points a centimetre apart enclose nothing.
  assert.equal(encodeRing([[77, 12], [77.00001, 12], [77.00001, 12.00003], [77, 12]], counters), null);
  assert.equal(counters.degenerate, 1);
});

test("OpenStreetMap member ways are joined into closed rings, and an open boundary is reported", () => {
  const way = (...points) => points.map(([lon, lat]) => ({ lon, lat }));
  const joined = assembleRings([way([0, 0], [1, 0]), way([1, 1], [1, 0]), way([1, 1], [0, 1], [0, 0])]);
  assert.equal(joined.open, 0);
  assert.deepEqual(joined.rings, [[[1, 1], [0, 1], [0, 0], [1, 0], [1, 1]]]);
  const broken = assembleRings([way([0, 0], [1, 0]), way([1, 0], [1, 1])]);
  assert.deepEqual(broken, { rings: [], open: 1 });
});

test("a KML placemark gives its name, its data fields and every ring of its polygons", () => {
  const kml = Buffer.from(`<kml><Document><Placemark><name>Ward &amp; one</name>
    <ExtendedData><SchemaData><SimpleData name="qwr">5.0</SimpleData></SchemaData><Data name="ZONE"><value>North</value></Data></ExtendedData>
    <MultiGeometry><Polygon><outerBoundaryIs><LinearRing><coordinates>77,12,0 77.1,12,0 77.1,12.1,0 77,12,0</coordinates></LinearRing></outerBoundaryIs>
    <innerBoundaryIs><LinearRing><coordinates>77.02,12.01 77.05,12.01 77.05,12.03 77.02,12.01</coordinates></LinearRing></innerBoundaryIs></Polygon></MultiGeometry>
    </Placemark><Placemark><name>a point</name><Point><coordinates>77,12</coordinates></Point></Placemark></Document></kml>`);
  assert.deepEqual(parseKml(kml), [{
    properties: { name: "Ward & one", qwr: "5.0", ZONE: "North" },
    polygons: [[[[77, 12], [77.1, 12], [77.1, 12.1], [77, 12]], [[77.02, 12.01], [77.05, 12.01], [77.05, 12.03], [77.02, 12.01]]]],
  }]);
});

test("a ward drawn inside another is left out with it, and a strip is measured", () => {
  const square = (x, y, size) => [x, y, size, 0, 0, size, -size, 0, 0, -size];
  const ward = (code, x, y, size) => ({ code, bbox: [x, y, x + size, y + size], rings: [square(x, y, size)] });
  const pairs = overlapPairs([ward("big", 0, 0, 1000), ward("inside", 100, 100, 200), ward("beside", 950, 0, 1000)]);
  assert.equal(pairs[0].a, "inside");
  assert.equal(pairs[0].b, "big");
  assert.equal(pairs[0].share, 1);
  assert.ok(pairs[0].share > CONTAINED_SHARE);
  const strip = pairs.find((pair) => pair.a === "beside" && pair.b === "big");
  assert.ok(strip.share > 0.03 && strip.share < 0.07, `a 50 in 1000 strip measured ${strip.share}`);
  // The real case: DataMeet's Navi Mumbai file draws ward 39 inside ward 41.
  const naviMumbai = snapshots.find(({ entry }) => entry.id === "MH/navi-mumbai").snapshot;
  assert.deepEqual(naviMumbai.provenance.wards_left_out_inside_another_ward, ["39", "41"]);
  assert.equal(naviMumbai.wards.some((entry) => entry.no === "39" || entry.no === "41"), false);
});
