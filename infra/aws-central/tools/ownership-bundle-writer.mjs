// Writes data/karnataka-ownership.bin, the file service/local-ownership.mjs reads.
// build-karnataka-geometry.mjs feeds it polygons layer by layer; this module quantises
// nothing and draws nothing. It stores each ring as it is given (integer coordinates),
// cuts it into chunks, and builds the grid the reader walks.
import crypto from "node:crypto";
import fs from "node:fs";

import {
  CHUNK_EDGES, HIGHWAY_LAYERS, OWNERSHIP_FORMAT, OWNERSHIP_LAYERS, OWNERSHIP_MAGIC,
  OWNERSHIP_SCHEMA_VERSION, REFERENCE_FX, REFERENCE_FX_BITS, REFERENCE_FX_NUMERATOR, REFERENCE_FY,
} from "../service/local-ownership.mjs";

// The grid covers Karnataka (lat 11.59 to 18.46, lng 74.04 to 78.59) with room to spare.
// A cell is 0.01 degrees, about 1.1 km.
const GRID = Object.freeze({ west: 73.9, south: 11.4, cols: 480, rows: 720, cellDegrees: 0.01 });
// Highway chunks are listed in every cell they come within this margin of, so a point can
// be buffered against them. 0.0007 degrees is 74 m of longitude at Karnataka's northern
// edge and 77 m of latitude, which leaves MAX_BUFFER_METRES with room.
const MARGIN_DEGREES = 0.0007;
export const MAX_BUFFER_METRES = 60;
// Two bounds keep the reader's side-of-edge test exact (see service/local-ownership.mjs).
// No cell's reference point can lie on an edge shorter than this: 2^17 units, 72 km at
// 1/200,000 degree.
const MAX_EDGE_UNITS = 2 ** (REFERENCE_FX_BITS - 1);
// And the reader multiplies a vertex's offset from the reference point (an integer number
// of 2^-18 units) by an edge's length: exact in a double while the product stays under
// 2^53, that is while offset times length is under 2^35 units squared. On 7 Oct 2026 the
// largest was about 2^31.
const MAX_OFFSET_TIMES_EDGE = 2 ** 34;

class Grow {
  constructor(View, capacity = 1 << 16) {
    this.View = View;
    this.data = new View(capacity);
    this.length = 0;
  }

  reserve(extra) {
    if (this.length + extra <= this.data.length) return;
    let capacity = this.data.length;
    while (capacity < this.length + extra) capacity *= 2;
    const next = new this.View(capacity);
    next.set(this.data.subarray(0, this.length));
    this.data = next;
  }

  push(value) {
    if (this.length === this.data.length) this.reserve(1);
    this.data[this.length++] = value;
  }

  view() {
    return this.data.subarray(0, this.length);
  }
}

const bytesOf = (view) => Buffer.from(view.buffer, view.byteOffset, view.byteLength);

// Consecutive duplicates go, and so does a vertex that lies exactly on the straight line
// between its neighbours: the ring is the same set of points without it. A ring whose
// vertices all fell on one point (a sliver smaller than the coordinate grid) is kept as
// that point, so a buffered lookup still finds it.
export function tidyRing(points) {
  const distinct = [];
  for (const point of points) {
    const previous = distinct[distinct.length - 1];
    if (previous && previous[0] === point[0] && previous[1] === point[1]) continue;
    distinct.push(point);
  }
  const first = distinct[0];
  const last = distinct[distinct.length - 1];
  if (distinct.length > 1 && (first[0] !== last[0] || first[1] !== last[1])) distinct.push(first);
  if (distinct.length === 1) return [first, first];
  const ring = [first];
  for (let index = 1; index < distinct.length - 1; index += 1) {
    const before = ring[ring.length - 1];
    const here = distinct[index];
    const after = distinct[index + 1];
    const cross = (here[0] - before[0]) * (after[1] - before[1]) - (here[1] - before[1]) * (after[0] - before[0]);
    const forward = (here[0] - before[0]) * (after[0] - here[0]) + (here[1] - before[1]) * (after[1] - here[1]);
    if (cross === 0 && forward > 0) continue;
    ring.push(here);
  }
  ring.push(distinct[distinct.length - 1]);
  return ring;
}

