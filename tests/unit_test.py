# -*- coding: utf-8 -*-
"""Unit tests for the engine's pure logic.

These reach the real functions through StandaloneAPI.__pure, so a test exercises exactly
the code that runs in production. No network, no photo, no model: everything here is
deterministic and should stay that way.
"""
import json, sys, pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES = r"""
(() => {
  const P = StandaloneAPI.__pure;
  const out = [];
  const eq = (name, got, want) => out.push([name, JSON.stringify(got) === JSON.stringify(want), got, want]);
  const ok = (name, cond, detail) => out.push([name, !!cond, detail === undefined ? cond : detail, true]);

  // ---- distMeters: the dedupe radius and the 8 m capture spacing both rest on this ----
  const d = P.distMeters(12.9115, 77.6427, 12.9115, 77.6427);
  ok("distMeters: same point is zero", d === 0, d);
  const north = P.distMeters(12.9115, 77.6427, 12.91240, 77.6427);   // ~100 m north
  ok("distMeters: 100 m north", Math.abs(north - 100) < 3, Math.round(north));
  // A degree of longitude is shorter at this latitude; a formula ignoring that reads ~111 m.
  const east = P.distMeters(12.9115, 77.6427, 12.9115, 77.64362);
  ok("distMeters: east distance accounts for latitude", Math.abs(east - 100) < 5, Math.round(east));

  // ---- post-detection event grouping ----
  const event = { decision:"accept", status:"draft", dedupe_eligible:true,
    capture_source:"drive_live", drive_id:"d1", source_event_key:"live:d1:1",
    source_event_keys:["live:d1:1"], source_offset_s:10, captured_at:1800000010,
    created_at:1800000010, last_seen_at:1800000010,
    lat:12.9115, lng:77.6427, gps_accuracy:5, speed_mps:8, heading:90,
    damage_type:"pothole_cavity", size:"medium" };
  eq("dedupe: exact retained-video frame is certain",
     P.roadEventMatch({...event, capture_source:"drive_vod"}, event).kind, "same_source");
  const adjacent = {...event, source_event_key:"live:d1:2", source_offset_s:13,
    captured_at:1800000013, lat:12.91159}; // ~10 m north
  eq("dedupe: adjacent same-drive observation groups after detection",
     P.roadEventMatch(adjacent, event).kind, "same_drive");
  eq("dedupe: a later nearby defect in the same drive remains distinct",
     P.roadEventMatch({...adjacent, source_offset_s:15, captured_at:1800000015}, event), null);
  const middle = {...event, source_event_key:"middle", source_offset_s:4,
    captured_at:1800000014, lat:12.911608};
  const first = {...event, source_event_key:"first", source_offset_s:0,
    captured_at:1800000010, lat:12.9115};
  const last = {...event, source_event_key:"last", source_offset_s:8,
    captured_at:1800000018, lat:12.911716};
  const middleCluster = {...middle, event_sightings:[
    {lat:middle.lat,lng:middle.lng,source_offset_s:4,captured_at:middle.captured_at,
     gps_accuracy:5,speed_mps:8,heading:90,source_event_key:"middle"},
    {lat:first.lat,lng:first.lng,source_offset_s:0,captured_at:first.captured_at,
     gps_accuracy:5,speed_mps:8,heading:90,source_event_key:"first"},
  ]};
  eq("dedupe: a middle-first completion cannot chain two outer defects",
     P.roadEventMatch(last, middleCluster), null);
  const noGps = {...event, lat:null, lng:null, gps_accuracy:null};
  eq("dedupe: no-GPS footage uses its stable drive offset",
     P.roadEventMatch({...noGps, source_event_key:"vod:d1:0:11000", source_offset_s:11,
       captured_at:1800000011}, noGps).kind, "same_drive");
  eq("dedupe: no-GPS grouping stays within two seconds",
     P.roadEventMatch({...noGps, source_event_key:"vod:d1:0:13001", source_offset_s:13.001,
       captured_at:1800000013.001}, noGps), null);
  const priorDrive = {...event, drive_id:"old", source_event_key:"live:old:1"};
  const laterDrive = {...event, drive_id:"new", source_event_key:"live:new:1",
    source_offset_s:2, captured_at:1800001010, created_at:1800001010, last_seen_at:1800001010,
    lat:12.911563}; // ~7 m north
  eq("dedupe: precise recent repeat across drives groups",
     P.roadEventMatch(laterDrive, priorDrive).kind, "prior_drive");
  const revisitFirst = {...laterDrive, lat:priorDrive.lat, source_offset_s:1,
    captured_at:1800001010, source_event_key:"live:new:revisit-1"};
  const revisitCanonical = {...priorDrive, event_sightings:[
    {drive_id:"old",lat:priorDrive.lat,lng:priorDrive.lng,source_offset_s:10,
     captured_at:priorDrive.captured_at,gps_accuracy:5,speed_mps:8,heading:90,
     source_event_key:priorDrive.source_event_key},
    {drive_id:"new",lat:revisitFirst.lat,lng:revisitFirst.lng,source_offset_s:1,
     captured_at:revisitFirst.captured_at,gps_accuracy:5,speed_mps:8,heading:90,
     source_event_key:revisitFirst.source_event_key},
  ]};
  const revisitAdjacent = {...revisitFirst, lat:12.91159, source_offset_s:4,
    captured_at:1800001013, source_event_key:"live:new:revisit-2"};
  eq("dedupe: revisit gets its own adjacent-sighting envelope",
     P.roadEventMatch(revisitAdjacent, revisitCanonical).kind, "same_drive");
  eq("dedupe: cross-drive event beyond eight metres stays distinct",
     P.roadEventMatch({...laterDrive, lat:12.91159}, priorDrive), null);
  eq("dedupe: old location can become a new repair occurrence",
     P.roadEventMatch({...laterDrive, captured_at:1800000010 + 31*86400,
       created_at:1800000010 + 31*86400, last_seen_at:1800000010 + 31*86400}, priorDrive), null);
  eq("dedupe: different surface-damage types stay separate",
     P.roadEventMatch({...laterDrive, damage_type:"surface_breakup"}, priorDrive), null);
  eq("dedupe: opposite travel headings do not merge carriageways",
     P.roadEventMatch({...laterDrive, heading:270}, priorDrive), null);
  eq("dedupe: poor cross-drive GPS never auto-merges",
     P.roadEventMatch({...laterDrive, gps_accuracy:40}, priorDrive), null);
  const failedPatch = {...laterDrive, damage_type:"failed_patch", lat:12.911536}; // ~4 m
  eq("dedupe: cavity and failed-patch family can match very close",
     P.roadEventMatch(failedPatch, priorDrive).kind, "prior_drive");
  eq("dedupe: cavity and failed-patch mismatch tightens the radius",
     P.roadEventMatch({...failedPatch, lat:12.911554}, priorDrive), null);
  eq("dedupe: a new routable report is not hidden by an unrouted one",
     P.roadEventMatch(laterDrive, {...priorDrive, status:"unrouted"}), null);
  eq("dedupe: Debug evidence never suppresses a real run",
     P.roadEventMatch(laterDrive, {...priorDrive, debug_capture:true, dedupe_eligible:false}), null);
  eq("dedupe: an explicit manual report is not silently swallowed",
     P.roadEventMatch({...laterDrive, capture_source:"manual", drive_id:null}, priorDrive), null);
  eq("dedupe: small and large observations remain separate",
     P.roadEventMatch({...laterDrive, size:"large"}, {...priorDrive, size:"small"}), null);

  // ---- streamed road-damage decision contract ----
  const accepted = '{"reportable":true,"assessment":"clear","image_quality":"usable","damage_type":"pothole_cavity","on_drivable_surface":true,"has_broken_edge_or_rim":true,"has_depth_or_surface_loss":true,"temporal_consistency":"consistent","size":"large","description":"x"}';
  const rejected = '{"reportable":false,"assessment":"absent","image_quality":"usable","damage_type":"none","on_drivable_surface":false,"has_broken_edge_or_rim":false,"has_depth_or_surface_loss":false,"temporal_consistency":"not_applicable","size":null,"description":"none"}';
  const uncertain = '{"reportable":true,"assessment":"uncertain","image_quality":"degraded","damage_type":"failed_patch","on_drivable_surface":true,"has_broken_edge_or_rim":true,"has_depth_or_surface_loss":true,"temporal_consistency":"consistent","size":"medium","description":"x"}';
  eq("peek: nothing yet", P.peekVerdict('{"report'), null);
  eq("peek: partial damage type cannot decide",
     P.peekVerdict('{"reportable":true,"assessment":"clear","image_quality":"usable","damage_type":"pothole_cav'), null);
  eq("peek: false is final without invented confidence",
     P.peekVerdict('{"reportable": false'), {accepted:false, review:false, damage_type:"none", assessment:"absent"});
  const earlyAccepted = P.peekVerdict(accepted.slice(0, accepted.indexOf(',"size"')));
  eq("peek: accepted uses semantic policy", earlyAccepted,
     {accepted:true, review:false, damage_type:"pothole_cavity", assessment:"clear"});
  const earlyReview = P.peekVerdict(uncertain.slice(0, uncertain.indexOf(',"size"')));
  eq("peek: uncertainty becomes review", earlyReview,
     {accepted:false, review:true, damage_type:"failed_patch", assessment:"uncertain"});

  ok("reject: not yet decidable", P.peekReject('{"reportable"') === false);
  ok("reject: false is immediately final", P.peekReject('{"reportable": false') === true);
  ok("reject: true alone is not final", P.peekReject('{"reportable": true') === false);
  ok("reject: uncertain becomes final only after evidence fields", P.peekReject(uncertain) === true);

  // An accepted response must never be reported as rejected at any prefix.
  let wrongAbort = null;
  for (let i = 1; i <= accepted.length; i++) if (P.peekReject(accepted.slice(0, i))) { wrongAbort = i; break; }
  ok("reject: never aborts an accepted frame at any prefix", wrongAbort === null, wrongAbort);

  const rv = P.rejectedVerdict(rejected.slice(0, rejected.indexOf(',"size"')));
  ok("rejectedVerdict: not reportable", rv.reportable === false, rv);
  ok("rejectedVerdict: shape is complete",
     ["assessment","image_quality","damage_type","on_drivable_surface",
      "has_broken_edge_or_rim","has_depth_or_surface_loss","temporal_consistency",
      "size","description"].every((k) => k in rv), Object.keys(rv));

  // ---- final semantic gate ----
  const good = { reportable:true, assessment:"clear", image_quality:"usable",
    damage_type:"pothole_cavity", on_drivable_surface:true,
    has_broken_edge_or_rim:true, has_depth_or_surface_loss:false,
    temporal_consistency:"consistent" };
  for (const type of ["pothole_cavity","failed_patch","surface_breakup","rut_or_depression","other_road_damage"]) {
    eq(`decision: accepts clear ${type}`, P.decisionFor({...good, damage_type:type}), "accept");
  }
  eq("decision: probable strong evidence accepts", P.decisionFor({...good, assessment:"probable"}), "accept");
  eq("decision: uncertainty is review", P.decisionFor({...good, assessment:"uncertain"}), "review");
  eq("decision: unusable is review", P.decisionFor({...good, image_quality:"unusable"}), "review");
  eq("decision: contradictory none rejects", P.decisionFor({...good, damage_type:"none"}), "reject");
  eq("decision: off-road rejects", P.decisionFor({...good, on_drivable_surface:false}), "reject");
  eq("decision: no structural cue is review",
     P.decisionFor({...good, has_broken_edge_or_rim:false, has_depth_or_surface_loss:false}), "review");

  // ---- multimodal request builder and capability-safe settings ----
  const req = P.buildDetectionRequest(["a","b",null,"c","d","e"], "PROMPT", "gpt-5.6", "original");
  const content = req.input[0].content;
  eq("request: selected model", req.model, "gpt-5.6");
  eq("request: image cap", content.filter((x) => x.type === "input_image").length, 4);
  ok("request: original detail lives on every image",
     content.filter((x) => x.type === "input_image").every((x) => x.detail === "original"), content);
  ok("request: prompt appears once and last", content.at(-1).type === "input_text" &&
     content.filter((x) => x.type === "input_text").length === 1, content);
  eq("settings: arbitrary model fails safe", P.normaliseModel("gpt-made-up"), "gpt-5-mini");
  eq("settings: original falls back on mini", P.normaliseDetail("original", "gpt-5-mini"), "high");

  // ---- deterministic burst-quality selection ----
  const pixels = (w, h, fn) => {
    const a = new Uint8ClampedArray(w * h * 4);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const v = fn(x, y), i = (y * w + x) * 4;
      a[i] = a[i + 1] = a[i + 2] = v; a[i + 3] = 255;
    }
    return a;
  };
  const sharp = scoreRoadPixels(pixels(16, 16, (x, y) => (x + y) % 2 ? 70 : 170), 16, 16);
  const flat = scoreRoadPixels(pixels(16, 16, () => 115), 16, 16);
  const clipped = scoreRoadPixels(pixels(16, 16, (x, y) => (x + y) % 2 ? 0 : 255), 16, 16);
  ok("quality: road-like edges beat a uniform frame", sharp.score > flat.score, {sharp, flat});
  ok("quality: clipped black/white frame is unusable", clipped.score < flat.score, clipped);
  eq("quality: highest score becomes primary", bestBurstIndex([
    {quality:{score:1}}, {quality:{score:7}}, {quality:{score:3}}]), 1);
  eq("quality: ties keep earliest frame", bestBurstIndex([
    {quality:{score:7}}, {quality:{score:7}}, {quality:{score:3}}]), 0);

  // ---- warrantyFor: decides a sentence in a letter naming a private company ----
  const NOW = Date.UTC(2026, 7, 20);
  eq("warranty: 6 months old is defect liability",
     P.warrantyFor("20-02-2026", NOW), {warranty:"within the defect liability period", warranty_code:"dlp"});
  eq("warranty: 2 years old is maintenance",
     P.warrantyFor("20-08-2024", NOW), {warranty:"within the maintenance period", warranty_code:"maint"});
  eq("warranty: 5 years old claims nothing",
     P.warrantyFor("20-08-2021", NOW), {warranty:"recorded for this stretch", warranty_code:"record"});
  eq("warranty: unparseable date claims nothing",
     P.warrantyFor("not a date", NOW), {warranty:"recorded for this stretch", warranty_code:"record"});
  eq("warranty: missing date claims nothing",
     P.warrantyFor(null, NOW), {warranty:"recorded for this stretch", warranty_code:"record"});
  eq("warranty: a future date claims nothing",
     P.warrantyFor("20-08-2027", NOW), {warranty:"recorded for this stretch", warranty_code:"record"});
  eq("warranty: month 13 is not a date",
     P.warrantyFor("20-13-2025", NOW), {warranty:"recorded for this stretch", warranty_code:"record"});

  // ---- listDict: the list must never carry the full-size evidence photo ----
  const rec = {id:1, photo:"P", photo_full:"F", status:"draft"};
  ok("listDict: omits the evidence copy", P.listDict(rec).photo_full === undefined, P.listDict(rec));
  ok("listDict: keeps the thumbnail", P.listDict(rec).photo_url === "P");
  ok("toDict: the detail form keeps both",
     P.toDict(rec).photo_full === "F" && P.toDict(rec).photo_url === "P");

  // ---- inCoverage: only ever gates speculation, never routing ----
  ok("inCoverage: no location is not covered", P.inCoverage(null, null, null) === false);

  return out;
})()
"""

def main():
    fails = []
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--disable-web-security"])
        pg = b.new_context(viewport={"width": 390, "height": 844}).new_page()
        pg.goto("http://localhost:8765/"); pg.wait_for_load_state("networkidle")
        pg.wait_for_function("typeof StandaloneAPI !== 'undefined' && StandaloneAPI.__pure", timeout=30000)
        results = pg.evaluate(CASES)
        b.close()
    for name, passed, got, want in results:
        if passed:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}\n         got  {got}\n         want {want}")
            fails.append(name)
    print()
    if fails:
        print(f"{len(fails)} of {len(results)} failed"); sys.exit(1)
    print(f"UNIT TESTS PASS ({len(results)} checks)")

main()
