// Rules of the production health gate that are worth testing apart from the script that
// runs them (tools/production-health.mjs judges on import, so it cannot be imported).

// Karnataka municipal lookups, by how the ward lookup went and what they answered. KGIS
// is Karnataka's register, so road_ownership "municipal" is a Karnataka town. A matched
// street tender sets tender_catalogue on both routes; the report route's outcome is
// "created" or "deduplicated" either way.
export const WARD_TENDER_QUERY = 'filter event="http_request" and road_ownership="municipal"'
  + ' and (route="/v1/tenders/resolve" or route="/v1/potholes/report")'
  + " | stats count() as n by ward_lookup, ward_tender_count, tender_catalogue";

// ward_lookup "unavailable" means the service could not read its own ward polygons:
// data/karnataka-ward-geometry.json is missing from the package or is not the bundle.
// Without this rule that failure is silent, because no ward is then resolved and the
// rule below has nothing to judge. Lines from before the ward release carry no
// ward_lookup at all and are not counted.
export function judgeWardSnapshot(rows) {
  const unavailable = rows.filter((row) => row.ward_lookup === "unavailable")
    .reduce((sum, row) => sum + (Number(row.n) || 0), 0);
  return unavailable
    ? { broken: true, unavailable, detail: `${unavailable} municipal lookups could not read the ward snapshot; data/karnataka-ward-geometry.json is missing from the package` }
    : { broken: false, unavailable, detail: "0 lookups without the ward snapshot" };
}

// Only a ward KGIS names is judged (ward_lookup "resolved"; a ward with only a number
// logs "resolved_unnamed" and has nothing to be matched on). 795 Bengaluru tenders named
// the ward or locality of 18 of 40 real pothole locations (45%) on 6 Oct 2026. Under one in five, across 30 or more lookups, means the ward
// snapshot, the tender table or the name matching has stopped working, not that the
// wards went quiet.
export function judgeWardTenders(rows, { minimum = 30, share = 0.2 } = {}) {
  let resolved = 0;
  let answered = 0;
  for (const row of rows) {
    if (row.ward_lookup !== "resolved") continue;
    const n = Number(row.n) || 0;
    resolved += n;
    if (Number(row.ward_tender_count) > 0 || row.tender_catalogue) answered += n;
  }
  if (resolved < minimum) {
    return { broken: false, resolved, answered, detail: `${resolved} lookups with a ward, too few to judge` };
  }
  const percent = ((100 * answered) / resolved).toFixed(1);
  return answered / resolved < share
    ? { broken: true, resolved, answered, detail: `${answered} of ${resolved} lookups with a ward (${percent}%) answered a ward tender or a street tender; the rule is ${share * 100}%. Check the packaged ward snapshot, the tender table and ward-tenders.mjs` }
    : { broken: false, resolved, answered, detail: `${answered} of ${resolved} lookups with a ward (${percent}%) answered a ward tender or a street tender` };
}

// Lookups by where the road class came from. local_lookup "unavailable" means the service
// could not read data/karnataka-ownership.bin: since 7 Oct 2026 the state GIS is never
// asked in a request, so without that file every Karnataka point is "unknown".
export const ROAD_LAYER_QUERY = 'filter event="http_request"'
  + ' and (route="/v1/tenders/resolve" or route="/v1/potholes/report")'
  + " | stats count() as n by local_lookup";

export function judgeRoadLayers(rows) {
  const unavailable = rows.filter((row) => row.local_lookup === "unavailable")
    .reduce((sum, row) => sum + (Number(row.n) || 0), 0);
  return unavailable
    ? { broken: true, unavailable, detail: `${unavailable} lookups could not read the road ownership layers; data/karnataka-ownership.bin is missing from the package` }
    : { broken: false, unavailable, detail: "0 lookups without the road ownership layers" };
}
