# -*- coding: utf-8 -*-
"""A failing connection stays visible on the Drive status line until a frame gets through.

The frame's .catch wrote "Connection trouble, still scanning" and its .finally rewrote
the line with the scanning template in the same tick, so a driver whose every frame was
failing read "Scanning (live)... 9 checked" until Stop, and only the summary said 20
frames had gone unchecked. The line now keeps the trouble text while failures are
unbroken and returns to scanning on the next success.

The drive context is the app's own drainDriveQueue state with synthetic frames; the
service answers 500 (not fatal) while the connection is "down".
"""

import sys

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, open_central, reply

mode = {"detect": "fail"}


def script(route, request, path, central):
    if path == "/v1/vision/detect" and mode["detect"] == "fail":
        return reply(route, 500, "internal_error", "The service could not complete this request.")
    return False


SETUP = r"""async () => {
  const canvas = document.createElement("canvas");
  canvas.width = 320; canvas.height = 240;
  const g = canvas.getContext("2d");
  g.fillStyle = "#777"; g.fillRect(0, 0, 320, 240);
  window.__frame = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", .8));
  const ctx = {
    stream: null, pos: null, lastCapPos: null,
    tally: { checked: 0, found: 0, already: 0, repaired: 0, captured: 0, dropped: 0 },
    duplicateIds: new Set(), queue: [], pending: new Set(), inFlight: 0, errors: 0,
    sessionId: "trouble-" + Date.now(), startedAt: Date.now(), captureSeq: 0,
    stillBusy: false, stopping: false, drained: false, readyAt: 0,
  };
  ctx.drainPromise = new Promise((resolve) => { ctx.resolveDrained = resolve; });
  drive = ctx;
  show("drive");
  window.__troubleCtx = ctx;
  window.__enqueue = (n) => {
    for (let i = 0; i < n; i++) enqueueDriveEvent(ctx, { frame: window.__frame,
      pos: { lat: 12.9716, lng: 77.5946 }, capturedAt: Date.now(),
      sourceOffsetMs: ctx.captureSeq * 1000, captureSeq: ++ctx.captureSeq,
      gpsAccuracy: 4, speed: 8, heading: 90 });
  };
}"""

HUD = """() => ({ hud: document.getElementById('driveStatus').textContent,
  trouble: t('net_trouble', { n: 0 }).split('(')[0].trim(),
  errors: window.__troubleCtx.errors, checked: window.__troubleCtx.tally.checked })"""

fails = []
with sync_playwright() as playwright:
    central = Central(script)
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_function("() => typeof enqueueDriveEvent === 'function'", timeout=30_000)
        page.wait_for_timeout(600)
        page.evaluate(SETUP)
        page.evaluate("() => window.__enqueue(3)")
        page.wait_for_function("() => window.__troubleCtx.errors >= 3", timeout=20_000)
        # Past the HUD's one-second refresh, when the scanning template would have landed.
        page.wait_for_timeout(2500)
        state = page.evaluate(HUD)
        if state["trouble"] not in state["hud"]:
            fails.append(f"three failed frames in a row, but the line reads: {state['hud']!r}")

        mode["detect"] = "ok"
        page.evaluate("() => window.__enqueue(1)")
        page.wait_for_function("() => window.__troubleCtx.tally.checked >= 1", timeout=20_000)
        page.wait_for_timeout(2500)
        state = page.evaluate(HUD)
        if state["trouble"] in state["hud"]:
            fails.append(f"a frame got through, but the line still reads: {state['hud']!r}")
        page.evaluate("() => { drive = null; }")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive net trouble HUD")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive net trouble HUD")
