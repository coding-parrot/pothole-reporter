import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// The street and locality of a point, answered from OpenStreetMap data packaged with the
// service instead of a call to the public Nominatim server.
//
// The data is what Nominatim itself serves, and the answer is meant to be the one
// Nominatim gives, so the rules are Nominatim's (5.3.0, read from its source):
//
//   which street    the nearest way Nominatim imports as a street, named or not, within
//                   0.007 degrees, measured in plain degrees of longitude and latitude
//                   as PostGIS does (reverse.py, lookup_street_poi). A street with no
//                   name answers no road. A closed way is a polygon: a point inside it
//                   is at distance zero, and it counts only when its centre is within
//                   the same 0.007 degrees.
//   which locality  the address Nominatim computed for that street when it indexed it,
//                   not anything about the point. tools/build-street-index.mjs does that
//                   computation (insert_addresslines and what reads it back) once, at
//                   build time, and stores the result beside the street.
//
// One file per quarter-degree tile per region, read on first use and checked against
// the SHA-256 its region's manifest recorded. A Bengaluru request reads one tile of
// Karnataka and nothing of Delhi.

export const STREET_DIRECTORY = new URL("../../../data/streets/", import.meta.url);
export const REGION_FORMAT = "pothole-street-address-region";
export const TILE_FORMAT = "pothole-street-address-tile";
export const TILE_VERSION = 1;
export const TILE_MAGIC = "PSA1";

// 0.006 degrees is Nominatim's search distance for a street and 0.001 what it adds to
// collect near ties; the nearest row of that wider search is its answer.
export const SNAP_LIMIT_DEGREES = 0.007;
// Past the snap limit Nominatim answers from the area polygons around the point. This
// module has no polygons at run time, so it answers with the area of the nearest street
// up to this far (about 3 km), and says that it did (basis "area").
export const AREA_LIMIT_DEGREES = 0.03;
// How far a named street may be to be offered beside an unnamed answer (about 220 m).
export const NAMED_LIMIT_DEGREES = 0.002;

// The address keys a tile stores for each street, as Nominatim labels them. "iso" is the
// state's ISO 3166-2 code and comes back as "ISO3166-2-lvl4".
export const TUPLE_FIELDS = Object.freeze(["neighbourhood", "residential", "quarter", "hamlet", "suburb",
  "village", "town", "city", "municipality", "state", "iso", "postcode"]);
// What is still true of open ground near a street: the parts at suburb level and above.
const AREA_FIELDS = new Set(["hamlet", "suburb", "village", "town", "city", "municipality", "state", "iso", "postcode"]);

export const FLAG_CLOSED = 1; // a closed way: a polygon to Nominatim
export const FLAG_MINOR = 2; // address rank 27 (service road, path, link) and not 26
export const FLAG_NAME = 4; // has a name to show
export const FLAG_REF = 8; // has a ref
export const FLAG_NO_NAME_TAG = 16; // the name shown is another of its names (its ref, usually): the way has no `name`

export function tileKeyOf(lat, lng, tileDegrees) {
  return `${Math.floor((lng + 180) / tileDegrees)}_${Math.floor((lat + 90) / tileDegrees)}`;
}

const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

