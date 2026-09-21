# -*- coding: utf-8 -*-
"""A tester who picks Kannada, Marathi or Bengali must not meet English on screen.

Two leaks this guards, both seen on a phone set to English (India):
  - the Pothole map's Back button was hardcoded markup that applyLang never touched;
  - every History date followed the phone's locale, so a Marathi card read
    "21 Sept, 07:17 pm" in the middle of Marathi text.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow

fails = []
with sync_playwright() as playwright:
    for lang in ("kn", "mr", "bn"):
        browser, page, errors = open_flow(playwright, storage={"app_lang": lang})
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.wait_for_function("() => typeof applyLang === 'function'", timeout=30_000)

        page.evaluate("show('dash')")
        page.wait_for_timeout(300)
        seen = page.evaluate("""() => ({
          back: document.getElementById("dashBack").textContent.trim(),
          want: I18N[LANG].back,
          date: fmtDate(Date.UTC(2026, 8, 21, 13, 47) / 1000),
        })""")
        if seen["back"] != seen["want"]:
            fails.append(f"{lang}: Pothole map Back reads {seen['back']!r}, not {seen['want']!r}")
        # The context locale is en-IN, as on most Indian phones.
        for english in ("Sept", "Sep", "am", "pm", "AM", "PM"):
            if english in seen["date"]:
                fails.append(f"{lang}: date follows the phone locale: {seen['date']!r}")
                break
        fails += error_failures(errors, f"{lang} Pothole map")

        # Background Drive Mode controls: the buttons a driver taps must be in the
        # chosen language, in both states they toggle between.
        for paused, recording in ((False, False), (True, True)):
            controls = page.evaluate("""([paused, recording]) => {
              updateNativeDriveHud({ isPaused: paused, recordingEnabled: recording,
                                     cameraActive: true, found: 0, status: "" });
              const text = (id) => document.getElementById(id).textContent.trim();
              return {
                seen: [text("nativePauseBtn"), text("nativeRecordBtn"),
                       text("nativeDriveStop"), text("openMapsBtn")],
                want: [I18N[LANG][paused ? "native_resume" : "native_pause"],
                       I18N[LANG][recording ? "native_video_on" : "native_video_off"],
                       new DOMParser().parseFromString(I18N[LANG].stop, "text/html")
                         .body.textContent.trim(),
                       I18N[LANG].open_maps],
              };
            }""", [paused, recording])
            if controls["seen"] != controls["want"]:
                fails.append(f"{lang}: Drive Mode controls read {controls['seen']}, "
                             f"not {controls['want']}")
        browser.close()

if fails:
    print("FAIL i18n_screens")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS i18n_screens")
