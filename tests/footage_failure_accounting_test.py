# -*- coding: utf-8 -*-
"""Saved-video failures stay visible, pending, and recoverable.

This is offline: Chromium creates one local WebM and the model boundary is mocked.
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright


APP = "http://localhost:8765/"
failures = []
INDEX = (pathlib.Path(__file__).resolve().parents[1] / "static" / "index.html").read_text()


def check(label, condition, detail=None):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(f"{label}: {detail!r}")


check("every saved-video metadata wait is bounded",
      "function waitForVodMetadata(video, timeoutMs)" in INDEX
      and INDEX.count("await waitForVodMetadata(") == 3)
check("primary and retry extraction both fail closed",
      "if (slot.ready) burst = await grabVodBurst" in INDEX
      and "try { burst = await retryVodBurst(clip, at); } catch (e) {}" in INDEX)


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=[
        "--disable-web-security", "--allow-running-insecure-content",
        "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
    ])
    page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function("typeof analyseFootage === 'function'", timeout=30000)

    result = page.evaluate("""async () => {
      localStorage.removeItem("debug_mode");
      localStorage.removeItem("keep_frames");
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {width: 320, height: 240}, audio: false,
      });
      const mime = ["video/webm;codecs=vp8", "video/webm"]
        .find((value) => MediaRecorder.isTypeSupported(value));
      if (!mime) throw new Error("No WebM MediaRecorder in test Chromium");
      const recorder = new MediaRecorder(stream, {mimeType: mime});
      const parts = [];
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size) parts.push(event.data);
      };
      recorder.start();
      await new Promise((resolve) => setTimeout(resolve, 1200));
      await new Promise((resolve) => { recorder.onstop = resolve; recorder.stop(); });
      stream.getTracks().forEach((track) => track.stop());
      const validClip = new Blob(parts, {type: mime});
      if (!validClip.size) throw new Error("Test recorder returned an empty clip");

      async function store(driveId, seq, blob) {
        const form = new FormData();
        form.append("segment", blob, `clip-${seq}.webm`);
        form.append("drive_id", driveId);
        form.append("seq", String(seq));
        await StandaloneAPI.handle("/api/footage", {method:"POST", body:form});
      }
      async function inspect(driveId) {
        const footage = (await StandaloneAPI.handle("/api/footage"))
          .find((item) => String(item.drive_id) === String(driveId));
        const drive = (await StandaloneAPI.handle("/api/drives"))
          .find((item) => String(item.id) === String(driveId));
        return {footage: footage || null, drive: drive || null};
      }
      async function analyse(driveId, frameResult, nullCanvas) {
        const originalApi = window.api;
        const originalToBlob = HTMLCanvasElement.prototype.toBlob;
        let frameCalls = 0;
        window.api = async (path, opts) => {
          if (path === "/api/frame") {
            frameCalls++;
            return typeof frameResult === "function" ? frameResult() : frameResult;
          }
          return originalApi(path, opts);
        };
        if (nullCanvas) {
          HTMLCanvasElement.prototype.toBlob = function(callback) { callback(null); };
        }
        window.__accountingAlert = "";
        window.alert = (message) => { window.__accountingAlert = String(message); };
        window.confirm = () => false;
        VOD_STEP_S = 10;
        try {
          await analyseFootage(driveId, {gps_track:[]});
          return {frameCalls, alert: window.__accountingAlert, ...(await inspect(driveId))};
        } finally {
          window.api = originalApi;
          HTMLCanvasElement.prototype.toBlob = originalToBlob;
        }
      }

      await store("analyzed-false", 0, validClip);
      const analyzedFalse = await analyse("analyzed-false", {analyzed:false}, false);

      await store("decode-failure", 0, validClip);
      const decodeFailure = await analyse("decode-failure", {analyzed:true}, true);

      await store("mixed-clips", 0, validClip);
      await store("mixed-clips", 1, new Blob(["not a video"], {type:"video/webm"}));
      const acceptedNo = {
        analyzed:true, accepted:false, stored:false, found:false,
        duplicate:false, decision:"reject",
      };
      await store("all-unreadable", 0,
        new Blob(["not a video"], {type:"video/webm"}));
      const allUnreadable = await analyse("all-unreadable", acceptedNo, false);
      const mixedClips = await analyse("mixed-clips", acceptedNo, false);

      await store("complete-run", 0, validClip);
      const completeRun = await analyse("complete-run", acceptedNo, false);
      return {analyzedFalse, decodeFailure, allUnreadable, mixedClips, completeRun};
    }""")
    browser.close()


analyzed_false = result["analyzedFalse"]
check("{analyzed:false} never increments checked",
      analyzed_false["drive"].get("analysis_checked") == 0, analyzed_false)
check("{analyzed:false} records a failed window",
      analyzed_false["drive"].get("analysis_failed") == 1
      and analyzed_false["drive"].get("analysis_complete") is False, analyzed_false)
check("{analyzed:false} keeps the footage",
      analyzed_false.get("footage") is not None, analyzed_false)

decode_failure = result["decodeFailure"]
check("null canvas output never reaches the model",
      decode_failure.get("frameCalls") == 0, decode_failure)
check("null canvas output is failed, not checked",
      decode_failure["drive"].get("analysis_extracted") == 0
      and decode_failure["drive"].get("analysis_checked") == 0
      and decode_failure["drive"].get("analysis_failed") == 1, decode_failure)
check("decode failure keeps the footage",
      decode_failure.get("footage") is not None, decode_failure)

all_unreadable = result["allUnreadable"]
check("an all-unreadable run never calls the detector",
      all_unreadable.get("frameCalls") == 0, all_unreadable)
check("an all-unreadable run persists its incomplete state",
      all_unreadable["drive"].get("analysis_planned") == 0
      and all_unreadable["drive"].get("analysis_checked") == 0
      and all_unreadable["drive"].get("analysis_unreadable_clips") == 1
      and all_unreadable["drive"].get("analysis_complete") is False,
      all_unreadable)
check("an all-unreadable run keeps the footage",
      all_unreadable.get("footage") is not None, all_unreadable)

mixed = result["mixedClips"]
check("one unreadable clip is persisted in analysis truth",
      mixed["drive"].get("analysis_unreadable_clips") == 1
      and mixed["drive"].get("analysis_complete") is False, mixed)
check("mixed readable/unreadable footage is retained",
      mixed.get("footage") is not None, mixed)

complete = result["completeRun"]
check("a fully checked run is marked complete",
      complete["drive"].get("analysis_checked") == complete["drive"].get("analysis_planned")
      and complete["drive"].get("analysis_complete") is True, complete)
check("only the complete non-debug run deletes footage",
      complete.get("footage") is None, complete)

if failures:
    print("\nFAILED")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)
print("footage failure accounting tests passed")
