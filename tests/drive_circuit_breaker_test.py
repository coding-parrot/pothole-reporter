# -*- coding: utf-8 -*-
"""A web drive stops uploading frames to a project server that keeps failing.

Non-fatal detect errors only changed the HUD to "Connection trouble, still scanning",
so a drive against a server answering 500 uploaded a 1280 px JPEG every few metres for
nothing, six at a time. After five failures in a row the drive now pauses its scans,
says so, and resumes once the server's health check answers again.

The drive context is the app's own drainDriveQueue/driveTick state with synthetic
frames, so the test needs no camera and no moving GPS.
"""

import sys

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, open_central, reply

mode = {"detect": "fail", "health": "down"}


def script(route, request, path, central):
    if path == "/v1/vision/detect" and mode["detect"] == "fail":
        return reply(route, 500, "internal_error", "The service could not complete this request.")
    if path == "/v1/health" and mode["health"] == "down":
        return reply(route, 500, "internal_error", "The service could not complete this request.")
    return False


SETUP = r"""async (count) => {
  window.__drivePausePollMs = 300;
  const canvas = document.createElement("canvas");
  canvas.width = 320; canvas.height = 240;
  const g = canvas.getContext("2d");
  g.fillStyle = "#777"; g.fillRect(0, 0, 320, 240);
  const frame = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", .8));
  const ctx = {
    stream: null, pos: null, lastCapPos: null,
    tally: { checked: 0, found: 0, already: 0, repaired: 0, captured: 0, dropped: 0 },
    duplicateIds: new Set(), queue: [], pending: new Set(), inFlight: 0, errors: 0,
    sessionId: "breaker-" + Date.now(), startedAt: Date.now(), captureSeq: 0,
    stillBusy: false, stopping: false, drained: false, readyAt: 0,
  };
  ctx.drainPromise = new Promise((resolve) => { ctx.resolveDrained = resolve; });
  drive = ctx;
  show("drive");
  for (let i = 0; i < count; i++) {
    enqueueDriveEvent(ctx, { frame, pos: { lat: 12.9716, lng: 77.5946 },
      capturedAt: Date.now(), sourceOffsetMs: i * 1000, captureSeq: ++ctx.captureSeq,
      gpsAccuracy: 4, speed: 8, heading: 90 });
  }
  window.__breakerCtx = ctx;
}"""

fails = []
with sync_playwright() as playwright:
    central = Central(script)
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_function("() => typeof enqueueDriveEvent === 'function'", timeout=30_000)
        page.wait_for_timeout(600)
        page.evaluate(SETUP, 20)
        page.wait_for_timeout(3000)
        detects = central.count("/v1/vision/detect")
        max_in_flight = page.evaluate("MAX_IN_FLIGHT")
        if detects > 5 + max_in_flight:
            fails.append(f"a failing server received {detects} frames; the drive never paused")
        state = page.evaluate("""() => ({ paused: !!window.__breakerCtx.paused,
          hud: document.getElementById('driveStatus').textContent, expected: t('drive_service_paused') })""")
        if not state["paused"] or state["hud"] != state["expected"]:
            fails.append(f"the drive did not say it paused: {state}")

        # Server back: the health poll resumes scanning and new frames are checked.
        mode["detect"] = "ok"
        mode["health"] = "ok"
        page.wait_for_function("() => !window.__breakerCtx.paused", timeout=10_000)
        before = central.count("/v1/vision/detect")
        page.evaluate("""async () => {
          const ctx = window.__breakerCtx;
          const canvas = document.createElement("canvas");
          canvas.width = 320; canvas.height = 240;
          canvas.getContext("2d").fillRect(0, 0, 320, 240);
          const frame = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", .8));
          enqueueDriveEvent(ctx, { frame, pos: { lat: 12.9716, lng: 77.5946 },
            capturedAt: Date.now(), sourceOffsetMs: 99000, captureSeq: ++ctx.captureSeq,
            gpsAccuracy: 4, speed: 8, heading: 90 });
        }""")
        page.wait_for_timeout(2000)
        if central.count("/v1/vision/detect") <= before:
            fails.append("scanning did not resume after the server came back")

        # Stop still finishes while paused work was dropped.
        page.evaluate("() => { window.__breakerCtx.stopping = true; finishDriveDrain(window.__breakerCtx); }")
        page.wait_for_function("() => window.__breakerCtx.drained", timeout=10_000)
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive circuit breaker")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive circuit breaker")
