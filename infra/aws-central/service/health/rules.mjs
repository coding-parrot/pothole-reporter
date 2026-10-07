// Rules of the production health gate that read rows or one answer and say what they
// mean, with no query run and no request made. window.mjs and canary.mjs call them.

// Every tender lookup and report, counted by where its road class came from, how its
// ward lookup went and what it answered. Four rules read these rows, each taking the
// lookups it is about. They were three queries until 7 Oct 2026; Logs Insights charges
// each query for every byte of the window it scans, whatever its filter keeps, and one
// query grouped more finely gives the same counts (checked against the three over the
// same 24 hours and the same 7 days of production: every verdict identical).
export const LOOKUP_QUERY = 'filter event="http_request" and not ispresent(canary)'
  + ' and (route="/v1/tenders/resolve" or route="/v1/potholes/report")'
  + " | stats count() as n by road_ownership, local_lookup, ward_lookup, ward_snapshot, ward_tender_count, tender_catalogue";

// Karnataka municipal lookups. KGIS is Karnataka's register, so road_ownership
// "municipal" is a Karnataka town. A matched street tender sets tender_catalogue on both
// routes; the report route's outcome is "created" or "deduplicated" either way.
export const municipalLookups = (rows) => rows.filter((row) => row.road_ownership === "municipal");

// Lookups outside Karnataka. There the ward comes from the snapshots
// data/wards/runtime.json switches on (service/india-wards.mjs), which deploy.sh stages
// with tools/stage-india-wards.mjs.
export const outsideStateLookups = (rows) => rows.filter((row) => row.road_ownership === "outside_state");

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

// Every drive frame the fast screen was asked about, in either mode that asks it. In
// openai_with_shadow_screen gpt-5-mini answers every frame and the screen is watched; in
// yolo_then_openai the screen decides, and gpt-5-mini sees only the frames it flags and
// the audited share of the frames it clears. One row is: what the phone was told
// (outcome), who told it (detector_provider: "openai" means outcome is gpt-5-mini's
// verdict, "yolo" that the screen answered alone), what the screen said, its raw score in
// buckets of 0.02, and whether it took over SLOW_SCREEN_MS. A line whose screen did not
// answer carries screen_error, no screen_assessment and no bucket. screen_audit_rate is
// on every line of the live mode and on no shadow line; screen_audited is on a cleared
// frame drawn for audit. Logs Insights leaves an absent field out of the row and prints
// true as 1. The shadow report, the curve, readiness and the live rules all read these
// rows (the report and the curve were two queries until 7 Oct 2026, for the reason given
// at LOOKUP_QUERY).
export const SLOW_SCREEN_MS = 300;
export const SCREEN_QUERY = 'filter event="http_request" and not ispresent(canary) and route="/v1/vision/detect" and status=200'
  + " and (ispresent(screen_assessment) or ispresent(screen_error) or ispresent(screen_score))"
  + ` | fields floor(screen_score * 50) as bucket, screen_ms > ${SLOW_SCREEN_MS} as slow`
  + " | stats count() as n, min(@timestamp) as first, max(@timestamp) as last"
  + " by outcome, screen_assessment, screen_error, bucket, detector_provider, screen_audit_rate, screen_audited, slow, screen_model";

const present = (value) => value !== undefined && value !== null && value !== "";
const yes = (value) => value === true || value === 1 || value === "1" || value === "true";
const frames = (row) => Number(row.n) || 0;
const percent = (value) => `${(100 * value).toFixed(1)}%`;
const screenAnswered = (row) => row.screen_assessment === "damaged" || row.screen_assessment === "undamaged";

// A window can also span a change of screen model, and the model that was replaced says
// nothing about the one answering now: on 7 Oct 2026 readiness read "22 of 28" when 25 of
// those frames belonged to the model retired four hours before. The model in service is
// the one seen last. Its rows are kept, with the lines whose screen did not answer (they
// carry no model) from its first frame onward.
const instant = (value) => {
  if (!present(value)) return NaN;
  const number = Number(value);
  return Number.isFinite(number) ? number : Date.parse(`${String(value).replace(" ", "T")}Z`);
};
export function currentScreenRows(rows) {
  let model = null;
  let last = -Infinity;
  for (const row of rows) {
    if (present(row.screen_model) && instant(row.last) > last) {
      last = instant(row.last);
      model = row.screen_model;
    }
  }
  // Rows without timestamps (older callers, tests of a single model) cannot be ordered:
  // with one model or none there is nothing to separate.
  const models = new Set(rows.filter((row) => present(row.screen_model)).map((row) => row.screen_model));
  if (models.size <= 1) return { model: models.size ? [...models][0] : null, rows };
  const first = Math.min(...rows.filter((row) => row.screen_model === model).map((row) => instant(row.first)));
  return {
    model,
    rows: rows.filter((row) => (present(row.screen_model)
      ? row.screen_model === model
      : instant(row.last) >= first)),
  };
}

