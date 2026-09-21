# -*- coding: utf-8 -*-
"""Drive says location is off when the location permission is refused.

The drive's watchPosition error callback was empty, so a refused permission (which will
never produce a fix) read "Waiting for GPS..." for as long as the tester waited, with
no frame ever checked and nothing saying why. The status line now names the refusal.
"""

import sys

from playwright.sync_api import sync_playwright

from web_drive_harness import open_web_drive

DENY_GEO = """
(() => {
  const proto = Object.getPrototypeOf(navigator.geolocation);
  proto.watchPosition = function (ok, fail) {
    setTimeout(() => fail && fail({ code: 1, PERMISSION_DENIED: 1, message: "User denied Geolocation" }), 50);
    return 7;
  };
  proto.getCurrentPosition = function (ok, fail) {
    setTimeout(() => fail && fail({ code: 1, PERMISSION_DENIED: 1, message: "User denied Geolocation" }), 5);
  };
})();
"""

fails = []
with sync_playwright() as playwright:
    browser, page, dialogs, errors = open_web_drive(playwright)
    try:
        page.context.add_init_script(script=DENY_GEO)
        page.reload()
        page.wait_for_function("() => !!(window.api && window.api.__stubbed)", timeout=30_000)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.locator("#driveBtn").click()
        page.wait_for_function("() => drive && drive.watchId != null", timeout=30_000)
        expected = page.evaluate("t('location_off')")
        try:
            page.wait_for_function("(text) => document.getElementById('driveStatus').textContent"
                                   " === text", arg=expected, timeout=2_000)
        except Exception:
            fails.append("location refused, but the line reads: "
                         + repr(page.locator("#driveStatus").text_content()))
        # It stays put rather than flipping back to "Waiting for GPS..." on the next tick.
        page.wait_for_timeout(2000)
        if page.locator("#driveStatus").text_content() != expected:
            fails.append("the location-off line was replaced by: "
                         + repr(page.locator("#driveStatus").text_content()))
        page.locator("#driveStop").click()
        page.wait_for_function("() => !drive && !driveFinalizing", timeout=30_000)
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive location denied")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive location denied")
