# -*- coding: utf-8 -*-
"""Frames a drive captured but never checked are counted in the summary, the drive record
and History.

On a slow link the capture queue overflows and its oldest frames are dropped; a failed
request loses its frame too. A 60 s drive dropped 71 of 131 captured frames and ended
with "9 damage events found, 60 frames checked.", as if the rest of the road had been
seen. The drive record kept only checked and found, so History could not say it either.
Here a slow detector and a burst of captures overflow the queue, every third request
fails, and all three places must carry both numbers.
"""

import sys

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

fails = []
with sync_playwright() as playwright:
    browser, page, dialogs, errors = open_web_drive(playwright)
    try:
        page.evaluate("""() => {
          window.__frameStub.delayMs = 1500;
          window.__frameStub.answer = (n) => {
            if (n % 3 === 0) throw new Error("Network request failed");
            return { found: false };
          };
        }""")
        page.locator("#driveBtn").click()
        page.wait_for_function("() => drive && drive.tally.captured >= 1", timeout=30_000)
        page.evaluate(BURST, 40)
        page.wait_for_timeout(2500)
        tally = page.evaluate("""() => {
          const ctx = drive;
          window.__stoppedId = ctx.sessionId;
          stopDrive();
          return { ...ctx.tally };
        }""")
        if not wait_for_dialog(page, dialogs, 1, 60):
            fails.append("no summary")
        page.wait_for_function("() => !driveFinalizing", timeout=30_000)
        final = page.evaluate("""async () => {
          const rec = (await api("/api/drives")).find((d) => String(d.id) === window.__stoppedId);
          return { rec, tally: null };
        }""")
        rec = final["rec"] or {}
        summary = dialogs[0] if dialogs else ""
        dropped, failed = tally["dropped"], tally.get("failed", 0)
        if dropped < 1 or failed < 1:
            fails.append(f"the setup did not overflow and fail frames: {tally}")
        if str(dropped) not in summary:
            fails.append(f"the summary hides {dropped} dropped frames: {summary!r}")
        if rec.get("dropped") != dropped or rec.get("failed", -1) < failed \
                or rec.get("captured", 0) < tally["captured"]:
            fails.append(f"the drive record lost the counts: tally {tally}, record "
                         f"{ {k: rec.get(k) for k in ('captured', 'checked', 'dropped', 'failed')} }")
        page.wait_for_timeout(500)
        history = page.locator("#home").inner_text()
        if f"{dropped} " not in history:
            fails.append(f"History does not show the {dropped} dropped frames: {history[:400]!r}")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive dropped frames")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive dropped frames")
