# -*- coding: utf-8 -*-
"""A pothole seen twice is two reports, and the earlier one still gains the sighting.

Repeat-detection dedupe was removed: the app no longer merges a second sighting into an
existing report, and no longer refuses to draft a complaint because the shared map has
seen that location before. Two things must survive that removal, and this suite pins both.

The identical observation arriving twice, a retry or a replayed frame, must stay
idempotent, or a flaky connection would multiply one pothole into many complaints.
And a revisit must still be recorded against the earlier report, because that accumulated
evidence is what decides whether a pothole was repaired or has come back.

No external service is contacted.
"""
import os
import json
import pathlib
import sys

from playwright.sync_api import sync_playwright


ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")

ACCEPTED = {
    "image_quality": "acceptable",
    "assessment": "damaged",
    "damage_type": "pothole_cavity",
    "size": "medium",
    "description": "A cavity with a broken rim is visible on the travelled surface.",
}

INIT = r"""
(accepted) => {
  try {
    localStorage.setItem("vision_provider", "personal");
    localStorage.setItem("openai_key", "test-key-never-sent");
    localStorage.removeItem("debug_mode");
  } catch (e) {}

  const realFetch = window.fetch.bind(window);
  window.__detectorCalls = 0;
  window.__detectionBarrier = null;
  window.__reverseDetectionPair = null;
  window.__armDetectionBarrier = (count) => {
    window.__detectionBarrier = { remaining: count, releases: [] };
  };
  window.__armReverseDetectionPair = () => {
    window.__reverseDetectionPair = { calls: 0, releaseFirst: null };
  };

  const responseFor = (payload, stream) => {
    const text = JSON.stringify(payload);
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
    if (target.startsWith("https://ffjvg34k07.execute-api.ap-south-1.amazonaws.com/")) {
      const path = new URL(target).pathname;
      const body = init.body ? JSON.parse(init.body) : {};
      const headers = {"content-type":"application/json", "x-request-id":"test-central"};
      if (path === "/v1/installations") return new Response(JSON.stringify({
        request_id:"test-install", install_id:"test-installation"
      }), {status:201, headers});
      if (path === "/v1/activity") return new Response(JSON.stringify({
        request_id:"test-activity", accepted:true, event:"vision_check"
      }), {status:202, headers});
      if (path === "/v1/tenders/resolve") return new Response(JSON.stringify({
        request_id:"test-tender", jurisdiction:{lat:body.lat,lng:body.lng,address:null,
          lgd:null,town:null,source:"unresolved",address_source:"unresolved"},
        tender:null, reason:"test_no_match"
      }), {status:200, headers});
      if (path === "/v1/potholes/report") return new Response(JSON.stringify({
        request_id:"test-report", duplicate:false, dedupe:null,
        pothole:{id:9001,lat:body.lat,lng:body.lng,damage_type:body.damage_type,size:body.size,
          first_seen_at:body.observed_at,last_seen_at:body.observed_at,
          seen_count:1,lgd:null,town:null}
      }), {status:201, headers});
      throw new Error(`Unexpected central request: ${path}`);
    }
    if (target.includes("api.openai.com/v1/models")) {
      return new Response('{"data":[]}', {
        status: 200, headers: { "content-type": "application/json" },
      });
    }
    if (target.includes("api.openai.com/v1/responses")) {
      const body = JSON.parse(init.body || "{}");
      const name = body.text && body.text.format && body.text.format.name;
      if (name !== "road_damage_assessment") {
        throw new Error(`Unexpected model call: ${name || "unnamed"}`);
      }
      window.__detectorCalls++;
      const make = () => responseFor(accepted, !!body.stream);
      const reverse = window.__reverseDetectionPair;
      if (reverse) {
        reverse.calls++;
        if (reverse.calls === 1) {
          return new Promise((resolve) => { reverse.releaseFirst = () => resolve(make()); });
        }
        if (reverse.calls === 2) {
          // Let the later frame finish detection and reach the persistence gate first.
          setTimeout(() => {
            const release = reverse.releaseFirst;
            window.__reverseDetectionPair = null;
            if (release) release();
          }, 75);
          return make();
        }
      }
      const barrier = window.__detectionBarrier;
      if (barrier && barrier.remaining > 0) {
        barrier.remaining--;
        const waiting = new Promise((resolve) => barrier.releases.push(() => resolve(make())));
        if (barrier.remaining === 0) {
          const releases = barrier.releases.splice(0);
          window.__detectionBarrier = null;
          queueMicrotask(() => releases.forEach((release) => release()));
        }
        return waiting;
      }
      return make();
    }
    if (target.includes("nominatim.openstreetmap.org")) {
      return new Response(JSON.stringify({
        display_name: "Test Road, Karnataka, India",
        address: { road: "Test Road", city: "Test City", postcode: "560001" },
      }), { status: 200, headers: { "content-type": "application/json" } });
    }
    if (target.includes("kgis.ksrsac.in")) {
      // A valid empty state-GIS answer leaves the accepted detection unrouted.  That is
      // enough for this test and avoids contract matching or a second model request.
      return new Response('{"features":[]}', {
        status: 200, headers: { "content-type": "application/json" },
      });
    }
    return realFetch(url, init);
  };
}
"""