// A window can hold both kinds of line (the hours either side of a switch), and each is
// evidence of a different thing, so every rule below is given only its own.
export const liveRows = (rows) => rows.filter((row) => present(row.screen_audit_rate));
export const shadowRows = (rows) => rows.filter((row) => !present(row.screen_audit_rate));

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

// The same frames by the bucket of the screen's raw score. screen_assessment is the
// score against the threshold the screen was deployed with; the score itself says what
// any other threshold would have done, which is what choosing one needs.
// Reported, never failed. The highest bucket edge at which the screen would still have
// flagged `target` of the frames gpt-5-mini judged damaged, and the share of undamaged
// frames a screen run at that threshold would have cleared.
export function shadowScreenCurve(rows, { target = 0.98, minimum = 100 } = {}) {
  const damaged = new Map();
  const undamaged = new Map();
  for (const row of rows) {
    const bucket = Number(row.bucket);
    const n = Number(row.n) || 0;
    if (!Number.isFinite(bucket) || !n) continue;
    const side = row.outcome === "damaged" ? damaged : row.outcome === "undamaged" ? undamaged : null;
    if (side) side.set(bucket, (side.get(bucket) || 0) + n);
  }
  const total = (side) => [...side.values()].reduce((sum, n) => sum + n, 0);
  const below = (side, edge) => [...side].reduce((sum, [bucket, n]) => sum + (bucket < edge ? n : 0), 0);
  const damagedTotal = total(damaged);
  const undamagedTotal = total(undamaged);
  if (damagedTotal < minimum) {
    return { broken: false, damaged: damagedTotal, threshold: null, clearedShare: null,
      detail: `${damagedTotal} scored frames judged damaged; ${minimum} are needed before a threshold can be read off` };
  }
  // A threshold at bucket edge e flags every frame in bucket e and above.
  let edge = 0;
  for (let candidate = 50; candidate >= 0; candidate -= 1) {
    if ((damagedTotal - below(damaged, candidate)) / damagedTotal >= target) {
      edge = candidate;
      break;
    }
  }
  const threshold = edge / 50;
  const recall = (damagedTotal - below(damaged, edge)) / damagedTotal;
  const cleared = below(undamaged, edge);
  const clearedShare = undamagedTotal ? cleared / undamagedTotal : null;
  return {
    broken: false, damaged: damagedTotal, undamaged: undamagedTotal, threshold, recall, cleared, clearedShare,
    detail: `a threshold of ${threshold.toFixed(2)} would have flagged ${(100 * recall).toFixed(1)}% of ${damagedTotal} damaged frames`
      + ` and cleared ${cleared} of ${undamagedTotal} undamaged (${clearedShare === null ? "n/a" : `${(100 * clearedShare).toFixed(1)}%`})`,
  };
}

// How the screen itself behaved on the rows given: the frames it was asked about, the
// ones it gave no answer for and the ones it took over SLOW_SCREEN_MS on. The p90 of its
// time is over that limit exactly when more than one frame in ten is slow, which is why
// the query counts slow frames instead of asking for a percentile: a percentile cannot
// be added up across the groups of one query, and a second query scans the window again.
function screenService(rows) {
  let screened = 0;
  let errors = 0;
  let slow = 0;
  for (const row of rows) {
    if (!screenAnswered(row) && !row.screen_error) continue;
    const n = frames(row);
    screened += n;
    if (!screenAnswered(row)) errors += n;
    if (yes(row.slow)) slow += n;
  }
  return { screened, errors, slow };
}

