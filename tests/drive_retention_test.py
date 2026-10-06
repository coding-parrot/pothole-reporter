# -*- coding: utf-8 -*-
"""Old drive frames and capped video are pruned; everything still needed stays.

Nothing ever removed a drive's rejected frames or its footage, so a phone that drove
for months carried every frame it had dismissed. The rule now: a drive whose reports
have all left the phone and that ended more than seven days ago loses its rejected
frames; footage is capped as a whole and evicted oldest drive first. The drive in
progress, drives of the last seven days, drives with an unfiled report, labelled frames,
accepted reports and manual photos are never touched. The pass runs at launch.
"""
import json
import os
import shutil
import sys
import tempfile

from playwright.sync_api import sync_playwright

import flow_harness

DAY = 86400
SEGMENT_BYTES = 64 * 1024
CAP_BYTES = 5 * SEGMENT_BYTES  # keeps two drives of two clips, not three

SEED = r"""
async ({ segmentBytes }) => {
  await StandaloneAPI.handle("/api/drives");
  const db = await new Promise((resolve, reject) => {
    const open = indexedDB.open("potholes");
    open.onerror = () => reject(open.error);
    open.onsuccess = () => resolve(open.result);
  });
  const now = Date.now() / 1000;
  const put = (store, rows) => new Promise((resolve, reject) => {
    const tx = db.transaction(store, "readwrite");
    for (const row of rows) tx.objectStore(store).put(row);
    tx.oncomplete = resolve;
    tx.onabort = tx.onerror = () => reject(tx.error);
  });
  const photo = () => new Blob([new Uint8Array(4096)], { type: "image/jpeg" });
  const row = (driveId, ageDays, tag, extra) => ({
    created_at: now - ageDays * DAY + 1, lat: 12.9, lng: 77.6, drive_id: driveId,
    client_observation_id: `${driveId}-${tag}`, photo: photo(), photo_full: photo(),
    capture_source: driveId ? "drive_live" : "manual_camera", debug_capture: true,
    decision: "reject", status: "rejected", condition_status: "open", ...extra,
  });
  const frame = (driveId, ageDays, n) => row(driveId, ageDays, `frame-${n}`, {});
  const filed = (driveId, ageDays, n) => row(driveId, ageDays, `filed-${n}`,
    { decision: "accept", status: "queued", sent_at: now - ageDays * DAY + 600 });
  const draft = (driveId, ageDays, n) => row(driveId, ageDays, `draft-${n}`,
    { decision: "accept", status: "draft" });
  const unrouted = (driveId, ageDays, n) => row(driveId, ageDays, `unrouted-${n}`,
    { decision: "accept", status: "unrouted", unrouted_reason: "outside_area" });
  const labelled = (driveId, ageDays, n) => row(driveId, ageDays, `label-${n}`,
    { human_label: "no_pothole" });
  const DAY = 86400;
  const reports = [
    // settled: 40 days old, its only report was filed, a labelled frame sits among rejects
    frame("old-settled", 40, 1), frame("old-settled", 40, 2), filed("old-settled", 40, 1),
    labelled("old-settled", 40, 1),
    // settled with an unrouted report: nobody to write to, so nothing is waiting
    frame("old-unrouted", 30, 1), unrouted("old-unrouted", 30, 1),
    // old but one report is still a draft
    frame("old-draft", 20, 1), draft("old-draft", 20, 1),
    // recent: three days old, everything filed
    frame("recent", 3, 1), filed("recent", 3, 1),
    // the drive in progress
    frame("live", 0, 1),
    // a manual photo, rejected by the detector, no drive: never pruned
    row(null, 60, "manual", {}),
  ];
  await put("reports", reports);
  const drive = (id, ageDays) => ({ id, started_at: now - ageDays * DAY,
    ended_at: now - ageDays * DAY + 1800, checked: 10, found: 1, already: 0, already_ids: [],
    captured: 10, dropped: 0, failed: 0, gps_track: [], capture_source: "drive_live" });
  await put("drives", [drive("old-settled", 40), drive("old-unrouted", 30),
    drive("old-draft", 20), drive("recent", 3), drive("live", 0), drive("old-video-only", 50)]);
  const clip = (driveId, ageDays, seq) => ({
    key: `${driveId}#${String(seq).padStart(5, "0")}`, drive_id: driveId, seq,
    blob: new Blob([new Uint8Array(segmentBytes)], { type: "video/webm" }),
    mime: "video/webm", bytes: segmentBytes,
    recording_started_at_ms: (now - ageDays * DAY) * 1000, source_offset_s: seq * 10,
    at: now - ageDays * DAY + seq * 10,
  });
  // 14 clips over a cap of 5: the oldest unprotected drives go first.
  await put("footage", [
    clip("old-video-only", 50, 0), clip("old-video-only", 50, 1),
    clip("old-settled", 40, 0), clip("old-settled", 40, 1),
    clip("old-unrouted", 30, 0), clip("old-unrouted", 30, 1),
    clip("old-draft", 20, 0), clip("old-draft", 20, 1),
    clip("recent", 3, 0), clip("recent", 3, 1),
    clip("live", 0, 0), clip("live", 0, 1),
  ]);
  db.close();
  return reports.length;
}
"""

