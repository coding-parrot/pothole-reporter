#!/usr/bin/env node
// Builds data/streets/<region>/: the street and locality of every mapped street in a
// region, which service/local-address.mjs answers from where geolocation.mjs asked the
// public Nominatim server. Source: OpenStreetMap (ODbL 1.0), the India extract Geofabrik
// publishes daily, pinned below by date and MD5.
//
//   node infra/aws-central/tools/build-street-index.mjs fetch
//     Downloads the pinned extract into data/streets/.work/ (1.7 GB, resumable) and
//     checks its MD5. One request to download.geofabrik.de.
//
//   node infra/aws-central/tools/build-street-index.mjs prepare [--region <id>]...
//     Needs osmium-tool (brew install osmium-tool). Filters the extract once to streets,
//     place names, administrative boundaries, named landuse and postcodes (about three
//     minutes, 0.8 GB of memory), renumbers its nodes (2.2 GB at the peak, the most any
//     step takes), averages the postcode centres of all India, then cuts each region's
//     box out with a 0.2 degree margin. Writes only under .work/.
//
//   node infra/aws-central/tools/build-street-index.mjs build [--region <id>]...
//     Reads a region's cut, computes every street's address the way Nominatim does, and
//     writes data/streets/<id>/manifest.json and one tile file per quarter degree.
//
//   node infra/aws-central/tools/build-street-index.mjs sizes
//     Prints what each built region costs in the package.
//
// Adding a state or a city is one line in REGIONS, then prepare and build for its id.
//
// The address of a street is not looked up around a point. Nominatim computes it once per
// street when it indexes the street (insert_addresslines in placex_triggers.sql), stores
// it, and reads it back for every point that snaps to that street (results.py). This
// tool is that computation, rule for rule, from Nominatim 5.3.0, the version
// nominatim.openstreetmap.org reported on 7 Oct 2026:
//
//   candidates   every named administrative boundary, place area and named landuse area
//                the street runs through, and every place node whose box (500 m to 15 km
//                either way, by kind) the street enters
//   ranks        a boundary's address rank is twice its admin_level, a place's is fixed
//                by its kind, and both move when one sits inside another of the same
//                rank; a place node that carries a boundary's name is that boundary's
//                label, not a place of its own
//   choice       by rank: areas before nodes, then nearest; a node counts only inside
//                the last boundary chosen
//   read back    one line per rank, an area that contains the street's centre first
//   postcode     the street's own tag, else that of the most specific place that has
//                one, else the nearest postcode centre within 5 km (the centre being
//                the mean position of every object in India tagged with that postcode)
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

import {
  FLAG_CLOSED, FLAG_MINOR, FLAG_NAME, FLAG_NO_NAME_TAG, FLAG_REF, REGION_FORMAT, SNAP_LIMIT_DEGREES, TILE_FORMAT,
  TILE_MAGIC, TILE_VERSION, TUPLE_FIELDS, tileKeyOf,
} from "../service/local-address.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const STREETS_ROOT = path.join(root, "data/streets");
const WORK = path.join(STREETS_ROOT, ".work");

export const EXTRACT = Object.freeze({
  source: "OpenStreetMap contributors, ODbL 1.0",
  licence: "https://opendatacommons.org/licenses/odbl/1-0/",
  distributor: "Geofabrik GmbH",
  extract_url: "https://download.geofabrik.de/asia/india-261006.osm.pbf",
  // The extract holds all OpenStreetMap data up to 2026-10-06T20:21:06Z.
  extract_date: "2026-10-06",
  extract_md5: "b02c63449ffbeda80757a75496bf7c8f",
  rules: "Nominatim 5.3.0",
});

// One line per region. `relation` is the OpenStreetMap boundary relation whose polygon is
// the coverage (a state); `boxes` are [west, south, east, north] in degrees (a city).
// `cut` is the box osmium cuts for a relation's region.
export const REGIONS = Object.freeze([
  { id: "karnataka", name: "Karnataka", relation: 2019939, cut: [74.0, 11.5, 78.65, 18.5] },
  { id: "pune", name: "Pune and Pimpri-Chinchwad", boxes: [[73.65, 18.38, 74.05, 18.72]] },
  { id: "delhi-ncr", name: "Delhi, Gurugram, Faridabad, Noida and Ghaziabad", boxes: [[76.8, 28.2, 77.65, 28.9]] },
  { id: "hyderabad", name: "Hyderabad", boxes: [[78.2, 17.2, 78.7, 17.65]] },
  { id: "visakhapatnam", name: "Visakhapatnam", boxes: [[83.05, 17.55, 83.45, 17.9]] },
  { id: "mumbai", name: "Mumbai, Thane and Navi Mumbai", boxes: [[72.75, 18.85, 73.2, 19.35]] },
  { id: "chennai", name: "Chennai", boxes: [[80.0, 12.8, 80.35, 13.25]] },
  { id: "kolkata", name: "Kolkata and Howrah", boxes: [[88.2, 22.4, 88.55, 22.75]] },
  { id: "ahmedabad", name: "Ahmedabad", boxes: [[72.4, 22.9, 72.75, 23.15]] },
  { id: "gandhinagar", name: "Gandhinagar", boxes: [[72.55, 23.15, 72.75, 23.3]] },
]);

export const TILE_DEGREES = 0.25;
export const CELL_DEGREES = 0.0025;
export const SCALE = 100_000;
export const SIMPLIFY_METRES = 2;
// The run-time grid reaches this many cells past a tile's edge, so a point at the edge
// still finds every street within the snap limit in its own tile.
export const GRID_MARGIN_CELLS = 3;
// Streets are kept this far outside a region's coverage, so a covered point's nearest
// street is the nearest street there is.
const COVERAGE_MARGIN_CELLS = 4;
const CUT_MARGIN_DEGREES = 0.2;

// ---------------------------------------------------------------------------------------
// What Nominatim imports, and under which rank (presets.lua, address-levels.json).

const ALWAYS_STREETS = new Set(["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
  "residential", "road", "living_street", "pedestrian", "construction"]);
const NAMED_STREETS = new Set(["service", "cycleway", "path", "steps", "bridleway", "track", "footway",
  "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"]);
const MINOR_STREETS = new Set(["service", "cycleway", "path", "footway", "steps", "bridleway", "motorway_link",
  "primary_link", "trunk_link", "secondary_link", "tertiary_link"]);
const EXCLUDED_FOOTWAYS = new Set(["sidewalk", "crossing", "link"]);
const MAIN_NAME = /^(?:name|int_name|reg_name|loc_name|old_name|alt_name|official_name|place_name|short_name)(?::.+)?$|^alt_name_/;
const EXTRA_NAME = new Set(["ref", "int_ref", "nat_ref", "reg_ref", "loc_ref", "old_ref", "ISO3166-2"]);
const IGNORED_NAME = /:prefix$|:suffix$|^name:prefix:|^name:suffix:|^name:etymology(?::|$)|^name:signed$|^name:botanical$/;

// [search rank, address rank] of a place by kind. Kinds with no address rank (a state, a
// locality, an isolated dwelling at rank 25) never enter a street's address.
const PLACE_RANKS = Object.freeze({
  county: [12, 12], district: [12, 12], municipality: [14, 14], city: [16, 16], town: [18, 16], borough: [18, 18],
  village: [19, 16], suburb: [19, 20], hamlet: [20, 20], croft: [20, 20], subdivision: [22, 22], allotments: [22, 22],
  quarter: [20, 22], neighbourhood: [24, 24],
});
const LANDUSE_KINDS = new Set(["residential", "farm", "farmyard", "industrial", "commercial", "allotments", "retail"]);
const ADMIN_LABELS = Object.freeze({
  4: "state", 5: "state_district", 6: "county", 7: "municipality", 8: "city", 9: "city_district", 10: "suburb",
  11: "neighbourhood", 12: "city_block",
});

// The names Nominatim keeps for an object, and whether any of them makes it "named".
export function namesOf(tags) {
  const names = {};
  let named = false;
  for (const [key, value] of Object.entries(tags)) {
    if (typeof value !== "string" || !value || IGNORED_NAME.test(key)) continue;
    if (MAIN_NAME.test(key)) { names[key] = value; named = true; } else if (EXTRA_NAME.has(key)) names[key] = value;
  }
  return { names, named };
}

// The name Nominatim shows when the request names no language: `name`, else the name a
// linked place node brought, else the first of whatever it has, in hstore's key order
// (shorter keys first, then by bytes).
export function displayName(names) {
  const keys = Object.keys(names);
  if (!keys.length) return "";
  if (keys.length > 1) {
    if (names.name) return names.name;
    if (names._place_name) return names._place_name;
  }
  keys.sort((left, right) => left.length - right.length || (left < right ? -1 : left > right ? 1 : 0));
  return names[keys[0]];
}

// India's postcode as Nominatim normalises it: six digits, an optional space after three.
export function postcodeOf(tags) {
  for (const key of ["addr:postcode", "postcode", "postal_code"]) {
    const match = /^(\d{3}) ?(\d{3})$/.exec(String(tags[key] || "").trim());
    if (match) return match[1] + match[2];
  }
  return null;
}

function flat(coordinates) {
  const out = new Float64Array(coordinates.length * 2);
  for (let index = 0; index < coordinates.length; index += 1) {
    out[index * 2] = coordinates[index][0];
    out[index * 2 + 1] = coordinates[index][1];
  }
  return out;
}