function parseTile(bytes) {
  if (bytes.length < 8 || bytes.toString("latin1", 0, 4) !== TILE_MAGIC) throw new Error("not a street tile");
  const headerLength = bytes.readUInt32LE(4);
  if (8 + headerLength > bytes.length) throw new Error("truncated street tile header");
  const header = JSON.parse(bytes.toString("utf8", 8, 8 + headerLength));
  if (header.format !== TILE_FORMAT || header.version !== TILE_VERSION) throw new Error("not this street tile format");
  if (!(header.scale > 0) || !(header.cell_degrees > 0) || !Array.isArray(header.origin)
      || JSON.stringify(header.tuple_fields) !== JSON.stringify(TUPLE_FIELDS)) throw new Error("street tile header is incomplete");
  for (const name of ["strings", "tuples", "coverage", "streets"]) {
    const section = header.sections?.[name];
    if (!Array.isArray(section) || !(section[0] >= 8 + headerLength) || section[0] + section[1] > bytes.length) {
      throw new Error(`street tile section ${name} is out of bounds`);
    }
  }
  const fields = TUPLE_FIELDS.length;
  // Strings are UTF-8 separated by line feeds; string 0 is empty and means "none".
  const [stringsAt, stringsLength] = header.sections.strings;
  const stringStart = new Uint32Array(header.counts.strings + 1);
  let found = 0;
  stringStart[0] = stringsAt;
  for (let at = stringsAt; at < stringsAt + stringsLength && found < header.counts.strings; at += 1) {
    if (bytes[at] === 10) { found += 1; stringStart[found] = at + 1; }
  }
  if (found !== header.counts.strings) throw new Error("street tile strings are short");
  const tuples = new Uint32Array(header.counts.tuples * fields);
  let at = header.sections.tuples[0];
  for (let index = 0; index < tuples.length; index += 1) {
    let value = 0;
    let shift = 0;
    let byte;
    do { byte = bytes[at]; at += 1; value |= (byte & 127) << shift; shift += 7; } while (byte & 128);
    tuples[index] = value >>> 0;
  }
  const scale = header.scale;
  const cell = Math.round(header.cell_degrees * scale);
  const margin = header.grid_margin_cells;
  const cols = header.coverage.cols + 2 * margin;
  const rows = header.coverage.rows + 2 * margin;
  const tile = {
    header, bytes, stringStart, tuples, scale, cell, cols, rows, margin,
    streetOffset: new Uint32Array(header.counts.streets),
    cellStart: new Uint32Array(cols * rows + 1),
    cellItems: null,
    seen: new Uint32Array(header.counts.streets),
    round: 0,
  };
  indexStreets(tile);
  return tile;
}

// Variable-length integers, read through one shared cursor so the hot loops allocate
// nothing: 7 bits a byte, low bits first, zigzag for signed values.
let source = null;
let cursor = 0;
function unsigned() {
  let value = 0;
  let shift = 0;
  let byte;
  do { byte = source[cursor]; cursor += 1; value |= (byte & 127) << shift; shift += 7; } while (byte & 128);
  return value >>> 0;
}
function signed() {
  const raw = unsigned();
  return (raw >>> 1) ^ -(raw & 1);
}

