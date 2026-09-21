#!/usr/bin/env python3
"""Debug mode is the one switch for kept video and exported frames.

Builds before 1.39.1 had separate Record video and Save frames boxes. 1.39.1 replaced
them with Debug but did not migrate, so an upgraded phone kept recording (about 18 MB
per Drive minute) while Settings showed Debug off. What Settings shows must be what the
app does, from the first boot after the upgrade.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow


PROBE = """async () => {
  const canvas = document.createElement("canvas");
  canvas.width = 32; canvas.height = 24;
  const stream = canvas.captureStream(5);
  startRecording(stream, "debug-switch-drive", Date.now());
  const recording = !!recCtx;
  if (recCtx) { recCtx.active = false; try { recCtx.recorder && recCtx.recorder.stop(); } catch (_) {} }
  stream.getTracks().forEach((track) => track.stop());
  recCtx = null;
  return {
    recording, keepFrames: keepFrames(), debug: isDebug(),
    settingsBox: (openSettings(), $("setDebug").checked),
  };
}"""


def boot(playwright, storage):
    browser, page, errors = open_flow(playwright, native=False, storage=storage)
    try:
        page.wait_for_function("() => typeof startRecording === 'function'")
        return page.evaluate(PROBE), errors
    finally:
        browser.close()


failures = []
with sync_playwright() as playwright:
    legacy_on, errors = boot(playwright, {"debug_mode": "0", "record_video": "1",
                                          "keep_frames": "1"})
    failures += error_failures(errors, "legacy keys on, Debug off")
    unset, errors = boot(playwright, {"record_video": "1", "keep_frames": "1"})
    failures += error_failures(errors, "legacy keys on, Debug never set")
    debug_on, errors = boot(playwright, {"debug_mode": "1", "record_video": "0",
                                         "keep_frames": "0"})
    failures += error_failures(errors, "Debug on")

for name, state in (("Debug off", legacy_on), ("Debug unset", unset)):
    if state["settingsBox"]:
        failures.append(f"{name}: Settings shows Debug on: {state}")
    if state["recording"] or state["keepFrames"]:
        failures.append(f"{name}: recording or frame export still on while Settings shows Debug off: {state}")
if not (debug_on["settingsBox"] and debug_on["recording"] and debug_on["keepFrames"]):
    failures.append(f"Debug on did not record and export frames: {debug_on}")

if failures:
    print("DEBUG SWITCH SINGLE SOURCE TEST FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("DEBUG SWITCH SINGLE SOURCE TEST PASS")
