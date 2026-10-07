import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  SNAP_LIMIT_DEGREES, STREET_DIRECTORY, TILE_FORMAT, createLocalAddress,
} from "../service/local-address.mjs";
import {
  REGIONS, buildRegion, createAddressModel, fuzzyBox, junctionsOf, simplify, streetFromFeature,
} from "../tools/build-street-index.mjs";

// service/local-address.mjs answers the street and locality of a point from OpenStreetMap
// data packaged with the service, where geolocation.mjs asked the public Nominatim
// server (300 to 1,000 ms, one request a second, a single point of failure). The module
// exists to give the SAME answer, so every rule below is Nominatim's own, read from its
// source (5.3.0, what nominatim.openstreetmap.org ran on 7 Oct 2026), and the fixtures
// are small pieces of OpenStreetMap shaped like the real thing.

const quiet = { error() {}, log() {} };
const way = (id, tags, coordinates) => ({
  type: "Feature", geometry: { type: "LineString", coordinates }, properties: { "@type": "way", "@id": id, ...tags },
});
const node = (id, tags, lng, lat) => ({
  type: "Feature", geometry: { type: "Point", coordinates: [lng, lat] }, properties: { "@type": "node", "@id": id, ...tags },
});
const square = (lng, lat, half) => [[lng - half, lat - half], [lng + half, lat - half], [lng + half, lat + half],
  [lng - half, lat + half], [lng - half, lat - half]];
const area = (type, id, tags, ring) => ({
  type: "Feature", geometry: { type: "Polygon", coordinates: [ring] }, properties: { "@type": type, "@id": id, ...tags },
});

const PROVENANCE = {
  source: "OpenStreetMap contributors, ODbL 1.0",
  extract_url: "https://download.geofabrik.de/asia/india-261006.osm.pbf",
  extract_date: "2026-10-06",
  extract_md5: "b02c63449ffbeda80757a75496bf7c8f",
};

// A village-sized piece of a city around 12.97 N, 77.59 E: a state, a city, a ward (the
// kind of boundary Bengaluru's suburbs come from), three streets and a few place nodes.
const TOWN = [
  area("relation", 1, { boundary: "administrative", admin_level: "4", name: "Karnataka", "ISO3166-2": "IN-KA" },
    square(77.6, 12.97, 0.5)),
  area("relation", 2, { boundary: "administrative", admin_level: "8", name: "Bengaluru" }, square(77.6, 12.97, 0.2)),
  area("relation", 3, { boundary: "administrative", admin_level: "10", name: "Shanthala Nagar" },
    square(77.6, 12.97, 0.02)),
  node(10, { place: "neighbourhood", name: "Ashok Nagar" }, 77.6002, 12.9712),
  node(11, { place: "neighbourhood", name: "Too Far Layout" }, 77.6002, 12.98),
  way(100, { highway: "secondary", name: "Mahatma Gandhi Road", ref: "NH 4", "name:kn": "ಮಹಾತ್ಮ ಗಾಂಧಿ ರಸ್ತೆ" },
    [[77.595, 12.97], [77.6, 12.97], [77.605, 12.97]]),
  way(101, { highway: "residential" }, [[77.6, 12.9705], [77.6, 12.9725]]),
  way(102, { highway: "residential", name: "Church Street", postal_code: "560001" },
    [[77.5995, 12.9718], [77.6012, 12.9718]]),
  way(103, { highway: "service" }, [[77.6004, 12.9701], [77.6004, 12.9704]]),
  way(104, { highway: "trunk", ref: "NH 44" }, [[77.625, 12.95], [77.625, 12.99]]),
];

const scratch = [];
test.after(() => { for (const directory of scratch) rmSync(directory, { recursive: true, force: true }); });

async function packaged(features, { id = "town", boxes = [[77.55, 12.92, 77.65, 13.02]], postcodes = [] } = {}, into = null) {
  const directory = into || mkdtempSync(path.join(tmpdir(), "local-address-"));
  if (!into) scratch.push(directory);
  const manifest = await buildRegion({
    directory, region: { id, name: id, boxes }, features, postcodes, provenance: PROVENANCE, log() {},
  });
  return { directory, manifest };
}

