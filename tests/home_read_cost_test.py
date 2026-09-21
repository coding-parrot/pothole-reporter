# -*- coding: utf-8 -*-
"""Returning to Home does not copy every report's full-resolution evidence.

/api/reports reads every row with getAll() and only then drops photo_full. Evidence
stored as {bytes: ArrayBuffer} is copied in full by that read, so every Back press
deserialised each report's 4000px JPEG: measured 649 ms and about 200 MB of buffers for
50 camera reports. A Blob in IndexedDB is a handle; reading the row does not read its
bytes. The same 150 rows stored as Blobs listed in 26 ms.
"""
import json
import sys

from playwright.sync_api import sync_playwright

import flow_harness

REPORTS = 12

MAKE = r"""
async (count) => {
  const canvas = document.createElement("canvas");
  canvas.width = 3000; canvas.height = 4000;
  const g = canvas.getContext("2d");
  // Noise keeps the JPEG near a real 12 MP camera file instead of a few kilobytes.
  const image = g.createImageData(3000, 4000);
  const px = image.data;
  let seed = 7;
  for (let i = 0; i < px.length; i += 4) {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    px[i] = seed & 255; px[i + 1] = (seed >> 8) & 255; px[i + 2] = (seed >> 16) & 255;
    px[i + 3] = 255;
  }
  g.putImageData(image, 0, 0);
  const jpeg = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.9));
  for (let n = 0; n < count; n++) {
    const form = new FormData();
    form.append("photo", jpeg, `camera-${n}.jpg`);
    form.append("lat", String(12.9716 + n * 0.0005)); form.append("lng", "77.5946");
    form.append("gps_accuracy", "4"); form.append("captured_at_ms", String(Date.now() + n));
    await StandaloneAPI.handle("/api/report", { method: "POST", body: form });
  }
  return jpeg.size;
}
"""

MEASURE = r"""
async () => {
  const rows = await new Promise((resolve, reject) => {
    const open = indexedDB.open("potholes");
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const req = open.result.transaction("reports").objectStore("reports").getAll();
      req.onsuccess = () => { open.result.close(); resolve(req.result); };
      req.onerror = () => reject(req.error);
    };
  });
  const kind = (v) => !v ? null : v instanceof Blob ? "Blob"
    : v.bytes instanceof ArrayBuffer ? "bytes" : typeof v;
  const full = rows.map((r) => r.photo_full).filter(Boolean);
  let decodes = false;
  if (full.length) {
    const v = full[0];
    const blob = v instanceof Blob ? v : new Blob([v.bytes], { type: v.type });
    try { (await createImageBitmap(blob)).close(); decodes = true; } catch (e) {}
  }
  const times = [];
  let listed = null;
  for (let i = 0; i < 4; i++) {
    const t0 = performance.now();
    listed = await StandaloneAPI.handle("/api/reports");
    times.push(performance.now() - t0);
  }
  times.shift();  // the first read also opens the database
  times.sort((a, b) => a - b);
  return {
    rows: rows.length, accepted: full.length,
    photo_kinds: [...new Set(rows.map((r) => kind(r.photo)))],
    full_kinds: [...new Set(full.map(kind))],
    full_bytes: full.map((v) => v instanceof Blob ? v.size : v.bytes.byteLength),
    full_decodes: decodes,
    listed: Array.isArray(listed) ? listed.length : null,
    listed_has_photo: Array.isArray(listed) && listed.every((r) => !!r.photo_url),
    median_ms: times[1],
  };
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
            source = page.evaluate(MAKE, REPORTS)
            result = page.evaluate(MEASURE)
        finally:
            browser.close()

    print(f"  camera JPEG {source} B; {result}")
    if result["accepted"] != REPORTS:
        failures.append(f"expected {REPORTS} accepted reports with evidence: {result}")
    if result["full_kinds"] != ["Blob"] or result["photo_kinds"] != ["Blob"]:
        failures.append("evidence is stored as copied bytes, not as Blobs, so every list "
                        f"read deserialises it: photo {result['photo_kinds']}, "
                        f"photo_full {result['full_kinds']}")
    if not result["full_decodes"]:
        failures.append("stored photo_full does not decode")
    if result["listed"] != REPORTS or not result["listed_has_photo"]:
        failures.append(f"Home list lost reports or thumbnails: {result}")
    # Bytes rows cost about 25 ms each on a Mac; Blob rows list in a few milliseconds.
    if result["median_ms"] > 100:
        failures.append(f"/api/reports took {result['median_ms']:.0f} ms for {REPORTS} "
                        "camera reports")

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("HOME READ COST TEST PASS")


if __name__ == "__main__":
    main()
