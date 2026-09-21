# -*- coding: utf-8 -*-
"""Half-written history rows render as plain history, not as template debris.

A drive the app was killed during has no frame count yet, and a report restored from
an older build can lack its timestamp or photo. Home once showed ", live frames
checked", "Invalid Date" and a broken-image frame for them.
"""

import sys
import time

from playwright.sync_api import sync_playwright

from browser_test_utils import open_app


SEED = r"""
async (now) => {
  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  await new Promise((resolve, reject) => {
    const tx = db.transaction(["drives", "reports"], "readwrite");
    tx.objectStore("drives").put({ id: "half-drive", started_at: now - 3000, ended_at: null });
    tx.objectStore("reports").put({ status: "draft", photo_url: null, lat: 12.97, lng: 77.59,
                                    is_pothole: true, assessment: "damaged" });
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}
"""

failures = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--disable-web-security"])
    context = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True,
                                  device_scale_factor=2.625)
    page = context.new_page()
    open_app(page, "test-key-never-sent")
    page.evaluate(SEED, int(time.time()))
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_function("document.querySelector('[data-drive=\"half-drive\"]')", timeout=15_000)
    state = page.evaluate("""() => ({
      text: document.getElementById("list").innerText,
      emptyImages: [...document.querySelectorAll("#list img")]
        .filter((img) => !img.getAttribute("src")).length,
      tSkipsMissing: t("drive_group", { date: "D", found: "F", known: "", checked: undefined,
                                        video: "" }),
    })""")
    for debris in (", live", "Invalid Date", "undefined", "NaN"):
        if debris in state["text"]:
            failures.append(f"history shows {debris!r}: {state['text']!r}")
    if state["emptyImages"]:
        failures.append(f"{state['emptyImages']} thumbnail(s) render as an empty image frame")
    if "," in state["tSkipsMissing"]:
        failures.append(f"t() prints a missing value as a comma: {state['tSkipsMissing']!r}")
    context.close()
    browser.close()

if failures:
    print("FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS partial records render")