test("the nearest street within the snap limit answers its road, ref and localities", async () => {
  const { directory } = await packaged(TOWN);
  const local = createLocalAddress({ directory, logger: quiet });
  const found = local.lookup(12.97008, 77.5982);
  assert.equal(found.road, "Mahatma Gandhi Road");
  assert.equal(found.ref, "NH 4");
  assert.equal(found.suburb, "Shanthala Nagar");
  assert.equal(found.neighbourhood, "Ashok Nagar");
  assert.equal(found.city, "Bengaluru");
  assert.equal(found.source, "osm_extract");
  assert.equal(found.extract_date, "2026-10-06");
  assert.equal(found.region, "town");
  assert.ok(found.distance_m > 8 && found.distance_m < 10, `${found.distance_m} m`);
  // The same shape geolocation.mjs reads from the geocoder, so addressFromGeocoder and
  // partsFromGeocoder take it unchanged.
  assert.deepEqual(found.address, {
    road: "Mahatma Gandhi Road", neighbourhood: "Ashok Nagar", suburb: "Shanthala Nagar", city: "Bengaluru",
    state: "Karnataka", "ISO3166-2-lvl4": "IN-KA", country: "India", country_code: "in",
  });
  assert.deepEqual(found.namedetails, { name: "Mahatma Gandhi Road", ref: "NH 4" });
});

test("beyond the snap limit there is no road, only the area the nearest street is in", async () => {
  const { directory } = await packaged(TOWN);
  const local = createLocalAddress({ directory, logger: quiet });
  // 0.007 degrees is Nominatim's own search distance for a street: 0.006 plus the 0.001
  // it adds to collect near ties (reverse.py, lookup_street_poi).
  assert.equal(SNAP_LIMIT_DEGREES, 0.007);
  const inside = local.lookup(12.9769, 77.6);
  assert.equal(inside.road, null);
  assert.equal(inside.basis, "street", "the unnamed street at 0.0044 degrees is the answer, and it has no name");
  const beyond = local.lookup(12.9805, 77.6);
  assert.equal(beyond.road, null);
  assert.equal(beyond.ref, null);
  assert.equal(beyond.basis, "area");
  assert.equal(beyond.suburb, "Shanthala Nagar");
  assert.equal(beyond.neighbourhood, null, "a neighbourhood is a fact about a street, not about open ground near it");
  const nothing = local.lookup(12.93, 77.56);
  assert.equal(nothing.basis, "none");
  assert.deepEqual([nothing.road, nothing.suburb, nothing.city, nothing.postcode], [null, null, null, null]);
});

test("an unnamed street that is nearer wins, as it does in Nominatim, and has no road name", async () => {
  // 227 of the 419 real points checked on 7 Oct 2026 snap to a street with no name. The
  // geocoder answers those with no road at all, and the tender matcher then matches on
  // the locality. A nearer named street is offered beside the answer, never in it.
  const { directory } = await packaged(TOWN);
  const local = createLocalAddress({ directory, logger: quiet });
  const found = local.lookup(12.9716, 77.60003);
  assert.equal(found.road, null);
  assert.equal(found.address.road, undefined);
  assert.equal(found.nearest_named.road, "Church Street");
  assert.ok(found.nearest_named.distance_m > 20 && found.nearest_named.distance_m < 25);
});

test("at an exact tie a named street is preferred to an unnamed one", async () => {
  // Two streets that meet at a node are equally far from any point nearest that node.
  // PostgreSQL returns either. The six such points in the measured set are all answered
  // by: the street class (rank 26) before a path (rank 27), then a name, then the older way.
  const meeting = [
    ...TOWN.slice(0, 3),
    way(201, { highway: "residential" }, [[77.6, 12.97], [77.6, 12.969]]),
    way(202, { highway: "residential", name: "Brigade Road" }, [[77.6, 12.97], [77.601, 12.97]]),
    way(203, { highway: "footway", name: "Brigade Walk" }, [[77.6, 12.97], [77.601, 12.969]]),
  ];
  const { directory } = await packaged(meeting);
  const local = createLocalAddress({ directory, logger: quiet });
  assert.equal(local.lookup(12.9701, 77.5999).road, "Brigade Road");
});

