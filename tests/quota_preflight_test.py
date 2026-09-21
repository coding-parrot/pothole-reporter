#!/usr/bin/env python3
"""A Photo on a full phone must be refused before it spends a detection.

createReport ran the vision call, the central resolver and the shared-map registration
before its first local write. On a phone out of storage every attempt spent one of the
install's daily detections and left a shared-map row the phone had no record of, then
failed to save. The origin quota is lowered with CDP and filled, the way a full phone
looks to the WebView.
"""

import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from flow_harness import APP, open_flow, report_form_script


QUOTA = 80 * 1024 * 1024

FILL = """async () => {
  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  // Random bytes so nothing compresses. Halve the row on each refusal until even a
  // quarter megabyte no longer fits: that is a full phone.
  const chunk = (size) => {
    const bytes = new Uint8Array(size);
    for (let i = 0; i < size; i += 65536) crypto.getRandomValues(bytes.subarray(i, i + 65536));
    return new Blob([bytes], { type: "video/webm" });
  };
  let seq = 0, size = 4 * 1024 * 1024;
  while (size >= 256 * 1024) {
    try {
      await new Promise((resolve, reject) => {
        const tx = db.transaction("footage", "readwrite");
        tx.objectStore("footage").put({ key: `fill#${seq}`, drive_id: "fill", seq,
          blob: chunk(size), bytes: size });
        tx.oncomplete = resolve;
        tx.onabort = () => reject(tx.error);
      });
      seq += 1;
    } catch (error) { size = Math.floor(size / 2); }
  }
  db.close();
  return { rows: seq };
}"""

# Headless Chromium answers navigator.storage.estimate() with a fixed pseudo-quota and
# ignores the CDP override there, while an Android WebView reports the real figures.
# Report what CDP says the origin has, which is what the phone's WebView would say.
STUB_ESTIMATE = """(numbers) => {
  navigator.storage.estimate = async () => ({ quota: numbers.quota, usage: numbers.usage });
}"""

failures = []
with sync_playwright() as playwright:
    browser, page, errors = open_flow(playwright, native=False)
    calls = []
    page.on("request", lambda request: calls.append(urlparse(request.url).path)
            if request.url.startswith("https://flow-harness.test") else None)
    try:
        origin = "{0.scheme}://{0.netloc}".format(urlparse(APP))
        cdp = page.context.new_cdp_session(page)
        cdp.send("Storage.overrideQuotaForOrigin", {"origin": origin, "quotaSize": QUOTA})
        fill = page.evaluate(FILL)
        numbers = cdp.send("Storage.getUsageAndQuota", {"origin": origin})
        fill["free"] = numbers["quota"] - numbers["usage"]
        page.evaluate(STUB_ESTIMATE, {"quota": numbers["quota"], "usage": numbers["usage"]})
        calls.clear()
        outcome = page.evaluate("""async (script) => {
          try { return { report: await (eval(script))() }; }
          catch (error) { return { error: String(error && error.message || error) }; }
        }""", report_form_script())
        page.wait_for_timeout(300)
        spent = [path for path in calls
                 if path in ("/v1/vision/detect", "/v1/potholes/report", "/v1/tenders/resolve")]
        cdp.send("Storage.overrideQuotaForOrigin", {"origin": origin})
        page.evaluate("""() => new Promise((resolve) => {
          const request = indexedDB.deleteDatabase("potholes");
          request.onsuccess = request.onerror = request.onblocked = () => resolve();
        })""")
    finally:
        browser.close()

print(f"  fill: {fill}")
print(f"  outcome: {outcome}")
if fill["free"] > 8 * 1024 * 1024:
    failures.append(f"could not fill the lowered quota: {fill}")
if "out of storage" not in (outcome.get("error") or ""):
    failures.append(f"a Photo on a full phone was not refused with the storage error: {outcome}")
if spent:
    failures.append(f"a Photo on a full phone still called the service: {spent}")

if failures:
    print("QUOTA PREFLIGHT TEST FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("QUOTA PREFLIGHT TEST PASS")