// A way Nominatim would snap a point to at zoom 17, or null. Every street class is
// imported with or without a name; service roads, paths and links only with one.
export function streetFromFeature(feature) {
  const tags = feature?.properties;
  if (!tags || tags["@type"] !== "way" || !tags.highway) return null;
  const kind = tags.highway;
  const { names, named } = namesOf(tags);
  if (!ALWAYS_STREETS.has(kind)) {
    if (!NAMED_STREETS.has(kind) || !named) return null;
    if (kind === "footway" && EXCLUDED_FOOTWAYS.has(tags.footway)) return null;
  }
  const geometry = feature.geometry;
  const coordinates = geometry?.type === "LineString" ? geometry.coordinates
    : geometry?.type === "Polygon" ? geometry.coordinates[0] : null;
  if (!coordinates || coordinates.length < 2) return null;
  const coords = flat(coordinates);
  const last = coords.length - 2;
  // Nominatim makes a polygon of every closed way.
  const closed = coordinates.length >= 4 && coords[0] === coords[last] && coords[1] === coords[last + 1];
  const display = displayName(names);
  return {
    id: Number(tags["@id"]), kind, rank: MINOR_STREETS.has(kind) ? 27 : 26, coords, closed,
    display, ref: typeof tags.ref === "string" ? tags.ref : "", hasNameTag: Boolean(names.name), postcode: postcodeOf(tags),
  };
}

function polygonsOf(geometry) {
  const polygons = geometry.type === "Polygon" ? [geometry.coordinates]
    : geometry.type === "MultiPolygon" ? geometry.coordinates : null;
  return polygons ? polygons.map((rings) => rings.map(flat)) : null;
}

// A named feature that can be a line of a street's address, or null.
export function placeFromFeature(feature) {
  const tags = feature?.properties;
  const geometry = feature?.geometry;
  if (!tags || !geometry) return null;
  const isArea = geometry.type === "Polygon" || geometry.type === "MultiPolygon";
  if (!isArea && geometry.type !== "Point") return null;
  const osmType = tags["@type"] === "node" ? "N" : tags["@type"] === "way" ? "W" : "R";
  let cls = null;
  let kind = null;
  let ranks = null;
  let adminLevel = 15;
  let placeTag = null;
  if (isArea && tags.boundary === "administrative") {
    cls = "boundary";
    kind = "administrative";
    adminLevel = /^\d{1,2}$/.test(String(tags.admin_level || "")) ? Number(tags.admin_level) : 15;
    if (adminLevel < 4 || adminLevel > 12) return null;
    ranks = [adminLevel * 2, adminLevel * 2];
    // A boundary that is also tagged as a place keeps the place as an extra tag.
    if (typeof tags.place === "string" && !tags.place.startsWith("isl")) placeTag = tags.place;
  } else if (typeof tags.place === "string" && PLACE_RANKS[tags.place]) {
    cls = "place";
    kind = tags.place;
    ranks = PLACE_RANKS[kind];
  } else if (isArea && LANDUSE_KINDS.has(tags.landuse)) {
    cls = "landuse";
    kind = tags.landuse;
    ranks = [24, 24];
  } else {
    return null;
  }
  const { names, named } = namesOf(tags);
  if (!displayName(names)) return null;
  // A boundary and a landuse area are imported only with a name; a place always is, and
  // one with a ref alone still has something to show.
  if (cls !== "place" && !named) return null;
  const place = {
    key: `${osmType}${tags["@id"]}`, osmType, cls, kind, adminLevel, placeTag, isArea, names,
    rankSearch: ranks[0] - (tags.capital === "yes" ? 1 : 0), rankAddress: ranks[1], baseAddress: ranks[1],
    wikidata: typeof tags.wikidata === "string" ? tags.wikidata : null,
    postcode: postcodeOf(tags), linked: false, linkedPlace: null, x: 0, y: 0, geom: null, box: null,
  };
  if (isArea) {
    place.geom = new Area(polygonsOf(geometry));
    [place.x, place.y] = place.geom.pointOnSurface();
  } else {
    [place.x, place.y] = geometry.coordinates;
  }
  return place;
}

// A postcode boundary relation: where one is mapped it is the postcode of everything
// whose centre it covers.
export function postcodeAreaFromFeature(feature) {
  const tags = feature?.properties;
  const geometry = feature?.geometry;
  if (!tags || tags["@type"] !== "relation" || tags.boundary !== "postal_code") return null;
  if (geometry?.type !== "Polygon" && geometry?.type !== "MultiPolygon") return null;
  const postcode = postcodeOf(tags);
  if (!postcode) return null;
  const geom = new Area(polygonsOf(geometry));
  const [x, y] = geom.pointOnSurface();
  return { postcode, geom, x, y };
}

// ---------------------------------------------------------------------------------------
// Geometry, in plain degrees of longitude and latitude, as PostGIS measures an SRID 4326
// geometry.

function segmentDistance(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const length2 = dx * dx + dy * dy;
  let turn = length2 ? ((px - ax) * dx + (py - ay) * dy) / length2 : 0;
  if (turn < 0) turn = 0; else if (turn > 1) turn = 1;
  return Math.hypot(ax + turn * dx - px, ay + turn * dy - py);
}

export function lineDistance(coords, px, py) {
  let best = Infinity;
  for (let index = 2; index < coords.length; index += 2) {
    const distance = segmentDistance(px, py, coords[index - 2], coords[index - 1], coords[index], coords[index + 1]);
    if (distance < best) best = distance;
  }
  return best;
}

const snap7 = (value) => Math.round(value * 1e7) / 1e7;

// ST_LineInterpolatePoint(line, 0.5): halfway along the line's length in degrees.
export function lineMidpoint(coords) {
  let total = 0;
  for (let index = 2; index < coords.length; index += 2) {
    total += Math.hypot(coords[index] - coords[index - 2], coords[index + 1] - coords[index - 1]);
  }
  let left = total / 2;
  for (let index = 2; index < coords.length; index += 2) {
    const length = Math.hypot(coords[index] - coords[index - 2], coords[index + 1] - coords[index - 1]);
    if (left <= length && length > 0) {
      const turn = left / length;
      return [snap7(coords[index - 2] + turn * (coords[index] - coords[index - 2])),
        snap7(coords[index - 1] + turn * (coords[index + 1] - coords[index - 1]))];
    }
    left -= length;
  }
  return [coords[0], coords[1]];
}

// ST_PointOnSurface of a line as GEOS computes it: the inner vertex nearest the line's
// centre of length, or the nearer end when it has no inner vertex. Nominatim placed a
// street here until 5.0; rows it has not re-indexed since still are.
export function lineInteriorPoint(coords) {
  let sx = 0; let sy = 0; let total = 0;
  for (let index = 2; index < coords.length; index += 2) {
    const length = Math.hypot(coords[index] - coords[index - 2], coords[index + 1] - coords[index - 1]);
    sx += (length * (coords[index] + coords[index - 2])) / 2;
    sy += (length * (coords[index + 1] + coords[index - 1])) / 2;
    total += length;
  }
  const cx = total ? sx / total : coords[0];
  const cy = total ? sy / total : coords[1];
  let best = -1;
  let least = Infinity;
  const consider = (index) => {
    const distance = Math.hypot(coords[index] - cx, coords[index + 1] - cy);
    if (distance < least) { least = distance; best = index; }
  };
  for (let index = 2; index < coords.length - 2; index += 2) consider(index);
  if (best < 0) { consider(0); consider(coords.length - 2); }
  return [coords[best], coords[best + 1]];
}

const orient = (ax, ay, bx, by, cx, cy) => (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);

// A set of polygons with holes, answered by the even-odd rule over every ring. Edges are
// filed by bands of latitude so a point is tested against the few edges beside it.
export class Area {
  constructor(polygons) {
    this.polygons = polygons;
    let count = 0;
    let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
    for (const rings of polygons) {
      for (const ring of rings) {
        count += ring.length / 2 - 1;
        for (let index = 0; index < ring.length; index += 2) {
          if (ring[index] < minX) minX = ring[index];
          if (ring[index] > maxX) maxX = ring[index];
          if (ring[index + 1] < minY) minY = ring[index + 1];
          if (ring[index + 1] > maxY) maxY = ring[index + 1];
        }
      }
    }
    this.bbox = [minX, minY, maxX, maxY];
    const edges = new Float64Array(Math.max(0, count) * 4);
    let at = 0;
    for (const rings of polygons) {
      for (const ring of rings) {
        for (let index = 2; index < ring.length; index += 2) {
          edges[at] = ring[index - 2]; edges[at + 1] = ring[index - 1]; edges[at + 2] = ring[index]; edges[at + 3] = ring[index + 1];
          at += 4;
        }
      }
    }
    this.edges = edges;
    const bands = Math.max(1, Math.min(4096, Math.ceil(count / 6)));
    this.bands = bands;
    this.bandHeight = (maxY - minY) / bands || 1;
    const starts = new Uint32Array(bands + 1);
    const bandOf = (y) => Math.max(0, Math.min(bands - 1, Math.floor((y - minY) / this.bandHeight)));
    this.bandOf = bandOf;
    for (let edge = 0; edge < count; edge += 1) {
      const low = bandOf(Math.min(edges[edge * 4 + 1], edges[edge * 4 + 3]));
      const high = bandOf(Math.max(edges[edge * 4 + 1], edges[edge * 4 + 3]));
      for (let band = low; band <= high; band += 1) starts[band + 1] += 1;
    }
    for (let band = 0; band < bands; band += 1) starts[band + 1] += starts[band];
    const items = new Uint32Array(starts[bands]);
    const fill = starts.slice(0, bands);
    for (let edge = 0; edge < count; edge += 1) {
      const low = bandOf(Math.min(edges[edge * 4 + 1], edges[edge * 4 + 3]));
      const high = bandOf(Math.max(edges[edge * 4 + 1], edges[edge * 4 + 3]));
      for (let band = low; band <= high; band += 1) { items[fill[band]] = edge; fill[band] += 1; }
    }
    this.bandStart = starts;
    this.bandItems = items;
  }

