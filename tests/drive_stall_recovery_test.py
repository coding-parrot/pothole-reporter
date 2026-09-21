# -*- coding: utf-8 -*-
"""A short network stall does not lock live Drive up long after the network is back.

Each of the six in-flight slots used to wait out the shared client's 100 s timeout,
although API Gateway and the Lambda give up at 29 s, so nothing real was being waited
for. A 10 s tunnel held every slot for about 100 s and every new frame was dropped.
Drive frames now give up just past the server's own ceiling, so new frames are checked
again within about half a minute of the stall ending.

The stall is an in-page fetch that never answers until its request is aborted, like a
dead cell. The drive context is the app's own drainDriveQueue state with synthetic
frames, as in the circuit breaker suite.
"""

import sys
import time

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, open_central

SETUP = r"""async () => {
  window.__drivePausePollMs = 300;
  window.__stall = true;
  const realFetch = window.fetch;
  window.fetch = (url, init) => {
    if (window.__stall && String(url).includes("/v1/vision/detect")) {
      return new Promise((_, reject) => {
        const signal = init && init.signal;
        if (signal) signal.addEventListener("abort",
          () => reject(new DOMException("The operation was aborted.", "AbortError")));
      });
    }
    return realFetch(url, init);
  };
  const canvas = document.createElement("canvas");
  canvas.width = 320; canvas.height = 240;
  const g = canvas.getContext("2d");
  g.fillStyle = "#777"; g.fillRect(0, 0, 320, 240);
  window.__frame = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", .8));
  const ctx = {
    stream: null, pos: null, lastCapPos: null,
    tally: { checked: 0, found: 0, already: 0, repaired: 0, captured: 0, dropped: 0 },
    duplicateIds: new Set(), queue: [], pending: new Set(), inFlight: 0, errors: 0,
    sessionId: "stall-" + Date.now(), startedAt: Date.now(), captureSeq: 0,
    stillBusy: false, stopping: false, drained: false, readyAt: 0,
  };
  ctx.drainPromise = new Promise((resolve) => { ctx.resolveDrained = resolve; });
  drive = ctx;
  show("drive");
  window.__stallCtx = ctx;
  // A frame a second, like the live capture loop.
  window.__feed = setInterval(() => {
    if (drive !== ctx) return;
    enqueueDriveEvent(ctx, { frame: window.__frame, pos: { lat: 12.9716, lng: 77.5946 },
      capturedAt: Date.now(), sourceOffsetMs: ctx.captureSeq * 1000,
      captureSeq: ++ctx.captureSeq, gpsAccuracy: 4, speed: 8, heading: 90 });
  }, 1000);
}"""

fails = []
with sync_playwright() as playwright:
    central = Central()
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_function("() => typeof enqueueDriveEvent === 'function'", timeout=30_000)
        page.wait_for_timeout(600)
        page.evaluate(SETUP)
        page.wait_for_timeout(10_000)
        if page.evaluate("window.__stallCtx.tally.checked"):
            fails.append("a frame was checked during the stall; the stub did not hold requests")
        page.evaluate("() => { window.__stall = false; }")
        released = time.time()
        try:
            page.wait_for_function("() => window.__stallCtx.tally.checked > 0", timeout=35_000,
                                   polling=500)
        except Exception:
            state = page.evaluate("""() => ({ inFlight: window.__stallCtx.inFlight,
              queued: window.__stallCtx.queue.length, dropped: window.__stallCtx.tally.dropped,
              hud: document.getElementById('driveStatus').textContent })""")
            fails.append(f"no frame was checked within 35 s of the network coming back: {state}")
        else:
            print(f"first check {time.time() - released:.1f} s after the stall ended")
        page.evaluate("() => { clearInterval(window.__feed); drive = null; }")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive stall recovery")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive stall recovery")
