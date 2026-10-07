const EARTH_RADIUS_M = 6_371_000;
const WEB_MERCATOR_RADIUS_M = 6_378_137;
const CELL_METRES = 30;
// Cells are square in Web Mercator, so a ground radius spans 1 / cos(lat) as many of
// them. Past 75 degrees a 30 m radius needs more than the 100 cells one DynamoDB
// transaction can lock. Nobody reports potholes there.
export const REPORT_MAX_ABS_LAT = 75;

export function validLatLng(lat, lng) {
  return Number.isFinite(lat) && Number.isFinite(lng)
    && lat >= -85 && lat <= 85 && lng >= -180 && lng <= 180;
}

export function metresBetween(lat1, lng1, lat2, lng2) {
  const radians = Math.PI / 180;
  const p1 = lat1 * radians;
  const p2 = lat2 * radians;
  const dp = (lat2 - lat1) * radians;
  const dl = (lng2 - lng1) * radians;
  const a = Math.sin(dp / 2) ** 2
    + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function projectedCell(lat, lng) {
  const radians = Math.PI / 180;
  const x = WEB_MERCATOR_RADIUS_M * lng * radians;
  const y = WEB_MERCATOR_RADIUS_M
    * Math.log(Math.tan(Math.PI / 4 + lat * radians / 2));
  return {
    x: Math.floor(x / CELL_METRES),
    y: Math.floor(y / CELL_METRES),
  };
}

export function spatialCell(lat, lng) {
  const cell = projectedCell(lat, lng);
  return `${cell.x}:${cell.y}`;
}

export function nearbyCells(lat, lng, radiusMetres) {
  const centre = projectedCell(lat, lng);
  // A point anywhere in the centre cell reaches at most ceil(R / cell) cells either
  // side. The extra ring once added for safety took a report in Karnataka from 25 cells
  // to 49, and the old clamp and cap missed duplicates past about 75 degrees.
  const projectedRadius = radiusMetres * (WEB_MERCATOR_RADIUS_M / EARTH_RADIUS_M)
    / Math.cos(lat * Math.PI / 180);
  const span = Math.ceil(projectedRadius / CELL_METRES);
  const cells = [];
  for (let y = centre.y - span; y <= centre.y + span; y += 1) {
    for (let x = centre.x - span; x <= centre.x + span; x += 1) {
      cells.push(`${x}:${y}`);
    }
  }
  return cells;
}

export function roundedPublicCoordinate(value) {
  return Math.round(value * 100_000) / 100_000;
}

// The local geometry (town polygons, the state boundary, highway centre lines) is stored
// the way the app's highway tiles are: integer coordinates at a fixed scale, each ring or
// line as [x0, y0, dx1, dy1, dx2, dy2, ...]. Decoding is done per candidate after a
// bounding-box test, so a lookup touches a handful of rings, not the whole state.
export function decodeRun(encoded) {
  const points = new Array(encoded.length / 2);
  let x = encoded[0];
  let y = encoded[1];
  points[0] = [x, y];
  for (let index = 2; index < encoded.length; index += 2) {
    x += encoded[index];
    y += encoded[index + 1];
    points[index / 2] = [x, y];
  }
  return points;
}

export function withinBox(x, y, box, padX = 0, padY = padX) {
  return x >= box[0] - padX && x <= box[2] + padX && y >= box[1] - padY && y <= box[3] + padY;
}

// Even-odd rule across every ring, so holes and multi-part polygons need no orientation
// bookkeeping: a point inside an outer ring and inside one of its holes crosses an odd
// number of edges twice and is outside. x and y are in the rings' scaled units.
export function pointInRings(x, y, rings) {
  let inside = false;
  for (const encoded of rings) {
    const ring = decodeRun(encoded);
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if ((yi > y) !== (yj > y) && x < xi + ((y - yi) * (xj - xi)) / (yj - yi)) {
        inside = !inside;
      }
    }
  }
  return inside;
}

// Same local flat-earth metric as the app's highway matcher, so the server and the phone
// agree on how far a fix is from a mapped carriageway.
export function metresToSegment(lng, lat, aLng, aLat, bLng, bLat) {
  const radians = Math.PI / 180;
  const metresPerLng = 111_320 * Math.cos(lat * radians);
  const ax = (aLng - lng) * metresPerLng;
  const ay = (aLat - lat) * 110_540;
  const bx = (bLng - lng) * metresPerLng;
  const by = (bLat - lat) * 110_540;
  const dx = bx - ax;
  const dy = by - ay;
  const denominator = dx * dx + dy * dy;
  const turn = denominator ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / denominator)) : 0;
  return Math.hypot(ax + turn * dx, ay + turn * dy);
}

// Metres in one degree of latitude and of longitude at a latitude, on the WGS84
// ellipsoid. KGIS buffers a query point geodesically; over the few metres a highway
// buffer spans, these two factors reproduce that to well under a millimetre.
export function metresPerDegree(lat) {
  const phi = lat * Math.PI / 180;
  return {
    lat: 111_132.92 - 559.82 * Math.cos(2 * phi) + 1.175 * Math.cos(4 * phi) - 0.0023 * Math.cos(6 * phi),
    lng: 111_412.84 * Math.cos(phi) - 93.5 * Math.cos(3 * phi) + 0.118 * Math.cos(5 * phi),
  };
}

export function metresToPolyline(lng, lat, encoded, scale) {
  const line = decodeRun(encoded);
  let nearest = Infinity;
  for (let index = 1; index < line.length; index += 1) {
    const distance = metresToSegment(lng, lat,
      line[index - 1][0] / scale, line[index - 1][1] / scale,
      line[index][0] / scale, line[index][1] / scale);
    if (distance < nearest) nearest = distance;
  }
  return nearest;
}