HELPERS = r"""
async function jpeg() {
  const canvas = document.createElement("canvas");
  canvas.width = 64; canvas.height = 64;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#777"; ctx.fillRect(0, 0, 64, 64);
  ctx.fillStyle = "#222"; ctx.fillRect(18, 28, 28, 16);
  return await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
}
async function requestBody(path, opts = {}) {
  const fd = new FormData();
  if (path === "/api/frame") {
    fd.append("photo", await jpeg(), "event-before.jpg");
    fd.append("photo", await jpeg(), "event-primary.jpg");
    fd.append("photo", await jpeg(), "event-after.jpg");
    fd.append("primary_index", "1");
  } else {
    fd.append("photo", await jpeg(), "event.jpg");
  }
  if (opts.lat != null) fd.append("lat", String(opts.lat));
  if (opts.lng != null) fd.append("lng", String(opts.lng));
  if (path === "/api/frame") {
    if (!opts.driveId || !opts.sourceKey) throw new Error("Drive test input lacks stable identity");
    fd.append("drive_id", String(opts.driveId));
    fd.append("capture_source", opts.captureSource || "drive_live");
    fd.append("source_event_key", opts.sourceKey);
  }
  if (opts.capturedAt != null) fd.append("captured_at_ms", String(opts.capturedAt));
  if (opts.sourceOffset != null) fd.append("source_offset_ms", String(opts.sourceOffset));
  fd.append("gps_accuracy", String(opts.gpsAccuracy == null ? 5 : opts.gpsAccuracy));
  fd.append("speed", String(opts.speed == null ? 8 : opts.speed));
  fd.append("heading", String(opts.heading == null ? 90 : opts.heading));
  return fd;
}
async function submit(path, opts = {}) {
  return StandaloneAPI.handle(path, { method: "POST", body: await requestBody(path, opts) });
}
"""

def shape(result):
    """Keep a failing log readable; report photos would otherwise dominate it."""
    report = result.get("report") or {}
    return {
        "stored": result.get("stored"), "duplicate": result.get("duplicate"),
        "id": result.get("id") or report.get("id"),
        "status": result.get("status") or report.get("status"),
        "seen_count": report.get("seen_count"),
    }


failures = []
remote_leaks = []

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--disable-web-security", "--allow-running-insecure-content"])
    context = browser.new_context(viewport={"width": 390, "height": 844})

    def block_real_remote(route):
        if route.request.url.startswith(APP):
            route.continue_()
        else:
            remote_leaks.append(route.request.url)
            route.abort()

    context.route("**/*", block_real_remote)
    context.add_init_script(f"({INIT})({json.dumps(ACCEPTED)});")
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function("typeof StandaloneAPI !== 'undefined'", timeout=30000)

    result = page.evaluate("""async () => {
      %s
      await StandaloneAPI.handle("/api/reports", { method: "DELETE" });

      // Two frames of the same pothole, one second apart in the same drive. Close enough
      // that the old build merged them into a single report.
      const at = 1805000000000;
      const first = await submit("/api/frame", {
        lat: 12.900000, lng: 77.600000, driveId: "repeat-1",
        sourceKey: "live:repeat-1:1", capturedAt: at, sourceOffset: 1000,
      });
      const second = await submit("/api/frame", {
        lat: 12.900000, lng: 77.600000, driveId: "repeat-1",
        sourceKey: "live:repeat-1:2", capturedAt: at + 1000, sourceOffset: 2000,
      });

      // The identical observation again: same source key, same everything. A retry.
      const replay = await submit("/api/frame", {
        lat: 12.900000, lng: 77.600000, driveId: "repeat-1",
        sourceKey: "live:repeat-1:2", capturedAt: at + 1000, sourceOffset: 2000,
      });

      const reports = await StandaloneAPI.handle("/api/reports", { method: "GET" });
      const ids = reports.map((r) => r.id);
      const priorSeenCount = (reports.find((r) => r.id === Math.min(...ids)) || {}).seen_count;
      // Whether an email can actually be composed depends on routing and on the host,
      // neither of which this suite models. What matters is that nothing is refused for
      // being a repeat, so the refusal reasons are collected rather than the successes.
      const sendErrors = [];
      for (const report of reports) {
        try {
          await StandaloneAPI.handle(`/api/reports/${report.id}/send`, { method: "POST" });
        } catch (error) { sendErrors.push(String(error && error.message || error)); }
      }
      return { first, second, replay, count: reports.length, ids, priorSeenCount,
               sendErrors, statuses: reports.map((r) => r.status) };
    }""" % HELPERS)

    browser.close()

if result["count"] != 2:
    failures.append(f"a pothole seen twice should be two reports, got {result['count']}: "
                    f"{result['ids']}")
if result["second"].get("duplicate"):
    failures.append(f"the second sighting was suppressed as a duplicate: "
                    f"{shape(result['second'])}")
if not result["second"].get("stored"):
    failures.append(f"the second sighting was not stored: {shape(result['second'])}")
if not result["replay"].get("duplicate"):
    failures.append(f"an identical replayed observation created another report rather "
                    f"than staying idempotent: {shape(result['replay'])}")
# The earlier report carries the revisit as evidence. Repair verification reads this to
# decide whether a pothole is still there.
if not result["priorSeenCount"] or result["priorSeenCount"] < 2:
    failures.append(f"the earlier report did not record the repeat sighting: "
                    f"seen_count={result['priorSeenCount']}")
repeat_refusals = [message for message in result["sendErrors"]
                   if "already reported" in message.lower() or "duplicate" in message.lower()]
if repeat_refusals:
    failures.append(f"a complaint was refused for being a repeat: {repeat_refusals}")
if "duplicate" in result["statuses"]:
    failures.append(f"a report was parked in the duplicate state: {result['statuses']}")
if remote_leaks:
    failures.append(f"contacted a remote host: {sorted(set(remote_leaks))[:3]}")

if failures:
    print("REPEAT DETECTION TEST FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("REPEAT DETECTION TEST PASS")
