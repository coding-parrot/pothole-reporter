# -*- coding: utf-8 -*-
"""Home stays fast and small when the phone holds hundreds of reports and drives.

A tester with months of use wrote that the app crashes once its data grows and only
clearing it helps. /api/reports read every row with getAll(), so Home received every
photo (and the evidence copy) of every report ever made, kept them all in
loadReports.latest, and painted one card per report with an object URL pinning each
photo. Drive frames and footage were never pruned.

This suite seeds 600 reports with 1 MB photos and 3 drives of 200 frames each straight
into IndexedDB, opens Home, scrolls the list, reopens Home, and measures wall time, the
JS heap (CDP Performance.getMetrics JSHeapUsedSize) and the renderer's resident memory.
The budgets are what a low-end WebView can afford, not what a Mac can.

Env: POTHOLE_TEST_APP (default http://localhost:8765/), POTHOLE_MEMORY_REPORTS (600),
POTHOLE_MEMORY_FRAMES (200 per drive). Run with --report to only print the numbers.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

import flow_harness

REPORTS = int(os.environ.get("POTHOLE_MEMORY_REPORTS", "600"))
FRAMES_PER_DRIVE = int(os.environ.get("POTHOLE_MEMORY_FRAMES", "200"))
DRIVES = 3
PHOTO_BYTES = 1024 * 1024
FRAME_BYTES = 256 * 1024
SEGMENT_BYTES = 2 * 1024 * 1024
SEGMENTS_PER_DRIVE = 12

HEAP_BUDGET_MB = 150
RSS_BUDGET_MB = 600
HOME_COLD_BUDGET_MS = 4000
HOME_WARM_BUDGET_MS = 1500
DAY = 86400

SEED = r"""
async ({ reports, drives, framesPerDrive, photoBytes, frameBytes, segmentBytes,
         segmentsPerDrive }) => {
  // Open through the app so the v8 schema and indexes exist before the direct writes.
  await StandaloneAPI.handle("/api/drives");
  const db = await new Promise((resolve, reject) => {
    const open = indexedDB.open("potholes");
    open.onerror = () => reject(open.error);
    open.onsuccess = () => resolve(open.result);
  });
  // Random bytes defeat any compression on the way to disk, so a megabyte stays one.
  const noise = (bytes) => {
    const out = new Uint8Array(bytes);
    for (let i = 0; i < bytes; i += 65536) {
      crypto.getRandomValues(out.subarray(i, Math.min(bytes, i + 65536)));
    }
    return out;
  };
  const photoNoise = noise(photoBytes), frameNoise = noise(frameBytes);
  const segmentNoise = noise(segmentBytes);
  // One Blob object per row, used for both photo fields as a Drive frame is; a copy
  // per row keeps every row's bytes distinct on disk. The photo_full of a manual report
  // is larger in life, but the temp profile's quota is 2 GB and this is enough to show
  // what reading every row costs.
  const blobOf = (bytes, type) => new Blob([bytes.slice(0)], { type });
  const letter = "Respected Sir/Madam,\n" + "The road surface is broken and unsafe. ".repeat(60);
  const now = Date.now() / 1000;
  const driveIds = [];
  for (let d = 0; d < drives; d++) driveIds.push(`drive-${1000 + d}`);
  // Blobs are made just before their write: hundreds of megabytes of Blobs alive at
  // once trip the renderer's blob memory limit before IndexedDB sees them.
  const batch = async (store, rows) => {
    for (let i = 0; i < rows.length; i += 25) {
      await new Promise((resolve, reject) => {
        const tx = db.transaction(store, "readwrite");
        const s = tx.objectStore(store);
        for (const row of rows.slice(i, i + 25)) {
          if (row._blob) {
            const blob = row._blob();
            for (const field of row._fields) row[field] = blob;
            delete row._blob; delete row._fields;
          }
          s.put(row);
        }
        tx.oncomplete = resolve;
        tx.onabort = tx.onerror = () => reject(tx.error);
      });
    }
  };
  const reportRows = [];
  for (let n = 0; n < reports; n++) {
    const ageDays = (n / reports) * 120;
    const filed = n % 3 !== 0;
    reportRows.push({
      _blob: () => blobOf(photoNoise, "image/jpeg"), _fields: ["photo", "photo_full"],
      created_at: now - ageDays * 86400, lat: 12.9 + (n % 40) * 0.001, lng: 77.5 + (n % 37) * 0.001,
      address: `Road ${n}, Ward ${n % 50}, Bengaluru`, client_observation_id: `seed-${n}`,
      damage_type: "pothole_cavity", assessment: "pothole", image_quality: "ok", size: "medium",
      decision: "accept", description: "A deep pothole in the left lane.",
      email_subject: `Pothole on Road ${n}`, email_body: letter, whatsapp_text: letter,
      portal_copy_text: letter, status: filed ? "queued" : "draft",
      sent_at: filed ? now - ageDays * 86400 + 600 : null,
      condition_status: "open", drive_id: null, capture_source: "manual_camera",
      location_source: "device_gps", captured_at: now - ageDays * 86400, gps_accuracy: 5,
      dedupe_eligible: true, seen_count: 1, server_pothole_id: String(5000 + n),
      vision_provider: "shared_server", event_sightings: [], sighting_drive_ids: [],
      officer_email: "commissioner@example.gov.in", officer_name: "Commissioner",
      last_seen_at: now - ageDays * 86400,
    });
  }
  await batch("reports", reportRows);
  const frameRows = [];
  driveIds.forEach((driveId, d) => {
    const ageDays = [0.2, 10, 40][d];
    for (let f = 0; f < framesPerDrive; f++) {
      const accepted = f % 50 === 0;
      frameRows.push({
        _blob: () => blobOf(frameNoise, "image/jpeg"), _fields: ["photo", "photo_full"],
        created_at: now - ageDays * 86400 + f, lat: 13.0 + f * 0.0002, lng: 77.6 + d * 0.01,
        address: null, client_observation_id: `frame-${driveId}-${f}`,
        damage_type: accepted ? "pothole_cavity" : null, decision: accepted ? "accept" : "reject",
        status: accepted ? "queued" : "rejected", sent_at: accepted ? now - ageDays * 86400 + 900 : null,
        condition_status: "open", drive_id: driveId, capture_source: "drive_live",
        debug_capture: true, dedupe_eligible: accepted, seen_count: accepted ? 1 : 0,
        event_sightings: [], sighting_drive_ids: accepted ? [driveId] : [],
        server_pothole_id: accepted ? `d${d}-${f}` : null,
      });
    }
  });
  await batch("reports", frameRows);
  const driveRows = driveIds.map((id, d) => ({
    id, started_at: now - [0.2, 10, 40][d] * 86400, ended_at: now - [0.2, 10, 40][d] * 86400 + 1800,
    checked: framesPerDrive, found: Math.ceil(framesPerDrive / 50), already: 0, already_ids: [],
    captured: framesPerDrive, dropped: 0, failed: 0, gps_track: [], capture_source: "drive_live",
  }));
  await batch("drives", driveRows);
  const footageRows = [];
  driveIds.forEach((id, d) => {
    for (let s = 0; s < segmentsPerDrive; s++) {
      footageRows.push({
        key: `${id}#${String(s).padStart(5, "0")}`, drive_id: id, seq: s,
        _blob: () => blobOf(segmentNoise, "video/webm"), _fields: ["blob"], mime: "video/webm", bytes: segmentBytes,
        recording_started_at_ms: (now - [0.2, 10, 40][d] * 86400) * 1000,
        source_offset_s: s * 10, at: now - [0.2, 10, 40][d] * 86400 + s * 10,
      });
    }
  });
  await batch("footage", footageRows);
  db.close();
  return { reports: reportRows.length, frames: frameRows.length, footage: footageRows.length };
}
"""

HEAP = "() => performance.memory ? performance.memory.usedJSHeapSize : null"


def renderer_rss_mb(browser_cdp):
    """Resident size of the largest renderer of this browser, in MB (macOS/Linux ps)."""
    info = browser_cdp.send("SystemInfo.getProcessInfo")["processInfo"]
    pids = [p["id"] for p in info if p["type"] == "renderer"]
    best = 0
    for pid in pids:
        try:
            out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
            best = max(best, int(out or 0) / 1024)
        except Exception:
            pass
    return round(best)


def sample(page, cdp, browser_cdp, label):
    cdp.send("HeapProfiler.collectGarbage")
    metrics = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
    heap_mb = round(metrics.get("JSHeapUsedSize", 0) / (1024 * 1024))
    rss_mb = renderer_rss_mb(browser_cdp)
    nodes = int(metrics.get("Nodes", 0))
    print(f"  [{label}] heap {heap_mb} MB, renderer RSS {rss_mb} MB, DOM nodes {nodes}")
    return {"heap_mb": heap_mb, "rss_mb": rss_mb, "nodes": nodes}


def new_context(playwright, profile_dir):
    # A persistent profile: new_context() is incognito, where IndexedDB and Blob storage
    # are memory-backed, capped near 400 MB, and would count as renderer memory here.
    context = playwright.chromium.launch_persistent_context(
        profile_dir, headless=True, args=["--enable-precise-memory-info"],
        viewport={"width": 412, "height": 915}, is_mobile=True, device_scale_factor=2.625)
    context.add_init_script(script="(() => {" + "\n".join([
        f'localStorage.setItem("service_url", {json.dumps(flow_harness.SERVICE)});',
        'localStorage.setItem("data_notice_version", '
        f'{json.dumps(flow_harness.DATA_NOTICE_VERSION)});',
        'localStorage.setItem("initial_setup_complete", "1");',
        'localStorage.setItem("vision_provider", "shared");',
        'localStorage.setItem("app_lang", "en");',
    ]) + "})();")
    context.route("**/*", lambda route: route.continue_()
                  if route.request.url.startswith(flow_harness.APP)
                  else flow_harness.support_services(route, route.request))
    context.route(f"{flow_harness.SERVICE}/**", flow_harness.central_service)
    return context


def main():
    report_only = "--report" in sys.argv
    failures = []
    numbers = {}
    profile_dir = tempfile.mkdtemp(prefix="pothole-memory-")
    with sync_playwright() as playwright:
        context = new_context(playwright, profile_dir)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(flow_harness.APP)
            page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
            t0 = time.time()
            seeded = page.evaluate(SEED, {
                "reports": REPORTS, "drives": DRIVES, "framesPerDrive": FRAMES_PER_DRIVE,
                "photoBytes": PHOTO_BYTES, "frameBytes": FRAME_BYTES,
                "segmentBytes": SEGMENT_BYTES, "segmentsPerDrive": SEGMENTS_PER_DRIVE,
            })
            print(f"  seeded {seeded} in {time.time() - t0:.1f} s")
            page.close()

            # Cold start: a fresh page against the populated database.
            page = context.new_page()
            cdp = context.new_cdp_session(page)
            cdp.send("Performance.enable")
            browser_cdp = context.browser.new_browser_cdp_session()
            t0 = time.time()
            page.goto(flow_harness.APP)
            page.locator("#list .card").first.wait_for(state="visible", timeout=120_000)
            numbers["home_cold_ms"] = round((time.time() - t0) * 1000)
            print(f"  cold start to first Home card: {numbers['home_cold_ms']} ms")
            cards = page.locator("#list .card").count()
            print(f"  cards painted on Home: {cards}")
            numbers["cards"] = cards
            numbers["after_home"] = sample(page, cdp, browser_cdp, "after Home")

            # Scroll the whole list so every lazy thumbnail is requested, as a tester
            # looking for an old report does.
            page.evaluate("""async () => {
              const list = document.getElementById("list");
              for (let y = 0; y <= document.body.scrollHeight; y += 700) {
                window.scrollTo(0, y);
                await new Promise((r) => setTimeout(r, 15));
              }
              await new Promise((r) => setTimeout(r, 500));
            }""")
            numbers["after_scroll"] = sample(page, cdp, browser_cdp, "after scrolling the list")

            # Returning to Home re-reads history: every Back press pays this.
            warm = page.evaluate("""async () => {
              const t0 = performance.now();
              await loadReports();
              return performance.now() - t0;
            }""")
            numbers["home_warm_ms"] = round(warm)
            print(f"  warm Home reload: {numbers['home_warm_ms']} ms")
            numbers["after_reload"] = sample(page, cdp, browser_cdp, "after Home reload")
            # Drive groups are collapsed; expanding the newest one must not load the others.
            page.locator("#list .group-head").first.click()
            page.wait_for_timeout(300)
            numbers["after_expand"] = sample(page, cdp, browser_cdp, "after expanding a drive")
            retained = page.evaluate("""() => {
              const live = (loadReports.latest || []);
              return { rows: live.length,
                       with_photo_handles: live.filter((r) => r.photo_url).length };
            }""")
            print(f"  rows held in memory after render: {retained}")
            numbers["retained"] = retained
        finally:
            context.close()
            shutil.rmtree(profile_dir, ignore_errors=True)

    print("  numbers:", json.dumps(numbers))
    if report_only:
        return
    worst_heap = max(v["heap_mb"] for k, v in numbers.items() if isinstance(v, dict) and "heap_mb" in v)
    worst_rss = max(v["rss_mb"] for k, v in numbers.items() if isinstance(v, dict) and "rss_mb" in v)
    if numbers["home_cold_ms"] > HOME_COLD_BUDGET_MS:
        failures.append(f"cold start to Home took {numbers['home_cold_ms']} ms "
                        f"(budget {HOME_COLD_BUDGET_MS})")
    if numbers["home_warm_ms"] > HOME_WARM_BUDGET_MS:
        failures.append(f"returning to Home took {numbers['home_warm_ms']} ms "
                        f"(budget {HOME_WARM_BUDGET_MS})")
    if worst_heap > HEAP_BUDGET_MB:
        failures.append(f"JS heap reached {worst_heap} MB (budget {HEAP_BUDGET_MB})")
    if worst_rss > RSS_BUDGET_MB:
        failures.append(f"renderer RSS reached {worst_rss} MB (budget {RSS_BUDGET_MB})")
    if numbers["retained"]["with_photo_handles"] > 100:
        failures.append("Home keeps a photo handle for every report in memory: "
                        f"{numbers['retained']}")
    if numbers["cards"] > 100:
        failures.append(f"Home painted {numbers['cards']} cards at once; it must window the list")

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("HISTORY MEMORY BUDGET TEST PASS")


if __name__ == "__main__":
    main()
