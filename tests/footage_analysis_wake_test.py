# -*- coding: utf-8 -*-
"""Saved-drive analysis keeps the screen on and pauses cleanly while the app is hidden.

Analysing a long drive takes minutes. The imported-video path held a screen wake lock
but the saved-drive path did not, so the phone dimmed, locked, and Android froze or
killed the WebView partway through with nothing on screen saying to keep it open.
Here the analysis runs against a real recorded clip with the detector mocked, the page
is hidden in the middle, and the test reads the wake lock, the detector calls made
while hidden, and the progress line.
"""

import os
import sys

from playwright.sync_api import sync_playwright

APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")

fails = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=[
        "--disable-web-security", "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream"])
    try:
        page = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True,
                                   device_scale_factor=2.625).new_page()
        page.goto(APP)
        page.wait_for_function("typeof analyseFootage === 'function'", timeout=30_000)
        result = page.evaluate("""async () => {
          localStorage.removeItem("debug_mode");
          localStorage.removeItem("keep_frames");
          const stream = await navigator.mediaDevices.getUserMedia({
            video: { width: 320, height: 240 }, audio: false });
          const mime = ["video/webm;codecs=vp8", "video/webm"]
            .find((value) => MediaRecorder.isTypeSupported(value));
          const recorder = new MediaRecorder(stream, { mimeType: mime });
          const parts = [];
          recorder.ondataavailable = (event) => { if (event.data && event.data.size) parts.push(event.data); };
          recorder.start();
          await new Promise((resolve) => setTimeout(resolve, 1500));
          await new Promise((resolve) => { recorder.onstop = resolve; recorder.stop(); });
          stream.getTracks().forEach((track) => track.stop());
          const form = new FormData();
          form.append("segment", new Blob(parts, { type: mime }), "clip-0.webm");
          form.append("drive_id", "wake-drive");
          form.append("seq", "0");
          await StandaloneAPI.handle("/api/footage", { method: "POST", body: form });

          let hidden = false;
          Object.defineProperty(document, "visibilityState", { configurable: true,
            get: () => hidden ? "hidden" : "visible" });
          Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
          const setHidden = (value) => {
            hidden = value;
            document.dispatchEvent(new Event("visibilitychange"));
          };
          const locks = { requested: 0, released: 0, held: 0 };
          Object.defineProperty(navigator, "wakeLock", { configurable: true, value: {
            async request(type) {
              locks.requested++;
              locks.held++;
              let released = false;
              return { type, release: async () => {
                if (released) return;
                released = true;
                locks.released++;
                locks.held--;
              }, addEventListener() {} };
            },
          } });
          const original = window.api;
          let calls = 0, callsWhileHidden = 0, paused = false;
          let hint = "";
          window.api = async (path, opts) => {
            if (path !== "/api/frame") return original(path, opts);
            calls++;
            if (hidden) callsWhileHidden++;
            hint = document.getElementById("progressText").textContent;
            await new Promise((resolve) => setTimeout(resolve, 150));
            if (calls >= 2 && !paused) {
              paused = true;
              setHidden(true);
              setTimeout(() => setHidden(false), 2000);
            }
            return { analyzed: true, accepted: false, stored: false, found: false,
                     duplicate: false, decision: "reject" };
          };
          window.alert = () => {};
          window.confirm = () => false;
          VOD_STEP_S = 0.05;
          try {
            await analyseFootage("wake-drive", { gps_track: [] });
          } finally {
            window.api = original;
          }
          const drive = (await StandaloneAPI.handle("/api/drives"))
            .find((item) => String(item.id) === "wake-drive") || {};
          return { locks, calls, callsWhileHidden, hint, keepOpen: t("analysis_keep_open"),
                   checked: drive.analysis_checked, planned: drive.analysis_planned,
                   complete: drive.analysis_complete };
        }""")
    except Exception as error:
        result = None
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if result:
    if result["locks"]["requested"] < 2:
        fails.append(f"the screen was not held, or not held again after the pause: {result['locks']}")
    if result["locks"]["held"] != 0:
        fails.append(f"a wake lock outlived the analysis: {result['locks']}")
    # A seeker that already holds a decoded frame when the page hides may still send it
    # (three seekers in desktop Chromium); nothing new may start until it is visible.
    if result["callsWhileHidden"] > 3:
        fails.append(f"{result['callsWhileHidden']} frames were sent while the app was hidden")
    if result["keepOpen"] not in result["hint"]:
        fails.append(f"the progress screen does not say to keep the app open: {result['hint']!r}")
    if not result["complete"] or result["checked"] != result["planned"] or result["calls"] < 5:
        fails.append(f"the analysis did not finish at full rate after the pause: {result}")

if fails:
    print("FAIL footage analysis wake")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS footage analysis wake")