  inBox(x, y) {
    return x >= this.bbox[0] && x <= this.bbox[2] && y >= this.bbox[1] && y <= this.bbox[3];
  }

  contains(x, y) {
    if (!this.inBox(x, y)) return false;
    const { edges, bandStart, bandItems } = this;
    const band = this.bandOf(y);
    let inside = false;
    for (let item = bandStart[band]; item < bandStart[band + 1]; item += 1) {
      const edge = bandItems[item] * 4;
      const y1 = edges[edge + 1];
      const y2 = edges[edge + 3];
      if ((y1 > y) !== (y2 > y) && x < edges[edge] + ((y - y1) * (edges[edge + 2] - edges[edge])) / (y2 - y1)) inside = !inside;
    }
    return inside;
  }

  // On an edge to within a tenth of a millimetre: a node the area shares with a way.
  onBoundary(x, y, tolerance = 1e-9) {
    if (x < this.bbox[0] - tolerance || x > this.bbox[2] + tolerance || y < this.bbox[1] - tolerance || y > this.bbox[3] + tolerance) return false;
    const { edges, bandStart, bandItems } = this;
    const band = this.bandOf(y);
    for (let item = bandStart[band]; item < bandStart[band + 1]; item += 1) {
      const edge = bandItems[item] * 4;
      if (segmentDistance(x, y, edges[edge], edges[edge + 1], edges[edge + 2], edges[edge + 3]) <= tolerance) return true;
    }
    return false;
  }

  covers(x, y) {
    return this.contains(x, y) || this.onBoundary(x, y);
  }

  containsStrictly(x, y) {
    return this.contains(x, y) && !this.onBoundary(x, y);
  }

  // Distance to the area: zero inside, else to its nearest edge.
  distanceTo(x, y) {
    if (this.contains(x, y)) return 0;
    const { edges, bandStart, bandItems, bands, bandHeight } = this;
    const home = Math.floor((y - this.bbox[1]) / bandHeight);
    let best = Infinity;
    for (let step = 0; step < bands + Math.abs(home) + 1; step += 1) {
      // Bands are searched outward; one whole band height away nothing can be nearer.
      if ((step - 1) * bandHeight > best) break;
      for (const band of step === 0 ? [home] : [home - step, home + step]) {
        if (band < 0 || band >= bands) continue;
        for (let item = bandStart[band]; item < bandStart[band + 1]; item += 1) {
          const edge = bandItems[item] * 4;
          const distance = segmentDistance(x, y, edges[edge], edges[edge + 1], edges[edge + 2], edges[edge + 3]);
          if (distance < best) best = distance;
        }
      }
      if (home - step < 0 && home + step >= bands) break;
    }
    return best;
  }

  // Whether a segment passes from one side of an edge to the other.
  crosses(ax, ay, bx, by) {
    if (Math.max(ax, bx) < this.bbox[0] || Math.min(ax, bx) > this.bbox[2] || Math.max(ay, by) < this.bbox[1] || Math.min(ay, by) > this.bbox[3]) return false;
    const { edges, bandStart, bandItems } = this;
    const low = this.bandOf(Math.min(ay, by));
    const high = this.bandOf(Math.max(ay, by));
    for (let band = low; band <= high; band += 1) {
      for (let item = bandStart[band]; item < bandStart[band + 1]; item += 1) {
        const edge = bandItems[item] * 4;
        const cx = edges[edge]; const cy = edges[edge + 1]; const dx = edges[edge + 2]; const dy = edges[edge + 3];
        const o1 = orient(ax, ay, bx, by, cx, cy);
        const o2 = orient(ax, ay, bx, by, dx, dy);
        if ((o1 > 0) === (o2 > 0) || o1 === 0 || o2 === 0) continue;
        const o3 = orient(cx, cy, dx, dy, ax, ay);
        const o4 = orient(cx, cy, dx, dy, bx, by);
        if ((o3 > 0) !== (o4 > 0) && o3 !== 0 && o4 !== 0) return true;
      }
    }
    return false;
  }

  // Where along a segment (0 to 1) it passes through an edge of the area.
  crossingTurns(ax, ay, bx, by, out) {
    if (Math.max(ax, bx) < this.bbox[0] || Math.min(ax, bx) > this.bbox[2] || Math.max(ay, by) < this.bbox[1] || Math.min(ay, by) > this.bbox[3]) return out;
    const { edges, bandStart, bandItems } = this;
    const low = this.bandOf(Math.min(ay, by));
    const high = this.bandOf(Math.max(ay, by));
    for (let band = low; band <= high; band += 1) {
      for (let item = bandStart[band]; item < bandStart[band + 1]; item += 1) {
        const edge = bandItems[item] * 4;
        const cx = edges[edge]; const cy = edges[edge + 1]; const dx = edges[edge + 2]; const dy = edges[edge + 3];
        const o1 = orient(ax, ay, bx, by, cx, cy);
        const o2 = orient(ax, ay, bx, by, dx, dy);
        if ((o1 > 0) === (o2 > 0) && o1 !== 0 && o2 !== 0) continue;
        const o3 = orient(cx, cy, dx, dy, ax, ay);
        const o4 = orient(cx, cy, dx, dy, bx, by);
        if (o3 === o4 || (o3 > 0) === (o4 > 0)) continue;
        const turn = o3 / (o3 - o4);
        if (turn > 0 && turn < 1) out.push(turn);
      }
    }
    return out;
  }

  // ST_PointOnSurface as GEOS computes it: on the horizontal line halfway between the
  // two vertex latitudes that bracket the middle of the polygon's box, the midpoint of
  // the widest stretch inside. For several polygons, the widest stretch of any.
  pointOnSurface() {
    let best = null;
    let bestWidth = -1;
    for (const rings of this.polygons) {
      const shell = rings[0];
      let minY = Infinity; let maxY = -Infinity;
      for (let index = 1; index < shell.length; index += 2) {
        if (shell[index] < minY) minY = shell[index];
        if (shell[index] > maxY) maxY = shell[index];
      }
      const centre = (minY + maxY) / 2;
      let low = minY; let high = maxY;
      for (const ring of rings) {
        for (let index = 1; index < ring.length; index += 2) {
          const y = ring[index];
          if (y <= centre) { if (y > low) low = y; } else if (y < high) high = y;
        }
      }
      const scan = (low + high) / 2;
      const crossings = [];
      for (const ring of rings) {
        for (let index = 2; index < ring.length; index += 2) {
          const x1 = ring[index - 2]; const y1 = ring[index - 1]; const x2 = ring[index]; const y2 = ring[index + 1];
          if ((y1 > scan && y2 > scan) || (y1 < scan && y2 < scan) || y1 === y2) continue;
          if ((y1 === scan && y2 < scan) || (y2 === scan && y1 < scan)) continue;
          crossings.push(x1 + ((scan - y1) * (x2 - x1)) / (y2 - y1));
        }
      }
      crossings.sort((left, right) => left - right);
      for (let index = 0; index + 1 < crossings.length; index += 2) {
        const width = crossings[index + 1] - crossings[index];
        if (width > bestWidth) { bestWidth = width; best = [snap7((crossings[index] + crossings[index + 1]) / 2), snap7(scan)]; }
      }
    }
    return best || [this.polygons[0][0][0], this.polygons[0][0][1]];
  }

  // Whether another area lies wholly inside this one (ST_Contains, to the precision a
  // sample of its vertices gives) and is not the same shape.
  containsArea(other) {
    const [minX, minY, maxX, maxY] = other.bbox;
    if (minX < this.bbox[0] || minY < this.bbox[1] || maxX > this.bbox[2] || maxY > this.bbox[3]) return false;
    if (other.edges.length === this.edges.length && other.bbox.every((value, index) => value === this.bbox[index])) return false;
    const total = other.edges.length / 4;
    const step = Math.max(1, Math.floor(total / 400));
    for (let edge = 0; edge < total; edge += step) {
      if (!this.covers(other.edges[edge * 4], other.edges[edge * 4 + 1])) return false;
    }
    return true;
  }
}

// ST_NPoints(ST_Intersection(area, line)) > 1: the line has a vertex inside, or passes
// through an edge, or touches the boundary at two of its vertices (a road that is the
// boundary shares its nodes with it).
export function lineMeetsArea(area, coords) {
  let touches = 0;
  for (let index = 0; index < coords.length; index += 2) {
    if (!area.inBox(coords[index], coords[index + 1])) continue;
    if (area.onBoundary(coords[index], coords[index + 1])) touches += 1;
    else if (area.contains(coords[index], coords[index + 1])) return true;
  }
  if (touches >= 2) return true;
  for (let index = 2; index < coords.length; index += 2) {
    if (area.crosses(coords[index - 2], coords[index - 1], coords[index], coords[index + 1])) return true;
  }
  return false;
}

function lineMeetsBox(coords, box) {
  for (let index = 0; index < coords.length; index += 2) {
    if (coords[index] >= box[0] && coords[index] <= box[2] && coords[index + 1] >= box[1] && coords[index + 1] <= box[3]) return true;
  }
  for (let index = 2; index < coords.length; index += 2) {
    // Liang and Barsky: the part of the segment inside the box, if any.
    const ax = coords[index - 2]; const ay = coords[index - 1];
    const dx = coords[index] - ax; const dy = coords[index + 1] - ay;
    let enter = 0; let leave = 1; let outside = false;
    for (const [push, room] of [[-dx, ax - box[0]], [dx, box[2] - ax], [-dy, ay - box[1]], [dy, box[3] - ay]]) {
      if (push === 0) { if (room < 0) outside = true; continue; }
      const turn = room / push;
      if (push < 0) { if (turn > enter) enter = turn; } else if (turn < leave) leave = turn;
    }
    if (!outside && enter < leave) return true;
  }
  return false;
}

