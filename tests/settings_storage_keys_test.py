# -*- coding: utf-8 -*-
"""Saving Settings must not write settings the app never reads back.

The phone camera is the only capture source, so drive_capture_source was stored as a
constant on every Save and read nowhere. An older build may still have left one (or a
dashcam address) behind, and Save is where both get cleared. The old legacy-key list
went unreferenced when its check was removed; this pins that it stays gone.

keep_frames only ever mirrored Debug, which is what let the two drift apart on upgrade.
Frame export reads Debug itself now, so a copy an older build left is cleared at boot.
"""

import os
import sys

from playwright.sync_api import sync_playwright


APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")

failures = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--disable-web-security"])
    try:
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_init_script(
            """try {
              if (!sessionStorage.getItem("__seeded")) {
                sessionStorage.setItem("__seeded", "1");
                localStorage.setItem("drive_capture_source", "dashcam");
                localStorage.setItem("dashcam_rtsp_url", "rtsp://192.168.1.1/live");
                localStorage.setItem("debug_mode", "0");
                localStorage.setItem("keep_frames", "1");
              }
            } catch (e) {}
            window.alert = () => {};"""
        )
        page = context.new_page()
        page.goto(APP)
        page.wait_for_function(
            "typeof initialSettingsRequired === 'boolean' && document.getElementById('settings')",
            timeout=30_000,
        )
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.locator("#gearBtn").click()
        page.locator("#settings").wait_for(state="visible", timeout=30_000)
        page.locator("#setSave").click()
        page.wait_for_function("localStorage.getItem('initial_setup_complete') === '1'",
                               timeout=30_000)

        state = page.evaluate(
            """() => ({
              captureSource: localStorage.getItem("drive_capture_source"),
              rtsp: localStorage.getItem("dashcam_rtsp_url"),
              legacyList: typeof LEGACY_SETTINGS_KEYS !== "undefined",
              keepFramesKey: localStorage.getItem("keep_frames"),
              keepFrames: keepFrames(),
              keys: Object.keys(localStorage).sort(),
            })"""
        )
        if state["captureSource"] is not None:
            failures.append(f"Save still stores drive_capture_source: {state['captureSource']!r}")
        if state["rtsp"] is not None:
            failures.append("Save left an older build's dashcam address in storage")
        if state["legacyList"]:
            failures.append("the unreferenced LEGACY_SETTINGS_KEYS list is still defined")
        if state["keepFramesKey"] is not None:
            failures.append(f"keep_frames is still stored beside debug_mode: {state['keepFramesKey']!r}")
        if state["keepFrames"]:
            failures.append("frame export is on while Debug is off")
        print("keys after Save:", ", ".join(state["keys"]))
    finally:
        browser.close()

if failures:
    for failure in failures:
        print("FAIL:", failure)
    sys.exit(1)
print("SETTINGS STORAGE KEYS TEST PASS")
