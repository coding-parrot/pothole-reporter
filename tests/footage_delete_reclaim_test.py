#!/usr/bin/env python3
"""A Photo taken right after Delete footage must find the freed space.

storageError tells a tester on a full phone to delete old drives and their video. The
footage delete resolved as soon as the rows were gone, but the WebView reports the space
back several seconds later (measured 5.3 s on a 300 MB volume), so the retry the message
invites was refused as out of storage. Headless Chromium never returns the space within
a test and keeps a pseudo-quota in estimate(), so estimate() is answered from a phone
that is full until 1.5 s after the delete, which is how the device behaved.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import open_flow, report_form_script


MB = 1024 * 1024

RUN = """async (script) => {
  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  await new Promise((resolve, reject) => {
    const tx = db.transaction("footage", "readwrite");
    tx.objectStore("footage").put({ key: "fill#0", drive_id: "fill", seq: 0,
      blob: new Blob([new Uint8Array(512 * 1024)], { type: "video/webm" }), bytes: 512 * 1024 });
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error);
  });
  db.close();

  const quota = 100 * __MB__;
  let usage = quota - 256 * 1024;
  navigator.storage.estimate = async () => ({ quota, usage });
  const started = performance.now();
  setTimeout(() => { usage = 12 * __MB__; }, 1500);
  await StandaloneAPI.handle("/api/footage/fill", { method: "DELETE" });
  const deleteMs = Math.round(performance.now() - started);
  try { return { deleteMs, report: !!(await (eval(script))()) }; }
  catch (error) { return { deleteMs, error: String(error && error.message || error) }; }
}""".replace("__MB__", str(MB))

# Plenty of room: the delete must not stall on a phone that was never short of space.
ROOMY = """async () => {
  navigator.storage.estimate = async () => ({ quota: 100 * __MB__, usage: 10 * __MB__ });
  const started = performance.now();
  await StandaloneAPI.handle("/api/footage/none", { method: "DELETE" });
  return Math.round(performance.now() - started);
}""".replace("__MB__", str(MB))

failures = []
with sync_playwright() as playwright:
    browser, page, errors = open_flow(playwright, native=False)
    try:
        outcome = page.evaluate(RUN, report_form_script())
        roomy_ms = page.evaluate(ROOMY)
    finally:
        browser.close()

print(f"  outcome: {outcome}")
print(f"  roomy delete: {roomy_ms} ms")
if not outcome.get("report"):
    failures.append(f"a Photo right after Delete footage still failed: {outcome}")
if outcome.get("deleteMs", 0) > 9000:
    failures.append(f"Delete footage waited past its bound: {outcome}")
if roomy_ms > 500:
    failures.append(f"Delete footage waited on a phone with room: {roomy_ms} ms")

if failures:
    print("FOOTAGE DELETE RECLAIM TEST FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("FOOTAGE DELETE RECLAIM TEST PASS")
