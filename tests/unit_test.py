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

  // ---- orientation-aware Drive road region; manual Photo bypasses this transform ----
  eq("road region: portrait excludes sky and dashboard", P.selectRoadRegion(480, 720),
     {x:0, y:288, width:480, height:187});
  eq("road region: landscape excludes sky and dashboard", P.selectRoadRegion(1280, 720),
     {x:0, y:346, width:1280, height:216});
  eq("road region: near-square uses its explicit geometry", P.selectRoadRegion(1000, 1000),
     {x:0, y:400, width:1000, height:300});
  let invalidRoadRegionThrows = false;
  try { P.selectRoadRegion(0, 720); } catch (_) { invalidRoadRegionThrows = true; }
  ok("road region: invalid dimensions fail closed", invalidRoadRegionThrows);

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

  // ---- streamed binary pothole contract ----
  const accepted = '{"is_pothole":true,"looks_like_speed_breaker":false,"image_quality":"usable","surface_type":"bituminous_asphalt","on_drivable_surface":true,"has_localized_cavity":true,"has_broken_edge_or_rim":true,"has_depth_or_surface_loss":true,"temporal_consistency":"consistent","size":"large","description":"localized cavity"}';
  const rejected = '{"is_pothole":false,"looks_like_speed_breaker":false,"image_quality":"usable","surface_type":"bituminous_asphalt","on_drivable_surface":true,"has_localized_cavity":false,"has_broken_edge_or_rim":false,"has_depth_or_surface_loss":false,"temporal_consistency":"not_applicable","size":null,"description":"no cavity"}';
  const speedBreaker = '{"is_pothole":true,"looks_like_speed_breaker":true,"image_quality":"usable","surface_type":"bituminous_asphalt","on_drivable_surface":true,"has_localized_cavity":true,"has_broken_edge_or_rim":true,"has_depth_or_surface_loss":true,"temporal_consistency":"consistent","size":"medium","description":"painted transverse raised ridge"}';
  const surfaceBreakup = '{"is_pothole":true,"looks_like_speed_breaker":false,"image_quality":"usable","surface_type":"bituminous_asphalt","on_drivable_surface":true,"has_localized_cavity":false,"has_broken_edge_or_rim":true,"has_depth_or_surface_loss":true,"temporal_consistency":"consistent","size":"medium","description":"broad breakup"}';
  const temporaryCavity = '{"is_pothole":true,"looks_like_speed_breaker":false,"image_quality":"usable","surface_type":"temporary_drivable_surface","on_drivable_surface":true,"has_localized_cavity":true,"has_broken_edge_or_rim":true,"has_depth_or_surface_loss":true,"temporal_consistency":"consistent","size":"medium","description":"persistent localized cavity in an active temporary traffic lane"}';
  eq("peek: nothing yet", P.peekVerdict('{"is_pot'), null);
  eq("peek: YES cannot be announced before size",
     P.peekVerdict(accepted.slice(0, accepted.indexOf(',"size"'))), null);
  eq("peek: complete physical pothole is YES", P.peekVerdict(accepted),
     {accepted:true, review:false, damage_type:"pothole_cavity", assessment:"clear"});
  eq("peek: explicit NO is immediately final", P.peekVerdict('{"is_pothole":false'),
     {accepted:false, review:false, damage_type:"none", assessment:"absent"});
  eq("peek: speed breaker vetoes contradictory pothole fields", P.peekVerdict(speedBreaker),
     {accepted:false, review:false, damage_type:"none", assessment:"absent"});
  eq("peek: surface breakup without a localized cavity is NO", P.peekVerdict(surfaceBreakup),
     {accepted:false, review:false, damage_type:"none", assessment:"absent"});

  ok("reject: not yet decidable", P.peekReject('{"is_pot') === false);
  ok("reject: explicit NO is immediately final", P.peekReject('{"is_pothole":false') === true);
  ok("reject: YES alone is not final", P.peekReject('{"is_pothole":true') === false);
  ok("reject: speed breaker becomes final after required fields", P.peekReject(speedBreaker) === true);
  ok("reject: non-cavity breakup becomes final after required fields", P.peekReject(surfaceBreakup) === true);
  ok("reject: valid temporary-surface Drive cavity is not aborted", P.peekReject(temporaryCavity, true) === false);

  // An accepted response must never be reported as rejected at any prefix.
  let wrongAbort = null;
  for (let i = 1; i <= accepted.length; i++) if (P.peekReject(accepted.slice(0, i))) { wrongAbort = i; break; }
  ok("reject: never aborts an accepted frame at any prefix", wrongAbort === null, wrongAbort);

  const rv = P.rejectedVerdict(rejected.slice(0, rejected.indexOf(',"size"')));
  ok("rejectedVerdict: binary NO", rv.is_pothole === false && rv.reportable === false, rv);
  ok("rejectedVerdict: shape is complete",
     ["is_pothole","looks_like_speed_breaker","assessment","image_quality","damage_type",
      "on_drivable_surface","has_localized_cavity","has_broken_edge_or_rim",
      "has_depth_or_surface_loss","temporal_consistency","surface_type",
      "size","description"].every((k) => k in rv), Object.keys(rv));

  // ---- final semantic gate ----
  const good = { is_pothole:true, looks_like_speed_breaker:false,
    image_quality:"usable", surface_type:"bituminous_asphalt",
    on_drivable_surface:true, has_localized_cavity:true,
    has_broken_edge_or_rim:true, has_depth_or_surface_loss:true,
    temporal_consistency:"consistent", size:"medium" };
  for (const size of ["small","medium","large"]) {
    eq(`decision: accepts clear ${size} pothole`, P.decisionFor({...good, size}), "accept");
  }
  eq("decision: manual Photo accepts a defensible single view",
     P.decisionFor({...good, temporal_consistency:"single_view"}, false, 1), "accept");
  eq("decision: Drive requires chronological consistency",
     P.decisionFor({...good, temporal_consistency:"single_view"}, true, 3), "reject");
  eq("decision: Drive rejects one source frame even if model claims consistency",
     P.decisionFor(good, true, 1), "reject");
  eq("decision: Drive accepts at least two consistent source frames",
     P.decisionFor(good, true, 3), "accept");
  eq("decision: speed breaker hard-vetoes an otherwise accepted pothole",
     P.decisionFor({...good, looks_like_speed_breaker:true}), "reject");
  const missingBreaker = {...good}; delete missingBreaker.looks_like_speed_breaker;
  eq("decision: missing speed-breaker field fails closed", P.decisionFor(missingBreaker), "reject");
  eq("decision: mistyped speed-breaker field fails closed",
     P.decisionFor({...good, looks_like_speed_breaker:"false"}), "reject");
  eq("decision: model NO rejects", P.decisionFor({...good, is_pothole:false}), "reject");
  eq("decision: unusable is NO", P.decisionFor({...good, image_quality:"unusable"}), "reject");
  eq("decision: off-road rejects", P.decisionFor({...good, on_drivable_surface:false}), "reject");
  eq("decision: unknown surface fails closed", P.decisionFor({...good, surface_type:"unknown"}), "reject");
  eq("decision: unpaved surface fails closed", P.decisionFor({...good, surface_type:"unpaved_or_nonroad"}), "reject");
  const temporary = {...good, surface_type:"temporary_drivable_surface"};
  eq("decision: complete temporary traffic-surface cavity accepts in Drive",
     P.decisionFor(temporary, true, 3), "accept");
  eq("decision: temporary surface fails closed for one Photo",
     P.decisionFor({...temporary, temporal_consistency:"single_view"}, false, 1), "reject");
  eq("decision: temporary surface needs a discrete localized cavity",
     P.decisionFor({...temporary, has_localized_cavity:false}, true, 3), "reject");
  eq("decision: temporary surface needs a distinct broken rim",
     P.decisionFor({...temporary, has_broken_edge_or_rim:false}, true, 3), "reject");
  eq("decision: no localized cavity is NO",
     P.decisionFor({...good, has_localized_cavity:false}), "reject");
  eq("decision: missing broken rim is NO",
     P.decisionFor({...good, has_broken_edge_or_rim:false}), "reject");
  eq("decision: missing depth or surface loss is NO",
     P.decisionFor({...good, has_depth_or_surface_loss:false}), "reject");
  eq("decision: inconsistent views are NO",
     P.decisionFor({...good, temporal_consistency:"inconsistent"}), "reject");
  eq("decision: YES without size is NO", P.decisionFor({...good, size:null}), "reject");

  // ---- native detector upgrade bridge ----
  const nativeV12 = P.nativeDetectorContract({prompt_version:"pothole-binary-v12", schema_version:7});
  ok("native bridge: v12 accepts the temporary traffic-surface vocabulary",
     nativeV12 && nativeV12.kind === "current_v12"
       && nativeV12.surfaceTypes.has("temporary_drivable_surface"));
  const nativeV11 = P.nativeDetectorContract({prompt_version:"pothole-binary-v11", schema_version:7});
  ok("native bridge: unshipped v11 rows stay unsupported", nativeV11 === null);
  const nativeV10 = P.nativeDetectorContract({prompt_version:"pothole-binary-v10", schema_version:7});
  ok("native bridge: pending v10 rows remain importable",
     nativeV10 && nativeV10.kind === "legacy_v10"
       && nativeV10.surfaceTypes.has("temporary_drivable_surface"));
  const nativeV9 = P.nativeDetectorContract({prompt_version:"pothole-binary-v9", schema_version:7});
  ok("native bridge: pending v9 rows remain importable",
     nativeV9 && nativeV9.kind === "legacy_v9"
       && nativeV9.surfaceTypes.has("temporary_drivable_surface"));
  const nativeV8 = P.nativeDetectorContract({prompt_version:"pothole-binary-v8", schema_version:7});
  ok("native bridge: pending v8 rows remain importable",
     nativeV8 && nativeV8.kind === "legacy_v8"
       && nativeV8.surfaceTypes.has("temporary_drivable_surface"));
  const nativeV7 = P.nativeDetectorContract({prompt_version:"pothole-binary-v7", schema_version:7});
  ok("native bridge: pending v7 rows remain importable",
     nativeV7 && nativeV7.kind === "legacy_v7"
       && nativeV7.surfaceTypes.has("temporary_drivable_surface"));
  const nativeV6 = P.nativeDetectorContract({prompt_version:"pothole-binary-v6", schema_version:6});
  ok("native bridge: unsynced strict v6 paved reports remain importable",
     nativeV6 && nativeV6.kind === "legacy_v6" && nativeV6.surfaceTypes.has("bituminous_asphalt"));
  ok("native bridge: v6 cannot claim the temporary-surface class",
     nativeV6 && !nativeV6.surfaceTypes.has("temporary_drivable_surface"));
  eq("native bridge: older detector contracts stay obsolete",
     P.nativeDetectorContract({prompt_version:"road-damage-v4", schema_version:4}), null);

  // ---- strict physical repair status ----
  const repairPrior = {...event, id:41, photo:"data:image/jpeg;base64,eA==",
    drive_id:"old-drive", sighting_drive_ids:["old-drive"], condition_status:"open"};
  const revisit = {...event, capture_source:"drive_live", drive_id:"new-drive",
    source_event_key:"live:new-drive:1", lat:repairPrior.lat, lng:repairPrior.lng,
    gps_accuracy:4, speed_mps:8, heading:90, debug_capture:false,
    observed_at:1800001010};
  ok("repair: precise new-drive same-carriageway target matches",
     !!P.repairTargetMatch(revisit, repairPrior));
  eq("repair: missing revisit timestamp fails closed",
     P.repairTargetMatch({...revisit, observed_at:undefined}, repairPrior), null);
  eq("repair: equal timestamp is not later evidence",
     P.repairTargetMatch({...revisit, observed_at:repairPrior.last_seen_at}, repairPrior), null);
  eq("repair: earlier timestamp is not repair evidence",
     P.repairTargetMatch({...revisit, observed_at:repairPrior.last_seen_at - 1}, repairPrior), null);
  eq("repair: same drive can never close its own detection",
     P.repairTargetMatch({...revisit, drive_id:"old-drive"}, repairPrior), null);
  eq("repair: poor GPS cannot prove the same footprint",
     P.repairTargetMatch({...revisit, gps_accuracy:12.1}, repairPrior), null);
  eq("repair: opposite carriageway cannot close a report",
     P.repairTargetMatch({...revisit, heading:270}, repairPrior), null);
  eq("repair: ambiguity fails closed",
     P.findRepairCandidateFromReports(revisit, [repairPrior, {...repairPrior, id:42}]), null);
  eq("repair: generic absence alone is not a fixed decision",
     P.repairConditionFor({current_condition:"uncertain", assessment:"clear", image_quality:"usable",
       same_location_visible:true, completed_repair_visible:true}), null);
  eq("repair: probable completed repair is review only",
     P.repairConditionFor({current_condition:"repaired", assessment:"probable", image_quality:"usable",
       same_location_visible:true, completed_repair_visible:true}), "repair_review");
  eq("repair: fixed requires every visual gate",
     P.repairConditionFor({current_condition:"repaired", assessment:"clear", image_quality:"usable",
       same_location_visible:true, completed_repair_visible:true}), "fixed");
  eq("repair: fixed history does not suppress a recurrence",
     P.roadEventMatch(revisit, {...repairPrior, condition_status:"fixed"}), null);

  // ---- tender scope: a road name is not proof that the road is the work ----
  ok("tender scope: cited drain/footpath tender is excluded",
     !P.tenderCoversCarriageway("Construction of drain and footpath at Binny Cresent cross road, link road Benson town and surrounding area in Ward No 127 Jayamahal",
       "BBMP/2023-24/OW/WORK_INDENT2505"));
  ok("tender scope: road and drain mixed work is eligible",
     P.tenderCoversCarriageway("Improvements to roads and drains in Byrasandra surroundings in Ward no 112"));
  ok("tender scope: drain and CC road reverse mixed work is eligible",
     P.tenderCoversCarriageway("Construction of cc drain and cc road at ward no 4"));
  ok("tender scope: road used as a pipeline location is excluded",
     !P.tenderCoversCarriageway("Providing and laying water supply HDPE pipeline at burial ground road"));
  ok("tender scope: project-management consultancy is not physical road work",
     !P.tenderCoversCarriageway("Project Management Consultancy Services for construction and strengthening of roads in Mumbai"));
  ok("tender scope: DPR consultancy is not physical road work",
     !P.tenderCoversCarriageway("Consultancy services for preparation of detailed project report DPR for widening of NH 66"));
  ok("tender scope: authority engineer is not physical road work",
     !P.tenderCoversCarriageway("Appointment of Authority Engineer for supervision of rehabilitation and upgradation of NH 48"));
  ok("tender scope: survey and investigation is not physical road work",
     !P.tenderCoversCarriageway("Survey and investigation for construction of concrete road from Rampur to Sitapur"));
  ok("tender scope: third-party quality monitoring is not physical road work",
     !P.tenderCoversCarriageway("Third party quality monitoring of PMGSY road maintenance works"));
  ok("tender scope: commercial facility beside an NH is not road work",
     !P.tenderCoversCarriageway("Development of commercial facility at Auhar on NH 154 under PPP mode"));
  ok("tender scope: utility shifting for widening is not the widening work",
     !P.tenderCoversCarriageway("Utility shifting work as part of widening and strengthening of a route connecting NH123"));
  ok("tender scope: EPC road works are not mistaken for consultancy",
     P.tenderCoversCarriageway("Engineering procurement and construction for widening and strengthening of NH 48"));

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
  eq("Drive: accuracy-tested model is pinned", P.DRIVE_DETECTION_MODEL, "gpt-5.6");
  eq("Drive: accuracy-tested detail is pinned", P.DRIVE_DETECTION_DETAIL, "high");

  // ---- saved-video accounting: only completed model verdicts count ----
  eq("footage: every planned verdict completed is a truthful success",
     P.summarizeFootageAnalysis({planned:4, extracted:4, checked:4, failed:0,
       unreadableClips:0, aborted:false}),
     {planned:4, extracted:4, checked:4, failed:0, unreadableClips:0,
       aborted:false, skipped:0, complete:true, incompleteItems:0});
  eq("footage: extraction failure remains incomplete",
     P.summarizeFootageAnalysis({planned:4, extracted:3, checked:3, failed:1,
       unreadableClips:0, aborted:false}).complete, false);
  eq("footage: analyzed false cannot be hidden as checked",
     P.summarizeFootageAnalysis({planned:4, extracted:4, checked:3, failed:1,
       unreadableClips:0, aborted:false}).checked, 3);
  eq("footage: aborted windows are explicitly skipped",
     P.summarizeFootageAnalysis({planned:8, extracted:4, checked:3, failed:1,
       unreadableClips:0, aborted:true}).skipped, 4);
  eq("footage: one unreadable clip blocks completion",
     P.summarizeFootageAnalysis({planned:4, extracted:4, checked:4, failed:0,
       unreadableClips:1, aborted:false}).complete, false);
  const roundedBurst = (at, duration) =>
    P.vodBurstTimes(at, duration, 0.4).map((value) => +value.toFixed(1));
  const s1Samples = P.vodSampleTimes(59.99, 0.5)
    .filter((value) => value >= 34.9 && value <= 35.4)
    .map((value) => +value.toFixed(1));
  const s2Samples = P.vodSampleTimes(48.99, 0.5)
    .filter((value) => value >= 3.9 && value <= 4.4)
    .map((value) => +value.toFixed(1));
  eq("footage: segment 1 second 35 has two overlapping candidate windows",
     s1Samples, [34.9, 35.4]);
  eq("footage: segment 1 second 35 exact burst payloads",
     s1Samples.map((at) => roundedBurst(at, 59.99)),
     [[34.5,34.9,35.3],[35,35.4,35.8]]);
  eq("footage: segment 2 second 4 has two overlapping candidate windows",
     s2Samples, [3.9, 4.4]);
  eq("footage: segment 2 second 4 exact burst payloads",
     s2Samples.map((at) => roundedBurst(at, 48.99)),
     [[3.5,3.9,4.3],[4,4.4,4.8]]);

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

  // ---- contract truth: publication date never establishes award or DLP ----
  const contract = P.contractVerificationFor({tender_number:"T/1", title:"Road resurfacing",
    published:"20-02-2026"});
  ok("contract: carriageway scope is explicit", contract.scope_verified === true, contract);
  ok("contract: segment remains unverified", contract.segment_verified === false, contract);
  ok("contract: award remains unverified", contract.award_verified === false, contract);
  ok("contract: DLP remains unverified regardless of recent publication",
     contract.dlp_status === "unverified" && contract.dlp_verified === false, contract);

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
