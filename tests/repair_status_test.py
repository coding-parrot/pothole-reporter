# -*- coding: utf-8 -*-
"""A revisit only closes a physical pothole after strict before/after proof."""
import json
import pathlib

from playwright.sync_api import sync_playwright


APP = "http://localhost:8765/"

ACCEPTED = {
    "is_pothole": True,
    "looks_like_speed_breaker": False,
    "image_quality": "usable", "on_drivable_surface": True,
    "has_localized_cavity": True,
    "has_broken_edge_or_rim": True, "has_depth_or_surface_loss": True,
    "temporal_consistency": "consistent", "size": "medium",
    "description": "A broken cavity is visible on the travelled surface.",
}
ABSENT = {
    "is_pothole": False,
    "looks_like_speed_breaker": False,
    "image_quality": "usable", "on_drivable_surface": True,
    "has_localized_cavity": False,
    "has_broken_edge_or_rim": False, "has_depth_or_surface_loss": False,
    "temporal_consistency": "not_applicable", "size": None,
    "description": "No reportable damage is visible in the current burst.",
}
SPEED_BREAKER = {
    **ACCEPTED,
    "looks_like_speed_breaker": True,
    # Deliberately contradictory model fields reproduce the tester failure: the hard
    # veto must win even if another part of the model response calls it a pothole.
    "description": "A painted transverse raised ridge spans the lane.",
}
REPAIRED = {
    "same_location_visible": True, "completed_repair_visible": True,
    "current_condition": "repaired", "assessment": "clear",
    "image_quality": "usable",
    "description": "The same lane edge and curb align; intact asphalt covers the old cavity footprint.",
}
UNCERTAIN = {
    "same_location_visible": False, "completed_repair_visible": False,
    "current_condition": "not_visible", "assessment": "uncertain",
    "image_quality": "usable",
    "description": "The exact old footprint cannot be located in the current view.",
}

INIT = r"""
(fixtures) => {
  localStorage.setItem("openai_key", "test-key-never-sent");
  localStorage.removeItem("debug_mode");
  window.__assessments = [];
  window.__repairs = [];
  window.__modelCalls = [];
  window.__repairClock = Date.now();
  const realFetch = window.fetch.bind(window);
  const answer = (value, stream) => {
    const text = JSON.stringify(value);
    if (stream) {
      const event = JSON.stringify({ type: "response.output_text.delta", delta: text });
      return new Response(`data: ${event}\n\ndata: [DONE]\n\n`, {
        status: 200, headers: { "content-type": "text/event-stream" },
      });
    }
    return new Response(JSON.stringify({ output: [{
      type: "message", content: [{ type: "output_text", text }],
    }] }), { status: 200, headers: { "content-type": "application/json" } });
  };
  window.fetch = async (url, init = {}) => {
    const target = String(url);
    if (target.includes("api.openai.com/v1/models")) {
      return new Response('{"data":[]}', { status: 200, headers: { "content-type": "application/json" } });
    }
    if (target.includes("api.openai.com/v1/responses")) {
      const body = JSON.parse(init.body || "{}");
      const name = body.text && body.text.format && body.text.format.name;
      window.__modelCalls.push(name);
      const queue = name === "road_repair_verification" ? window.__repairs : window.__assessments;
      if (!queue.length) throw new Error(`No mocked ${name} response left`);
      return answer(queue.shift(), !!body.stream);
    }
    if (target.includes("nominatim.openstreetmap.org")) {
      return new Response(JSON.stringify({ display_name: "Test Road, Bengaluru, Karnataka, India",
        address: { road: "Test Road", city: "Bengaluru", state: "Karnataka", country: "India" } }),
        { status: 200, headers: { "content-type": "application/json" } });
    }
    if (target.includes("kgis.ksrsac.in")) {
      return new Response('{"features":[]}', { status: 200, headers: { "content-type": "application/json" } });
    }
    return realFetch(url, init);
  };
}
"""

HELPERS = r"""
async function repairJpeg() {
  const c = document.createElement("canvas"); c.width = 96; c.height = 72;
  const x = c.getContext("2d"); x.fillStyle = "#777"; x.fillRect(0, 0, 96, 72);
  x.fillStyle = "#222"; x.fillRect(30, 38, 30, 18);
  return await new Promise((resolve) => c.toBlob(resolve, "image/jpeg", 0.85));
}
async function repairDataUrl() {
  const blob = await repairJpeg();
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}
async function tinyDataUrl() {
  const c = document.createElement("canvas"); c.width = 1; c.height = 1;
  return c.toDataURL("image/png");
}
async function imageDimensions(value) {
  const blob = value instanceof Blob ? value : await (await fetch(value)).blob();
  const bitmap = await createImageBitmap(blob);
  const out = [bitmap.width, bitmap.height]; bitmap.close(); return out;
}
async function repairSubmit(drive, key, lat, verdict, comparison) {
  window.__assessments.push(verdict);
  if (comparison) window.__repairs.push(comparison);
  const fd = new FormData();
  fd.append("photo", await repairJpeg(), "road-before.jpg");
  fd.append("photo", await repairJpeg(), "road-primary.jpg");
  fd.append("photo", await repairJpeg(), "road-after.jpg");
  fd.append("primary_index", "1");
  fd.append("lat", String(lat)); fd.append("lng", "77.642700");
  fd.append("drive_id", drive); fd.append("capture_source", "drive_live");
  fd.append("source_event_key", key); fd.append("gps_accuracy", "4");
  fd.append("speed", "8"); fd.append("heading", "90");
  fd.append("captured_at_ms", String(++window.__repairClock));
  return StandaloneAPI.handle("/api/frame", { method: "POST", body: fd });
}
"""