// place_node_fuzzy_area: the box Nominatim gives a place node, by search rank. It buffers
// the node in its UTM zone and takes the envelope, so the half-size is `radius` grid
// metres, which the zone's scale factor makes a few parts in ten thousand off true metres.
export function fuzzyBox(lng, lat, rankSearch) {
  const radius = rankSearch <= 16 ? 15_000 : rankSearch <= 18 ? 4_000 : rankSearch <= 19 ? 2_000 : rankSearch <= 20 ? 1_000 : 500;
  return boxOfMetres(lng, lat, radius);
}

export function boxOfMetres(lng, lat, radius) {
  const phi = (lat * Math.PI) / 180;
  const central = Math.floor((lng + 180) / 6) * 6 - 177;
  const k = 0.9996 * (1 + (((lng - central) * Math.PI) / 180 * Math.cos(phi)) ** 2 / 2);
  const ground = radius / k;
  const perLat = 111_132.954 - 559.822 * Math.cos(2 * phi) + 1.175 * Math.cos(4 * phi);
  const perLng = 111_412.84 * Math.cos(phi) - 93.5 * Math.cos(3 * phi) + 0.118 * Math.cos(5 * phi);
  return [lng - ground / perLng, lat - ground / perLat, lng + ground / perLng, lat + ground / perLat];
}

// Douglas and Peucker, with distances in metres at the line's own latitude.
export function simplify(coords, metres) {
  const points = coords.length / 2;
  if (points <= 2 || !(metres > 0)) return coords;
  const kx = 111_320 * Math.cos((coords[1] * Math.PI) / 180);
  const ky = 110_574;
  const keep = new Uint8Array(points);
  keep[0] = 1;
  keep[points - 1] = 1;
  const stack = [[0, points - 1]];
  while (stack.length) {
    const [first, last] = stack.pop();
    let worst = 0;
    let at = -1;
    const ax = coords[first * 2] * kx; const ay = coords[first * 2 + 1] * ky;
    const bx = coords[last * 2] * kx; const by = coords[last * 2 + 1] * ky;
    for (let index = first + 1; index < last; index += 1) {
      const distance = segmentDistance(coords[index * 2] * kx, coords[index * 2 + 1] * ky, ax, ay, bx, by);
      if (distance > worst) { worst = distance; at = index; }
    }
    if (worst > metres) { keep[at] = 1; stack.push([first, at], [at, last]); }
  }
  let kept = 0;
  for (let index = 0; index < points; index += 1) kept += keep[index];
  const out = new Float64Array(kept * 2);
  let to = 0;
  for (let index = 0; index < points; index += 1) {
    if (!keep[index]) continue;
    out[to] = coords[index * 2]; out[to + 1] = coords[index * 2 + 1];
    to += 2;
  }
  return out;
}

// ---------------------------------------------------------------------------------------
// The address model: Nominatim's ranks, links and address lines.

class CellIndex {
  constructor(cell) { this.cell = cell; this.cells = new Map(); this.large = []; this.round = 0; this.seen = new Map(); }

  add(item, box) {
    const { cell } = this;
    const x0 = Math.floor(box[0] / cell); const x1 = Math.floor(box[2] / cell);
    const y0 = Math.floor(box[1] / cell); const y1 = Math.floor(box[3] / cell);
    if ((x1 - x0 + 1) * (y1 - y0 + 1) > 400) { this.large.push([item, box]); return; }
    for (let x = x0; x <= x1; x += 1) {
      for (let y = y0; y <= y1; y += 1) {
        const key = x * 100_000 + y;
        const list = this.cells.get(key);
        if (list) list.push(item); else this.cells.set(key, [item]);
      }
    }
  }

  // Every item whose box may overlap the query box, once.
  query(box, out = []) {
    const { cell, seen } = this;
    out.length = 0;
    for (const [item, own] of this.large) {
      if (own[0] <= box[2] && own[2] >= box[0] && own[1] <= box[3] && own[3] >= box[1]) out.push(item);
    }
    const x0 = Math.floor(box[0] / cell); const x1 = Math.floor(box[2] / cell);
    const y0 = Math.floor(box[1] / cell); const y1 = Math.floor(box[3] / cell);
    const single = x0 === x1 && y0 === y1;
    this.round += 1;
    for (let x = x0; x <= x1; x += 1) {
      for (let y = y0; y <= y1; y += 1) {
        const list = this.cells.get(x * 100_000 + y);
        if (!list) continue;
        for (const item of list) {
          if (!single) {
            if (seen.get(item) === this.round) continue;
            seen.set(item, this.round);
          }
          out.push(item);
        }
      }
    }
    return out;
  }
}

const lower = (value) => String(value || "").toLowerCase();

function labelOf(place) {
  if (place.placeTag) return place.placeTag;
  if (place.linkedPlace) return place.linkedPlace;
  if (place.cls === "boundary") return ADMIN_LABELS[Math.floor(place.rankAddress / 2)] || "administrative";
  return place.kind;
}

