# -*- coding: utf-8 -*-
"""A debug frame export that stops part way must not be reported as a finished export.

Once one write fails (a full disk, most often) analyseFootage stops writing frames, but
the summary said "N frames written" whenever any had been, so a partial export on a full
phone read as success. No external service is contacted: the detector and the Android
Filesystem plugin are stubbed at the page boundary.
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright


APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")
fails = []

RUN = """async (failAfter) => {
  await StandaloneAPI.handle("/api/reports", { method: "DELETE" });
  localStorage.setItem("debug_mode", "1");
  localStorage.setItem("keep_frames", "1");

  const stream = await navigator.mediaDevices.getUserMedia({
    video: { width: 320, height: 240 }, audio: false,
  });
  const mime = ["video/webm;codecs=vp8", "video/webm"]
    .find((type) => MediaRecorder.isTypeSupported(type));
  const recorder = new MediaRecorder(stream, { mimeType: mime });
  const parts = [];
  recorder.ondataavailable = (event) => { if (event.data && event.data.size) parts.push(event.data); };
  recorder.start();
  await new Promise((resolve) => setTimeout(resolve, 1200));
  await new Promise((resolve) => { recorder.onstop = resolve; recorder.stop(); });
  stream.getTracks().forEach((track) => track.stop());
  const blob = new Blob(parts, { type: mime });

  const driveId = `partial-export-${failAfter}`;
  const base = 1800000000000;
  for (let seq = 0; seq < 4; seq++) {
    const fd = new FormData();
    fd.append("segment", blob, `clip-${seq}.webm`);
    fd.append("drive_id", driveId);
    fd.append("seq", String(seq));
    fd.append("recording_started_at_ms", String(base + seq * 2000));
    fd.append("source_offset_ms", String(seq * 2000));
    await StandaloneAPI.handle("/api/footage", { method: "POST", body: fd });
  }

  const writes = [];
  window.Capacitor = { isNativePlatform: () => false, Plugins: { Filesystem: {
    async writeFile(options) {
      const frames = writes.filter((path) => /frame-\\d+\\.jpg$/.test(path)).length;
      if (/frame-\\d+\\.jpg$/.test(options.path) && frames >= failAfter) {
        throw new Error("simulated disk full");
      }
      writes.push(options.path);
      return { uri: options.path };
    },
  } } };

  const originalApi = window.api;
  window.api = async (path, opts) => {
    if (path === "/api/frame") {
      const observation = { image_quality: "acceptable", assessment: "undamaged",
        damage_type: null, size: null, description: "The road is intact." };
      return { analyzed: true, accepted: false, stored: false, found: false,
        duplicate: false, duplicate_of: null, decision: "reject", review: false,
        ...observation, observation,
        detector: { model: "test", detail: "high", prompt_version: "test" } };
    }
    return originalApi(path, opts);
  };
  let message = "";
  window.alert = (text) => { message = String(text); };
  window.confirm = () => false;
  VOD_STEP_S = 10; // one sample from each short clip
  await analyseFootage(driveId, { started_at: base / 1000, gps_track: [] });
  window.api = originalApi;
  delete window.Capacitor;
  return { message, frames: writes.filter((path) => /frame-\\d+\\.jpg$/.test(path)).length };
}"""


with sync_playwright() as p:
    browser = p.chromium.launch(args=[
        "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
    ])
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script("localStorage.setItem('vision_provider', 'shared')")

    def block_remote(route):
        url = route.request.url
        if url.startswith(APP) or url.startswith("blob:") or url.startswith("data:"):
            route.continue_()
        elif url.endswith("/v1/health"):
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "request_id": "test-health", "ok": True, "shared_vision_configured": True}))
        else:
            route.abort()

    context.route("**/*", block_remote)
    page = context.new_page()
    page.goto(APP)
    page.wait_for_function("typeof StandaloneAPI !== 'undefined'", timeout=30000)
    try:
        partial = page.evaluate(RUN, 2)
        complete = page.evaluate(RUN, 100)
    finally:
        context.close()
        browser.close()

if partial["frames"] != 2:
    fails.append(f"stub did not stop after 2 frames: {partial}")
elif "2 frames written" in partial["message"] and "stopped" not in partial["message"]:
    fails.append(f"a partial export was reported as finished: {partial['message']!r}")
elif "stopped" not in partial["message"]:
    fails.append(f"a partial export did not say it stopped: {partial['message']!r}")
if complete["frames"] != 4 or "4 frames written" not in complete["message"] \
        or "stopped" in complete["message"]:
    fails.append(f"a complete export was not reported as written: {complete}")

print(f"  partial : {partial['message']!r}")
print(f"  complete: {complete['message']!r}")
if fails:
    print("\nFRAME EXPORT PARTIAL TEST FAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("\nFRAME EXPORT PARTIAL TEST PASS")
