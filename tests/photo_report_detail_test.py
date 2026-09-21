# -*- coding: utf-8 -*-
"""A fresh photo report's detail card tells the tester what the app knows about it.

Three things the app stored never reached the card. Every photo was saved as capture
source "manual", so the camera and imported-photo lines were dead strings. A draft with
repair evidence showed the "Repair needs checking" chip but no Confirm fixed or Reopen,
so an emailed complaint could never be closed. A pothole the shared map already knew
read as a plain Draft. The progress line also printed a literal "{type}".
"""

import json
import sys

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, capture, open_central, routed
from flow_harness import envelope

CAMERA_REPORT = r"""
async () => {
  const canvas = document.createElement("canvas");
  canvas.width = 160; canvas.height = 120;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#777"; ctx.fillRect(0, 0, 160, 120);
  ctx.fillStyle = "#111"; ctx.fillRect(50, 52, 60, 38);
  const photo = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", .88));
  const form = new FormData();
  form.append("photo", photo, "camera.jpg");
  form.append("issue_type", "road_damage");
  form.append("capture_source", "manual_camera");
  form.append("lat", "12.9716"); form.append("lng", "77.5946");
  form.append("gps_accuracy", "4"); form.append("captured_at_ms", String(Date.now()));
  const report = await StandaloneAPI.handle("/api/report", { method: "POST", body: form });
  openDetail(report, [report]);
  return { source: report.capture_source, text: document.getElementById("detail").innerText };
}
"""

# A revisit that met the fixed bar, written onto an emailed complaint.
SEED_REPAIR = r"""
async (id) => {
  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  await new Promise((resolve, reject) => {
    const tx = db.transaction("reports", "readwrite");
    const store = tx.objectStore("reports");
    const get = store.get(Number(id));
    get.onsuccess = () => {
      const row = get.result;
      Object.assign(row, {
        status: "queued", condition_status: "repair_review",
        repair_photo: row.photo, repair_current_condition: "repaired",
        repair_same_location_visible: true, repair_completed_visible: true,
        repair_image_quality: "usable",
        repair_description: "Intact asphalt covers the old cavity footprint.",
      });
      store.put(row);
    };
    tx.oncomplete = resolve;
    tx.onerror = tx.onabort = () => reject(tx.error);
  });
  db.close();
  await loadReports();
  const row = loadReports.latest.find((item) => item.id === id);
  openDetail(row, loadReports.latest);
  return [...document.querySelectorAll("#detail button")].map((button) => button.id);
}
"""


def duplicate_report(route, request, path, central):
    if routed(route, request, path, central):
        return True
    if path != "/v1/potholes/report":
        return False
    body = json.loads(request.post_data or "{}")
    envelope(route, {
        "duplicate": True, "dedupe": {"distance_m": 4.2},
        "pothole": {
            "id": 4242, "lat": body.get("lat"), "lng": body.get("lng"),
            "damage_type": body.get("damage_type"), "size": body.get("size"),
            "first_seen_at": body.get("observed_at"),
            "last_seen_at": body.get("observed_at"), "seen_count": 3,
            "lgd": "248127", "town": "Kalaburagi",
        },
    }, 200)
    return True


fails = []
with sync_playwright() as playwright:
    browser, page, dialogs, errors = open_central(playwright, Central(routed))
    try:
        page.wait_for_timeout(600)
        texts = page.evaluate("""() => ({
          camera: t("capture_provenance_camera"), imported: t("capture_provenance_import"),
        })""")

        camera = page.evaluate(CAMERA_REPORT)
        if camera["source"] != "manual_camera":
            fails.append(f"camera photo stored capture source {camera['source']!r}")
        if texts["camera"] not in camera["text"]:
            fails.append("camera photo detail has no camera provenance line")

        # The Photo button's file picker is the imported path.
        page.evaluate("show('home')")
        outcome, message = capture(page, dialogs)
        if outcome != "detail":
            fails.append(f"imported photo did not reach detail: {outcome} {message!r}")
        else:
            imported = page.evaluate("""async () => ({
              source: (await StandaloneAPI.handle('/api/reports'))[0].capture_source,
              text: document.getElementById('detail').innerText })""")
            if imported["source"] != "manual_import":
                fails.append(f"imported photo stored capture source {imported['source']!r}")
            if texts["imported"] not in imported["text"]:
                fails.append("imported photo detail has no imported-photo line")

        # Repair evidence on an emailed complaint offers both decisions.
        report_id = page.evaluate("async () => (await StandaloneAPI.handle('/api/reports'))[0].id")
        buttons = page.evaluate(SEED_REPAIR, report_id)
        if "confirmFixedBtn" not in buttons or "reopenConditionBtn" not in buttons:
            fails.append(f"queued report with repair evidence lacks the decisions: {buttons}")
        else:
            page.locator("#confirmFixedBtn").click()
            page.wait_for_function("""() => !document.getElementById('confirmFixedBtn')""")
            after = page.evaluate("""async (id) => ({
              condition: (await StandaloneAPI.handle('/api/reports'))
                .find((row) => row.id === id).condition_status,
              send: !!document.getElementById('sendBtn') })""", report_id)
            if after["condition"] != "fixed":
                fails.append(f"Confirm fixed did not mark the report fixed: {after}")
            if after["send"]:
                fails.append("a pothole confirmed fixed still offers Email complaint")

        # Streamed verdicts never print a raw placeholder.
        for detail in ("{accepted: true, damage_type: 'failed_patch'}", "{accepted: true}"):
            line = page.evaluate(f"""() => {{
              show('progress');
              window.dispatchEvent(new CustomEvent('pipeline-verdict', {{ detail: {detail} }}));
              const text = document.getElementById('progressText').textContent;
              show('home');
              return text;
            }}""")
            if "{" in line or not line.strip():
                fails.append(f"streamed verdict line reads {line!r}")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    finally:
        browser.close()

    # A pothole the shared map already had says so, and can still be emailed. A map match
    # is not a filed complaint, so the card must not call it already reported.
    browser, page, dialogs, errors = open_central(playwright, Central(duplicate_report))
    try:
        page.wait_for_timeout(600)
        camera = page.evaluate(CAMERA_REPORT)
        expected = page.evaluate("""() => [t("map_known"), t("seen_count", { n: 3 })]""")
        for text in expected:
            if text not in camera["text"]:
                fails.append(f"known pothole detail omits {text!r}: {camera['text']!r}")
        if page.evaluate("""(text) => text.includes(t("duplicate_title"))""", camera["text"]):
            fails.append(f"known pothole detail calls it already reported: {camera['text']!r}")
        if not page.evaluate("() => !!document.getElementById('sendBtn')"):
            fails.append("known pothole detail lost its Email action")
    finally:
        browser.close()

if fails:
    print("FAIL photo report detail")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS photo report detail")
