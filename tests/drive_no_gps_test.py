# -*- coding: utf-8 -*-
"""A drive that never got a GPS fix says so when it stops.

Frames are sampled only with a position. With location silenced the HUD said "Still no
GPS fix after 30 s", and Stop went straight back Home with no dialog and no drive record:
the tester could not tell that nothing had been checked, or why. An immediate cancel
stays quiet, and a drive that did get a fix never shows this note.
"""

import sys

from playwright.sync_api import sync_playwright

from web_drive_harness import open_web_drive

SILENT_GPS = """
(() => {
  const proto = Object.getPrototypeOf(navigator.geolocation);
  proto.watchPosition = function () { return 1; };
  proto.clearWatch = function () {};
  proto.getCurrentPosition = function () {};
})();
"""


def drive(playwright, silent, seconds):
    browser, page, dialogs, errors = open_web_drive(playwright)
    try:
        if silent:
            page.context.add_init_script(script=SILENT_GPS)
            page.reload()
            page.wait_for_function("() => !!window.StandaloneAPI && window.api.__stubbed",
                                   timeout=30_000)
            page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.locator("#driveBtn").click()
        page.locator("#driveStop").wait_for(state="visible", timeout=30_000)
        page.wait_for_timeout(seconds * 1000)
        page.locator("#driveStop").click()
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.wait_for_timeout(1500)
        return list(dialogs), page.evaluate("t('drive_end_no_gps')"), errors
    finally:
        browser.close()


fails = []
with sync_playwright() as playwright:
    shown, note, errors = drive(playwright, silent=True, seconds=5)
    if len(shown) != 1 or "GPS" not in shown[0] or shown[0] != note:
        fails.append(f"a drive with no GPS fix ended with {shown}, want one alert {note!r}")
    fails += [f"no fix: page error {e}" for e in errors[:3]]

    shown, note, errors = drive(playwright, silent=True, seconds=1)
    if shown:
        fails.append(f"an immediate cancel nagged about GPS: {shown}")

    shown, note, errors = drive(playwright, silent=False, seconds=5)
    if note in shown:
        fails.append(f"a drive that had a fix still blamed GPS: {shown}")
    fails += [f"with fix: page error {e}" for e in errors[:3]]

if fails:
    print("FAIL drive no GPS")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive no GPS")