test("a road with a ref and no name is answered by its ref, and a ref passes through", async () => {
  const { directory } = await packaged(TOWN);
  const local = createLocalAddress({ directory, logger: quiet });
  const highway = local.lookup(12.96, 77.6251);
  // Nominatim shows the only name it has. namedetails.ref is what geolocation.mjs reads
  // to open the national highway contracts.
  assert.equal(highway.road, "NH 44");
  assert.equal(highway.ref, "NH 44");
  assert.deepEqual(highway.namedetails, { ref: "NH 44" });
  assert.equal(highway.suburb, null, "outside the ward");
  assert.equal(highway.city, "Bengaluru");
});

test("which ways are streets is Nominatim's import rule", () => {
  const is = (tags, coordinates = [[77.6, 12.97], [77.601, 12.97]]) => Boolean(streetFromFeature(way(1, tags, coordinates)));
  assert.equal(is({ highway: "residential" }), true, "a residential street is a street with or without a name");
  assert.equal(is({ highway: "service" }), false, "an unnamed service road is not imported");
  assert.equal(is({ highway: "service", name: "Mall Access" }), true);
  assert.equal(is({ highway: "footway", name: "Sidewalk", footway: "sidewalk" }), false);
  assert.equal(is({ highway: "footway", name: "Lake Walk" }), true);
  assert.equal(is({ highway: "trunk_link" }), false);
  assert.equal(is({ highway: "trunk", ref: "NH 44" }), true);
  assert.equal(is({ highway: "service", ref: "S1" }), false, "a ref alone does not make a way named");
  assert.equal(is({ highway: "bus_stop", name: "Stop" }), false);
  assert.equal(is({ highway: "construction" }), true);
  assert.equal(streetFromFeature(way(1, { highway: "residential" }, [[77.6, 12.97], [77.601, 12.97]])).rank, 26);
  assert.equal(streetFromFeature(way(1, { highway: "path", name: "Trail" }, [[77.6, 12.97], [77.601, 12.97]])).rank, 27);
});

test("a locality comes from the boundary a street runs through", async () => {
  const model = createAddressModel({ features: TOWN });
  const inWard = model.addressOf(streetFromFeature(TOWN[5]));
  assert.equal(inWard.suburb, "Shanthala Nagar");
  assert.equal(inWard.city, "Bengaluru");
  assert.equal(inWard.state, "Karnataka");
  const outside = model.addressOf(streetFromFeature(TOWN[9]));
  assert.equal(outside.suburb, undefined);
  assert.equal(outside.city, "Bengaluru");
});

test("a place node reaches as far as Nominatim's box for its kind and no farther", async () => {
  // place_node_fuzzy_area: a square of 500 m either way for a neighbourhood, 1 km for a
  // hamlet or quarter, 2 km for a village or suburb, 4 km for a town, 15 km for a city.
  const metres = (box) => ((box[3] - box[1]) / 2) * 110_600;
  assert.ok(Math.abs(metres(fuzzyBox(77.6, 12.97, 24)) - 500) < 2);
  assert.ok(Math.abs(metres(fuzzyBox(77.6, 12.97, 20)) - 1_000) < 4);
  assert.ok(Math.abs(metres(fuzzyBox(77.6, 12.97, 19)) - 2_000) < 8);
  assert.ok(Math.abs(metres(fuzzyBox(77.6, 12.97, 18)) - 4_000) < 16);
  assert.ok(Math.abs(metres(fuzzyBox(77.6, 12.97, 16)) - 15_000) < 60);
  const street = (lat) => way(300, { highway: "residential", name: "1st Cross" }, [[77.6, lat], [77.6005, lat]]);
  const features = [...TOWN.slice(0, 3), node(20, { place: "neighbourhood", name: "Langford Town" }, 77.6, 12.96)];
  const model = createAddressModel({ features });
  assert.equal(model.addressOf(streetFromFeature(street(12.9642))).neighbourhood, "Langford Town", "465 m north");
  assert.equal(model.addressOf(streetFromFeature(street(12.9648))).neighbourhood, undefined, "531 m north");
});

test("the nearer of two place nodes of a kind names the street, and a village is not a suburb", async () => {
  const features = [
    TOWN[0],
    node(30, { place: "village", name: "Hosahalli" }, 77.6, 12.97),
    node(31, { place: "village", name: "Kadahalli" }, 77.61, 12.97),
    node(32, { place: "hamlet", name: "Thotada Mane" }, 77.601, 12.9705),
    way(310, { highway: "unclassified" }, [[77.6035, 12.9702], [77.6035, 12.9712]]),
  ];
  const address = createAddressModel({ features }).addressOf(streetFromFeature(features[4]));
  assert.equal(address.village, "Hosahalli");
  assert.equal(address.hamlet, "Thotada Mane");
  assert.equal(address.suburb, undefined);
  assert.equal(address.city, undefined);
});