export function createAddressModel({
  features = null, places = null, postcodeAreas = null, relationLabels = new Map(), postcodes = [], lineCentre = "midpoint",
} = {}) {
  const all = places || features.map(placeFromFeature).filter(Boolean);
  const postal = postcodeAreas || (features || []).map(postcodeAreaFromFeature).filter(Boolean);
  const centreOfLine = lineCentre === "interior" ? lineInteriorPoint : lineMidpoint;
  const areas = all.filter((place) => place.isArea);
  const nodes = all.filter((place) => !place.isArea);
  const areaIndex = new CellIndex(0.02);
  for (const area of areas) areaIndex.add(area, area.geom.bbox);
  const scratch = [];
  const areasCovering = (x, y) => areaIndex.query([x, y, x, y], scratch).filter((area) => area.geom.covers(x, y));

  // Ranks and links, in the order Nominatim indexes: boundaries from the largest down,
  // then place areas, then landuse, and place nodes after every area.
  const byName = new Map();
  const byWikidata = new Map();
  const byKey = new Map();
  for (const node of nodes) {
    byKey.set(node.key, node);
    if (node.names.name) {
      const key = lower(node.names.name);
      if (byName.has(key)) byName.get(key).push(node); else byName.set(key, [node]);
    }
    if (node.wikidata) {
      if (byWikidata.has(node.wikidata)) byWikidata.get(node.wikidata).push(node); else byWikidata.set(node.wikidata, [node]);
    }
  }
  const order = (area) => (area.cls === "boundary" ? area.adminLevel : area.cls === "place" ? 100 + area.baseAddress : 200);
  const ordered = [...areas].sort((left, right) => order(left) - order(right) || (left.key < right.key ? -1 : 1));
  const wikidataAgrees = (node, area) => !node.wikidata || !area.wikidata || node.wikidata === area.wikidata;
  const linkable = (node, area) => !node.linked && area.geom.covers(node.x, node.y);
  for (const area of ordered) {
    let parentLevel = 3;
    if (area.cls === "boundary" && area.osmType === "R") {
      // An inner boundary is never ranked at or above the boundary around it.
      let parent = null;
      for (const other of areasCovering(area.x, area.y)) {
        if (other !== area && other.cls === "boundary" && other.osmType === "R" && other.adminLevel < area.adminLevel
            && other.rankAddress >= 1 && other.rankAddress <= 25 && (!parent || other.adminLevel > parent.adminLevel)) parent = other;
      }
      if (parent) {
        parentLevel = parent.rankAddress;
        if (parent.rankAddress >= area.rankAddress) area.rankAddress = parent.rankAddress >= 24 ? 25 : parent.rankAddress + 2;
      }
      if (area.rankAddress > 9) {
        let around = null;
        for (const other of areasCovering(area.geom.edges[0], area.geom.edges[1])) {
          if (other !== area && other.cls === "place" && other.rankAddress >= 1 && other.rankAddress <= 23
              && other.baseAddress >= area.rankAddress && (!around || other.baseAddress > around.baseAddress)
              && other.geom.containsArea(area.geom)) around = other;
        }
        if (around) area.rankAddress = around.rankAddress + 2;
      }
    } else if (area.cls === "place" && area.rankAddress >= 16 && area.rankAddress <= 23) {
      let around = null;
      for (const other of areasCovering(area.geom.edges[0], area.geom.edges[1])) {
        if (other !== area && other.baseAddress < 24 && other.rankAddress >= 1 && other.rankAddress <= 25
            && other.baseAddress >= area.rankAddress && (!around || other.baseAddress > around.baseAddress)
            && other.geom.containsArea(area.geom)) around = other;
      }
      if (around) area.rankAddress = around.rankAddress + 2;
    }
    if (area.rankAddress <= 0) continue;
    // The place node that stands for this area: its label member, or the node of the
    // same wikidata item, or a node of the same name and rank inside it.
    const name = lower(area.names.name);
    let linked = null;
    const label = area.osmType === "R" ? byKey.get(`N${relationLabels.get(Number(area.key.slice(1)))}`) : null;
    if (label) linked = label;
    if (!linked && area.wikidata) {
      const same = (byWikidata.get(area.wikidata) || []).filter((node) => linkable(node, area));
      linked = same.find((node) => lower(node.names.name) === name) || same[0] || null;
    }
    if (!linked && area.placeTag && name) {
      for (const node of nodes) {
        if (node.kind !== area.placeTag || !node.names.name || !wikidataAgrees(node, area) || !linkable(node, area)) continue;
        const other = lower(node.names.name);
        if (name.includes(other) || other.includes(name)) { linked = node; break; }
      }
    }
    if (!linked && name) {
      linked = (byName.get(name) || []).find((node) => node.baseAddress === area.rankAddress
        && wikidataAgrees(node, area) && linkable(node, area)) || null;
    }
    if (linked) {
      linked.linked = true;
      if (area.geom.containsStrictly(linked.x, linked.y)) { area.x = linked.x; area.y = linked.y; }
      if (linked.baseAddress > parentLevel && linked.baseAddress < 26) area.rankAddress = linked.baseAddress;
      area.linkedPlace = linked.kind;
      for (const [key, value] of Object.entries(linked.names)) {
        if (area.names[key] !== value) area.names[`_place_${key}`] = value;
      }
    } else if (area.placeTag && area.rankAddress >= 4 && area.rankAddress <= 25 && PLACE_RANKS[area.placeTag]) {
      const rank = PLACE_RANKS[area.placeTag][1];
      if (rank > parentLevel && rank < 26) area.rankAddress = rank;
    }
  }
  // A place node inside a boundary relation of its own rank is a part of it.
  for (const node of nodes) {
    if (node.linked || node.rankAddress < 16 || node.rankAddress > 23) continue;
    const inside = areasCovering(node.x, node.y).some((area) => area.osmType === "R"
      && ((area.cls === "place" && area.baseAddress === node.rankAddress)
        || (area.cls === "boundary" && area.rankAddress === node.rankAddress)));
    if (inside) node.rankAddress += 2;
  }
  // The postcode a place hands to the streets it names: its own tag, else that of the
  // most specific area around it that is tagged with one.
  for (const place of all) {
    if (place.postcode) continue;
    let from = null;
    for (const area of areasCovering(place.x, place.y)) {
      if (area !== place && area.postcode && area.rankAddress < place.rankAddress && (!from || area.rankAddress > from.rankAddress)) from = area;
    }
    if (from) place.inheritedPostcode = from.postcode;
  }
  for (const place of all) if (!place.postcode && place.inheritedPostcode) place.postcode = place.inheritedPostcode;

  const nodeIndex = new CellIndex(0.05);
  for (const node of nodes) {
    if (node.linked || node.rankAddress <= 0 || node.rankAddress >= 25) continue;
    node.box = fuzzyBox(node.x, node.y, node.rankSearch);
    nodeIndex.add(node, node.box);
  }
  const postcodeIndex = new CellIndex(0.1);
  for (const entry of postcodes) postcodeIndex.add({ ...entry, box: boxOfMetres(entry.lng, entry.lat, 5_000) }, boxOfMetres(entry.lng, entry.lat, 5_000));

  const postalIndex = new CellIndex(0.02);
  for (const area of postal) postalIndex.add(area, area.geom.bbox);

  // get_nearest_postcode: a mapped postcode area that covers the point, else the nearest
  // postcode centre whose 5 km box covers it.
  function nearestPostcode(x, y) {
    let best = null;
    let bestDistance = Infinity;
    for (const area of postalIndex.query([x, y, x, y], [])) {
      if (!area.geom.covers(x, y)) continue;
      const distance = Math.hypot(area.x - x, area.y - y);
      if (distance < bestDistance || (distance === bestDistance && area.postcode < best.postcode)) { best = area; bestDistance = distance; }
    }
    if (best) return best.postcode;
    for (const entry of postcodeIndex.query([x, y, x, y], [])) {
      const box = entry.box;
      if (x < box[0] || x > box[2] || y < box[1] || y > box[3]) continue;
      const distance = Math.hypot(entry.lng - x, entry.lat - y);
      if (distance < bestDistance || (distance === bestDistance && entry.postcode < best.postcode)) { best = entry; bestDistance = distance; }
    }
    return best ? best.postcode : null;
  }

  const found = [];
  const nearby = [];
  // What Nominatim stores for a street when it indexes it: every candidate line of its
  // address, and which one it chose for each rank.
  function rowsOf(street) {
    const coords = street.coords;
    const line = !street.closed;
    let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
    for (let index = 0; index < coords.length; index += 2) {
      if (coords[index] < minX) minX = coords[index];
      if (coords[index] > maxX) maxX = coords[index];
      if (coords[index + 1] < minY) minY = coords[index + 1];
      if (coords[index + 1] > maxY) maxY = coords[index + 1];
    }
    const [cx, cy] = street.centroid || (street.centroid = line ? centreOfLine(coords) : new Area([[coords]]).pointOnSurface());
    // A line is matched on its whole length, anything else on its centre.
    const box = line ? [minX, minY, maxX, maxY] : [cx, cy, cx, cy];
    const candidates = [];
    for (const area of areaIndex.query(box, found)) {
      if (area.rankAddress <= 0 || area.rankAddress >= 25) continue;
      if (!(line ? lineMeetsArea(area.geom, coords) : area.geom.covers(cx, cy))) continue;
      const toCentre = line ? lineDistance(coords, area.x, area.y) : Math.hypot(cx - area.x, cy - area.y);
      candidates.push({ place: area, guess: false, distance: area.geom.distanceTo(cx, cy) + 0.00001 * toCentre });
    }
    for (const node of nodeIndex.query(box, nearby)) {
      if (!(line ? lineMeetsBox(coords, node.box) : cx >= node.box[0] && cx <= node.box[2] && cy >= node.box[1] && cy <= node.box[3])) continue;
      candidates.push({ place: node, guess: true, distance: line ? lineDistance(coords, node.x, node.y) : Math.hypot(cx - node.x, cy - node.y) });
    }
    // Among towns and villages, which share rank 16, a city counts as four times nearer
    // than it is and a town as twice.
    const weight = ({ place, distance }) => distance * (place.rankAddress !== 16 ? 1
      : place.rankSearch === 15 ? 0.2 : place.rankSearch === 16 ? 0.25 : place.rankSearch === 18 ? 0.5 : 1);
    candidates.sort((left, right) => left.place.rankAddress - right.place.rankAddress
      || left.guess - right.guess || weight(left) - weight(right) || (left.place.key < right.place.key ? -1 : 1));
    const have = new Set();
    const nearest = new Map();
    let boundary = null;
    let nodeBox = null;
    let postcode = null;
    const rows = [];
    for (const candidate of candidates) {
      const { place, guess, distance } = candidate;
      const rank = place.rankAddress;
      if (guess && boundary && !boundary.contains(place.x, place.y)) continue;
      let isAddress = !have.has(rank);
      if (guess) {
        const least = nearest.has(rank) ? nearest.get(rank) : 1;
        if (!isAddress && distance > 2 * least) continue;
        if (distance < least) nearest.set(rank, distance);
      }
      if (isAddress) {
        if (guess && nodeBox) {
          isAddress = place.x >= nodeBox[0] && place.x <= nodeBox[2] && place.y >= nodeBox[1] && place.y <= nodeBox[3];
        }
        if (!guess && boundary) isAddress = boundary.contains(place.x, place.y);
      }
      if (isAddress) {
        have.add(rank);
        if (place.postcode) postcode = place.postcode;
        if (guess) nodeBox = place.box;
        else { nodeBox = null; boundary = place.geom; }
      }
      rows.push({ place, guess, distance, isAddress, order: 0 });
    }
    return { rows, postcode: street.postcode || postcode || nearestPostcode(cx, cy) };
  }

  // What Nominatim reads back for a point that snapped to the street at (x, y): one line
  // per rank. An area that contains the point comes before the line chosen at indexing
  // when that line is not such an area itself, so a street that crosses a ward boundary
  // answers with the ward the point is in.
  function readBack(street, stored, x, y, trace = null) {
    const { rows, postcode } = stored;
    for (const row of rows) {
      row.order = row.isAddress ? 0 : !row.guess && row.place.geom.containsStrictly(x, y) ? 1 : -1;
    }
    const sorted = [...rows].sort((left, right) => right.place.rankAddress - left.place.rankAddress || right.order - left.order
      || left.guess - right.guess || right.distance - left.distance || right.place.rankSearch - left.place.rankSearch);
    if (trace) {
      trace.push(...sorted.map((row) => ({
        key: row.place.key, name: displayName(row.place.names), kind: `${row.place.cls}/${row.place.kind}`, label: labelOf(row.place),
        rank: row.place.rankAddress, search: row.place.rankSearch, node: row.guess, distance: row.distance, chosen: row.isAddress, order: row.order,
      })));
    }
    const parts = {};
    if (street.display) parts.road = street.display;
    let rank = -1;
    for (const { place } of sorted) {
      if (place.rankAddress === rank) continue;
      rank = place.rankAddress;
      const name = displayName(place.names);
      const label = labelOf(place);
      if (name && !(label in parts)) parts[label] = name;
      if (place.names["ISO3166-2"] && place.cls === "boundary" && place.adminLevel === 4) parts.iso = place.names["ISO3166-2"];
    }
    if (postcode) parts.postcode = postcode;
    return parts;
  }

  // Every line of the address of a point on a street, label to name. Without a point,
  // the street's own centre.
  function addressOf(street, x = null, y = null, trace = null) {
    const stored = rowsOf(street);
    return readBack(street, stored, x ?? street.centroid[0], y ?? street.centroid[1], trace);
  }

  // The street cut where its address changes: [{ coords, parts }], in order along it.
  // Most streets are one piece. One that runs through an area it was not filed under at
  // indexing (the next ward, say) is cut at that area's edge.
  function piecesOf(street) {
    const stored = rowsOf(street);
    const coords = street.coords;
    const others = street.closed ? [] : stored.rows.filter((row) => !row.isAddress && !row.guess).map((row) => row.place.geom);
    if (!others.length) return [{ coords, parts: readBack(street, stored, street.centroid[0], street.centroid[1]) }];
    const pieces = [];
    const answers = new Map();
    let current = null;
    const turns = [];
    for (let index = 2; index < coords.length; index += 2) {
      const ax = coords[index - 2]; const ay = coords[index - 1]; const bx = coords[index]; const by = coords[index + 1];
      turns.length = 0;
      for (const area of others) area.crossingTurns(ax, ay, bx, by, turns);
      turns.sort((left, right) => left - right);
      turns.push(1);
      let from = 0;
      for (const to of turns) {
        if (to <= from && to !== 1) continue;
        const mx = ax + ((from + to) / 2) * (bx - ax);
        const my = ay + ((from + to) / 2) * (by - ay);
        let key = "";
        for (let other = 0; other < others.length; other += 1) if (others[other].containsStrictly(mx, my)) key += `${other},`;
        if (!answers.has(key)) answers.set(key, readBack(street, stored, mx, my));
        const sx = ax + from * (bx - ax); const sy = ay + from * (by - ay);
        const ex = ax + to * (bx - ax); const ey = ay + to * (by - ay);
        if (!current || current.key !== key) {
          current = { key, points: [sx, sy], parts: answers.get(key) };
          pieces.push(current);
        }
        if (to > from) current.points.push(ex, ey);
        from = to;
      }
    }
    return pieces.filter((piece) => piece.points.length >= 4).map((piece) => ({ coords: Float64Array.from(piece.points), parts: piece.parts }));
  }

  return { addressOf, piecesOf, nearestPostcode, places: all, areas, nodes, area: (key) => areas.find((area) => area.key === key) || null };
}