export function createOwnershipWriter({ scale }) {
  if (!Number.isInteger(scale / 100) || (scale / 100) % 2) throw new Error("the cell must be an even number of units");
  const grid = {
    x0: Math.round(GRID.west * scale), y0: Math.round(GRID.south * scale),
    cell: scale / 100, cols: GRID.cols, rows: GRID.rows, margin: Math.ceil(MARGIN_DEGREES * scale),
  };
  const half = grid.cell / 2;
  const coords = new Grow(Uint8Array, 1 << 24);
  const ringVertices = new Grow(Uint32Array);
  const polyRings = new Grow(Uint32Array);
  const polyAttr = new Grow(Uint32Array);
  const polyObjectId = new Grow(Uint32Array);
  const polyOffset = new Grow(Uint32Array);
  const chunkX = new Grow(Int32Array);
  const chunkY = new Grow(Int32Array);
  const chunkOffset = new Grow(Uint32Array);
  const chunkPoly = new Grow(Uint32Array);
  const chunkEdges = new Grow(Uint8Array);
  const chunkCellPairs = { cell: new Grow(Uint32Array, 1 << 20), item: new Grow(Uint32Array, 1 << 20) };
  const insidePairs = { cell: new Grow(Uint32Array, 1 << 20), item: new Grow(Uint32Array, 1 << 20) };
  const layers = [];
  let layer = null;

  const varint = (value) => {
    let zigzag = value < 0 ? -2 * value - 1 : 2 * value;
    // The reader assembles a value with 32-bit shifts, four bytes of seven bits at most.
    if (!Number.isInteger(value) || zigzag >= 2 ** 28) throw new Error(`coordinate ${value} does not fit`);
    coords.reserve(5);
    while (zigzag >= 0x80) {
      coords.data[coords.length++] = (zigzag & 0x7f) | 0x80;
      zigzag = Math.floor(zigzag / 128);
    }
    coords.data[coords.length++] = zigzag;
  };

  // Is the reference point of column `col` to the right of where the edge crosses the
  // reference line of row `row`? Exact: everything is scaled to integers.
  const fxScale = 2n ** BigInt(REFERENCE_FX_BITS);
  const referenceRightOfCrossing = (col, row, ax, ay, dx, dy) => {
    const n1 = BigInt(grid.x0 + col * grid.cell + half - ax) * fxScale + BigInt(REFERENCE_FX_NUMERATOR);
    const n2 = BigInt(2 * (grid.y0 + row * grid.cell + half - ay) + 1);
    const value = n1 * BigInt(dy) * 2n - n2 * BigInt(dx) * fxScale;
    if (value === 0n) throw new Error("a cell's reference point lies on an edge; the bound on edge length was broken");
    return (value > 0n) === (dy > 0);
  };

  function addPolygon({ objectid, attribute = 0xffffffff, rings }) {
    const polygon = polyRings.length;
    const step = layer.step;
    const withMargin = HIGHWAY_LAYERS.includes(layer.name) ? grid.margin : 0;
    const crossings = new Map();
    polyOffset.push(coords.length);
    polyAttr.push(attribute);
    polyObjectId.push(objectid);
    let kept = 0;
    for (const given of rings) {
      layer.source_vertices += given.length;
      const ring = tidyRing(given);
      if (ring.length === 2 && ring[0] === ring[1]) layer.collapsed_rings += 1;
      kept += 1;
      ringVertices.push(ring.length);
      layer.vertices += ring.length;
      varint(ring[0][0]);
      varint(ring[0][1]);
      let cells = null;
      let reach = null;
      for (let index = 1; index < ring.length; index += 1) {
        const [ax, ay] = [ring[index - 1][0] * step, ring[index - 1][1] * step];
        const [bx, by] = [ring[index][0] * step, ring[index][1] * step];
        const dx = bx - ax;
        const dy = by - ay;
        if (Math.abs(dx) >= MAX_EDGE_UNITS || Math.abs(dy) >= MAX_EDGE_UNITS) {
          throw new Error(`${layer.name} ${objectid}: an edge of ${Math.max(Math.abs(dx), Math.abs(dy))} units is too long for the reader`);
        }
        if ((index - 1) % CHUNK_EDGES === 0) {
          chunkX.push(ax);
          chunkY.push(ay);
          chunkOffset.push(coords.length);
          chunkPoly.push(polygon);
          chunkEdges.push(Math.min(CHUNK_EDGES, ring.length - index));
          cells = new Set();
          reach = { west: ax, south: ay, east: ax, north: ay, edge: 0 };
        }
        reach.west = Math.min(reach.west, bx);
        reach.east = Math.max(reach.east, bx);
        reach.south = Math.min(reach.south, by);
        reach.north = Math.max(reach.north, by);
        reach.edge = Math.max(reach.edge, Math.abs(dx), Math.abs(dy));
        varint(ring[index][0] - ring[index - 1][0]);
        varint(ring[index][1] - ring[index - 1][1]);
        // Every cell the edge's box (with the margin, for a highway) overlaps.
        const colFrom = Math.floor((Math.min(ax, bx) - withMargin - grid.x0) / grid.cell);
        const colTo = Math.floor((Math.max(ax, bx) + withMargin - grid.x0) / grid.cell);
        const rowFrom = Math.floor((Math.min(ay, by) - withMargin - grid.y0) / grid.cell);
        const rowTo = Math.floor((Math.max(ay, by) + withMargin - grid.y0) / grid.cell);
        if (colFrom < 0 || rowFrom < 0 || colTo >= grid.cols || rowTo >= grid.rows) {
          throw new Error(`${layer.name} ${objectid} reaches outside the grid`);
        }
        for (let row = rowFrom; row <= rowTo; row += 1) {
          for (let col = colFrom; col <= colTo; col += 1) cells.add(row * grid.cols + col);
        }
        if (index % CHUNK_EDGES === 0 || index === ring.length - 1) {
          const chunk = chunkX.length - 1;
          for (const cell of cells) {
            const centreX = grid.x0 + (cell % grid.cols) * grid.cell + half;
            const centreY = grid.y0 + Math.floor(cell / grid.cols) * grid.cell + half;
            const offset = 1 + Math.max(centreX - reach.west, reach.east - centreX, centreY - reach.south, reach.north - centreY);
            if (offset * reach.edge >= MAX_OFFSET_TIMES_EDGE) {
              throw new Error(`${layer.name} ${objectid}: a chunk reaches ${offset} units from a cell that lists it with an edge of `
                + `${reach.edge}; the reader's arithmetic would round. Shorten CHUNK_EDGES or store this layer more coarsely.`);
            }
            chunkCellPairs.cell.push(cell);
            chunkCellPairs.item.push(chunk);
          }
        }
        // Where the edge crosses a row's reference line, the columns to the right of the
        // crossing change sides. No vertex is on a reference line (it is at a half unit).
        if (dy !== 0) {
          const base = grid.y0 + half + REFERENCE_FY;
          const rowLow = Math.ceil((Math.min(ay, by) - base) / grid.cell);
          const rowHigh = Math.floor((Math.max(ay, by) - base) / grid.cell);
          for (let row = rowLow; row <= rowHigh; row += 1) {
            const crossingX = ax + ((base + row * grid.cell - ay) * dx) / dy;
            let col = Math.ceil((crossingX - grid.x0 - half - REFERENCE_FX) / grid.cell);
            while (!referenceRightOfCrossing(col, row, ax, ay, dx, dy)) col += 1;
            while (referenceRightOfCrossing(col - 1, row, ax, ay, dx, dy)) col -= 1;
            if (!crossings.has(row)) crossings.set(row, []);
            crossings.get(row).push(col);
          }
        }
      }
    }
    polyRings.push(kept);
    layer.rings += kept;
    for (const [row, cols] of crossings) {
      if (cols.length % 2) throw new Error(`${layer.name} ${objectid}: an open ring`);
      cols.sort((left, right) => left - right);
      for (let index = 0; index < cols.length; index += 2) {
        for (let col = cols[index]; col < cols[index + 1]; col += 1) {
          insidePairs.cell.push(row * grid.cols + col);
          insidePairs.item.push(polygon);
        }
      }
    }
    layer.polygons += 1;
  }

  function beginLayer(name, { scale: layerScale, ...meta }) {
    if (OWNERSHIP_LAYERS[layers.length] !== name) throw new Error(`layer ${name} is out of order`);
    if (!Number.isInteger(scale / layerScale)) throw new Error(`${name}: ${layerScale} does not divide ${scale}`);
    layer = {
      name, scale: layerScale, step: scale / layerScale,
      first_polygon: polyRings.length, polygons: 0, first_ring: ringVertices.length, rings: 0,
      vertices: 0, source_vertices: 0, collapsed_rings: 0,
      coords: { offset: coords.length, length: 0 },
      ...meta,
    };
    layers.push(layer);
  }

  function endLayer(extra = {}) {
    Object.assign(layer, extra);
    layer.coords.length = coords.length - layer.coords.offset;
    const hash = crypto.createHash("sha256");
    const end = layer.first_polygon + layer.polygons;
    hash.update(bytesOf(polyRings.view().subarray(layer.first_polygon, end)));
    hash.update(bytesOf(ringVertices.view().subarray(layer.first_ring, layer.first_ring + layer.rings)));
    hash.update(bytesOf(coords.view().subarray(layer.coords.offset, coords.length)));
    hash.update(bytesOf(polyAttr.view().subarray(layer.first_polygon, end)));
    hash.update(bytesOf(polyObjectId.view().subarray(layer.first_polygon, end)));
    hash.update(JSON.stringify(layer.names || layer.towns || null));
    layer.content_sha256 = hash.digest("hex");
    layer = null;
  }

  // Pairs of (cell, item) into the two arrays the reader walks: where each cell's items
  // start, and the items, in ascending order within a cell.
  function byCell(pairs) {
    const cells = grid.cols * grid.rows;
    const start = new Uint32Array(cells + 1);
    const cell = pairs.cell.view();
    const item = pairs.item.view();
    for (let index = 0; index < cell.length; index += 1) start[cell[index] + 1] += 1;
    for (let index = 0; index < cells; index += 1) start[index + 1] += start[index];
    const cursor = start.slice(0, cells);
    const items = new Uint32Array(cell.length);
    for (let index = 0; index < cell.length; index += 1) items[cursor[cell[index]]++] = item[index];
    for (let index = 0; index < cells; index += 1) items.subarray(start[index], start[index + 1]).sort();
    return { start, items };
  }

  function finish(file, header) {
    if (layers.length !== OWNERSHIP_LAYERS.length) throw new Error("a layer is missing");
    if (chunkX.length >= 2 ** 24) throw new Error("too many chunks for a packed run");
    const inside = byCell(insidePairs);
    const chunks = byCell(chunkCellPairs);
    // Consecutive chunks (the same ring carrying on) are stored as one run:
    // first chunk in the high 24 bits, how many more follow in the low 8.
    const runStart = new Uint32Array(chunks.start.length);
    const runs = new Grow(Uint32Array, 1 << 18);
    for (let cell = 0; cell < grid.cols * grid.rows; cell += 1) {
      runStart[cell] = runs.length;
      let index = chunks.start[cell];
      const end = chunks.start[cell + 1];
      while (index < end) {
        const first = chunks.items[index];
        let more = 0;
        while (index + 1 < end && more < 255 && chunks.items[index + 1] === first + more + 1) {
          more += 1;
          index += 1;
        }
        runs.push(first * 256 + more);
        index += 1;
      }
    }
    runStart[grid.cols * grid.rows] = runs.length;
    const sections = {
      coords: ["u8", coords.view()],
      ringVertices: ["u32", ringVertices.view()],
      polyRings: ["u32", polyRings.view()],
      polyAttr: ["u32", polyAttr.view()],
      polyObjectId: ["u32", polyObjectId.view()],
      polyOffset: ["u32", polyOffset.view()],
      chunkX: ["i32", chunkX.view()],
      chunkY: ["i32", chunkY.view()],
      chunkOffset: ["u32", chunkOffset.view()],
      chunkPoly: ["u32", chunkPoly.view()],
      chunkEdges: ["u8", chunkEdges.view()],
      cellChunkStart: ["u32", runStart],
      cellChunkRuns: ["u32", runs.view()],
      cellInsideStart: ["u32", inside.start],
      cellInside: ["u32", inside.items],
    };
    const align = (value) => (value + 3) & ~3;
    const table = {};
    let offset = 0;
    for (const [name, [type, view]] of Object.entries(sections)) {
      table[name] = { type, offset, length: view.length };
      offset = align(offset + view.byteLength);
    }
    const headerBytes = Buffer.from(JSON.stringify({
      format: OWNERSHIP_FORMAT,
      schema_version: OWNERSHIP_SCHEMA_VERSION,
      ...header,
      coordinate_scale: scale,
      grid: { x0: grid.x0, y0: grid.y0, cell: grid.cell, cols: grid.cols, rows: grid.rows, margin: grid.margin },
      max_buffer_metres: MAX_BUFFER_METRES,
      chunk_edges: CHUNK_EDGES,
      reference: { fx_numerator: REFERENCE_FX_NUMERATOR, fx_bits: REFERENCE_FX_BITS, fy: REFERENCE_FY },
      chunks: chunkX.length,
      layers,
      // Offsets are from the first 4-byte boundary after this header.
      sections: table,
    }));
    const prefix = Buffer.alloc(align(12 + headerBytes.length));
    prefix.write(OWNERSHIP_MAGIC, 0, "latin1");
    prefix.writeUInt32LE(headerBytes.length, 8);
    headerBytes.copy(prefix, 12);
    const parts = [prefix];
    for (const [, view] of Object.values(sections)) {
      parts.push(bytesOf(view));
      if (view.byteLength % 4) parts.push(Buffer.alloc(4 - (view.byteLength % 4)));
    }
    const hash = crypto.createHash("sha256");
    for (const part of parts) hash.update(part);
    const digest = hash.digest("hex");
    const size = parts.reduce((sum, part) => sum + part.length, 0);
    if (fs.existsSync(file) && fs.statSync(file).size === size
        && crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex") === digest) {
      return { changed: false, size, sha256: digest };
    }
    const handle = fs.openSync(`${file}.tmp`, "w");
    for (const part of parts) fs.writeSync(handle, part);
    fs.closeSync(handle);
    fs.renameSync(`${file}.tmp`, file);
    return { changed: true, size, sha256: digest };
  }

  return { beginLayer, addPolygon, endLayer, finish, grid };
}
