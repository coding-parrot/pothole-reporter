# -*- coding: utf-8 -*-
"""A phone held upright is not a low-resolution camera.

The drive asks the camera for 1280 x 720 and warns on the HUD when it gets less. A phone
in portrait hands the same frame back turned: 720 wide, 1280 tall. The check looked at
the width alone, so on every real phone tried on AWS Device Farm (Pixel 8a, Galaxy A13 5G,
Galaxy A15, Redmi Note 13; 7 Oct 2026) the HUD said "camera only 720px / 30fps" for the
whole drive while the camera was delivering exactly what was asked. The desktop fake
camera is landscape, so no suite saw it.

The long side is what the request was about. A camera that really is small (480 x 640)
still gets the warning, and the warning names its long side.
"""

import sys

from playwright.sync_api import sync_playwright

from web_drive_harness import open_web_drive


def hud_for(playwright, width, height):
    browser, page, dialogs, errors = open_web_drive(playwright)
    try:
        page.context.add_init_script(script="""
        (() => {
          const real = MediaStreamTrack.prototype.getSettings;
          MediaStreamTrack.prototype.getSettings = function () {
            return { ...real.call(this), width: %d, height: %d, frameRate: 30 };
          };
        })();""" % (width, height))
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI && window.api.__stubbed", timeout=30_000)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.locator("#driveBtn").click()
        # The scanning line is the one that carries the warning; wait until it is up.
        page.wait_for_function(
            "() => /\\d+ checked/.test(document.getElementById('driveStatus').textContent)",
            timeout=30_000)
        text = page.evaluate("document.getElementById('driveStatus').textContent")
        page.locator("#driveStop").click()
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        return text, errors
    finally:
        browser.close()


fails = []
with sync_playwright() as playwright:
    text, errors = hud_for(playwright, 720, 1280)
    if "camera only" in text:
        fails.append(f"a 720 x 1280 portrait camera was called low resolution: {text!r}")
    fails += [f"portrait: page error {e}" for e in errors[:3]]

    text, errors = hud_for(playwright, 1280, 720)
    if "camera only" in text:
        fails.append(f"a 1280 x 720 camera was called low resolution: {text!r}")

    text, errors = hud_for(playwright, 480, 640)
    if "camera only 640px" not in text:
        fails.append(f"a 480 x 640 camera was not flagged by its long side: {text!r}")

if fails:
    print("FAIL drive portrait camera warning")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive portrait camera warning")