// Whether shadow mode has shown enough to let the screen answer (yolo_then_openai).
// Report only: it never fails a run. Five conditions, each printed with its counts,
// over whatever window the run was given (a week is the useful one):
//   at least 300 frames gpt-5-mini judged damaged,
//   the screen flagged at least 98% of them at the threshold it is deployed with,
//   it cleared at least 30% of the frames gpt-5-mini judged undamaged,
//   it gave no answer on under 1% of frames,
//   its p90 is under 300 ms (at most one frame in ten over SLOW_SCREEN_MS).
// A sixth is wanted and cannot be checked here: that the damaged frames come from more
// than one phone. The request line carries no install id and no marker derived from one,
// and none is added for this; the line says so every time.
export function reportShadowReadiness(rows, { damagedFrames = 300, recall = 0.98, clearedShare = 0.3, errorShare = 0.01, slowShare = 0.1 } = {}) {
  const shadow = reportShadowScreen(rows);
  const service = screenService(rows);
  if (!service.screened) {
    return { broken: false, ready: false, detail: "no drive frames were shadow screened in the window; nothing to say about switching on" };
  }
  const of = (part, whole) => (whole ? percent(part / whole) : "n/a");
  const conditions = [
    [shadow.damaged >= damagedFrames, `${shadow.damaged} frames gpt-5-mini judged damaged (${damagedFrames} needed)`],
    [shadow.damaged > 0 && shadow.recall >= recall,
      shadow.damaged ? `the screen flagged ${shadow.flagged} of ${shadow.damaged} damaged frames, live recall ${percent(shadow.recall)} (${recall * 100}% needed)`
        : `no damaged frame to measure recall on (${recall * 100}% needed)`],
    [shadow.undamaged > 0 && shadow.clearedShare >= clearedShare,
      `cleared ${shadow.cleared} of ${shadow.undamaged} frames gpt-5-mini judged undamaged, ${of(shadow.cleared, shadow.undamaged)} (${clearedShare * 100}% needed)`],
    [service.errors < errorShare * service.screened,
      `${service.errors} of ${service.screened} frames had no screen answer, ${of(service.errors, service.screened)} (under ${errorShare * 100}% needed)`],
    [service.slow <= slowShare * service.screened,
      `${service.slow} of ${service.screened} frames took the screen over ${SLOW_SCREEN_MS} ms, ${of(service.slow, service.screened)} (p90 is under ${SLOW_SCREEN_MS} ms up to ${slowShare * 100}%)`],
  ];
  const met = conditions.filter(([holds]) => holds).map(([, text]) => text);
  const notMet = conditions.filter(([holds]) => !holds).map(([, text]) => text);
  const ready = notMet.length === 0;
  const detail = [
    `${ready ? "READY to switch on by the request log" : "NOT READY to switch on"}: ${met.length} of ${conditions.length} conditions met`,
    ...(met.length ? [`Met: ${met.join("; ")}`] : []),
    ...(notMet.length ? [`Not met: ${notMet.join("; ")}`] : []),
    "Not checked: that the damaged frames come from more than one phone (the request log carries no install marker)",
  ].join(". ");
  return { broken: false, ready, met: met.length, conditions: conditions.length, detail };
}

// The Wilson score interval for a share seen as `successes` of `trials` (95% at z 1.96).
// Unlike the plain interval it does not collapse to nothing when no success was seen,
// which is the usual case here: most audited samples hold no miss at all.
export function wilson(successes, trials, z = 1.96) {
  if (!trials) return { low: 0, high: 1 };
  const share = successes / trials;
  const scale = 1 + (z * z) / trials;
  const centre = (share + (z * z) / (2 * trials)) / scale;
  const half = (z * Math.sqrt((share * (1 - share)) / trials + (z * z) / (4 * trials * trials))) / scale;
  return { low: Math.max(0, centre - half), high: Math.min(1, centre + half) };
}

