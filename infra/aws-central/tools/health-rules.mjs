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

// Drive frames judged in openai_with_shadow_screen: gpt-5-mini's verdict (outcome) beside
// what the fast screen said about the same frame. A line whose screen did not answer
// carries screen_error and no screen_assessment.
export const SHADOW_SCREEN_QUERY = 'filter event="http_request" and route="/v1/vision/detect" and status=200'
  + " and (ispresent(screen_assessment) or ispresent(screen_error))"
  + " | stats count() as n by outcome, screen_assessment, screen_error";

// A report, never a failure: shadow mode exists to find out how good the screen is, so
// a poor number here is the finding, not an outage. Live recall is, of the frames
// gpt-5-mini judged damaged, the share the screen also flagged: the potholes
// yolo_then_openai would have kept. The cleared share is, of the frames gpt-5-mini judged
// undamaged, the share the screen cleared: the gpt-5-mini calls that mode would save.
export function reportShadowScreen(rows) {
  let damaged = 0;
  let flagged = 0;
  let undamaged = 0;
  let cleared = 0;
  let unanswered = 0;
  for (const row of rows) {
    const n = Number(row.n) || 0;
    const screen = row.screen_assessment;
    if (screen !== "damaged" && screen !== "undamaged") {
      if (row.screen_error) unanswered += n;
      continue;
    }
    if (row.outcome === "damaged") {
      damaged += n;
      if (screen === "damaged") flagged += n;
    } else if (row.outcome === "undamaged") {
      undamaged += n;
      if (screen === "undamaged") cleared += n;
    }
  }
  const recall = damaged ? flagged / damaged : null;
  const clearedShare = undamaged ? cleared / undamaged : null;
  const percent = (value) => `${(100 * value).toFixed(1)}%`;
  const detail = damaged + undamaged + unanswered === 0
    ? "no drive frames were shadow screened in the window"
    : [
      damaged ? `the screen flagged ${flagged} of ${damaged} frames gpt-5-mini judged damaged (live recall ${percent(recall)})`
        : "gpt-5-mini judged no shadow-screened frame damaged",
      undamaged ? `cleared ${cleared} of ${undamaged} it judged undamaged (${percent(clearedShare)})`
        : "gpt-5-mini judged no shadow-screened frame undamaged",
      `${unanswered} frames had no screen answer`,
    ].join("; ");
  return { broken: false, damaged, flagged, recall, undamaged, cleared, clearedShare, unanswered, detail };
}