test("a suburb node inside a ward of the same rank becomes the more specific of the two", async () => {
  // Nominatim moves an unlinked place node two ranks down when a boundary relation of its
  // own rank contains it. In Bengaluru that makes a mapped suburb node the "suburb" of the
  // answer and leaves the ward under it.
  const features = [...TOWN.slice(0, 3), node(40, { place: "suburb", name: "Richmond Town" }, 77.6, 12.9695),
    way(320, { highway: "residential", name: "Wood Street" }, [[77.5995, 12.9692], [77.6005, 12.9692]])];
  const address = createAddressModel({ features }).addressOf(streetFromFeature(features[4]));
  assert.equal(address.suburb, "Richmond Town");
  // A node that carries the boundary's own name is the boundary's label, not a place.
  const linked = [...TOWN.slice(0, 3), node(41, { place: "suburb", name: "Shanthala Nagar" }, 77.6, 12.9695), features[4]];
  assert.equal(createAddressModel({ features: linked }).addressOf(streetFromFeature(linked[4])).suburb, "Shanthala Nagar");
});

test("a postcode comes from the street's own tag, else the nearest postcode centre within 5 km", async () => {
  const { directory } = await packaged(TOWN, {
    postcodes: [{ postcode: "560025", lng: 77.6, lat: 12.969 }, { postcode: "560042", lng: 77.62, lat: 12.975 },
      { postcode: "560099", lng: 77.8, lat: 12.97 }],
  });
  const local = createLocalAddress({ directory, logger: quiet });
  assert.equal(local.lookup(12.97181, 77.6008).postcode, "560001", "Church Street is tagged");
  assert.equal(local.lookup(12.97008, 77.5982).postcode, "560025");
  assert.equal(local.lookup(12.96, 77.6251).postcode, "560042");
});

test("a mapped postcode area is the postcode of every street whose centre it covers", async () => {
  // Bengaluru's postcodes are mapped as boundary relations. Where one covers a street's
  // centre Nominatim never looks at the postcode centres, however near one is.
  const features = [...TOWN, area("relation", 50, { boundary: "postal_code", postal_code: "560 008", name: "Halasuru - 560008" },
    square(77.598, 12.97, 0.004))];
  const model = createAddressModel({ features, postcodes: [{ postcode: "560025", lng: 77.6, lat: 12.9701 }] });
  assert.equal(model.addressOf(streetFromFeature(TOWN[5])).postcode, "560008", "its centre is inside the area");
  assert.equal(model.addressOf(streetFromFeature(TOWN[7])).postcode, "560001", "its own tag still comes first");
  assert.equal(model.addressOf(streetFromFeature(TOWN[9])).postcode, "560025", "outside the area: the nearest centre within 5 km");
});

test("a street that crosses a ward boundary answers with the ward the point is in", async () => {
  // Nominatim files a street under the ward its middle is in, and then, for each point,
  // prefers a ward that contains the point's place on the street. 11 of the 419 measured
  // answers are a ward the street's middle is not in.
  const half = (id, name, west, east) => area("relation", id, { boundary: "administrative", admin_level: "10", name },
    [[west, 12.96], [east, 12.96], [east, 12.98], [west, 12.98], [west, 12.96]]);
  const features = [TOWN[0], TOWN[1], half(60, "Jogupalya", 77.59, 77.6), half(61, "Halasuru", 77.6, 77.61),
    way(600, { highway: "tertiary", name: "Old Madras Road" }, [[77.598, 12.97], [77.6, 12.97], [77.603, 12.97]])];
  const model = createAddressModel({ features });
  const street = streetFromFeature(features[4]);
  assert.equal(model.addressOf(street).suburb, "Halasuru", "the middle of the street is east of the line");
  assert.equal(model.addressOf(street, 77.599, 12.97).suburb, "Jogupalya");
  assert.deepEqual(model.piecesOf(street).map((piece) => [piece.parts.suburb, Array.from(piece.coords)]),
    [["Jogupalya", [77.598, 12.97, 77.6, 12.97]], ["Halasuru", [77.6, 12.97, 77.603, 12.97]]]);
  const { directory, manifest } = await packaged(features);
  assert.equal(manifest.counts.streets, 1);
  assert.equal(manifest.counts.street_pieces, 2);
  const local = createLocalAddress({ directory, logger: quiet });
  assert.deepEqual([local.lookup(12.97002, 77.599).suburb, local.lookup(12.97002, 77.602).suburb], ["Jogupalya", "Halasuru"]);
  assert.equal(local.lookup(12.97002, 77.599).road, "Old Madras Road");
});