// The screen's recall once it answers users, estimated from live lines.
//
// In yolo_then_openai gpt-5-mini judges two kinds of drive frame: every frame the screen
// flagged, and the audited sample of the frames it cleared. An audited frame is by
// construction one the screen cleared, so each one gpt-5-mini calls damaged is a miss.
//
//   caught   = flagged frames gpt-5-mini judged damaged          (counted, all of them)
//   cleared  = frames the screen cleared                         (counted, all of them)
//   audited  = cleared frames gpt-5-mini also judged             (the sample)
//   missed   = audited frames gpt-5-mini judged damaged          (misses seen)
//   missed^  = missed x cleared / audited                        (misses estimated)
//   recall^  = caught / (caught + missed^)
//
// cleared / audited is 1 / the audit rate as it really ran. It is used in place of the
// configured rate so that an audit lost to a gpt-5-mini error (it is drawn, logged and
// has no verdict) shrinks the sample instead of hiding misses.
//
// Assumptions. (1) The audited frames are a uniform random sample of the cleared ones:
// the draw is made by the server after the screen has answered and depends on nothing in
// the request, and a lost audit is lost for reasons unrelated to the image. (2)
// gpt-5-mini's verdict is the truth being measured against, as in shadow mode; what it
// misses itself is in neither number. (3) One audit rate held for the window; across a
// change of rate the pooled share is right only if the screen missed equally often
// before and after, so judge a window that starts after the change. (4) Flagged frames
// gpt-5-mini failed on (not a 200) are in no count.
//
// The interval is the Wilson 95% score interval on the share of misses in the audited
// sample (missed of audited, the frames actually looked at), applied to the cleared
// frames nobody looked at. It is deliberately not computed on the scaled counts: at a
// rate of 0.1 that would treat one audited miss as ten observed ones and claim ten times
// the evidence there is. caught is a count, not a sample, and adds no width.
//
// What reached users is better than recall^ by exactly the misses the audit saw: those
// frames were answered by gpt-5-mini.
export function estimateLiveRecall(rows) {
  let caught = 0;
  let cleared = 0;
  let drawn = 0;
  let audited = 0;
  let missed = 0;
  for (const row of rows) {
    const n = frames(row);
    const judged = row.detector_provider === "openai";
    if (row.screen_assessment === "damaged") {
      if (judged && row.outcome === "damaged") caught += n;
    } else if (row.screen_assessment === "undamaged") {
      cleared += n;
      if (!yes(row.screen_audited)) continue;
      drawn += n;
      if (!judged) continue;
      audited += n;
      if (row.outcome === "damaged") missed += n;
    }
  }
  if (!audited) {
    return { caught, cleared, drawn, audited, missed, missedEstimate: null, damagedEstimate: null, recall: null, recallLow: null, recallHigh: null };
  }
  const unseen = cleared - audited;
  const share = wilson(missed, audited);
  const recallAt = (misses) => (caught + misses ? caught / (caught + misses) : null);
  const missedEstimate = (missed * cleared) / audited;
  return {
    caught, cleared, drawn, audited, missed, missedEstimate,
    damagedEstimate: caught + missedEstimate,
    recall: recallAt(missedEstimate),
    recallLow: recallAt(missed + share.high * unseen),
    recallHigh: recallAt(missed + share.low * unseen),
  };
}

// Fails when the estimate's point value is under 98% and the window held at least 50
// damaged frames by the same estimate; below that it says "too few to judge". The point
// value moves in steps: at an audit rate of 0.1 one audited miss stands for ten, so the
// interval is printed beside it every time and is the thing to read before acting.
function judgeLiveRecall(rows, { target = 0.98, minimum = 50 } = {}) {
  const estimate = estimateLiveRecall(rows);
  const { caught, cleared, audited, missed } = estimate;
  if (!audited) {
    return { broken: false, ...estimate, detail: `${caught} flagged frames gpt-5-mini confirmed damaged; no cleared frame was audited, so what the screen missed cannot be estimated` };
  }
  const seen = `${caught} flagged frames gpt-5-mini confirmed damaged; ${missed} of ${audited} audited frames ${missed === 1 ? "was a pothole" : "were potholes"} the screen had cleared,`
    + ` about ${Math.round(estimate.missedEstimate)} missed among ${cleared} cleared`;
  if (estimate.damagedEstimate < minimum) {
    return { broken: false, ...estimate, detail: `about ${Math.round(estimate.damagedEstimate)} damaged frames (${seen}), too few to judge; the rule needs ${minimum}` };
  }
  const said = `estimated recall ${percent(estimate.recall)} (95% interval ${percent(estimate.recallLow)} to ${percent(estimate.recallHigh)}): ${seen}`;
  return estimate.recall < target
    ? { broken: true, ...estimate, detail: `${said}; the rule is ${target * 100}%. The screen is answering users: read the interval, and go back to SharedDetectorProvider=openai_with_shadow_screen (README, "Letting the screen answer") if it holds` }
    : { broken: false, ...estimate, detail: said };
}

