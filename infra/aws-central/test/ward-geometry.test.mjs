import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

import { WARD_GEOMETRY_FORMAT, WARD_GEOMETRY_PATH, wardAt } from "../service/geolocation.mjs";

// data/karnataka-ward-geometry.json is what the service names a municipal point's ward
// from. It is generated from data/karnataka-ward-polygons.json, a dated copy of the KGIS
// "Ward New" layer, and this suite is what tells a stale bundle from a fresh one.

const root = new URL("../../../", import.meta.url);
const read = (path) => readFileSync(new URL(path, root));
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
const bundleBytes = read("data/karnataka-ward-geometry.json");
const bundle = JSON.parse(bundleBytes);
const WARD_LAYER = "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/Admin_Dynamic_New/MapServer/2";

test("the service reads the ward bundle the deploy packages", () => {
  assert.equal(WARD_GEOMETRY_PATH.pathname, new URL("data/karnataka-ward-geometry.json", root).pathname);
  assert.equal(bundle.format, WARD_GEOMETRY_FORMAT);
  assert.equal(bundle.coordinate_scale, 100_000);
  const deploy = read("infra/aws-central/deploy.sh").toString();
  assert.match(deploy, /cp data\/karnataka-ward-geometry\.json "\$TMP_DIR\/package\/data\/"/);
});

test("the wards are the snapshot's, and the snapshot is the one on disk", () => {
  const snapshotBytes = read("data/karnataka-ward-polygons.json");
  assert.equal(bundle.snapshot_sha256, sha256(snapshotBytes),
    "rebuild: node infra/aws-central/tools/build-karnataka-geometry.mjs");
  const snapshot = JSON.parse(snapshotBytes);
  assert.equal(snapshot.format, "pothole-kgis-ward-polygons");
  assert.equal(snapshot.source, WARD_LAYER);
  assert.equal(bundle.source, WARD_LAYER);
  assert.equal(snapshot.spatial_reference, 4326);
  assert.match(snapshot.retrieved_at, /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(bundle.retrieved_at, snapshot.retrieved_at);
  assert.equal(bundle.source_last_edited, snapshot.source_last_edited);
  // The layer held 7,421 wards on 6 Oct 2026. A snapshot that lost a fifth of them is a
  // truncated download, not a redrawn register.
  assert.ok(snapshot.count >= 6_000, `only ${snapshot.count} wards`);
  assert.equal(snapshot.wards.length, snapshot.count);
  assert.equal(bundle.count, snapshot.count);
  const bundled = Object.values(bundle.towns).reduce((sum, town) => sum + town.wards.length, 0);
  assert.equal(bundled, snapshot.count, "every snapshot ward is in the bundle");
  assert.equal(new Set(snapshot.wards.map((ward) => ward.code)).size, snapshot.count, "no ward code twice");
  for (const [townCode, town] of Object.entries(bundle.towns)) {
    assert.equal(town.bbox.length, 4);
    for (const [code, no, name, bbox, rings] of town.wards) {
      assert.ok(code.startsWith(townCode), `${code} is filed under ${townCode}`);
      assert.ok(no === null || typeof no === "string");
      assert.ok(name === null || (typeof name === "string" && name.trim() === name && name));
      assert.equal(bbox.length, 4);
      assert.ok(rings.length >= 1 && rings.every((ring) => ring.length >= 8));
    }
  }
});

test("the Bengaluru corporations are all there, with named wards", () => {
  // KGIS town codes 20G1 to 20G5 are the five Greater Bengaluru corporations.
  const towns = JSON.parse(read("data/karnataka-town-polygons.json")).towns;
  for (const town of towns.filter((entry) => /^20G/.test(entry.kgis_code || ""))) {
    const wards = bundle.towns[town.kgis_code]?.wards || [];
    assert.ok(wards.length >= 50, `${town.name} has ${wards.length} wards`);
    assert.ok(wards.every((ward) => /^\d+ - \S/.test(ward[2] || "")), `${town.name} wards are "<number> - <name>"`);
  }
  assert.equal(["20G1", "20G2", "20G3", "20G4", "20G5"].every((code) => bundle.towns[code]), true);
  // Every town the wards are filed under is a town the road-ownership snapshot knows,
  // but for one: the 20 wards under code 1006 lie inside Bhatkal's polygon (1002).
  const known = new Set(towns.map((town) => town.kgis_code));
  assert.deepEqual(Object.keys(bundle.towns).filter((code) => !known.has(code)), ["1006"]);
});

test("the package stays small: the ward bundle is under 8 MB", () => {
  assert.ok(bundleBytes.length < 8_000_000, `${bundleBytes.length} bytes`);
});

// Answers recorded from the live KGIS layer on 6 Oct 2026, so the snapshot can be checked
// against the register it was copied from.
test("the polygons place recorded KGIS answers where KGIS placed them", () => {
  const recorded = [
    [12.94572, 77.71055, "20G3041", "41", "41 - Munnenkolalu"],
    [12.95906, 77.7207, "20G3037", "37", "37 - Kundalahalli"],
    [12.99657, 77.62034, "20G1010", "10", "10 - Cox Town"],
    [12.99335, 77.61848, "20G2047", "47", "47 - Pulakeshi Nagar"],
    [12.98222, 77.61563, "20G1007", "7", "7 - K Kamaraj Ward"],
    [12.97298, 77.62247, "20G1024", "24", "24 - Agaram"],
    [12.92718, 77.67314, "20G3047", "47", "47 - Bellanduru"],
    [12.98175, 77.69364, "20G3022", "22", "22 - Mahadevapura"],
  ];
  for (const [lat, lng, code, no, name] of recorded) {
    assert.deepEqual(wardAt(bundle, lat, lng, code.slice(0, 4)), { code, no, name }, `${lat},${lng}`);
    assert.deepEqual(wardAt(bundle, lat, lng, null), { code, no, name }, `${lat},${lng} with no town given`);
  }
  assert.equal(wardAt(bundle, 23.181854, 72.652801, null), null, "Gandhinagar");
  // Venkatapur is filed under town 1006, inside the polygon the town layer calls Bhatkal
  // (1002). The ward polygon holds the point, so the ward is named whichever town asks.
  assert.equal(wardAt(bundle, 14.016705, 74.53339, "1002")?.name, "Venkatapur");
});

test("where two towns' wards overlap, the caller's town wins", () => {
  const square = (x, y) => [x, y, 100, 0, 0, 100, -100, 0];
  const overlapping = {
    coordinate_scale: 100_000,
    towns: {
      A: { bbox: [0, 0, 100, 100], wards: [["A001", "1", "Left", [0, 0, 100, 100], [square(0, 0)]]] },
      B: { bbox: [50, 0, 150, 100], wards: [["B001", "1", "Right", [50, 0, 150, 100], [square(50, 0)]]] },
    },
  };
  assert.equal(wardAt(overlapping, 0.0005, 0.00075, "A").name, "Left");
  assert.equal(wardAt(overlapping, 0.0005, 0.00075, "B").name, "Right");
  assert.equal(wardAt(overlapping, 0.0005, 0.00125, "A").name, "Right", "only B holds this point");
  assert.equal(wardAt(overlapping, 0.0005, 0.002, "A"), null);
});
