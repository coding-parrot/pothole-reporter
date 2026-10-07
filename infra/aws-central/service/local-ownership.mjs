// Karnataka road ownership answered from one packaged file, with no network call.
//
// data/karnataka-ownership.bin holds every polygon the live KGIS lookup read: the three
// highway land-cover layers (national 289, state 290, district 291), the 319 towns, the
// gram panchayats, and the state boundary. tools/build-karnataka-geometry.mjs writes it;
// this module only reads it.
//
// The file is one buffer of typed arrays, so loading is a single read and nothing is
// parsed but a small JSON header. A point lookup touches one cell of a uniform grid:
//
//   - every ring is cut into chunks of at most CHUNK_EDGES edges, and a cell lists the
//     chunks that pass through it (highway chunks also within the cell's margin, so a
//     point can be buffered by up to header.max_buffer_metres);
//   - a cell also lists the polygons that contain its reference point R. A point P in the
//     cell is inside a polygon when (R is inside) differs from (the segment R to P crosses
//     the polygon's boundary an odd number of times), and every edge that segment can
//     cross is in the cell's own list.
//
// R is the cell centre moved by (REFERENCE_FX, REFERENCE_FY). Vertices are integers, so
// with FY = 1/2 no vertex shares R's y, and with FX an odd multiple of 2^-18 no edge
// shorter than 2^17 units can pass through R (the builder asserts the bound). That makes
// "which side of this edge is R on" a question with one answer, and the products below
// that answer it are exact in a double for the same reason.
import { readFile } from "node:fs/promises";

import { metresPerDegree } from "./spatial.mjs";

export const OWNERSHIP_BUNDLE_PATH = new URL("../../../data/karnataka-ownership.bin", import.meta.url);
export const OWNERSHIP_FORMAT = "pothole-karnataka-ownership";
export const OWNERSHIP_MAGIC = "PKAOWN01";
export const OWNERSHIP_SCHEMA_VERSION = 1;
export const CHUNK_EDGES = 64;
export const REFERENCE_FX_NUMERATOR = 83_443;
export const REFERENCE_FX_BITS = 18;
export const REFERENCE_FX = REFERENCE_FX_NUMERATOR / 2 ** REFERENCE_FX_BITS;
export const REFERENCE_FY = 0.5;
// Order of the layers in the file. Polygon ids are assigned layer by layer in this order,
// the layers KGIS edits most often last, so a refresh that changes a town or a panchayat
// leaves the highways' ids, and most of the file, as they were.
export const OWNERSHIP_LAYERS = Object.freeze([
  "state", "national_highway", "state_highway", "district_highway", "town", "gram_panchayat",
]);
export const HIGHWAY_LAYERS = Object.freeze(["national_highway", "state_highway", "district_highway"]);

const TYPES = { u8: Uint8Array, u32: Uint32Array, i32: Int32Array };
const LITTLE_ENDIAN = new Uint8Array(new Uint32Array([1]).buffer)[0] === 1;

export function openOwnershipBundle(input) {
  if (!LITTLE_ENDIAN) throw new Error("the ownership bundle needs a little-endian host");
  let bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  // Typed-array views need the file to start on a 4-byte boundary of its ArrayBuffer.
  // readFile gives large files their own buffer, so this copy is for small test fixtures.
  if (bytes.byteOffset % 4) bytes = new Uint8Array(bytes);
  if (bytes.length < 12 || Buffer.from(bytes.buffer, bytes.byteOffset, 8).toString("latin1") !== OWNERSHIP_MAGIC) {
    throw new Error("not an ownership bundle");
  }
  const headerBytes = new DataView(bytes.buffer, bytes.byteOffset, 12).getUint32(8, true);
  const header = JSON.parse(Buffer.from(bytes.buffer, bytes.byteOffset + 12, headerBytes).toString("utf8"));
  if (header.format !== OWNERSHIP_FORMAT || header.schema_version !== OWNERSHIP_SCHEMA_VERSION) {
    throw new Error("not the expected ownership bundle");
  }
  const reference = header.reference || {};
  if (header.chunk_edges !== CHUNK_EDGES || reference.fx_numerator !== REFERENCE_FX_NUMERATOR
      || reference.fx_bits !== REFERENCE_FX_BITS || reference.fy !== REFERENCE_FY) {
    throw new Error("the ownership bundle was built for a different reader");
  }
  // Section offsets count from the first 4-byte boundary after the header.
  const dataStart = (12 + headerBytes + 3) & ~3;
  const sections = {};
  for (const [name, { type, offset, length }] of Object.entries(header.sections)) {
    const View = TYPES[type];
    if (!View || offset % 4 || dataStart + offset + length * View.BYTES_PER_ELEMENT > bytes.length) {
      throw new Error(`ownership bundle section ${name} is out of bounds`);
    }
    sections[name] = new View(bytes.buffer, bytes.byteOffset + dataStart + offset, length);
  }
  const layers = header.layers;
  if (!Array.isArray(layers) || layers.map((layer) => layer.name).join() !== OWNERSHIP_LAYERS.join()) {
    throw new Error("the ownership bundle does not hold the expected layers");
  }
  const polygons = sections.polyRings.length;
  const polyLayer = new Uint8Array(polygons);
  const polyStep = new Uint8Array(polygons);
  const polyFirstRing = new Uint32Array(polygons + 1);
  layers.forEach((layer, index) => {
    polyLayer.fill(index, layer.first_polygon, layer.first_polygon + layer.polygons);
    polyStep.fill(layer.step, layer.first_polygon, layer.first_polygon + layer.polygons);
  });
  for (let polygon = 0; polygon < polygons; polygon += 1) {
    polyFirstRing[polygon + 1] = polyFirstRing[polygon] + sections.polyRings[polygon];
  }
  const grid = header.grid;
  return {
    header, layers, grid, ...sections, polyLayer, polyStep, polyFirstRing,
    bytes: bytes.length,
    scale: header.coordinate_scale,
    layerIndex: Object.fromEntries(layers.map((layer, index) => [layer.name, index])),
    buffered: layers.map((layer) => HIGHWAY_LAYERS.includes(layer.name)),
  };
}

