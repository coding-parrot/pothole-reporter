# -*- coding: utf-8 -*-
"""A shared-mode video import plans only the checks today's budget can pay for.

The planner gave a 12 minute clip 200 samples (one every 3.6 s) and the confirm promised
"the full timeline", but each install gets 50 shared checks a day and samples run in
timeline order, so the cap ended the run at minute 3 and minutes 3 to 12 were never
looked at. Planning against what is left spreads those checks over the whole video.
"""

import datetime
import sys

from playwright.sync_api import sync_playwright

import flow_harness as fh

PLAN = r"""
(state) => {
  if (state) localStorage.setItem("shared_checks_today", JSON.stringify(state));
  else localStorage.removeItem("shared_checks_today");
  const clips = [{ duration: 720 }];
  const plan = allocateImportedSamples(clips, importSampleLimit());
  const confirmText = t("video_import_confirm", { files: 1, frames: plan.frames,
    step: Math.max(0.1, plan.step).toFixed(1) });
  return { frames: plan.frames, first: clips[0].times[0],
           last: clips[0].times[clips[0].times.length - 1], confirmText };
}
"""

fails = []
today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
with sync_playwright() as playwright:
    browser, page, errors = fh.open_flow(playwright, native=False)
    try:
        fresh = page.evaluate(PLAN, {"day": today, "used": 0, "limit": 50})
        if fresh["frames"] != 50:
            fails.append(f"a full day's budget of 50 planned {fresh['frames']} checks")
        if not (fresh["first"] < 10 and fresh["last"] > 700):
            fails.append(f"the 50 checks do not span the clip: {fresh['first']} to {fresh['last']}")
        if "50 sampled checks" not in fresh["confirmText"]:
            fails.append(f"the confirm does not state 50 checks: {fresh['confirmText'][:120]}")
        spent = page.evaluate(PLAN, {"day": today, "used": 30, "limit": 50})
        if spent["frames"] != 20:
            fails.append(f"20 checks left today planned {spent['frames']}")
        # Yesterday's count is spent budget no longer; only the cap carries over.
        stale = page.evaluate(PLAN, {"day": "2000-01-01", "used": 50, "limit": 50})
        if stale["frames"] != 50:
            fails.append(f"yesterday's usage shrank today's plan to {stale['frames']}")
        page.evaluate("() => { localStorage.setItem('vision_provider', 'personal');"
                      " localStorage.setItem('openai_key', 'sk-budget-test'); }")
        personal = page.evaluate(PLAN, {"day": today, "used": 50, "limit": 50})
        if personal["frames"] != 720:
            fails.append(f"a personal key is not bound by the shared cap: {personal['frames']}")
        fails += fh.error_failures(errors, "video import budget")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL video import budget")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS video import budget")