test("simplifying a street keeps every vertex where another street joins", async () => {
  // A side street joins a through street at a vertex that lies almost on the straight
  // line between its neighbours. Dropped, the through street moves half a metre and a
  // point nearest the junction is no longer exactly as far from both, which is how five
  // of the 419 measured points came to snap to another street than Nominatim's.
  const through = way(700, { highway: "residential" }, [[77.6, 12.97], [77.6005, 12.970005], [77.601, 12.97]]);
  const side = way(701, { highway: "residential", name: "Temple Street" }, [[77.6005, 12.970005], [77.6005, 12.969]]);
  const coords = streetFromFeature(through).coords;
  assert.equal(simplify(coords, 2).length, 4, "two points: the middle one is 0.55 m off the line");
  const junction = junctionsOf([streetFromFeature(through), streetFromFeature(side)]);
  assert.equal(junction(77.6005, 12.970005), true);
  assert.equal(junction(77.6, 12.97), false);
  assert.equal(simplify(coords, 2, junction).length, 6);
  const { directory } = await packaged([...TOWN.slice(0, 3), through, side]);
  // North of the junction and nearest to it: both streets are exactly as far, and the
  // named one is the answer.
  assert.equal(createLocalAddress({ directory, logger: quiet }).lookup(12.97006, 77.6005).road, "Temple Street");
});

test("a point inside a street mapped as a closed loop belongs to the loop", async () => {
  // Nominatim imports every closed way as a polygon, so a point inside a block's ring
  // road is at distance zero from it whatever other street is nearer. It only counts a
  // polygon whose centre is within the snap limit, so a ring two kilometres across does
  // not swallow the town inside it.
  const lane = way(401, { highway: "residential", name: "Inner Lane" }, [[77.595, 12.97], [77.605, 12.97]]);
  const small = [...TOWN.slice(0, 3), way(400, { highway: "residential", name: "Block Ring" }, square(77.6, 12.97, 0.002)), lane];
  const block = await packaged(small);
  assert.equal(createLocalAddress({ directory: block.directory, logger: quiet }).lookup(12.9701, 77.6).road, "Block Ring");
  assert.equal(createLocalAddress({ directory: block.directory, logger: quiet }).lookup(12.9731, 77.6).road, "Block Ring",
    "outside the loop it is a line like any other");
  const large = [...TOWN.slice(0, 3), way(400, { highway: "residential", name: "Outer Ring" }, square(77.6, 12.97, 0.015)), lane];
  const ring = await packaged(large);
  const local = createLocalAddress({ directory: ring.directory, logger: quiet });
  assert.equal(local.lookup(12.9701, 77.6).road, "Outer Ring", "its centre is 11 m away");
  assert.equal(local.lookup(12.975, 77.6055).road, "Inner Lane", "its centre is 820 m away, so the ring is no candidate at all");
});

test("a point outside every packaged region is not answered", async () => {
  const { directory } = await packaged(TOWN);
  const local = createLocalAddress({ directory, logger: quiet });
  assert.equal(local.lookup(28.61, 77.21), null, "Delhi is not in this package");
  assert.equal(local.lookup(12.97, 77.7), null, "same tile, outside the region's box");
  assert.equal(local.lookup(Number.NaN, 77.6), null);
});