// One read per process per path. The Lambda keeps it across invocations.
const loaded = new Map();
export function loadOwnershipBundle(path = OWNERSHIP_BUNDLE_PATH) {
  const key = String(path);
  if (!loaded.has(key)) loaded.set(key, readFile(path).then(openOwnershipBundle));
  return loaded.get(key);
}

// Every polygon within bufferMetres of the point (inside counts as zero metres), as
// { polygon, layer, metres }. Only the highway layers are buffered; a town, a panchayat
// or the state is a hit only when the point is inside it. With bufferMetres 0 the highway
// layers are plain containment too.
export function polygonsAt(bundle, lat, lng, bufferMetres = 0) {
  const hits = [];
  if (!(bufferMetres >= 0 && bufferMetres <= bundle.header.max_buffer_metres)) {
    throw new RangeError(`the bundle supports buffers up to ${bundle.header.max_buffer_metres} m`);
  }
  const { scale, grid } = bundle;
  const px = lng * scale;
  const py = lat * scale;
  const col = Math.floor((px - grid.x0) / grid.cell);
  const row = Math.floor((py - grid.y0) / grid.cell);
  // Written so that NaN fails it.
  if (!(col >= 0 && col < grid.cols && row >= 0 && row < grid.rows)) return hits;
  const cell = row * grid.cols + col;
  // P and every vertex are taken relative to the cell's reference point R.
  const originX = grid.x0 + col * grid.cell + grid.cell / 2;
  const originY = grid.y0 + row * grid.cell + grid.cell / 2;
  const prx = px - originX - REFERENCE_FX;
  const pry = py - originY - REFERENCE_FY;
  const { lat: metresPerLat, lng: metresPerLng } = metresPerDegree(lat);
  const mx = metresPerLng / scale;
  const my = metresPerLat / scale;
  const {
    coords, chunkX, chunkY, chunkOffset, chunkPoly, chunkEdges, polyLayer, polyStep, buffered,
    cellChunkStart, cellChunkRuns, cellInsideStart, cellInside,
  } = bundle;
  const ids = [];
  const parity = [];
  const nearest = [];
  for (let index = cellInsideStart[cell]; index < cellInsideStart[cell + 1]; index += 1) {
    ids.push(cellInside[index]);
    parity.push(1);
    nearest.push(Infinity);
  }
  let slot = -1;
  let slotPolygon = -1;
  for (let index = cellChunkStart[cell]; index < cellChunkStart[cell + 1]; index += 1) {
    const run = cellChunkRuns[index];
    const first = run >>> 8;
    const last = first + (run & 0xff);
    for (let chunk = first; chunk <= last; chunk += 1) {
      const polygon = chunkPoly[chunk];
      if (polygon !== slotPolygon) {
        slotPolygon = polygon;
        slot = ids.indexOf(polygon);
        if (slot < 0) {
          slot = ids.push(polygon) - 1;
          parity.push(0);
          nearest.push(Infinity);
        }
      }
      const step = polyStep[polygon];
      const measure = bufferMetres > 0 && buffered[polyLayer[polygon]];
      let offset = chunkOffset[chunk];
      let ax = chunkX[chunk] - originX - REFERENCE_FX;
      let ay = chunkY[chunk] - originY - REFERENCE_FY;
      // Which side of the line R to P the vertex is on. A vertex exactly on the line is
      // counted on one fixed side, for both edges that share it.
      let sideA = prx * ay - pry * ax > 0;
      let crossings = 0;
      let closest = nearest[slot];
      for (let edge = chunkEdges[chunk]; edge > 0; edge -= 1) {
        let byte = coords[offset++];
        let value = byte & 0x7f;
        for (let shift = 7; byte & 0x80; shift += 7) {
          byte = coords[offset++];
          value |= (byte & 0x7f) << shift;
        }
        const dx = ((value >>> 1) ^ -(value & 1)) * step;
        byte = coords[offset++];
        value = byte & 0x7f;
        for (let shift = 7; byte & 0x80; shift += 7) {
          byte = coords[offset++];
          value |= (byte & 0x7f) << shift;
        }
        const dy = ((value >>> 1) ^ -(value & 1)) * step;
        const bx = ax + dx;
        const by = ay + dy;
        const sideB = prx * by - pry * bx > 0;
        if (sideA !== sideB) {
          // The edge crosses the line through R and P; it crosses the segment between
          // them when R and P are on opposite sides of the edge.
          const sideR = ax * dy - ay * dx > 0;
          const sideP = (ax - prx) * dy - (ay - pry) * dx > 0;
          if (sideR !== sideP) crossings += 1;
        }
        if (measure) {
          const ux = (ax - prx) * mx;
          const uy = (ay - pry) * my;
          const vx = dx * mx;
          const vy = dy * my;
          const length = vx * vx + vy * vy;
          let turn = length ? -(ux * vx + uy * vy) / length : 0;
          turn = turn < 0 ? 0 : turn > 1 ? 1 : turn;
          const ex = ux + turn * vx;
          const ey = uy + turn * vy;
          const distance = ex * ex + ey * ey;
          if (distance < closest) closest = distance;
        }
        ax = bx;
        ay = by;
        sideA = sideB;
      }
      parity[slot] ^= crossings & 1;
      nearest[slot] = closest;
    }
  }
  const limit = bufferMetres * bufferMetres;
  for (let index = 0; index < ids.length; index += 1) {
    if (parity[index]) hits.push({ polygon: ids[index], layer: polyLayer[ids[index]], metres: 0 });
    else if (nearest[index] <= limit) {
      hits.push({ polygon: ids[index], layer: polyLayer[ids[index]], metres: Math.sqrt(nearest[index]) });
    }
  }
  return hits;
}