STATE = r"""
async () => {
  const history = await StandaloneAPI.handle("/api/history");
  const rows = history.reports.map((r) => [r.drive_id, r.client_observation_id.split("-").slice(-2).join("-"), r.status]);
  const footage = Object.fromEntries(history.footage.map((f) => [f.drive_id, f.segments]));
  const frames = {};
  for (const r of history.reports) {
    const key = r.drive_id || "manual";
    frames[key] = (frames[key] || 0) + 1;
  }
  return { rows: rows.length, by_drive: frames, footage, has_blob: history.reports.some((r) => r.photo_url) };
}
"""


def main():
    failures = []
    profile_dir = tempfile.mkdtemp(prefix="pothole-retention-")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            profile_dir, headless=True, viewport={"width": 412, "height": 915},
            is_mobile=True, device_scale_factor=2.625)
        try:
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
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(flow_harness.APP)
            page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
            seeded = page.evaluate(SEED, {"segmentBytes": SEGMENT_BYTES})
            before = page.evaluate(STATE)
            print(f"  seeded {seeded} rows: {before}")
            if before["rows"] != seeded or before["has_blob"]:
                failures.append(f"history summary is wrong before pruning: {before}")

            result = page.evaluate("""async (cap) => StandaloneAPI.handle("/api/storage/prune", {
              method: "POST", body: JSON.stringify({ active_drive_id: "live", footage_cap_bytes: cap }) })""",
                                   CAP_BYTES)
            after = page.evaluate(STATE)
            print(f"  prune: {result}")
            print(f"  after: {after}")
            expected_rows = {
                "old-settled": 2,   # the filed report and the labelled frame survive
                "old-unrouted": 1,  # the unrouted report survives, its frame goes
                "old-draft": 2,     # a draft is waiting: everything stays
                "recent": 2,        # seven-day grace
                "live": 1,          # the drive in progress
                "manual": 1,        # never a drive, never pruned
            }
            if after["by_drive"] != expected_rows:
                failures.append(f"frames kept per drive {after['by_drive']}, expected {expected_rows}")
            if result.get("frames_deleted") != 3:
                failures.append(f"expected 3 frames deleted, got {result}")
            # 12 clips of 64 KB against a 320 KB cap: the three oldest unprotected drives
            # go (old-video-only, old-settled, old-unrouted), then 6 clips = 384 KB is
            # still over the cap, but every remaining drive is protected, so it stops.
            expected_footage = {"old-draft": 2, "recent": 2, "live": 2}
            if after["footage"] != expected_footage:
                failures.append(f"footage kept {after['footage']}, expected {expected_footage}")
            if result.get("footage_drives_evicted") != ["old-video-only", "old-settled", "old-unrouted"]:
                failures.append(f"eviction order wrong: {result.get('footage_drives_evicted')}")
            if result.get("footage_bytes") != 6 * SEGMENT_BYTES:
                failures.append(f"footage total after eviction {result.get('footage_bytes')}, "
                                f"expected {6 * SEGMENT_BYTES}")
            if not result.get("freed_bytes"):
                failures.append("prune reported no freed bytes")

            # A second pass is idempotent.
            again = page.evaluate("""async (cap) => StandaloneAPI.handle("/api/storage/prune", {
              method: "POST", body: JSON.stringify({ active_drive_id: "live", footage_cap_bytes: cap }) })""",
                                  CAP_BYTES)
            if again.get("frames_deleted") or again.get("footage_drives_evicted"):
                failures.append(f"second prune removed more: {again}")

            # Launch runs the pass by itself: plant one more settled drive's frame and reload.
            page.evaluate(r"""async () => {
              const db = await new Promise((resolve, reject) => {
                const open = indexedDB.open("potholes");
                open.onerror = () => reject(open.error);
                open.onsuccess = () => resolve(open.result);
              });
              const now = Date.now() / 1000;
              await new Promise((resolve, reject) => {
                const tx = db.transaction(["reports", "drives"], "readwrite");
                tx.objectStore("reports").put({ created_at: now - 12 * 86400, lat: 12.9, lng: 77.6,
                  drive_id: "launch-old", client_observation_id: "launch-old-frame-1",
                  photo: new Blob([new Uint8Array(4096)], { type: "image/jpeg" }),
                  capture_source: "drive_live", debug_capture: true, decision: "reject",
                  status: "rejected", condition_status: "open" });
                tx.objectStore("drives").put({ id: "launch-old", started_at: now - 12 * 86400,
                  ended_at: now - 12 * 86400 + 600, checked: 1, found: 0, already: 0,
                  already_ids: [], gps_track: [], capture_source: "drive_live" });
                tx.oncomplete = resolve;
                tx.onabort = tx.onerror = () => reject(tx.error);
              });
              db.close();
            }""")
            page.reload()
            page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
            page.wait_for_function("""async () => {
              const h = await StandaloneAPI.handle("/api/history");
              return !h.reports.some((r) => r.drive_id === "launch-old");
            }""", timeout=15_000)
            print("  launch pass removed the planted settled drive's frame")
        finally:
            context.close()
            shutil.rmtree(profile_dir, ignore_errors=True)

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("DRIVE RETENTION TEST PASS")


if __name__ == "__main__":
    main()