// One pass over the streets notes every (cell, street) pair; the pairs are then counted
// and dealt into one list per cell. A street is filed under every cell a segment's box
// overlaps; anything past the grid's edge is filed under the edge cell.
function indexStreets(tile) {
  const { header, cols, rows, cell, margin } = tile;
  const limit = SNAP_LIMIT_DEGREES * tile.scale;
  const end = header.sections.streets[0] + header.sections.streets[1];
  const lastStreet = new Int32Array(cols * rows).fill(-1);
  const counts = new Uint32Array(cols * rows);
  const maxCol = cols - 1;
  const maxRow = rows - 1;
  let pairs = new Uint32Array(Math.max(1_024, header.counts.streets * 4));
  let used = 0;
  let street = 0;
  // File the current street under every cell of a box given in tile units.
  const file = (minX, maxX, minY, maxY) => {
    let x0 = Math.floor(minX / cell) + margin; if (x0 < 0) x0 = 0; else if (x0 > maxCol) x0 = maxCol;
    let x1 = Math.floor(maxX / cell) + margin; if (x1 < 0) x1 = 0; else if (x1 > maxCol) x1 = maxCol;
    let y0 = Math.floor(minY / cell) + margin; if (y0 < 0) y0 = 0; else if (y0 > maxRow) y0 = maxRow;
    let y1 = Math.floor(maxY / cell) + margin; if (y1 < 0) y1 = 0; else if (y1 > maxRow) y1 = maxRow;
    for (let cy = y0; cy <= y1; cy += 1) {
      for (let cx = x0; cx <= x1; cx += 1) {
        const index = cy * cols + cx;
        if (lastStreet[index] === street) continue;
        lastStreet[index] = street;
        counts[index] += 1;
        if (used + 2 > pairs.length) { const grown = new Uint32Array(pairs.length * 2); grown.set(pairs); pairs = grown; }
        pairs[used] = index;
        pairs[used + 1] = street;
        used += 2;
      }
    }
  };
  source = tile.bytes;
  cursor = header.sections.streets[0];
  for (street = 0; street < header.counts.streets; street += 1) {
    if (cursor >= end) throw new Error("street tile streets are short");
    tile.streetOffset[street] = cursor;
    const flags = unsigned();
    if (flags & FLAG_NAME) unsigned();
    if (flags & FLAG_REF) unsigned();
    unsigned();
    const closed = (flags & FLAG_CLOSED) !== 0;
    const centreX = closed ? signed() : 0;
    const centreY = closed ? signed() : 0;
    const points = unsigned();
    let x = signed();
    let y = signed();
    let minX = x; let maxX = x; let minY = y; let maxY = y;
    if (points === 1) file(x, x, y, y);
    for (let point = 1; point < points; point += 1) {
      const nx = x + signed();
      const ny = y + signed();
      file(x < nx ? x : nx, x < nx ? nx : x, y < ny ? y : ny, y < ny ? ny : y);
      x = nx; y = ny;
      if (x < minX) minX = x; else if (x > maxX) maxX = x;
      if (y < minY) minY = y; else if (y > maxY) maxY = y;
    }
    // A closed way is a polygon: a point inside it is at distance zero, wherever its
    // edges are. It counts only within the snap limit of its centre, so it is also
    // filed under the cells of its box that are that near the centre.
    if (closed && Math.max(minX, centreX - limit) <= Math.min(maxX, centreX + limit)
        && Math.max(minY, centreY - limit) <= Math.min(maxY, centreY + limit)) {
      file(Math.max(minX, centreX - limit), Math.min(maxX, centreX + limit),
        Math.max(minY, centreY - limit), Math.min(maxY, centreY + limit));
    }
  }
  source = null;
  if (cursor !== end) throw new Error("street tile streets do not end where the header says");
  let total = 0;
  for (let index = 0; index < counts.length; index += 1) { tile.cellStart[index] = total; total += counts[index]; }
  tile.cellStart[counts.length] = total;
  tile.cellItems = new Uint32Array(total);
  const fill = tile.cellStart.slice(0, counts.length);
  for (let at = 0; at < used; at += 2) { tile.cellItems[fill[pairs[at]]] = pairs[at + 1]; fill[pairs[at]] += 1; }
}

function covered(tile, lat, lng) {
  const { header, bytes } = tile;
  const cx = Math.floor((lng - header.origin[0]) / header.cell_degrees);
  const cy = Math.floor((lat - header.origin[1]) / header.cell_degrees);
  if (cx < 0 || cy < 0 || cx >= header.coverage.cols || cy >= header.coverage.rows) return false;
  const bit = cy * header.coverage.cols + cx;
  return (bytes[header.sections.coverage[0] + (bit >> 3)] & (1 << (bit & 7))) !== 0;
}

// The squared distance, in the tile's integer units, from a point to one street, with
// what the street is and the offset to its nearest point. Infinity for a closed way
// whose centre is past the limit.
const measured = { flags: 0, name: 0, ref: 0, tuple: 0, distance2: 0, dx: 0, dy: 0 };
function measure(tile, street, px, py, limit2) {
  source = tile.bytes;
  cursor = tile.streetOffset[street];
  const flags = unsigned();
  measured.flags = flags;
  measured.name = flags & FLAG_NAME ? unsigned() : 0;
  measured.ref = flags & FLAG_REF ? unsigned() : 0;
  measured.tuple = unsigned();
  const closed = (flags & FLAG_CLOSED) !== 0;
  if (closed) {
    const cx = signed() - px;
    const cy = signed() - py;
    if (cx * cx + cy * cy >= limit2) { measured.distance2 = Infinity; return measured; }
  }
  const points = unsigned();
  let x = signed();
  let y = signed();
  let best = Infinity;
  let bestX = 0;
  let bestY = 0;
  if (points === 1) { bestX = x - px; bestY = y - py; best = bestX * bestX + bestY * bestY; }
  let inside = false;
  for (let point = 1; point < points; point += 1) {
    const dx = signed();
    const dy = signed();
    const qx = px - x;
    const qy = py - y;
    const length2 = dx * dx + dy * dy;
    let turn = length2 ? (qx * dx + qy * dy) / length2 : 0;
    if (turn < 0) turn = 0; else if (turn > 1) turn = 1;
    // The nearest point is computed first and subtracted second, so two streets that
    // meet at a node give that node the very same distance and the tie is exact.
    const ox = x + turn * dx - px;
    const oy = y + turn * dy - py;
    const distance2 = ox * ox + oy * oy;
    if (distance2 < best) { best = distance2; bestX = ox; bestY = oy; }
    if (closed && (y > py) !== (y + dy > py) && px < x + ((py - y) * dx) / dy) inside = !inside;
    x += dx;
    y += dy;
  }
  measured.distance2 = inside ? 0 : best;
  measured.dx = inside ? 0 : bestX;
  measured.dy = inside ? 0 : bestY;
  return measured;
}