test("a region is read only when a point falls in it", async () => {
  const { directory } = await packaged(TOWN);
  const delhi = [
    area("relation", 9, { boundary: "administrative", admin_level: "4", name: "Delhi", "ISO3166-2": "IN-DL" }, square(77.21, 28.61, 0.3)),
    way(900, { highway: "primary", name: "Janpath" }, [[77.218, 28.61], [77.218, 28.63]]),
  ];
  await packaged(delhi, { id: "delhi", boxes: [[77.1, 28.5, 77.3, 28.7]] }, directory);
  const reads = [];
  const local = createLocalAddress({
    directory, logger: quiet, readFile: (file) => { reads.push(path.relative(directory, file)); return readFileSync(file); },
  });
  assert.deepEqual(reads, [], "nothing is read before the first lookup");
  assert.equal(local.lookup(12.97008, 77.5982).road, "Mahatma Gandhi Road");
  assert.ok(reads.every((file) => !file.startsWith("delhi") || file === path.join("delhi", "manifest.json")), reads.join(", "));
  assert.equal(reads.filter((file) => file.endsWith(".bin")).length, 1);
  const before = reads.length;
  assert.equal(local.lookup(12.9716, 77.60003).road, null);
  assert.equal(reads.length, before, "a second lookup in a loaded tile reads nothing");
  const janpath = local.lookup(28.62, 77.2181);
  assert.equal(janpath.road, "Janpath");
  assert.equal(janpath.address.state, "Delhi");
  assert.equal(janpath.region, "delhi");
  assert.equal(reads.filter((file) => file.endsWith(".bin")).length, 2);
  assert.deepEqual(local.stats().regions.map((region) => region.id).sort(), ["delhi", "town"]);
});

test("a corrupt tile is refused, and the refusal names the hash the manifest recorded", async () => {
  const { directory, manifest } = await packaged(TOWN);
  const [tile] = Object.values(manifest.tiles);
  const file = path.join(directory, "town", tile.file);
  const bytes = readFileSync(file);
  assert.equal(createHash("sha256").update(bytes).digest("hex"), tile.sha256);
  assert.equal(bytes.length, tile.bytes);
  bytes[bytes.length - 3] ^= 0x01;
  writeFileSync(file, bytes);
  const errors = [];
  const local = createLocalAddress({ directory, logger: { error: (line) => errors.push(JSON.parse(line)), log() {} } });
  assert.equal(local.lookup(12.97008, 77.5982), null);
  assert.equal(local.lookup(12.97008, 77.5982), null);
  assert.equal(errors.length, 1, "logged once, not per lookup");
  assert.equal(errors[0].event, "local_address_tile_refused");
  assert.equal(errors[0].expected_sha256, tile.sha256);
  assert.equal(errors[0].actual_sha256, createHash("sha256").update(bytes).digest("hex"));
  assert.equal(errors[0].region, "town");
  // A file of the right hash that is not a tile is refused as well.
  const junk = Buffer.from("not a tile");
  writeFileSync(file, junk);
  const forged = JSON.parse(readFileSync(path.join(directory, "town", "manifest.json")));
  forged.tiles[Object.keys(forged.tiles)[0]].sha256 = createHash("sha256").update(junk).digest("hex");
  forged.tiles[Object.keys(forged.tiles)[0]].bytes = junk.length;
  writeFileSync(path.join(directory, "town", "manifest.json"), JSON.stringify(forged));
  const again = createLocalAddress({ directory, logger: quiet });
  assert.equal(again.lookup(12.97008, 77.5982), null);
});

test("a missing directory answers nothing and says so once", () => {
  const errors = [];
  const local = createLocalAddress({ directory: "/nonexistent/streets", logger: { error: (line) => errors.push(JSON.parse(line)), log() {} } });
  assert.equal(local.lookup(12.97, 77.59), null);
  assert.equal(local.lookup(12.98, 77.59), null);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].event, "local_address_unavailable");
});

test("the manifest carries the provenance of the extract", async () => {
  const { manifest } = await packaged(TOWN);
  assert.equal(manifest.format, "pothole-street-address-region");
  assert.deepEqual(
    [manifest.provenance.source, manifest.provenance.extract_url, manifest.provenance.extract_date, manifest.provenance.extract_md5],
    [PROVENANCE.source, PROVENANCE.extract_url, PROVENANCE.extract_date, PROVENANCE.extract_md5]);
  assert.equal(manifest.provenance.licence, "https://opendatacommons.org/licenses/odbl/1-0/");
  assert.equal(manifest.tile_format, TILE_FORMAT);
  assert.equal(manifest.counts.streets, 4, "the unnamed service road is not a street");
  assert.equal(manifest.counts.named_streets, 3);
  assert.equal(manifest.counts.areas, 3);
  // A boundary reaches a build twice when it is both in the region's cut and among the
  // boundaries around it, and a closed way with an area tag is exported in two shapes.
  const twice = await packaged([...TOWN, TOWN[2], TOWN[5]]);
  assert.equal(twice.manifest.counts.areas, 3);
  assert.equal(twice.manifest.counts.streets, 4);
});