// Under 100 frames a single failure is already over 1%, so fewer are said and not judged.
function judgeLiveScreenAnswers(rows, { share = 0.01, minimum = 100 } = {}) {
  const { screened, errors } = screenService(rows);
  if (screened < minimum) return { broken: false, detail: `${screened} drive frames, too few to judge (${errors} had no screen answer)` };
  const said = `${errors} of ${screened} drive frames (${percent(errors / screened)}) had no screen answer`;
  return errors > share * screened
    ? { broken: true, detail: `${said}; the rule is ${share * 100}%. Each of them went to gpt-5-mini instead, so phones were answered, slowly; check the screen function's own log` }
    : { broken: false, detail: said };
}

function judgeLiveScreenSpeed(rows, { share = 0.1, minimum = 100 } = {}) {
  const { screened, slow } = screenService(rows);
  if (screened < minimum) return { broken: false, detail: `${screened} drive frames, too few to judge (${slow} took the screen over ${SLOW_SCREEN_MS} ms)` };
  const said = `${slow} of ${screened} drive frames (${percent(slow / screened)}) took the screen over ${SLOW_SCREEN_MS} ms`;
  return slow > share * screened
    ? { broken: true, detail: `${said}; the rule is a p90 under ${SLOW_SCREEN_MS} ms, at most ${share * 100}% of frames over it` }
    : { broken: false, detail: said };
}

// An audit that has stopped looks, to the recall rule, exactly like a screen that misses
// nothing. So: when the cleared frames of the window should have produced more than 20
// audits at the rate they were logged with (200 frames at the default 0.1) and gpt-5-mini
// judged none, the rule fails. By expected audits and not by a bare frame count, because
// at a rate of 0.01 two hundred frames produce none one time in seven with nothing wrong.
// A rate of 0 is the operator's choice and is said, not failed.
function judgeAuditRunning(rows, { expectedAudits = 20 } = {}) {
  const { cleared, drawn, audited } = estimateLiveRecall(rows);
  // Cleared frames by the rate each was logged with: a window can span a change of rate.
  const rates = new Map();
  for (const row of rows) {
    if (row.screen_assessment !== "undamaged") continue;
    const rate = Number(row.screen_audit_rate);
    rates.set(rate, (rates.get(rate) || 0) + frames(row));
  }
  const expected = [...rates].reduce((sum, [rate, n]) => sum + rate * n, 0);
  const named = [...rates.keys()].sort((left, right) => left - right).join(" and ");
  if (audited) return { broken: false, detail: `gpt-5-mini judged ${audited} of ${cleared} cleared frames (audit rate ${named})${drawn > audited ? `; ${drawn - audited} more were drawn and lost to gpt-5-mini errors` : ""}` };
  if (cleared && expected === 0) return { broken: false, detail: `the audit rate is 0: ${cleared} cleared frames and none sent to gpt-5-mini, so what the screen misses is not being measured` };
  return expected > expectedAudits
    ? { broken: true, detail: `${cleared} cleared frames at an audit rate of ${named} should have sent about ${Math.round(expected)} to gpt-5-mini and none was judged${drawn ? ` (${drawn} were drawn and lost to gpt-5-mini errors)` : ""}; the screen's misses are invisible until this is fixed` }
    : { broken: false, detail: `${cleared} cleared frames, about ${Math.round(expected)} audits expected and none judged yet; too few to call the audit dead` };
}

// The four live rules, in the order they are printed, over the live lines of a window.
export function judgeLiveScreen(rows) {
  return [
    ["live screen recall", judgeLiveRecall(rows)],
    ["live screen answers", judgeLiveScreenAnswers(rows)],
    ["live screen is fast", judgeLiveScreenSpeed(rows)],
    ["live audit is running", judgeAuditRunning(rows)],
  ];
}

// All lookups, by where the road class came from. local_lookup "unavailable" means the
// service could not read data/karnataka-ownership.bin: since 7 Oct 2026 the state GIS is
// never asked in a request, so without that file every Karnataka point is "unknown".
export function judgeRoadLayers(rows) {
  const unavailable = rows.filter((row) => row.local_lookup === "unavailable")
    .reduce((sum, row) => sum + (Number(row.n) || 0), 0);
  return unavailable
    ? { broken: true, unavailable, detail: `${unavailable} lookups could not read the road ownership layers; data/karnataka-ownership.bin is missing from the package` }
    : { broken: false, unavailable, detail: "0 lookups without the road ownership layers" };
}

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