failures = []
remote_leaks = []
with sync_playwright() as p:
    browser = p.chromium.launch(args=["--disable-web-security", "--allow-running-insecure-content"])
    context = browser.new_context(viewport={"width": 390, "height": 844})

    def route_request(route):
        if route.request.url.startswith(APP):
            route.continue_()
        else:
            remote_leaks.append(route.request.url)
            route.abort()

    context.route("**/*", route_request)
    context.add_init_script(f"({INIT})({json.dumps({'accepted': ACCEPTED})});")
    page = context.new_page()
    page.goto(APP)
    page.wait_for_function("typeof StandaloneAPI !== 'undefined'", timeout=30000)
    result = page.evaluate("""async (f) => {
      eval(f.helpers);
      await StandaloneAPI.handle("/api/reports", { method: "DELETE" });

      const first = await repairSubmit("drive-1", "live:drive-1:1", 12.911500, f.accepted, null);
      const before = await StandaloneAPI.handle("/api/reports", { method: "GET" });
      const complaintStatus = before[0].status;
      const repairTargets = await StandaloneAPI.handle("/api/repair-targets", {method:"GET"});

      const nativeBase = {
        target_report_id: before[0].id, drive_id: "native-revisit", lat: 12.911500,
        lng: 77.642700, gps_accuracy: 4, speed_mps: 8, heading: 90,
        current_condition: "repaired", assessment: "clear", image_quality: "usable",
        same_location_visible: true, completed_repair_visible: true,
        description: "The same footprint is covered by intact asphalt.",
        detection_model: "gpt-5-mini", image_detail: "high",
        prompt_version: "road-repair-v1", schema_version: 1,
      };
      const validEvidence = await repairDataUrl();
      const missingTimestamp = await StandaloneAPI.handle("/api/native-repair", {
        method: "POST", body: JSON.stringify({...nativeBase,
          source_event_key: "native:missing-time", current_photo_data_url: validEvidence}),
      });
      const earlierTimestamp = await StandaloneAPI.handle("/api/native-repair", {
        method: "POST", body: JSON.stringify({...nativeBase,
          source_event_key: "native:earlier-time", observed_at: before[0].last_seen_at - 1,
          current_photo_data_url: validEvidence}),
      });
      const malformedEvidence = await StandaloneAPI.handle("/api/native-repair", {
        method: "POST", body: JSON.stringify({...nativeBase,
          source_event_key: "native:bad-image", observed_at: before[0].last_seen_at + 1,
          current_photo_data_url: "not-an-image"}),
      });
      const tinyEvidence = await StandaloneAPI.handle("/api/native-repair", {
        method: "POST", body: JSON.stringify({...nativeBase,
          source_event_key: "native:tiny-image", observed_at: before[0].last_seen_at + 1,
          current_photo_data_url: await tinyDataUrl()}),
      });
      const missingProvenance = await StandaloneAPI.handle("/api/native-repair", {
        method: "POST", body: JSON.stringify({...nativeBase,
          source_event_key: "native:no-prompt", observed_at: before[0].last_seen_at + 1,
          current_photo_data_url: validEvidence, prompt_version: undefined}),
      });
      const revisit = await repairSubmit("drive-2", "live:drive-2:1", 12.911500, f.absent, f.repaired);
      const after = await StandaloneAPI.handle("/api/reports", { method: "GET" });
      const repairDimensions = await imageDimensions(after[0].repair_photo_url);
      const replay = await StandaloneAPI.handle("/api/native-repair", {
        method: "POST", body: JSON.stringify({...nativeBase, drive_id:"drive-2",
          source_event_key:"live:drive-2:1", observed_at:after[0].repair_observed_at,
          current_photo_data_url:validEvidence}),
      });
      const fixedGates = {};
      const attempts = {
        send: ["/send", {method:"POST"}],
        evidence: ["/evidence", {method:"GET"}],
        handoff: ["/handoff", {method:"GET"}],
        submitted: ["/submitted", {method:"POST", body:"{}"}],
        handoffOpened: ["/handoff-opened", {method:"POST"}],
        patch: ["", {method:"PATCH", body:JSON.stringify({email_subject:"stale", email_body:"stale"})}],
      };
      for (const [name, [suffix, options]] of Object.entries(attempts)) {
        try { await StandaloneAPI.handle(`/api/reports/${after[0].id}${suffix}`, options); fixedGates[name] = false; }
        catch (e) { fixedGates[name] = /verified fixed|fixed reports/i.test(e.message); }
      }

      // A later genuine cavity at the same point is a recurrence, not a duplicate of
      // the historical fixed event.
      const recurrence = await repairSubmit("drive-3", "live:drive-3:1", 12.911500, f.accepted, null);
      const withRecurrence = await StandaloneAPI.handle("/api/reports", { method: "GET" });

      // Generic absence plus an inconclusive comparison must leave another report open.
      const second = await repairSubmit("drive-4", "live:drive-4:1", 12.913000, f.accepted, null);
      const uncertain = await repairSubmit("drive-5", "live:drive-5:1", 12.913000, f.absent, f.uncertain);
      const beforeBreaker = (await StandaloneAPI.handle("/api/reports", { method: "GET" })).length;
      const breaker = await repairSubmit(
        "drive-breaker", "live:drive-breaker:1", 12.915000, f.speedBreaker, null
      );
      const finalReports = await StandaloneAPI.handle("/api/reports", { method: "GET" });
      const secondStored = finalReports.find((r) => r.id === (second.report && second.report.id));
      return { first, complaintStatus, repairTargets, missingTimestamp, earlierTimestamp,
        malformedEvidence, tinyEvidence, missingProvenance, revisit, replay,
        after: after[0], repairDimensions, fixedGates,
        recurrence, withRecurrence, uncertain, secondStored, breaker, beforeBreaker,
        afterBreaker: finalReports.length, calls: window.__modelCalls };
    }""", {
        "helpers": HELPERS, "accepted": ACCEPTED, "absent": ABSENT,
        "repaired": REPAIRED, "uncertain": UNCERTAIN, "speedBreaker": SPEED_BREAKER,
    })
    browser.close()