// The attributes KGIS publishes for a polygon: a highway's Name, a panchayat's
// KGISGPName, a town's record.
export function polygonAttributes(bundle, polygon) {
  const layer = bundle.layers[bundle.polyLayer[polygon]];
  const attribute = bundle.polyAttr[polygon];
  const objectid = bundle.polyObjectId[polygon];
  if (layer.name === "town") return { layer: layer.name, objectid, ...layer.towns[attribute] };
  return {
    layer: layer.name,
    objectid,
    name: layer.names && attribute !== 0xffffffff ? layer.names[attribute] : null,
  };
}

// A polygon's rings in the bundle's units (degrees times header.coordinate_scale), each
// a flat Int32Array [x0, y0, x1, y1, ...] with the first vertex repeated at the end. For
// tools and tests; the request path never decodes a whole polygon.
export function polygonRings(bundle, polygon) {
  const { coords, ringVertices, polyFirstRing, polyOffset, polyStep } = bundle;
  const step = polyStep[polygon];
  let offset = polyOffset[polygon];
  const read = () => {
    let byte = coords[offset++];
    let value = byte & 0x7f;
    for (let shift = 7; byte & 0x80; shift += 7) {
      byte = coords[offset++];
      value |= (byte & 0x7f) << shift;
    }
    return (value >>> 1) ^ -(value & 1);
  };
  const rings = [];
  for (let ring = polyFirstRing[polygon]; ring < polyFirstRing[polygon + 1]; ring += 1) {
    const points = new Int32Array(ringVertices[ring] * 2);
    let x = 0;
    let y = 0;
    for (let index = 0; index < points.length; index += 2) {
      x += read() * step;
      y += read() * step;
      points[index] = x;
      points[index + 1] = y;
    }
    rings.push(points);
  }
  return rings;
}