// Nearest street to (px, py) in tile units: the answer within `limit`, the nearest named
// street within `namedLimit` when the answer has no name, and failing an answer the
// nearest street of all within `areaLimit`.
function search(tile, px, py, limit, namedLimit, areaLimit) {
  const { cell, cols, rows, margin, cellStart, cellItems, seen } = tile;
  tile.round += 1;
  if (tile.round >= 0xfffffffe) { seen.fill(0); tile.round = 1; }
  const round = tile.round;
  const gx = Math.max(0, Math.min(cols - 1, Math.floor(px / cell) + margin));
  const gy = Math.max(0, Math.min(rows - 1, Math.floor(py / cell) + margin));
  const limit2 = limit * limit;
  const best = { street: -1, distance2: Infinity, flags: 0, name: 0, ref: 0, tuple: 0, dx: 0, dy: 0 };
  const named = { street: -1, distance2: Infinity, name: 0, ref: 0, dx: 0, dy: 0 };
  const visit = (index) => {
    for (let item = cellStart[index]; item < cellStart[index + 1]; item += 1) {
      const street = cellItems[item];
      if (seen[street] === round) continue;
      seen[street] = round;
      const found = measure(tile, street, px, py, limit2);
      const distance2 = found.distance2;
      if (distance2 === Infinity) continue;
      const minor = found.flags & FLAG_MINOR;
      const hasName = found.flags & FLAG_NAME;
      // An exact tie is two streets meeting at the node nearest the point. Street class
      // before a path, a name before none, the older way before the newer.
      if (distance2 < best.distance2 || (distance2 === best.distance2 && (
        minor < (best.flags & FLAG_MINOR) || (minor === (best.flags & FLAG_MINOR) && (
          hasName > (best.flags & FLAG_NAME) || (hasName === (best.flags & FLAG_NAME) && street < best.street)))))) {
        best.street = street; best.distance2 = distance2; best.flags = found.flags;
        best.name = found.name; best.ref = found.ref; best.tuple = found.tuple; best.dx = found.dx; best.dy = found.dy;
      }
      if (hasName && (distance2 < named.distance2 || (distance2 === named.distance2 && street < named.street))) {
        named.street = street; named.distance2 = distance2; named.name = found.name; named.ref = found.ref;
        named.dx = found.dx; named.dy = found.dy;
      }
    }
  };
  const lastRing = Math.ceil(areaLimit / cell) + 1;
  for (let ring = 0; ring <= lastRing; ring += 1) {
    const x0 = gx - ring; const x1 = gx + ring; const y0 = gy - ring; const y1 = gy + ring;
    for (let cy = Math.max(0, y0); cy <= Math.min(rows - 1, y1); cy += 1) {
      if (cy === y0 || cy === y1) {
        for (let cx = Math.max(0, x0); cx <= Math.min(cols - 1, x1); cx += 1) visit(cy * cols + cx);
      } else {
        if (x0 >= 0) visit(cy * cols + x0);
        if (x1 <= cols - 1 && x1 !== x0) visit(cy * cols + x1);
      }
    }
    // Everything not yet seen lies wholly outside the square of cells visited so far.
    // A side that has reached the grid's edge has nothing beyond it.
    const reach = Math.min(
      x0 <= 0 ? Infinity : px - (x0 - margin) * cell,
      x1 >= cols - 1 ? Infinity : (x1 + 1 - margin) * cell - px,
      y0 <= 0 ? Infinity : py - (y0 - margin) * cell,
      y1 >= rows - 1 ? Infinity : (y1 + 1 - margin) * cell - py);
    if (reach === Infinity) break;
    if (best.distance2 < reach * reach) {
      // The nearest street of all is known. Past the limit it is only the area's.
      if (best.distance2 > limit2) break;
      if ((best.flags & FLAG_NAME) || named.distance2 < reach * reach || reach >= namedLimit) break;
    } else if (reach >= areaLimit) break;
  }
  source = null;
  if (best.distance2 > areaLimit * areaLimit) best.street = -1;
  return { best, named };
}

