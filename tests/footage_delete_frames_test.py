# -*- coding: utf-8 -*-
"""Delete footage must also remove that drive's exported debug frames.

A debug analysis writes every checked frame, about 500 KB each, plus a manifest with the
GPS of each frame, into Documents/pothole-frames/<drive>/ in public storage, which
survives an uninstall. Delete footage removed only the video, so the frames stayed until
Delete all. The Android Filesystem plugin is stubbed at the page boundary.
"""
import sys

from playwright.sync_api import sync_playwright

from browser_test_utils import OFFLINE_KEY, open_app


JS = r"""
async (driveId) => {
  const realHandle = StandaloneAPI.handle;
  await realHandle("/api/reports", { method: "DELETE" });
  const clip = new FormData();
  clip.append("segment", new Blob([new Uint8Array(256)], { type: "video/webm" }), "clip.webm");
  clip.append("drive_id", driveId);
  clip.append("seq", "0");
  await realHandle("/api/footage", { method: "POST", body: clip });
  await loadReports();
  const head = document.querySelector(`[data-drive="${CSS.escape(driveId)}"]`);
  if (head) head.click();
  const button = document.querySelector(`[data-delfootage="${CSS.escape(driveId)}"]`);
  if (!button) return { error: "no Delete footage button" };

  const removed = [];
  window.Capacitor = { Plugins: { Filesystem: {
    rmdir: async (options) => { removed.push(options); },
    readdir: async () => { throw new Error("Directory does not exist."); },
  } } };
  const alerts = [];
  window.alert = (text) => alerts.push(String(text));
  window.confirm = () => true;
  button.click();
  const deadline = Date.now() + 5000;
  let footage = [];
  while (Date.now() < deadline) {
    footage = await realHandle("/api/footage");
    if (!footage.length && removed.length) break;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  await new Promise((resolve) => setTimeout(resolve, 200));
  delete window.Capacitor;
  await realHandle("/api/reports", { method: "DELETE" });
  return { removed, alerts, footage: footage.length };
}
"""


fails = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        open_app(page, OFFLINE_KEY)
        page.evaluate("localStorage.setItem('data_notice_version', DATA_NOTICE_VERSION)")
        normal = page.evaluate(JS, "drive-frames-1")
        hostile = page.evaluate(JS, "../escape")
    finally:
        browser.close()

print(f"  normal : {normal}")
print(f"  hostile: {hostile}")
wanted = {"path": "pothole-frames/drive-frames-1", "directory": "DOCUMENTS", "recursive": True}
if normal.get("footage") != 0:
    fails.append(f"the footage was not deleted: {normal}")
if wanted not in normal.get("removed", []):
    fails.append(f"Delete footage left the drive's exported frames: {normal}")
if normal.get("alerts"):
    fails.append(f"Delete footage alerted: {normal}")
if any("escape" in str(item.get("path")) or item.get("path") == "pothole-frames"
       for item in hostile.get("removed", [])):
    fails.append(f"a drive id that is not a plain name reached rmdir: {hostile}")

if fails:
    print("FOOTAGE DELETE FRAMES TEST FAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("FOOTAGE DELETE FRAMES TEST PASS")
