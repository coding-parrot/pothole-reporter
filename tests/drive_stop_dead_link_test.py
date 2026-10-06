# -*- coding: utf-8 -*-
"""A dead link keeps a drive's frames for a retry, and Stop still ends promptly.

During a drive a frame the link failed (no answer at all) goes back on the queue and the
status line says so; the server circuit breaker is not tripped, because nothing answered.
At Stop there is no later: the frames still waiting are sent once, the breaker ends the
attempt after five failures in a row, and everything never checked is counted in the
summary. Stop must not wait out a timeout per frame.

The detector stub answers after 1.5 s; the link "dies" before a burst of 24 frames.
"""

import sys
import time

from playwright.sync_api import sync_playwright

from web_drive_harness import open_web_drive, wait_for_dialog

BURST = r"""async (count) => {
  const ctx = drive;
  const canvas = document.createElement("canvas");
  canvas.width = 320; canvas.height = 240;
  canvas.getContext("2d").fillRect(0, 0, 320, 240);
  const frame = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", .8));
  for (let i = 0; i < count; i++) {
    ctx.tally.captured++;
    enqueueDriveEvent(ctx, { frame, pos: { ...ctx.pos }, capturedAt: Date.now(),
      sourceOffsetMs: Date.now() - ctx.startedAt, captureSeq: ++ctx.captureSeq,
      gpsAccuracy: 5, speed: 8, heading: 0 });
  }
}"""

STOP_BUDGET_S = 5  # two rounds of the 1.5 s stub, with room

fails = []
with sync_playwright() as playwright:
    browser, page, dialogs, errors = open_web_drive(playwright)
    try:
        page.evaluate("() => { window.__frameStub.delayMs = 1500; }")
        page.locator("#driveBtn").click()
        page.wait_for_function("() => drive && drive.tally.checked >= 1", timeout=30_000)
        page.evaluate("""() => {
          window.__frameStub.answer = () => {
            throw new Error("Could not reach the reporting service. Check the connection and try again.");
          };
        }""")
        page.evaluate(BURST, 24)
        # Long enough for the first failures to land and the frames to be queued again.
        page.wait_for_timeout(2500)
        waiting = page.evaluate("() => ({ queued: drive.queue.length, inFlight: drive.inFlight, "
                                "hud: document.getElementById('driveStatus').textContent, "
                                "trouble: t('net_trouble', { n: 0 }).split('(')[0].trim(), "
                                "paused: !!drive.paused })")
        if waiting["queued"] < 12:
            fails.append(f"frames the link failed were not kept for a retry: {waiting}")
        if waiting["trouble"] not in waiting["hud"]:
            fails.append(f"the status line does not say the connection is in trouble: {waiting['hud']!r}")
        if waiting["paused"]:
            fails.append("a dead link paused the drive as if the server were failing")
        started = time.monotonic()
        calls_at_stop = page.evaluate("() => window.__frameStub.calls")
        tally_ref = page.evaluate_handle("() => { const ctx = drive; stopDrive(); return ctx.tally; }")
        if not wait_for_dialog(page, dialogs, 1, 60):
            fails.append("no summary")
        stop_s = time.monotonic() - started
        page.wait_for_function("() => !driveFinalizing", timeout=30_000)
        tally = tally_ref.json_value()
        print(f"  Stop to summary: {stop_s:.1f} s; tally {tally}; waiting at Stop {waiting}")
        sent_after_stop = page.evaluate("() => window.__frameStub.calls") - calls_at_stop
        print(f"  frames sent after Stop: {sent_after_stop}")
        # Five failures in a row end it: one round of six and whatever was already sent.
        if sent_after_stop > 12:
            fails.append(f"{sent_after_stop} frames were sent into a dead link after Stop")
        if stop_s > STOP_BUDGET_S:
            fails.append(f"Stop on a dead link took {stop_s:.1f} s (budget {STOP_BUDGET_S} s)")
        unchecked = tally["captured"] - tally["checked"]
        counted = tally.get("failed", 0) + tally.get("dropped", 0)
        if counted != unchecked:
            fails.append(f"{unchecked} frames were never checked but {counted} are counted: {tally}")
        summary = dialogs[0] if dialogs else ""
        for number in (tally.get("failed", 0), tally.get("dropped", 0)):
            if number and str(number) not in summary:
                fails.append(f"the summary hides {number} unchecked frames: {summary!r}")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive stop on a dead link")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive stop on a dead link")
