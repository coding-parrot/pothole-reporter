# -*- coding: utf-8 -*-
"""A drive that runs the phone out of storage stops, says so, and spends no more checks.

A full phone used to leave the drive scanning: every later frame still spent one of the
install's shared detections, none could be saved, the HUD kept reading "Scanning", and
the summary said the frames "could not be checked because of the connection". Here the
IndexedDB transactions start aborting with QuotaExceededError (the way a real quota
failure arrives: the write succeeds, then the commit aborts) after a few frames.
"""

import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import flow_harness as fh
from web_drive_harness import open_web_drive, wait_for_dialog

detects = []

# Once window.__full is set, every readwrite transaction aborts at commit with a quota
# error, which is what Chromium reports when the origin is over its quota.
QUOTA = r"""
(() => {
  for (const method of ["put", "add"]) {
    const original = IDBObjectStore.prototype[method];
    IDBObjectStore.prototype[method] = function (...args) {
      const request = original.apply(this, args);
      if (window.__full) {
        const tx = this.transaction;
        request.addEventListener("success", () => {
          Object.defineProperty(tx, "error", {
            configurable: true,
            value: new DOMException("The quota has been exceeded.", "QuotaExceededError"),
          });
          try { tx.abort(); } catch (_) {}
        });
      }
      return request;
    };
  }
})();
"""


def service(route, request):
    if urlparse(request.url).path == "/v1/vision/detect":
        detects.append(1)
    return fh.central_service(route, request)


fails = []
with sync_playwright() as playwright:
    browser, page, dialogs, errors = open_web_drive(playwright, service=service,
                                                    stub_frames=False)
    try:
        page.evaluate(QUOTA)
        page.locator("#driveBtn").click()
        page.wait_for_function("() => drive && drive.tally.checked >= 2", timeout=60_000)
        page.evaluate("() => { window.__full = true; }")
        at_full = len(detects)
        if not wait_for_dialog(page, dialogs, 1, 40):
            fails.append("the drive kept scanning after the phone ran out of storage")
        page.wait_for_timeout(4000)
        after = len(detects) - at_full
        # Frames already sent when storage filled may still come back; nothing new may go.
        if after > 6:
            fails.append(f"{after} more shared detections were spent after storage filled")
        if page.evaluate("() => !!drive"):
            fails.append("the drive is still running")
        joined = " | ".join(dialogs)
        if "out of storage" not in joined:
            fails.append(f"the out-of-storage message was never shown: {dialogs}")
        if "because of the connection" in joined:
            fails.append(f"the summary blames the connection for a full phone: {dialogs}")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive storage full")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive storage full")