test("adding a state or a city box is one registry line", () => {
  assert.ok(REGIONS.length >= 10);
  for (const region of REGIONS) {
    assert.match(region.id, /^[a-z][a-z0-9-]*$/);
    assert.ok(region.relation || region.boxes?.length, `${region.id} names a boundary relation or a box`);
  }
  assert.deepEqual(REGIONS.slice(0, 1).map((region) => region.id), ["karnataka"]);
  for (const id of ["pune", "delhi-ncr", "hyderabad", "visakhapatnam", "mumbai", "chennai", "kolkata", "ahmedabad", "gandhinagar"]) {
    assert.ok(REGIONS.some((region) => region.id === id), id);
  }
});

// The packaged bundles themselves, when they are in the checkout.
const packagedRoot = fileURLToPath(STREET_DIRECTORY);
const regions = existsSync(packagedRoot)
  ? readdirSync(packagedRoot).filter((name) => existsSync(path.join(packagedRoot, name, "manifest.json"))) : [];

test("every packaged tile is the one its manifest hashed", { skip: regions.length === 0 }, () => {
  for (const region of regions) {
    const manifest = JSON.parse(readFileSync(path.join(packagedRoot, region, "manifest.json")));
    assert.match(manifest.provenance.extract_url, /^https:\/\/download\.geofabrik\.de\//);
    assert.match(manifest.provenance.extract_date, /^\d{4}-\d{2}-\d{2}$/);
    for (const tile of Object.values(manifest.tiles)) {
      const bytes = readFileSync(path.join(packagedRoot, region, tile.file));
      assert.equal(bytes.length, tile.bytes, `${region}/${tile.file}`);
      assert.equal(createHash("sha256").update(bytes).digest("hex"), tile.sha256, `${region}/${tile.file}`);
      assert.ok(bytes.length < 50_000_000, "under the size a repository file may be");
    }
  }
});

test("the packaged Bengaluru answers what Nominatim answered on 6 Oct 2026", { skip: !regions.includes("karnataka") }, () => {
  const local = createLocalAddress({ logger: quiet });
  // Three of the 419 cached answers: a named street, an unnamed one, and a ward centre.
  const cherry = local.lookup(12.92718, 77.67314);
  assert.equal(cherry.road, "Cherry Lane");
  assert.equal(cherry.neighbourhood, "Green Glen Layout");
  assert.equal(cherry.suburb, "Bellanduru");
  assert.equal(cherry.city, "Bengaluru");
  assert.equal(cherry.postcode, "560103");
  assert.equal(cherry.address.state, "Karnataka");
  assert.equal(cherry.address["ISO3166-2-lvl4"], "IN-KA");
  const cross = local.lookup(12.95826, 77.72103);
  assert.equal(cross.road, "15th Cross Road");
  assert.equal(cross.address.quarter, "BEML Layout 6th Stage");
});

test("a packaged lookup is under half a millisecond warm and a tile loads in under 150 ms", { skip: !regions.includes("karnataka") }, () => {
  // The first lookup of a fresh instance reads every region's manifest and the largest
  // tile there is (central Bengaluru, 1.9 MB), hashes it and indexes it. Measured on
  // 7 Oct 2026: 40 to 120 ms in a new process, 20 to 35 ms once the code is warm, and
  // 0.02 ms a lookup after that. The best of three is taken so that a busy machine does
  // not fail the gate; a real regression is slower every time.
  let cold = Infinity;
  let local = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    local = createLocalAddress({ logger: quiet });
    const started = performance.now();
    assert.ok(local.lookup(12.9716, 77.5946));
    cold = Math.min(cold, performance.now() - started);
  }
  assert.ok(cold < 150, `cold ${cold.toFixed(1)} ms`);
  let best = Infinity;
  for (let round = 0; round < 5; round += 1) {
    const from = performance.now();
    for (let index = 0; index < 2_000; index += 1) {
      local.lookup(12.9 + ((index * 37) % 1_000) / 10_000, 77.55 + ((index * 61) % 1_000) / 10_000);
    }
    best = Math.min(best, (performance.now() - from) / 2_000);
  }
  assert.ok(best < 0.5, `warm ${best.toFixed(4)} ms a lookup`);
});
