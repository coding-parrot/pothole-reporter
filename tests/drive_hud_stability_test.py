# -*- coding: utf-8 -*-
"""The Drive status line holds still long enough to be read at a glance.

Sampled every 100 ms, a moving drive rewrote the line about five times a second: the
engine's stage text ("AI checking for road damage...", "Writing the complaint...") was
copied into it for every frame in flight, and every 200 ms tick between captures swapped
the scanning line for "Holding position". The recording size vanished with it. A driver
gets a glance, not a read. The line now belongs to the drive: engine stages never reach
it, holding appears only after a real pause in capture, and it changes at most about
once a second.
"""

import sys

from playwright.sync_api import sync_playwright

from web_drive_harness import open_web_drive

SAMPLE_S = 15
ENGINE_TEXT = "AI checking for road damage..."

fails = []
with sync_playwright() as playwright:
    browser, page, dialogs, errors = open_web_drive(playwright)
    try:
        page.evaluate("() => { window.__frameStub.delayMs = 400; }")
        page.locator("#driveBtn").click()
        page.wait_for_function("() => drive && drive.tally.captured >= 2", timeout=30_000)
        samples = page.evaluate("""async ([seconds, engineText]) => {
          const seen = [];
          let last = null;
          const started = Date.now();
          const pump = setInterval(() => window.dispatchEvent(
            new CustomEvent("pipeline-progress", { detail: engineText })), 150);
          while (Date.now() - started < seconds * 1000) {
            const text = document.getElementById("driveStatus").textContent;
            if (text !== last) { seen.push([Date.now() - started, text]); last = text; }
            await new Promise((resolve) => setTimeout(resolve, 100));
          }
          clearInterval(pump);
          return { seen, captured: drive ? drive.tally.captured : 0,
                   holding: t("holding", { checked: 0, found: 0 }).split("(")[0].trim() };
        }""", [SAMPLE_S, ENGINE_TEXT])
        page.locator("#driveStop").click()
        page.wait_for_function("() => !drive && !driveFinalizing", timeout=30_000)
        changes = samples["seen"]
        if samples["captured"] < 8:
            fails.append(f"the drive captured only {samples['captured']} frames")
        engine = [text for _, text in changes if ENGINE_TEXT in text]
        if engine:
            fails.append(f"engine stage text reached the Drive line {len(engine)} times")
        holding = [text for _, text in changes if samples["holding"] in text]
        if holding:
            fails.append(f"a moving drive flipped to '{samples['holding']}' {len(holding)} times")
        rate = (len(changes) - 1) / SAMPLE_S
        if rate > 1.2:
            fails.append(f"the status line changed {len(changes) - 1} times in {SAMPLE_S} s")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive HUD stability")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive HUD stability")