function text(tile, index) {
  if (!index) return null;
  return tile.bytes.toString("utf8", tile.stringStart[index], tile.stringStart[index + 1] - 1);
}

export function createLocalAddress({
  directory = fileURLToPath(STREET_DIRECTORY),
  logger = console,
  readFile = readFileSync,
  maxLoadedTiles = 64,
} = {}) {
  let regions = null;
  // "<region>/<tile>" to the parsed tile, or null for one that was refused. Kept in the
  // order of use so the least recently used tile is the first to go.
  const tiles = new Map();

  function loadRegions() {
    regions = [];
    let names;
    try {
      names = readdirSync(directory, { withFileTypes: true })
        .filter((entry) => entry.isDirectory() && !entry.name.startsWith(".")).map((entry) => entry.name).sort();
    } catch (error) {
      logger.error(JSON.stringify({
        event: "local_address_unavailable", directory: String(directory),
        error_message: String(error?.message || error).slice(0, 300),
      }));
      return;
    }
    for (const name of names) {
      let manifest;
      try {
        manifest = JSON.parse(readFile(path.join(directory, name, "manifest.json")).toString("utf8"));
      } catch (error) {
        if (error?.code === "ENOENT") continue;
        logger.error(JSON.stringify({
          event: "local_address_region_refused", region: name,
          error_message: String(error?.message || error).slice(0, 300),
        }));
        continue;
      }
      if (manifest?.format !== REGION_FORMAT || manifest.tile_format !== TILE_FORMAT
          || !(manifest.tile_degrees > 0) || !manifest.tiles || typeof manifest.tiles !== "object") {
        logger.error(JSON.stringify({ event: "local_address_region_refused", region: name, error_message: "not a street address region" }));
        continue;
      }
      regions.push({ id: name, manifest });
    }
    if (!regions.length) {
      logger.error(JSON.stringify({ event: "local_address_unavailable", directory: String(directory), error_message: "no region is packaged" }));
    }
  }

  function tileOf(region, key) {
    const id = `${region.id}/${key}`;
    if (tiles.has(id)) {
      const kept = tiles.get(id);
      if (kept) { tiles.delete(id); tiles.set(id, kept); }
      return kept;
    }
    const entry = region.manifest.tiles[key];
    let tile = null;
    try {
      const bytes = readFile(path.join(directory, region.id, entry.file));
      const actual = sha256(bytes);
      if (actual !== entry.sha256 || bytes.length !== entry.bytes) {
        logger.error(JSON.stringify({
          event: "local_address_tile_refused", region: region.id, tile: key, file: entry.file,
          expected_sha256: entry.sha256, actual_sha256: actual, expected_bytes: entry.bytes, actual_bytes: bytes.length,
        }));
      } else {
        tile = parseTile(bytes);
      }
    } catch (error) {
      logger.error(JSON.stringify({
        event: "local_address_tile_refused", region: region.id, tile: key, file: entry.file,
        expected_sha256: entry.sha256, error_message: String(error?.message || error).slice(0, 300),
      }));
    }
    tiles.set(id, tile);
    let loaded = 0;
    for (const kept of tiles.values()) if (kept) loaded += 1;
    for (const [oldest, kept] of tiles) {
      if (loaded <= maxLoadedTiles) break;
      if (kept && oldest !== id) { tiles.delete(oldest); loaded -= 1; }
    }
    return tile;
  }

  function answer(region, tile, lat, lng) {
    const { header, scale } = tile;
    const px = (lng - header.origin[0]) * scale;
    const py = (lat - header.origin[1]) * scale;
    const limit = SNAP_LIMIT_DEGREES * scale;
    const { best, named } = search(tile, px, py, limit, NAMED_LIMIT_DEGREES * scale, AREA_LIMIT_DEGREES * scale);
    const provenance = region.manifest.provenance || {};
    const result = {
      road: null, ref: null, neighbourhood: null, suburb: null, city: null, postcode: null,
      source: "osm_extract", extract_date: provenance.extract_date || null, region: region.id,
      basis: "none", distance_m: null, address: {}, namedetails: {},
    };
    if (best.street < 0) return result;
    const snapped = best.distance2 <= limit * limit;
    result.basis = snapped ? "street" : "area";
    // Metres for a person to read. The choice of street was made in degrees, as Nominatim
    // makes it.
    const metres = (found) => Math.round(Math.hypot(
      (found.dx / scale) * 111_320 * Math.cos((lat * Math.PI) / 180), (found.dy / scale) * 110_574) * 10) / 10;
    result.distance_m = metres(best);
    const address = {};
    if (snapped) {
      const road = text(tile, best.name);
      const ref = text(tile, best.ref);
      if (road) { address.road = road; result.road = road; }
      if (ref) { result.ref = ref; result.namedetails.ref = ref; }
      if (road && !(best.flags & FLAG_NO_NAME_TAG)) result.namedetails = { name: road, ...result.namedetails };
      if (!road && named.street >= 0 && named.distance2 <= (NAMED_LIMIT_DEGREES * scale) ** 2) {
        result.nearest_named = {
          road: text(tile, named.name), ref: text(tile, named.ref),
          distance_m: metres(named),
        };
      }
    }
    const fields = TUPLE_FIELDS.length;
    for (let field = 0; field < fields; field += 1) {
      const name = TUPLE_FIELDS[field];
      if (!snapped && !AREA_FIELDS.has(name)) continue;
      const value = text(tile, tile.tuples[best.tuple * fields + field]);
      if (value) address[name === "iso" ? "ISO3166-2-lvl4" : name] = value;
    }
    address.country = region.manifest.country || "India";
    address.country_code = region.manifest.country_code || "in";
    result.address = address;
    result.neighbourhood = address.neighbourhood || null;
    result.suburb = address.suburb || null;
    result.city = address.city || null;
    result.postcode = address.postcode || null;
    return result;
  }

  return {
    // The address of a point, or null when no packaged region covers it (or its tile was
    // refused): the caller then has nothing local and asks whatever it asked before.
    // Fields are null where the data has no answer; `address` and `namedetails` have the
    // shape of Nominatim's own, so code written for the geocoder reads them unchanged.
    lookup(lat, lng) {
      if (!Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
      if (!regions) loadRegions();
      let result = null;
      for (const region of regions) {
        const key = tileKeyOf(lat, lng, region.manifest.tile_degrees);
        if (!region.manifest.tiles[key]) continue;
        const tile = tileOf(region, key);
        if (!tile || !covered(tile, lat, lng)) continue;
        const found = answer(region, tile, lat, lng);
        // Two regions can cover one point (a city box inside a state). The nearer street
        // is the answer.
        if (!result || (found.distance_m !== null && (result.distance_m === null || found.distance_m < result.distance_m))) {
          result = found;
        }
      }
      return result;
    },
    stats() {
      if (!regions) loadRegions();
      const loaded = [...tiles.entries()].filter(([, tile]) => tile);
      return {
        regions: regions.map((region) => ({
          id: region.id,
          extract_date: region.manifest.provenance?.extract_date || null,
          tiles: Object.keys(region.manifest.tiles).length,
          bytes: Object.values(region.manifest.tiles).reduce((sum, tile) => sum + tile.bytes, 0),
          loaded_tiles: loaded.filter(([id]) => id.startsWith(`${region.id}/`)).length,
        })),
        loaded_tiles: loaded.length,
        loaded_bytes: loaded.reduce((sum, [, tile]) => sum + tile.bytes.length, 0),
      };
    },
  };
}