// ---------------------------------------------------------------------------------------
// Tiles.

function varint(out, value) {
  let rest = value >>> 0;
  while (rest >= 128) { out.push((rest & 127) | 128); rest >>>= 7; }
  out.push(rest);
}
const zigzag = (value) => ((value << 1) ^ (value >> 31)) >>> 0;

const sha256 = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");

function encodeTile({ region, key, ix, iy, tileDegrees, streets, tupleOf, coverage }) {
  const originX = Math.round((ix * tileDegrees - 180) * SCALE);
  const originY = Math.round((iy * tileDegrees - 90) * SCALE);
  // Oldest way first; the pieces of one way keep their order along it (the sort is stable).
  streets.sort((left, right) => left.id - right.id);
  // Strings by how often they are used, so the common ones take one byte to name.
  const uses = new Map();
  const use = (value) => { if (value) uses.set(value, (uses.get(value) || 0) + 1); };
  const tupleIndex = new Map();
  const tuples = [];
  for (const street of streets) {
    use(street.display);
    use(street.ref);
    if (!tupleIndex.has(street.tuple)) { tupleIndex.set(street.tuple, tuples.length); tuples.push(street.tuple); }
  }
  for (const tuple of tuples) for (const value of tupleOf.get(tuple)) use(value);
  const strings = ["", ...[...uses.entries()].sort((left, right) => right[1] - left[1] || (left[0] < right[0] ? -1 : 1)).map(([value]) => value)];
  const stringIndex = new Map(strings.map((value, index) => [value, index]));
  const tupleBytes = [];
  for (const tuple of tuples) for (const value of tupleOf.get(tuple)) varint(tupleBytes, value ? stringIndex.get(value) : 0);
  const streetBytes = [];
  for (const street of streets) {
    const flags = (street.closed ? FLAG_CLOSED : 0) | (street.rank === 27 ? FLAG_MINOR : 0) | (street.display ? FLAG_NAME : 0)
      | (street.ref ? FLAG_REF : 0) | (street.display && !street.hasNameTag ? FLAG_NO_NAME_TAG : 0);
    varint(streetBytes, flags);
    if (street.display) varint(streetBytes, stringIndex.get(street.display));
    if (street.ref) varint(streetBytes, stringIndex.get(street.ref));
    varint(streetBytes, tupleIndex.get(street.tuple));
    if (street.closed) {
      varint(streetBytes, zigzag(Math.round(street.centroid[0] * SCALE) - originX));
      varint(streetBytes, zigzag(Math.round(street.centroid[1] * SCALE) - originY));
    }
    const units = street.units;
    varint(streetBytes, units.length / 2);
    let x = originX;
    let y = originY;
    for (let index = 0; index < units.length; index += 2) {
      varint(streetBytes, zigzag(units[index] - x));
      varint(streetBytes, zigzag(units[index + 1] - y));
      x = units[index];
      y = units[index + 1];
    }
  }
  const stringBytes = Buffer.from(`${strings.join("\n")}\n`, "utf8");
  const header = {
    format: TILE_FORMAT, version: TILE_VERSION, region, tile: key,
    origin: [ix * tileDegrees - 180, iy * tileDegrees - 90], tile_degrees: tileDegrees, scale: SCALE,
    cell_degrees: CELL_DEGREES, grid_margin_cells: GRID_MARGIN_CELLS, coverage: { cols: coverage.cols, rows: coverage.rows },
    counts: { streets: streets.length, strings: strings.length, tuples: tuples.length },
    tuple_fields: TUPLE_FIELDS, sections: {},
  };
  const sections = [["strings", stringBytes], ["tuples", Buffer.from(tupleBytes)], ["coverage", Buffer.from(coverage.bits)],
    ["streets", Buffer.from(streetBytes)]];
  // The header names where each section starts, which depends on the header's own
  // length: it is padded to a fixed size so the offsets can be written into it.
  const headerRoom = 1_024;
  let at = 8 + headerRoom;
  for (const [name, bytes] of sections) { header.sections[name] = [at, bytes.length]; at += bytes.length; }
  const headerBytes = Buffer.from(JSON.stringify(header).padEnd(headerRoom, " "), "utf8");
  if (headerBytes.length !== headerRoom) throw new Error(`tile header of ${key} does not fit ${headerRoom} bytes`);
  const prefix = Buffer.alloc(8);
  prefix.write(TILE_MAGIC, 0, "latin1");
  prefix.writeUInt32LE(headerRoom, 4);
  return Buffer.concat([prefix, headerBytes, ...sections.map(([, bytes]) => bytes)]);
}

