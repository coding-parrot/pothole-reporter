# -*- coding: utf-8 -*-
"""One transient 503 from the shared detector does not end a Drive.

The service answers 503 shared_vision_unavailable for a single OpenAI 5xx or a failed
fetch to OpenAI, which clears on the next frame. The client marked every 503 fatal, so
one hiccup stopped the drive with "Shared vision is temporarily unavailable." A lost
network, by contrast, only showed connection trouble. The transient code is now treated
like a network error (the circuit breaker still pauses a detector that keeps failing),
while a 503 that lasts the session, such as shared_credits_exhausted, still ends it.
A 503 or 429 with no service error code is API Gateway shedding load, not the service
speaking, and is a hiccup too.
"""

import sys

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, open_central, reply

mode = {"detect": "ok"}


def script(route, request, path, central):
    if path != "/v1/vision/detect" or mode["detect"] == "ok":
        return False
    if mode["detect"] == "transient_once":
        mode["detect"] = "ok"
        return reply(route, 503, "shared_vision_unavailable",
                     "Shared vision is temporarily unavailable.")
    if mode["detect"] == "retryable_once":
        # Any 503 the server marks retryable, whatever its code, is a hiccup too.
        mode["detect"] = "ok"
        return reply(route, 503, "vision_upstream_busy",
                     "The detector is busy; retry shortly.", {"retryable": True})
    if mode["detect"] in ("gateway_503_once", "gateway_429_once"):
        # API Gateway's own answer when the Lambda is throttled or the stage limit trips:
        # no service error code at all. One emulator drive holding six frames in flight
        # against four reserved Lambda slots produced exactly this, and the drive ended
        # with "The project server had a problem."
        status = 503 if mode["detect"] == "gateway_503_once" else 429
        mode["detect"] = "ok"
        route.fulfill(status=status, content_type="application/json",
                      body='{"message":"Service Unavailable"}' if status == 503
                      else '{"message":"Too Many Requests"}')
        return True
    return reply(route, 503, "shared_credits_exhausted",
                 "The shared vision credits are exhausted.")


SETUP = r"""async () => {
  const canvas = document.createElement("canvas");
  canvas.width = 320; canvas.height = 240;
  const g = canvas.getContext("2d");
  g.fillStyle = "#777"; g.fillRect(0, 0, 320, 240);
  window.__frame = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", .8));
  const ctx = {
    stream: { getTracks: () => [] }, pos: null, lastCapPos: null,
    tally: { checked: 0, found: 0, already: 0, repaired: 0, captured: 0, dropped: 0 },
    duplicateIds: new Set(), queue: [], pending: new Set(), inFlight: 0, errors: 0,
    sessionId: "transient-" + Date.now(), startedAt: Date.now(), captureSeq: 0,
    stillBusy: false, stopping: false, drained: false, readyAt: 0,
  };
  ctx.drainPromise = new Promise((resolve) => { ctx.resolveDrained = resolve; });
  drive = ctx;
  show("drive");
  window.__ctx503 = ctx;
  window.__enqueue = async (n) => {
    for (let i = 0; i < n; i++) {
      enqueueDriveEvent(ctx, { frame: window.__frame, pos: { lat: 12.9716, lng: 77.5946 },
        capturedAt: Date.now(), sourceOffsetMs: ctx.captureSeq * 1000,
        captureSeq: ++ctx.captureSeq, gpsAccuracy: 4, speed: 8, heading: 90 });
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
  };
}"""

fails = []
with sync_playwright() as playwright:
    central = Central(script)
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_function("() => typeof enqueueDriveEvent === 'function'", timeout=30_000)
        page.wait_for_timeout(600)
        page.evaluate(SETUP)
        mode["detect"] = "transient_once"
        page.evaluate("() => window.__enqueue(3)")
        page.wait_for_function("""() => window.__ctx503.stopping || (window.__ctx503.inFlight === 0
          && window.__ctx503.tally.checked + (window.__ctx503.tally.failed || 0) >= 3)""",
                               timeout=20_000)
        state = page.evaluate("""() => ({ same: !window.__ctx503.stopping,
          checked: window.__ctx503.tally.checked, failed: window.__ctx503.tally.failed || 0 })""")
        if not state["same"]:
            fails.append(f"one transient 503 ended the drive (alerts {dialogs})")
        if state["failed"] != 1 or state["checked"] != 2:
            fails.append(f"expected 1 failed and 2 checked frames, got {state}")
        if dialogs:
            fails.append(f"one transient 503 raised an alert: {dialogs}")

        mode["detect"] = "retryable_once"
        page.evaluate("() => window.__enqueue(2)")
        page.wait_for_function("""() => window.__ctx503.stopping || (window.__ctx503.inFlight === 0
          && window.__ctx503.tally.checked + (window.__ctx503.tally.failed || 0) >= 5)""",
                               timeout=20_000)
        state = page.evaluate("""() => ({ same: !window.__ctx503.stopping,
          checked: window.__ctx503.tally.checked, failed: window.__ctx503.tally.failed || 0 })""")
        if not state["same"] or state["failed"] != 2:
            fails.append(f"a 503 marked retryable ended the drive or was not counted: {state} {dialogs}")

        for step, total in (("gateway_503_once", 7), ("gateway_429_once", 9)):
            mode["detect"] = step
            page.evaluate("() => window.__enqueue(2)")
            page.wait_for_function(f"""() => window.__ctx503.stopping || (window.__ctx503.inFlight === 0
              && window.__ctx503.tally.checked + (window.__ctx503.tally.failed || 0) >= {total})""",
                                   timeout=20_000)
            state = page.evaluate("""() => ({ same: !window.__ctx503.stopping,
              failed: window.__ctx503.tally.failed || 0 })""")
            if not state["same"]:
                fails.append(f"a bare gateway answer ({step}) ended the drive: {state} {dialogs}")
                break
        if dialogs:
            fails.append(f"a bare gateway answer raised an alert: {dialogs}")

        # Credits exhausted cannot recover during the session: the drive ends and says so.
        mode["detect"] = "credits"
        dialogs.clear()
        page.evaluate("() => window.__enqueue(1)")
        page.wait_for_function("() => window.__ctx503.stopping", timeout=20_000)
        page.wait_for_timeout(1500)
        code = page.evaluate("(window.__ctx503.fatalError || {}).code || null")
        if code != "shared_credits_exhausted" or not dialogs:
            fails.append(f"exhausted credits ended the drive without saying why: {code} {dialogs}")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive transient 503")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive transient 503")
