# -*- coding: utf-8 -*-
"""A Drive report stores its evidence frame once, not twice.

Drive mode keeps the detector's own frame as the evidence copy, so photo and photo_full
hold the same JPEG. createReport converted it twice, and IndexedDB stored two identical
buffers per report: 200 drive reports were 73 MB, half of it duplicate. Structured
clone keeps a shared reference shared, so one object in both fields is serialised once
and reads back as one object.
"""
import json
import sys

from playwright.sync_api import sync_playwright

import flow_harness

RUN = r"""
async () => {
  const canvas = document.createElement("canvas");
  canvas.width = 1920; canvas.height = 1080;
  const g = canvas.getContext("2d");
  for (let i = 0; i < 400; i++) {
    g.fillStyle = `hsl(${(i * 37) % 360}, 40%, ${20 + (i % 50)}%)`;
    g.fillRect((i * 97) % 1920, (i * 53) % 1080, 90, 60);
  }
  const jpeg = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.9));
  const made = [];
  for (let seq = 0; seq < 2; seq++) {
    const fd = new FormData();
    fd.append("photo", jpeg, `frame-${seq}.jpg`);
    fd.append("lat", String(12.9716 + seq * 0.001)); fd.append("lng", "77.5946");
    fd.append("drive_id", "dedupe-drive"); fd.append("capture_source", "drive_live");
    fd.append("source_event_key", `dedupe-${seq}`); fd.append("gps_accuracy", "4");
    fd.append("speed", "8"); fd.append("heading", "90");
    fd.append("captured_at_ms", String(Date.now() + seq));
    made.push(await StandaloneAPI.handle("/api/frame", { method: "POST", body: fd }));
  }
  const rows = await new Promise((resolve, reject) => {
    const open = indexedDB.open("potholes");
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const req = open.result.transaction("reports").objectStore("reports").getAll();
      req.onsuccess = () => { open.result.close(); resolve(req.result); };
      req.onerror = () => reject(req.error);
    };
  });
  const size = (v) => !v ? 0 : v instanceof Blob ? v.size
    : v.bytes instanceof ArrayBuffer ? v.bytes.byteLength : String(v).length;
  const decodes = async (v) => {
    const blob = v instanceof Blob ? v : new Blob([v.bytes], { type: v.type });
    try { (await createImageBitmap(blob)).close(); return true; } catch (e) { return false; }
  };
  const out = [];
  for (const r of rows.filter((x) => x.drive_id === "dedupe-drive")) {
    out.push({
      decision: r.decision, photo: size(r.photo), photo_full: size(r.photo_full),
      shared: r.photo === r.photo_full,
      full_decodes: r.photo_full ? await decodes(r.photo_full) : false,
    });
  }
  return { made: made.map((m) => m && (m.decision || m.status || null)), rows: out };
}
"""


def main():
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 412, "height": 915},
                                          is_mobile=True, device_scale_factor=2.625)
            context.add_init_script(script="(() => {" + "\n".join([
                f'localStorage.setItem("service_url", {json.dumps(flow_harness.SERVICE)});',
                'localStorage.setItem("data_notice_version", '
                f'{json.dumps(flow_harness.DATA_NOTICE_VERSION)});',
                'localStorage.setItem("initial_setup_complete", "1");',
                'localStorage.setItem("vision_provider", "shared");',
            ]) + "})();")
            context.route("**/*", lambda route: route.continue_()
                          if route.request.url.startswith(flow_harness.APP)
                          else flow_harness.support_services(route, route.request))
            context.route(f"{flow_harness.SERVICE}/**", flow_harness.central_service)
            page = context.new_page()
            page.goto(flow_harness.APP)
            page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
            result = page.evaluate(RUN)
        finally:
            browser.close()

    print(f"  frames: {result['made']}")
    rows = result["rows"]
    if len(rows) != 2:
        failures.append(f"expected two drive reports, found {len(rows)}: {result}")
    for row in rows:
        print(f"  row: {row}")
        if row["decision"] != "accept":
            failures.append(f"drive frame was not accepted: {row}")
            continue
        if not row["photo"] or row["photo"] != row["photo_full"]:
            failures.append(f"drive evidence is not the detector frame: {row}")
        if not row["shared"]:
            failures.append("photo and photo_full are separate copies of the same frame, "
                            f"so the frame is stored twice: {row}")
        if not row["full_decodes"]:
            failures.append(f"photo_full no longer decodes for the email attachment: {row}")

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("DRIVE EVIDENCE DEDUPE TEST PASS")


if __name__ == "__main__":
    main()
