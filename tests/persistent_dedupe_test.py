# -*- coding: utf-8 -*-
"""Accepted road events are deduplicated after inference and survive reloads.

No external service is contacted.  The first pair of model responses is held behind a
barrier so both adjacent frames finish detection together; a read-then-write dedupe
implementation will therefore lose this test deterministically.
"""
import json
import pathlib
import sys

from playwright.sync_api import sync_playwright


ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = "http://localhost:8765/"

ACCEPTED = {
    "reportable": True,
    "assessment": "clear",
    "image_quality": "usable",
    "damage_type": "pothole_cavity",
    "on_drivable_surface": True,
    "has_broken_edge_or_rim": True,
    "has_depth_or_surface_loss": True,
    "temporal_consistency": "consistent",
    "size": "medium",
    "description": "A cavity with a broken rim is visible on the travelled surface.",
}

INIT = r"""
(accepted) => {
  try {
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
    if (target.endsWith("karnataka-bodies.json")) {
      return new Response('{"bodies":{"test":{"name":"Test body"}}}', {
        status: 200, headers: { "content-type": "application/json" },
      });
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
  fd.append("photo", await jpeg(), "event.jpg");
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


def one_found_one_duplicate(results):
    found = [r for r in results if r.get("found") is True]
    duplicates = [r for r in results if r.get("duplicate") is True]
    return len(found) == 1 and len(duplicates) == 1, found, duplicates


def shape(result):
    """Keep a failing suite log readable; report photos can otherwise dominate it."""
    report = result.get("report") or {}
    return {
        "analyzed": result.get("analyzed"), "accepted": result.get("accepted"),
        "stored": result.get("stored"), "found": result.get("found"),
        "duplicate": result.get("duplicate"),
        "id": result.get("id") or report.get("id"),
        "duplicate_of": result.get("duplicate_of"),
        "skipped": result.get("skipped"), "status": result.get("status") or report.get("status"),
    }


def expect_drive_result(failures, label, result, *, stored, duplicate):
    expected = {
        "analyzed": True, "accepted": True, "stored": stored,
        "found": stored, "duplicate": duplicate,
    }
    wrong = {key: (result.get(key), value) for key, value in expected.items()
             if result.get(key) is not value}
    observation = result.get("observation") or {}
    if observation.get("damage_type") != "pothole_cavity":
        wrong["observation.damage_type"] = (observation.get("damage_type"), "pothole_cavity")
    if wrong:
        failures.append(f"{label} response contract mismatch {wrong}: {shape(result)}")


failures = []
remote_leaks = []

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--disable-web-security", "--allow-running-insecure-content"])
    context = browser.new_context(viewport={"width": 390, "height": 844})

    def block_real_remote(route):
        url = route.request.url
        if url.startswith(APP):
            route.continue_()
        else:
            remote_leaks.append(url)
            route.abort()

    context.route("**/*", block_real_remote)
    context.add_init_script(f"({INIT})({json.dumps(ACCEPTED)});")
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function("typeof StandaloneAPI !== 'undefined'", timeout=30000)

    first = page.evaluate("""async () => {
      %s
      await StandaloneAPI.handle("/api/reports", { method: "DELETE" });

      // Model responses can finish in the opposite order to capture. A is an older
      // canonical; B1 bridges the revisit to A, while B2 is only close enough to B1.
      // B1 is submitted first but its mocked detector response is deliberately released
      // after B2. Persistence must still follow capture order and keep only A.
      const reverseAt = 1805000000000;
      const reverseA = await submit("/api/frame", {
        lat: 12.880000, lng: 77.640000, driveId: "order-a",
        sourceKey: "live:order-a:1", capturedAt: reverseAt,
        sourceOffset: 1000,
      });
      const b1Body = await requestBody("/api/frame", {
        lat: 12.879960, lng: 77.640000, driveId: "order-b",
        sourceKey: "live:order-b:1", capturedAt: reverseAt + 10 * 86400000,
        sourceOffset: 1000,
      });
      const b2Body = await requestBody("/api/frame", {
        lat: 12.879865, lng: 77.640000, driveId: "order-b",
        sourceKey: "live:order-b:2", capturedAt: reverseAt + 10 * 86400000 + 3000,
        sourceOffset: 4000,
      });
      window.__armReverseDetectionPair();
      const b1Promise = StandaloneAPI.handle("/api/frame", { method: "POST", body: b1Body });
      const b2Promise = StandaloneAPI.handle("/api/frame", { method: "POST", body: b2Body });
      const reversePair = await Promise.all([b1Promise, b2Promise]);
      const reverseReports = await StandaloneAPI.handle("/api/reports");
      const reverseDetectorCalls = window.__detectorCalls;

      await StandaloneAPI.handle("/api/reports", { method: "DELETE" });
      window.__detectorCalls = 0;

      // The middle observation finishes first. Each outer observation is within the
      // single-sighting threshold of the middle, but the outers are ~24 m and 8 s apart.
      // A pairwise-only matcher transitively collapsed all three into one event. A
      // canonical event must instead keep an all-pairs envelope, so one outer starts a
      // second canonical regardless of which outer transaction commits first.
      const spanAt = 1810000000000;
      const spanMiddle = await submit("/api/frame", {
        lat: 12.950000, lng: 77.700000, driveId: "span-drive",
        sourceKey: "live:span-drive:middle", capturedAt: spanAt + 4000,
        sourceOffset: 4000,
      });
      const spanOuter = await Promise.all([
        submit("/api/frame", {
          lat: 12.950000, lng: 77.699890, driveId: "span-drive",
          sourceKey: "live:span-drive:west", capturedAt: spanAt,
          sourceOffset: 0,
        }),
        submit("/api/frame", {
          lat: 12.950000, lng: 77.700110, driveId: "span-drive",
          sourceKey: "live:span-drive:east", capturedAt: spanAt + 8000,
          sourceOffset: 8000,
        }),
      ]);
      const spanReports = await StandaloneAPI.handle("/api/reports");

      // Keep the existing persistence/race expectations independent of this regression.
      await StandaloneAPI.handle("/api/reports", { method: "DELETE" });
      window.__detectorCalls = 0;
      window.__armDetectionBarrier(2);
      const at = 1800000000000;
      const adjacent = await Promise.all([
        submit("/api/frame", { lat: 12.911500, lng: 77.642700,
          driveId: "live-a", sourceKey: "live:live-a:1",
          capturedAt: at, sourceOffset: 0 }),
        submit("/api/frame", { lat: 12.911500, lng: 77.642740,
          driveId: "live-a", sourceKey: "live:live-a:2",
          capturedAt: at + 700, sourceOffset: 700 }),
      ]);
      const storedAfterRace = await StandaloneAPI.handle("/api/reports");
      // Roughly 8 m from either race input but seven seconds later. It is a separate
      // same-drive event: proximity without temporal continuity must not swallow it.
      const laterSameDrive = await submit("/api/frame", {
        lat: 12.911570, lng: 77.642720, driveId: "live-a",
        sourceKey: "live:live-a:3", capturedAt: at + 7000, sourceOffset: 7000,
      });
      const storedAfterLater = await StandaloneAPI.handle("/api/reports");
      return { reverseA, reversePair, reverseReports, reverseDetectorCalls,
               spanMiddle, spanOuter, spanReports,
               adjacent, storedAfterRace, laterSameDrive, storedAfterLater,
               detectorCalls: window.__detectorCalls };
    }""" % HELPERS)

    expect_drive_result(failures, "reverse-order canonical", first["reverseA"],
                        stored=True, duplicate=False)
    for i, result in enumerate(first["reversePair"], 1):
        expect_drive_result(failures, f"reverse-order revisit B{i}", result,
                            stored=False, duplicate=True)
        if result.get("duplicate_of") != first["reverseA"].get("report", {}).get("id"):
            failures.append(f"reverse-order B{i} did not resolve to A: {shape(result)}")
    if len(first["reverseReports"]) != 1:
        failures.append(
            "reverse model completion created a provisional second canonical: "
            + str([shape(r) for r in first["reversePair"]])
        )
    if first["reverseDetectorCalls"] != 3:
        failures.append(
            f"reverse-order case expected 3 detector calls, got {first['reverseDetectorCalls']}"
        )

    expect_drive_result(failures, "span middle", first["spanMiddle"],
                        stored=True, duplicate=False)
    span_outer_found = [r for r in first["spanOuter"] if r.get("found") is True]
    span_outer_duplicates = [r for r in first["spanOuter"] if r.get("duplicate") is True]
    if len(first["spanReports"]) < 2:
        failures.append(
            "all-pairs envelope collapsed a ~24 m / 8 s three-sighting chain into one canonical: "
            + str([shape(first["spanMiddle"]), *[shape(r) for r in first["spanOuter"]]])
        )
    if len(span_outer_found) != 1 or len(span_outer_duplicates) != 1:
        failures.append(
            "middle-first span expected one outer duplicate and one new canonical: "
            + str([shape(r) for r in first["spanOuter"]])
        )

    ok, found, duplicates = one_found_one_duplicate(first["adjacent"])
    if not ok:
        failures.append("concurrent adjacent frames were not exactly one find and one duplicate: "
                        + str([shape(r) for r in first["adjacent"]]))
    if len(first["storedAfterRace"]) != 1:
        failures.append(f"concurrent commit stored {len(first['storedAfterRace'])} reports, expected 1")
    if duplicates:
        duplicate = duplicates[0]
        expect_drive_result(failures, "concurrent duplicate", duplicate,
                            stored=False, duplicate=True)
        if duplicate.get("skipped") != "already reported nearby":
            failures.append(f"Drive duplicate has the wrong skip reason: {shape(duplicate)}")
        expected_id = found[0].get("report", {}).get("id") if found else None
        if duplicate.get("duplicate_of") != expected_id:
            failures.append(f"Drive duplicate does not identify the stored report: {shape(duplicate)}")
    if found:
        expect_drive_result(failures, "concurrent winner", found[0], stored=True, duplicate=False)
    if first["detectorCalls"] != 3:
        failures.append(f"post-detection contract violated: expected 3 detector calls, got {first['detectorCalls']}")
    expect_drive_result(failures, "later same-drive event", first["laterSameDrive"],
                        stored=True, duplicate=False)
    if len(first["storedAfterLater"]) != 2:
        failures.append("same-drive event >4 s away did not create a second report")

    # Recreate the document, not merely the API call: dedupe must come from IndexedDB,
    # not an in-memory set that disappears on navigation or app relaunch.
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_function("typeof StandaloneAPI !== 'undefined'", timeout=30000)
    second = page.evaluate("""async () => {
      %s
      window.__detectorCalls = 0;
      const at = 1800000000000;
      // A busy canonical may already contain many sightings from other drives. Seed 64
      // of them to prove the cap is per drive: B1 below must still be retained for B2.
      const baseline = await StandaloneAPI.handle("/api/reports");
      const canonical = baseline.slice().sort((a, b) => a.lat - b.lat)[0];
      await new Promise((resolve, reject) => {
        const req = indexedDB.open("potholes", 5);
        req.onerror = () => reject(req.error);
        req.onsuccess = () => {
          const db = req.result;
          const tx = db.transaction("reports", "readwrite");
          const store = tx.objectStore("reports");
          const get = store.get(canonical.id);
          get.onerror = () => reject(get.error);
          get.onsuccess = () => {
            const rec = get.result;
            rec.event_sightings = Array.from({length: 64}, (_, i) => ({
              drive_id: `old-revisit-${i}`,
              lat: rec.lat, lng: rec.lng, source_offset_s: i,
              captured_at: at + 9 * 86400000 + i,
              gps_accuracy: 5, speed_mps: 8, heading: 90,
              source_event_key: `live:old-revisit-${i}:1`,
            }));
            rec.sighting_drive_ids = rec.event_sightings.map((x) => x.drive_id);
            store.put(rec);
          };
          tx.oncomplete = () => { db.close(); resolve(); };
          tx.onabort = () => reject(tx.error || new Error("seed transaction aborted"));
          tx.onerror = () => {};
        };
      });
      // A different drive ten days later, precise GPS and a compatible heading. This is
      // within the 8 m historical radius of the persisted race winner.
      const crossNear = await submit("/api/frame", {
        lat: 12.911465, lng: 77.642720, driveId: "history-near",
        sourceKey: "live:history-near:1", capturedAt: at + 10 * 86400000,
        sourceOffset: 1000,
      });
      const afterCrossNear = await StandaloneAPI.handle("/api/reports");

      // A is the persisted live-a canonical and B1 above matched it by prior-drive GPS.
      // B2 is within 12 m / 3 s of B1 but deliberately beyond the 8 m latitude prefilter
      // and prior-drive radius of either possible winner from live-a. The canonical must
      // retain B1 as a drive-specific sighting, and candidate lookup must not hide that
      // canonical merely because its original A coordinate is outside the history band.
      const crossAdjacent = await submit("/api/frame", {
        lat: 12.911370, lng: 77.642720, driveId: "history-near",
        sourceKey: "live:history-near:2", capturedAt: at + 10 * 86400000 + 3000,
        sourceOffset: 4000,
      });
      const afterCrossAdjacent = await StandaloneAPI.handle("/api/reports");
      // If the regression fires, remove only its accidental report so later independent
      // count assertions do not cascade into noise. The failing response/store snapshot
      // above remains available to the Python assertions.
      if (crossAdjacent && crossAdjacent.stored && crossAdjacent.report) {
        await StandaloneAPI.handle(`/api/reports/${crossAdjacent.report.id}`, { method: "DELETE" });
      }

      // Roughly 9.5 m south of the race event and farther from the later same-drive
      // event: outside the conservative 8 m prior-drive radius.
      const crossFar = await submit("/api/frame", {
        lat: 12.911415, lng: 77.642700, driveId: "history-far",
        sourceKey: "live:history-far:1", capturedAt: at + 10 * 86400000 + 1000,
        sourceOffset: 1000,
      });
      const afterCrossFar = await StandaloneAPI.handle("/api/reports");

      // A VOD re-run gets an exact stable key. The second pass may happen much later;
      // source identity, not approximate process time, makes it the same observation.
      const vodFirst = await submit("/api/frame", {
        driveId: "vod-rerun", captureSource: "drive_vod",
        sourceKey: "vod:vod-rerun:0:200", capturedAt: at + 12 * 86400000,
        sourceOffset: 200,
      });
      const vodReplay = await submit("/api/frame", {
        driveId: "vod-rerun", captureSource: "drive_vod",
        sourceKey: "vod:vod-rerun:0:200", capturedAt: at + 20 * 86400000,
        sourceOffset: 600000,
      });
      const afterVodReplay = await StandaloneAPI.handle("/api/reports");

      const noGpsA = await submit("/api/frame", {
        driveId: "no-gps-vod", captureSource: "drive_vod",
        sourceKey: "vod:no-gps-vod:0:10000",
        capturedAt: at + 21 * 86400000 + 10000, sourceOffset: 10000,
      });
      const noGpsB = await submit("/api/frame", {
        driveId: "no-gps-vod", captureSource: "drive_vod",
        sourceKey: "vod:no-gps-vod:0:11000",
        capturedAt: at + 21 * 86400000 + 11000, sourceOffset: 11000,
      });
      const afterNoGps = await StandaloneAPI.handle("/api/reports");

      // Debug evidence is useful for evaluation but must never become a suppressor for
      // a later normal observation at the same place.
      localStorage.setItem("debug_mode", "1");
      const debug = await submit("/api/frame", {
        lat: 12.950000, lng: 77.700000, driveId: "debug-drive",
        sourceKey: "live:debug-drive:1", capturedAt: at + 22 * 86400000,
        sourceOffset: 1000,
      });
      localStorage.removeItem("debug_mode");
      const afterDebug = await StandaloneAPI.handle("/api/reports");
      const normalAfterDebug = await submit("/api/frame", {
        lat: 12.950000, lng: 77.700000, driveId: "normal-after-debug",
        sourceKey: "live:normal-after-debug:1", capturedAt: at + 22 * 86400000 + 1000,
        sourceOffset: 1000,
      });
      const afterNormal = await StandaloneAPI.handle("/api/reports");

      // Manual capture is an explicit report. Approximate GPS proximity never silently
      // turns it into an automatic duplicate.
      const manual = await submit("/api/report", {
        lat: 12.911500, lng: 77.642710, capturedAt: at + 23 * 86400000,
        sourceOffset: 1000,
      });
      const finalReports = await StandaloneAPI.handle("/api/reports");
      return { crossNear, afterCrossNear, crossAdjacent, afterCrossAdjacent,
               crossFar, afterCrossFar,
               vodFirst, vodReplay, afterVodReplay, noGpsA, noGpsB, afterNoGps,
               debug, afterDebug, normalAfterDebug, afterNormal, manual, finalReports,
               detectorCalls: window.__detectorCalls };
    }""" % HELPERS)

    expect_drive_result(failures, "cross-drive <=8 m", second["crossNear"],
                        stored=False, duplicate=True)
    if len(second["afterCrossNear"]) != 2:
        failures.append("cross-drive duplicate changed the two-report persisted baseline")
    expect_drive_result(failures, "same-drive observation after cross-drive duplicate",
                        second["crossAdjacent"], stored=False, duplicate=True)
    if second["crossAdjacent"].get("duplicate_of") != second["crossNear"].get("duplicate_of"):
        failures.append(
            "B2 did not resolve through B1 to the original A canonical: "
            + str([shape(second["crossNear"]), shape(second["crossAdjacent"])])
        )
    if len(second["afterCrossAdjacent"]) != 2:
        failures.append(
            "B2 created a report instead of using B1's drive-specific canonical sighting"
        )
    expect_drive_result(failures, "cross-drive >8 m", second["crossFar"],
                        stored=True, duplicate=False)
    if len(second["afterCrossFar"]) != 3:
        failures.append("cross-drive event >8 m away was not stored separately")

    expect_drive_result(failures, "first VOD source event", second["vodFirst"],
                        stored=True, duplicate=False)
    expect_drive_result(failures, "exact VOD source-key replay", second["vodReplay"],
                        stored=False, duplicate=True)
    vod_id = second["vodFirst"].get("report", {}).get("id")
    if second["vodReplay"].get("duplicate_of") != vod_id or len(second["afterVodReplay"]) != 4:
        failures.append("exact VOD source key did not resolve to its original stored report")

    expect_drive_result(failures, "first no-GPS VOD event", second["noGpsA"],
                        stored=True, duplicate=False)
    expect_drive_result(failures, "nearby no-GPS VOD event", second["noGpsB"],
                        stored=False, duplicate=True)
    if second["noGpsB"].get("duplicate_of") != second["noGpsA"].get("report", {}).get("id"):
        failures.append("same-drive no-GPS frames 1 s apart were not grouped: "
                        + str([shape(second["noGpsA"]), shape(second["noGpsB"])]))
    if len(second["afterNoGps"]) != 5:
        failures.append("no-GPS duplicate changed the persistent store")

    expect_drive_result(failures, "Debug observation", second["debug"],
                        stored=True, duplicate=False)
    expect_drive_result(failures, "normal observation after Debug", second["normalAfterDebug"],
                        stored=True, duplicate=False)
    if len(second["afterDebug"]) != 6 or len(second["afterNormal"]) != 7:
        failures.append("a Debug record suppressed the later normal observation")
    if second["debug"].get("report", {}).get("dedupe_eligible") is not False:
        failures.append("Debug report was stored as dedupe-eligible")

    if second["manual"].get("duplicate") or not second["manual"].get("id"):
        failures.append(f"explicit manual capture was deduplicated: {shape(second['manual'])}")
    if len(second["finalReports"]) != 8:
        failures.append(f"expected 8 conservative road events, got {len(second['finalReports'])}")
    if second["detectorCalls"] != 10:
        failures.append(f"second phase expected 10 detector calls, got {second['detectorCalls']}")

    # The matcher cannot exercise source offsets if VOD never supplies them. Keep this
    # integration assertion beside the API behavior test without decoding a real video.
    analyse_source = page.evaluate("String(analyseFootage)")
    for field in ("capture_source", "source_event_key", "source_offset_ms",
                  "captured_at_ms", "gps_accuracy", "speed", "heading"):
        if field not in analyse_source:
            failures.append(f"analyseFootage does not send {field}")

    browser.close()

if remote_leaks:
    failures.append("real remote request escaped the deterministic mocks: " + ", ".join(remote_leaks[:3]))

print(f"  concurrent stored : {len(first['storedAfterRace'])}")
print(f"  span canonicals   : {len(first['spanReports'])}")
print(f"  after cross-drive : {len(second['afterCrossNear'])}")
print(f"  final stored      : {len(second['finalReports'])}")
print(f"  detector calls    : {first['detectorCalls']} + {second['detectorCalls']}")
if failures:
    print("\nFAIL")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)
print("\nPERSISTENT DEDUPE TEST PASS")
