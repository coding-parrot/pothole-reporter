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
