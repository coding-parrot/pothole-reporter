# -*- coding: utf-8 -*-
"""Delete all finishes when Android refuses to remove the exported debug frames.

Debug frames are written to Documents/pothole-frames. After a reinstall, or on an
Android that hands shared Documents to the media provider, the app may no longer be
allowed to remove that folder. The wipe threw at that point, after native data was
already gone but before any report, setting or cache was, and said only "Delete
incomplete". Everything the app can delete is now deleted, and the tester is told in
their language which folder is left for them.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow

# Registered after the native stub: native data clears, the frame folder cannot be
# removed, and every other managed folder is already gone.
REFUSING_FILESYSTEM = """
(() => {
  window.__rmdirCalls = [];
  const plugins = window.Capacitor && window.Capacitor.Plugins;
  if (!plugins) return;
  plugins.DriveMode.clearAllData = async () => ({ ok: true });
  plugins.Filesystem = {
    async rmdir(options) {
      window.__rmdirCalls.push(options.path);
      if (options.path === "pothole-frames") {
        throw new Error("Permission denied: Documents/pothole-frames");
      }
      throw new Error("Directory does not exist");
    },
    async readdir() { throw new Error("Directory does not exist"); },
  };
})();
"""

fails = []
with sync_playwright() as playwright:
    browser, page, errors = open_flow(playwright, native=True, storage={"app_lang": "mr"})
    try:
        page.context.add_init_script(script=REFUSING_FILESYSTEM)
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.evaluate("""async () => {
          const db = await new Promise((resolve, reject) => {
            const request = indexedDB.open("potholes");
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
          });
          await new Promise((resolve, reject) => {
            const tx = db.transaction("reports", "readwrite");
            tx.objectStore("reports").put({ id: 9101, created_at: 1, status: "rejected",
              decision: "reject", photo: new Blob(["photo"]) });
            tx.oncomplete = resolve;
            tx.onabort = () => reject(tx.error);
          });
          db.close();
        }""")
        alerts = []
        page.on("dialog", lambda dialog: (
            alerts.append(dialog.message) if dialog.type == "alert" else None, dialog.accept()))
        expected = page.evaluate("t('wipe_frames_left', { dir: 'Documents/pothole-frames' })")
        page.evaluate("() => show('settings')")
        with page.expect_navigation(timeout=30_000):
            page.locator("#wipeBtn").click()
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.wait_for_timeout(1500)
        reports = page.evaluate("async () => (await StandaloneAPI.handle('/api/reports')).length")
        if reports:
            fails.append(f"History still holds {reports} report(s) after the wipe")
        if alerts != [expected]:
            fails.append(f"the tester was not told which folder is left: {alerts}")
        fails += error_failures(errors, "wipe with refused frame folder")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL wipe frames leftover")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS wipe frames leftover")