if not result["first"].get("found"):
    failures.append("initial live pothole was not stored")
if not result["revisit"].get("repaired") or result["after"].get("condition_status") != "fixed":
    failures.append("clear same-place completed repair was not marked fixed")
if result["after"].get("status") != result["complaintStatus"]:
    failures.append("physical repair overwrote complaint/submission status")
if not all(result["fixedGates"].values()):
    failures.append(f"fixed report actions were not all gated: {result['fixedGates']}")
if result["missingTimestamp"].get("reason") != "target_ambiguous_or_mismatched":
    failures.append(f"missing repair timestamp did not fail closed: {result['missingTimestamp']}")
if result["earlierTimestamp"].get("reason") != "target_ambiguous_or_mismatched":
    failures.append(f"earlier repair timestamp did not fail closed: {result['earlierTimestamp']}")
if result["malformedEvidence"].get("reason") != "repair_evidence_invalid":
    failures.append(f"malformed repair evidence was not rejected: {result['malformedEvidence']}")
if result["tinyEvidence"].get("reason") != "repair_evidence_invalid":
    failures.append(f"meaninglessly small repair evidence was not rejected: {result['tinyEvidence']}")
if result["missingProvenance"].get("reason") != "repair_provenance_invalid":
    failures.append(f"missing repair provenance was not rejected: {result['missingProvenance']}")
targets = result["repairTargets"].get("targets") or [{}]
target = targets[0]
if target.get("last_damage_observed_at") != result["after"].get("last_seen_at") or "damage_observed_at" in target:
    failures.append(f"native repair target timestamp contract is not canonical: {target}")
if result["repairDimensions"] != [96, 72]:
    failures.append(f"repair evidence was not the full context frame: {result['repairDimensions']}")
if not result["replay"].get("duplicate") or result["replay"].get("condition_status") != "fixed":
    failures.append(f"committed native repair retry was not idempotent: {result['replay']}")
if not result["recurrence"].get("found") or len(result["withRecurrence"]) != 2:
    failures.append("new damage after repair was suppressed as an old duplicate")
if result["uncertain"].get("repaired") or result["secondStored"].get("condition_status") != "open":
    failures.append("inconclusive revisit incorrectly marked a pothole fixed")
if result["breaker"].get("found") or result["breaker"].get("stored") \
        or result["afterBreaker"] != result["beforeBreaker"]:
    failures.append(f"speed breaker was persisted as damage: {result['breaker']}")
if result["calls"].count("road_repair_verification") != 2:
    failures.append(f"expected two separate before/after checks, got {result['calls']}")
if remote_leaks:
    failures.append(f"unexpected real network requests: {remote_leaks[:3]}")

if failures:
    raise SystemExit("repair status test failed:\n- " + "\n- ".join(failures))
print("repair status test passed")
