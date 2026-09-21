# -*- coding: utf-8 -*-
"""After Stop, the Drive screen counts down the frames still being checked.

Stop keeps checking the frames already captured, which on a slow link took 57 s. For
all of it the screen said only "Working...", with no sign of how much was left or that
anything was happening. A counted string for exactly this existed and was used only by
the native path. Here a 3 s detector leaves a backlog at Stop; the line must name it and
count down to the summary.
"""

import re
import sys

from playwright.sync_api import sync_playwright

from web_drive_harness import open_web_drive, wait_for_dialog

fails = []
with sync_playwright() as playwright:
    browser, page, dialogs, errors = open_web_drive(playwright)
    try:
        page.evaluate("() => { window.__frameStub.delayMs = 6000; }")
        page.locator("#driveBtn").click()
        page.wait_for_function("() => drive && drive.inFlight + drive.queue.length >= 8",
                               timeout=30_000)
        backlog = page.evaluate("""() => {
          const ctx = drive;
          window.__stopped = ctx;
          const n = ctx.inFlight + ctx.queue.length;
          stopDrive();
          return n;
        }""")
        first = page.locator("#driveStatus").text_content()
        prefix = page.evaluate("t('finishing_analysis', { n: 'N' }).split('N')[0]")
        if prefix not in first or str(backlog) not in first:
            fails.append(f"Stop with {backlog} frames left shows {first!r}")
        seen = page.evaluate("""async () => {
          const seen = [];
          for (let i = 0; i < 300 && !window.__stopped.drained; i++) {
            const text = document.getElementById("driveStatus").textContent;
            if (seen[seen.length - 1] !== text) seen.push(text);
            await new Promise((resolve) => setTimeout(resolve, 100));
          }
          return seen;
        }""")
        counts = [int(m.group(0)) for text in seen for m in [re.search(r"\d+", text)] if m]
        if len(counts) < 2 or counts != sorted(counts, reverse=True) or counts[-1] >= backlog:
            fails.append(f"the backlog never counted down: {seen}")
        if any(page.evaluate("t('working')") == text for text in seen):
            fails.append("the screen fell back to a bare 'Working...'")
        if not wait_for_dialog(page, dialogs, 1, 30):
            fails.append("no summary after the backlog drained")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive stop backlog")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive stop backlog")
