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

// Lookups outside Karnataka, by how the ward lookup went and which ward snapshot
// answered. There the ward comes from the snapshots data/wards/runtime.json switches on
// (service/india-wards.mjs), which deploy.sh stages with tools/stage-india-wards.mjs.
export const INDIA_WARD_QUERY = 'filter event="http_request" and road_ownership="outside_state"'
  + ' and (route="/v1/tenders/resolve" or route="/v1/potholes/report")'
  + " | stats count() as n by ward_lookup, ward_snapshot";

// ward_lookup "unavailable" on a lookup outside Karnataka means the service could not
// read a file it was told is switched on: with a ward_snapshot, that snapshot's file is
// missing from the package or is not the bytes the list pinned; with none, the list
// itself is missing. Either way every point in that city is answered with no ward and no
// ward tenders, which looks exactly like a city nobody has a ward file for. Lines from
// before this release say out_of_scope and carry no ward_snapshot.
export function judgeIndiaWardSnapshots(rows) {
  const missing = new Map();
  const placed = new Map();
  for (const row of rows) {
    const n = Number(row.n) || 0;
    if (row.ward_lookup === "unavailable") {
      const what = row.ward_snapshot || "data/wards/runtime.json";
      missing.set(what, (missing.get(what) || 0) + n);
    } else if ((row.ward_lookup === "resolved" || row.ward_lookup === "resolved_unnamed") && row.ward_snapshot) {
      placed.set(row.ward_snapshot, (placed.get(row.ward_snapshot) || 0) + n);
    }
  }
  const list = (counts, open, close) => [...counts].sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .map(([what, n]) => `${what} ${open}${n}${close}`).join(", ");
  const unavailable = [...missing.values()].reduce((sum, n) => sum + n, 0);
  const inWard = [...placed.values()].reduce((sum, n) => sum + n, 0);
  return unavailable
    ? { broken: true, unavailable, detail: `${unavailable} lookups outside Karnataka could not read a ward snapshot the package should hold: ${list(missing, "(", ")")}. Check that deploy.sh ran tools/stage-india-wards.mjs` }
    : { broken: false, unavailable, detail: `0 lookups without a switched-on ward snapshot; ${inWard} placed in a ward${inWard ? ` (${list(placed, "", "")})` : ""}` };
}

// The canary's point outside Karnataka: 760 m inside Shahibag ward of Ahmedabad (ward 16
// of the 48, which the committed file and a 2024 ward map draw alike over 97% of it),
// beside Rajasthan Hospital. Shahibag had ten open road notices on 7 Oct 2026, every one
// of which says "Shahibaug ward" in its title.
export const INDIA_WARD_CANARY = Object.freeze({
  lat: 23.05231, lng: 72.60005, hint: "Rajasthan Hospital Road, Shahibaug, Ahmedabad",
  ward: "SHAHIBAG", snapshot: "GJ/ahmedabad", says: /shahiba(?:u)?gh?\s+ward/i,
});

// What the live answer for that point says. The ward by name is required. Its tenders
// are not: notices close every week and the catalogue is refreshed weekly, so an empty
// list is a failure only when the caller knows better, that is when `expectedOpen` (the
// open notices this checkout's own catalogue holds for the ward) is above zero and the
// service's own count of the body's open notices is too. deploy.sh passes it, because
// what it just deployed is this checkout; the scheduled run does not, because the
// checkout may be a week ahead of production.
//   state "ok" | "fail" | "skip", with the sentence to print
export function judgeIndiaWardCanary({ status, body, expectedOpen = null, canary = INDIA_WARD_CANARY } = {}) {
  const jurisdiction = body?.jurisdiction;
  if (status !== 200 || !jurisdiction || jurisdiction.road_ownership !== "outside_state") {
    return { state: "fail", detail: `${status} ${JSON.stringify(body)?.slice(0, 200)}` };
  }
  // The release that names wards outside Karnataka also says which urban body a point is in.
  if (!Object.hasOwn(jurisdiction, "urban_body")) {
    return { state: "skip", detail: "the service answering is from before ward snapshots outside Karnataka; nothing to judge until it is deployed" };
  }
  const lookup = jurisdiction.lookup || {};
  if (lookup.ward !== "resolved" || jurisdiction.ward_name !== canary.ward || lookup.ward_snapshot !== canary.snapshot) {
    return { state: "fail", detail: `expected ${canary.ward} from ${canary.snapshot}; lookup.ward ${lookup.ward}, ward_name ${jurisdiction.ward_name}, ward_snapshot ${lookup.ward_snapshot}`
      + (lookup.ward === "unavailable" ? ". The package is missing data/wards/runtime.json or that snapshot's file" : "") };
  }
  const where = `${jurisdiction.ward_name} (ward ${jurisdiction.ward_no}, ${lookup.ward_snapshot})`;
  const tenders = Array.isArray(body.ward_tenders) ? body.ward_tenders : [];
  if (tenders.length) {
    const stray = tenders.find((tender) => !canary.says.test(String(tender?.title || "")));
    return stray
      ? { state: "fail", detail: `${where}: a ward tender does not say the ward: ${String(stray.title).slice(0, 120)}` }
      : { state: "ok", detail: `${where}; ${tenders.length} ward tenders, first: ${String(tenders[0].title).slice(0, 110)}` };
  }
  const urbanBody = jurisdiction.urban_body;
  if (!(urbanBody?.road_notices_open > 0)) {
    return { state: "skip", detail: `${where} answered; tenders not checked: the deployed catalogue holds no open road notice of ${urbanBody?.name || "Ahmedabad Municipal Corporation"} (its weekly pack has closed or passed its review date)` };
  }
  if (expectedOpen === null) {
    return { state: "skip", detail: `${where} answered with no ward tender; not checked further: notices close every week and this run does not know which catalogue is deployed` };
  }
  if (!expectedOpen) {
    return { state: "skip", detail: `${where} answered; tenders not checked: the catalogue holds no open notice for ${canary.ward} ward today (notices close every week)` };
  }
  return { state: "fail", detail: `${where} answered no ward tender, and the catalogue just deployed holds ${expectedOpen} open notices for the ward. Check india-ward-tenders.mjs and the staged road notice pack` };
}