// Builds one region's directory from OpenStreetMap features (GeoJSON, tags as properties
// with "@type" and "@id", as `osmium export` writes them). `features` may be an array or
// an async iterable; `postcodes` is [{ postcode, lng, lat }], the postcode centres.
export async function buildRegion({
  directory = STREETS_ROOT, region, features, relationLabels = new Map(), postcodes = [], provenance = EXTRACT,
  tileDegrees = TILE_DEGREES, simplifyMetres = SIMPLIFY_METRES, log = console.log,
}) {
  const streets = [];
  const places = [];
  const postcodeAreas = [];
  let read = 0;
  for await (const feature of features) {
    read += 1;
    const street = streetFromFeature(feature);
    if (street) { streets.push(street); continue; }
    const place = placeFromFeature(feature);
    if (place) { places.push(place); continue; }
    const postal = postcodeAreaFromFeature(feature);
    if (postal) postcodeAreas.push(postal);
  }
  log(`${region.id}: ${read} features read, ${streets.length} streets, ${places.length} named places and areas`);
  const model = createAddressModel({ places, postcodeAreas, relationLabels, postcodes });

  // Coverage: the cells of a fixed grid whose centre is inside the region's polygon or
  // boxes. Streets are kept a few cells beyond it.
  let covers;
  let extent;
  if (region.relation) {
    const outline = model.area(`R${region.relation}`);
    if (!outline) throw new Error(`${region.id}: boundary relation ${region.relation} is not in the features`);
    covers = (x, y) => outline.geom.contains(x, y);
    extent = outline.geom.bbox;
  } else {
    covers = (x, y) => region.boxes.some((box) => x >= box[0] && x < box[2] && y >= box[1] && y < box[3]);
    extent = [Math.min(...region.boxes.map((box) => box[0])), Math.min(...region.boxes.map((box) => box[1])),
      Math.max(...region.boxes.map((box) => box[2])), Math.max(...region.boxes.map((box) => box[3]))];
  }
  const cellsPerTile = Math.round(tileDegrees / CELL_DEGREES);
  const cellOfX = (x) => Math.floor((x + 180) / CELL_DEGREES + 1e-9);
  const cellOfY = (y) => Math.floor((y + 90) / CELL_DEGREES + 1e-9);
  const gx0 = cellOfX(extent[0]) - COVERAGE_MARGIN_CELLS - 1;
  const gy0 = cellOfY(extent[1]) - COVERAGE_MARGIN_CELLS - 1;
  const gcols = cellOfX(extent[2]) + COVERAGE_MARGIN_CELLS + 2 - gx0;
  const grows = cellOfY(extent[3]) + COVERAGE_MARGIN_CELLS + 2 - gy0;
  const inside = new Uint8Array(gcols * grows);
  for (let row = 0; row < grows; row += 1) {
    const y = (gy0 + row + 0.5) * CELL_DEGREES - 90;
    for (let col = 0; col < gcols; col += 1) {
      if (covers((gx0 + col + 0.5) * CELL_DEGREES - 180, y)) inside[row * gcols + col] = 1;
    }
  }
  // Dilate by the margin, one axis at a time.
  const wide = new Uint8Array(gcols * grows);
  const near = new Uint8Array(gcols * grows);
  for (let row = 0; row < grows; row += 1) {
    for (let col = 0; col < gcols; col += 1) {
      for (let step = -COVERAGE_MARGIN_CELLS; step <= COVERAGE_MARGIN_CELLS; step += 1) {
        const other = col + step;
        if (other >= 0 && other < gcols && inside[row * gcols + other]) { wide[row * gcols + col] = 1; break; }
      }
    }
  }
  for (let row = 0; row < grows; row += 1) {
    for (let col = 0; col < gcols; col += 1) {
      for (let step = -COVERAGE_MARGIN_CELLS; step <= COVERAGE_MARGIN_CELLS; step += 1) {
        const other = row + step;
        if (other >= 0 && other < grows && wide[other * gcols + col]) { near[row * gcols + col] = 1; break; }
      }
    }
  }
  const nearCoverage = (x, y) => {
    const col = cellOfX(x) - gx0;
    const row = cellOfY(y) - gy0;
    return col >= 0 && row >= 0 && col < gcols && row < grows && near[row * gcols + col] === 1;
  };

  // Each kept street: its address, its simplified geometry in integer units, and the
  // tiles whose grid it reaches.
  const tupleOf = new Map();
  const byTile = new Map();
  const margin = GRID_MARGIN_CELLS * CELL_DEGREES;
  let kept = 0;
  let records = 0;
  let named = 0;
  let pointsBefore = 0;
  let pointsAfter = 0;
  for (const street of streets) {
    const coords = street.coords;
    let wanted = false;
    let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
    for (let index = 0; index < coords.length; index += 2) {
      if (!wanted && nearCoverage(coords[index], coords[index + 1])) wanted = true;
      if (coords[index] < minX) minX = coords[index];
      if (coords[index] > maxX) maxX = coords[index];
      if (coords[index + 1] < minY) minY = coords[index + 1];
      if (coords[index + 1] > maxY) maxY = coords[index + 1];
    }
    if (!wanted) { street.coords = null; continue; }
    kept += 1;
    if (street.display) named += 1;
    pointsBefore += coords.length / 2;
    // One record per stretch of the street that has one address.
    for (const piece of model.piecesOf(street)) {
      const values = TUPLE_FIELDS.map((field) => piece.parts[field] || "");
      const tuple = values.join("\u0001");
      if (!tupleOf.has(tuple)) tupleOf.set(tuple, values);
      const simple = simplify(piece.coords, simplifyMetres);
      const units = [];
      let pieceMinX = Infinity; let pieceMinY = Infinity; let pieceMaxX = -Infinity; let pieceMaxY = -Infinity;
      for (let index = 0; index < simple.length; index += 2) {
        if (simple[index] < pieceMinX) pieceMinX = simple[index];
        if (simple[index] > pieceMaxX) pieceMaxX = simple[index];
        if (simple[index + 1] < pieceMinY) pieceMinY = simple[index + 1];
        if (simple[index + 1] > pieceMaxY) pieceMaxY = simple[index + 1];
        const x = Math.round(simple[index] * SCALE);
        const y = Math.round(simple[index + 1] * SCALE);
        if (units.length && units[units.length - 2] === x && units[units.length - 1] === y) continue;
        units.push(x, y);
      }
      pointsAfter += units.length / 2;
      records += 1;
      const record = {
        id: street.id, rank: street.rank, closed: street.closed, centroid: street.centroid, display: street.display,
        ref: street.ref, hasNameTag: street.hasNameTag, tuple, units,
      };
      const tx0 = Math.floor((pieceMinX - margin + 180) / tileDegrees); const tx1 = Math.floor((pieceMaxX + margin + 180) / tileDegrees);
      const ty0 = Math.floor((pieceMinY - margin + 90) / tileDegrees); const ty1 = Math.floor((pieceMaxY + margin + 90) / tileDegrees);
      for (let ix = tx0; ix <= tx1; ix += 1) {
        for (let iy = ty0; iy <= ty1; iy += 1) {
          const key = `${ix}_${iy}`;
          if (!byTile.has(key)) byTile.set(key, { ix, iy, streets: [] });
          byTile.get(key).streets.push(record);
        }
      }
    }
    street.coords = null;
    if (kept % 200_000 === 0) log(`${region.id}: ${kept} streets addressed`);
  }

  const out = path.join(directory, region.id);
  fs.mkdirSync(out, { recursive: true });
  const tiles = {};
  let bytesTotal = 0;
  // Every tile with a covered cell is written, streets or none: open country is answered
  // "no street here", not left to look like a region that was never packaged.
  for (let iy = Math.floor(gy0 / cellsPerTile); iy <= Math.floor((gy0 + grows) / cellsPerTile); iy += 1) {
    for (let ix = Math.floor(gx0 / cellsPerTile); ix <= Math.floor((gx0 + gcols) / cellsPerTile); ix += 1) {
      if (!byTile.has(`${ix}_${iy}`)) byTile.set(`${ix}_${iy}`, { ix, iy, streets: [] });
    }
  }
  for (const key of [...byTile.keys()].sort()) {
    const { ix, iy, streets: members } = byTile.get(key);
    // The tile's own slice of the coverage grid. A tile nothing of which is covered is
    // never asked, so it is not written.
    const bits = new Uint8Array(Math.ceil((cellsPerTile * cellsPerTile) / 8));
    let any = false;
    for (let row = 0; row < cellsPerTile; row += 1) {
      for (let col = 0; col < cellsPerTile; col += 1) {
        const gcol = ix * cellsPerTile + col - gx0;
        const grow = iy * cellsPerTile + row - gy0;
        if (gcol < 0 || grow < 0 || gcol >= gcols || grow >= grows || !inside[grow * gcols + gcol]) continue;
        const bit = row * cellsPerTile + col;
        bits[bit >> 3] |= 1 << (bit & 7);
        any = true;
      }
    }
    if (!any) continue;
    const bytes = encodeTile({
      region: region.id, key, ix, iy, tileDegrees, streets: members, tupleOf,
      coverage: { cols: cellsPerTile, rows: cellsPerTile, bits },
    });
    const file = `t_${key}.bin`;
    fs.writeFileSync(path.join(out, file), bytes);
    tiles[key] = { file, bytes: bytes.length, sha256: sha256(bytes), streets: members.length };
    bytesTotal += bytes.length;
  }
  for (const name of fs.readdirSync(out)) {
    if (/^t_.*\.bin$/.test(name) && !Object.values(tiles).some((tile) => tile.file === name)) fs.rmSync(path.join(out, name));
  }
  const manifest = {
    format: REGION_FORMAT, version: 1, region: region.id, name: region.name || region.id,
    country: "India", country_code: "in",
    provenance: {
      source: provenance.source, licence: provenance.licence || EXTRACT.licence,
      distributor: provenance.distributor || EXTRACT.distributor,
      extract_url: provenance.extract_url, extract_date: provenance.extract_date, extract_md5: provenance.extract_md5,
      rules: provenance.rules || EXTRACT.rules,
      coverage: region.relation ? `https://www.openstreetmap.org/relation/${region.relation}` : region.boxes,
      built_by: "infra/aws-central/tools/build-street-index.mjs",
    },
    tile_format: TILE_FORMAT, tile_version: TILE_VERSION, tile_degrees: tileDegrees, cell_degrees: CELL_DEGREES,
    scale: SCALE, simplify_metres: simplifyMetres, snap_limit_degrees: SNAP_LIMIT_DEGREES,
    counts: {
      streets: kept, named_streets: named, street_pieces: records, places: model.nodes.length, areas: model.areas.length,
      address_tuples: tupleOf.size, points_before_simplifying: pointsBefore, points: pointsAfter,
      tiles: Object.keys(tiles).length, bytes: bytesTotal,
    },
    tiles,
  };
  fs.writeFileSync(path.join(out, "manifest.json"), `${JSON.stringify(manifest, null, 1)}\n`);
  log(`${region.id}: ${kept} streets (${named} named) in ${Object.keys(tiles).length} tiles, ${bytesTotal} bytes`);
  return manifest;
}

// ---------------------------------------------------------------------------------------
// Command line: fetch, prepare, build, sizes.

function run(command, args) {
  console.log(`$ ${command} ${args.join(" ")}`);
  const result = spawnSync(command, args, { stdio: "inherit" });
  if (result.status !== 0) throw new Error(`${command} exited with ${result.status}`);
}

const extractFile = () => path.join(WORK, path.basename(EXTRACT.extract_url));
const cutBox = (region) => {
  const box = region.cut || [Math.min(...region.boxes.map((b) => b[0])), Math.min(...region.boxes.map((b) => b[1])),
    Math.max(...region.boxes.map((b) => b[2])), Math.max(...region.boxes.map((b) => b[3]))];
  return [box[0] - CUT_MARGIN_DEGREES, box[1] - CUT_MARGIN_DEGREES, box[2] + CUT_MARGIN_DEGREES, box[3] + CUT_MARGIN_DEGREES];
};

function fetchExtract() {
  fs.mkdirSync(WORK, { recursive: true });
  const file = extractFile();
  run("curl", ["-sS", "-L", "--fail", "-C", "-", "-A",
    "PotholeReporter-data/1 (+https://coding-parrot.github.io/pothole-reporter/; contact@aiengg.dev)", "-o", file, EXTRACT.extract_url]);
  const hash = crypto.createHash("md5");
  const fd = fs.openSync(file, "r");
  const chunk = Buffer.alloc(1 << 22);
  for (let size = fs.readSync(fd, chunk); size > 0; size = fs.readSync(fd, chunk)) hash.update(chunk.subarray(0, size));
  fs.closeSync(fd);
  const md5 = hash.digest("hex");
  if (md5 !== EXTRACT.extract_md5) throw new Error(`extract MD5 is ${md5}, expected ${EXTRACT.extract_md5}`);
  console.log(`extract verified: ${file}`);
}

async function* lines(file) {
  const reader = readline.createInterface({ input: fs.createReadStream(file), crlfDelay: Infinity });
  for await (const line of reader) if (line) yield line;
}

// The mean position of every object in India tagged with each postcode, as Nominatim's
// postcode table has it (tools/postcodes.py): coordinates summed as seven-digit integers.
async function averagePostcodes(file, areaFile) {
  // A postcode that is mapped as an area has no centre of its own, and an object inside
  // any postcode area does not count towards another postcode's centre.
  const areas = [];
  for await (const line of lines(areaFile)) {
    const area = postcodeAreaFromFeature(JSON.parse(line));
    if (area) areas.push(area);
  }
  const mapped = new Set(areas.map((area) => area.postcode));
  const index = new CellIndex(0.05);
  for (const area of areas) index.add(area, area.geom.bbox);
  const within = [];
  const sums = new Map();
  for await (const line of lines(file)) {
    const feature = JSON.parse(line);
    const tags = feature.properties;
    if (tags.boundary === "postal_code" && tags["@type"] === "relation") continue;
    const raw = [tags["addr:postcode"], tags.postcode, tags.postal_code].find((value) => typeof value === "string" && value);
    if (!raw || raw.includes(",") || raw.includes(";")) continue;
    const code = postcodeOf({ postcode: raw });
    if (!code) continue;
    const geometry = feature.geometry;
    let centre = null;
    if (geometry.type === "Point") centre = geometry.coordinates;
    else if (geometry.type === "LineString") {
      const coords = flat(geometry.coordinates);
      const last = coords.length - 2;
      centre = coords.length >= 8 && coords[0] === coords[last] && coords[1] === coords[last + 1]
        ? new Area([[coords]]).pointOnSurface() : lineMidpoint(coords);
    } else if (geometry.type === "Polygon" || geometry.type === "MultiPolygon") centre = new Area(polygonsOf(geometry)).pointOnSurface();
    if (!centre || mapped.has(code)) continue;
    if (index.query([centre[0], centre[1], centre[0], centre[1]], within).some((area) => area.geom.covers(centre[0], centre[1]))) continue;
    const sum = sums.get(code) || [0, 0, 0];
    sum[0] += Math.trunc(centre[0] * 1e7);
    sum[1] += Math.trunc(centre[1] * 1e7);
    sum[2] += 1;
    sums.set(code, sum);
  }
  console.log(`postcode areas: ${areas.length} mapped, ${mapped.size} postcodes`);
  return [...sums.entries()].sort((left, right) => (left[0] < right[0] ? -1 : 1))
    .map(([postcode, [x, y, count]]) => ({ postcode, lng: x / count / 1e7, lat: y / count / 1e7, count }));
}

async function prepare(regions) {
  const source = extractFile();
  if (!fs.existsSync(source)) throw new Error(`${source} is missing: run "fetch" first`);
  const streetSource = path.join(WORK, "india-streets-src.osm.pbf");
  const postcodeSource = path.join(WORK, "india-postcode-src.osm.pbf");
  if (!fs.existsSync(streetSource)) {
    run("osmium", ["tags-filter", source, "w/highway", "nwr/place", "wr/boundary=administrative,postal_code",
      "wr/landuse=residential,commercial,industrial,retail,farm,farmyard,allotments", "-o", streetSource]);
  }
  if (!fs.existsSync(postcodeSource)) {
    run("osmium", ["tags-filter", source, "nwr/addr:postcode", "nwr/postal_code", "nwr/postcode", "-o", postcodeSource]);
  }
  // OpenStreetMap node ids run to 13 billion and osmium keeps one bit per possible id
  // for every cut, which was several gigabytes for ten cuts at once. With the nodes
  // renumbered 1..N (way and relation ids are kept) the same cut needs a few megabytes.
  const denseSource = path.join(WORK, "india-streets-dense.osm.pbf");
  if (!fs.existsSync(denseSource)) run("osmium", ["renumber", "-t", "node", streetSource, "-o", denseSource]);
  const exportConfig = path.join(WORK, "export-config.json");
  fs.writeFileSync(exportConfig, JSON.stringify({
    attributes: { type: true, id: true }, linear_tags: ["highway"], area_tags: ["place", "boundary", "landuse"],
  }));
  const postcodeFile = path.join(WORK, "postcodes.json");
  if (!fs.existsSync(postcodeFile)) {
    const exported = path.join(WORK, "india-postcode-src.geojsonl");
    run("osmium", ["export", postcodeSource, "-f", "geojsonseq", "-x", "print_record_separator=false", "-o", exported, "--overwrite"]);
    const areaCut = path.join(WORK, "india-postcode-areas.osm.pbf");
    const areaFile = path.join(WORK, "india-postcode-areas.geojsonl");
    run("osmium", ["tags-filter", denseSource, "r/boundary=postal_code", "-o", areaCut, "--overwrite"]);
    run("osmium", ["export", areaCut, "-c", exportConfig, "-f", "geojsonseq", "-x", "print_record_separator=false", "-o", areaFile, "--overwrite"]);
    const centres = await averagePostcodes(exported, areaFile);
    fs.writeFileSync(postcodeFile, JSON.stringify(centres));
    fs.rmSync(exported);
    console.log(`postcodes: ${centres.length} centres`);
  }
  const wanted = regions.filter((region) => !fs.existsSync(path.join(WORK, `${region.id}.geojsonl`)));
  if (wanted.length) {
    const config = path.join(WORK, "cut-config.json");
    fs.writeFileSync(config, JSON.stringify({
      directory: WORK,
      extracts: wanted.map((region) => ({ output: `${region.id}.osm.pbf`, bbox: cutBox(region) })),
    }));
    run("osmium", ["extract", "-c", config, "-s", "smart", "-S", "types=multipolygon,boundary", "--overwrite", denseSource]);
    for (const region of wanted) {
      const cut = path.join(WORK, `${region.id}.osm.pbf`);
      run("osmium", ["export", cut, "-c", exportConfig, "-f", "geojsonseq", "-x", "print_record_separator=false",
        "-o", path.join(WORK, `${region.id}.geojsonl`), "--overwrite"]);
      // Relation members are not in the export. A boundary's label node is one.
      const relations = spawnSync("osmium", ["cat", cut, "-t", "relation", "-f", "opl"], { maxBuffer: 1 << 30 });
      if (relations.status !== 0) throw new Error("osmium cat failed");
      const labels = {};
      for (const line of relations.stdout.toString("utf8").split("\n")) {
        const id = /^r(\d+) /.exec(line);
        const label = /[M,]n(\d+)@label(?:,|$)/.exec(line);
        if (id && label) labels[id[1]] = Number(label[1]);
      }
      fs.writeFileSync(path.join(WORK, `${region.id}.labels.json`), JSON.stringify(labels));
    }
  }
}

async function* featuresOf(file) {
  for await (const line of lines(file)) {
    // Most lines are tagged nodes that are neither a street nor a place.
    if (!line.includes('"highway"') && !line.includes('"place"') && !line.includes('"boundary"') && !line.includes('"landuse"')) continue;
    yield JSON.parse(line);
  }
}

async function build(regions) {
  const postcodes = JSON.parse(fs.readFileSync(path.join(WORK, "postcodes.json"), "utf8"));
  for (const region of regions) {
    const started = Date.now();
    const labels = JSON.parse(fs.readFileSync(path.join(WORK, `${region.id}.labels.json`), "utf8"));
    await buildRegion({
      region, features: featuresOf(path.join(WORK, `${region.id}.geojsonl`)),
      relationLabels: new Map(Object.entries(labels).map(([id, node]) => [Number(id), node])), postcodes,
    });
    console.log(`${region.id}: built in ${((Date.now() - started) / 1000).toFixed(1)} s`);
  }
}

function sizes() {
  let total = 0;
  for (const region of REGIONS) {
    const file = path.join(STREETS_ROOT, region.id, "manifest.json");
    if (!fs.existsSync(file)) { console.log(`${region.id.padEnd(14)} not built`); continue; }
    const { counts, tiles } = JSON.parse(fs.readFileSync(file, "utf8"));
    const largest = Math.max(...Object.values(tiles).map((tile) => tile.bytes));
    total += counts.bytes;
    console.log(`${region.id.padEnd(14)} ${String(counts.streets).padStart(8)} streets ${String(counts.named_streets).padStart(7)} named `
      + `${String(counts.tiles).padStart(4)} tiles ${(counts.bytes / 1e6).toFixed(2).padStart(7)} MB (largest tile ${(largest / 1e6).toFixed(2)} MB)`);
  }
  console.log(`${"total".padEnd(14)} ${(total / 1e6).toFixed(2)} MB`);
}

async function main() {
  const [command, ...args] = process.argv.slice(2);
  const ids = args.flatMap((value, index) => (args[index - 1] === "--region" ? [value] : []));
  const regions = ids.length ? ids.map((id) => {
    const region = REGIONS.find((entry) => entry.id === id);
    if (!region) throw new Error(`unknown region ${id}: one of ${REGIONS.map((entry) => entry.id).join(", ")}`);
    return region;
  }) : [...REGIONS];
  if (command === "fetch") fetchExtract();
  else if (command === "prepare") await prepare(regions);
  else if (command === "build") await build(regions);
  else if (command === "sizes") sizes();
  else {
    console.error("usage: build-street-index.mjs fetch | prepare [--region <id>]... | build [--region <id>]... | sizes");
    process.exitCode = 2;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
}
