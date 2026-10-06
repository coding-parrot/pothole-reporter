# -*- coding: utf-8 -*-
"""A 425 from the shared map is "a moment, please", and the write is retried until it
lands.

Production, last 30 days: 45 reports got HTTP 425 location_dedupe_in_progress from
POST /v1/potholes/report because another frame near the same spot held the 15 s
location lock, usually the previous frame of the same drive. The client treated it
like an outage: the row went to the outbox and waited a minute (then two, then four)
before anyone tried again, the drive's HUD counted the frame as new in the meantime,
and the service was marked unavailable for a minute although it had just answered.

The lock is normally gone within a second. The write now retries in place with
backoff (1 s, 2 s, 4 s) before it falls back to the outbox, and a 425 no longer marks
the service down. The healthy stub answers the retry; the counted POSTs and the gaps
between them are the evidence.
"""

import sys
import time

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, capture, open_central, reply

STATE = """async () => {
  const reports = await StandaloneAPI.handle('/api/reports');
  const outbox = await StandaloneAPI.__pure.allCentralOutbox();
  return {
    reports: reports.map((r) => ({ pending: !!r.central_sync_pending,
      pothole: r.server_pothole_id || null, status: r.status })),
    outbox: outbox.map((row) => row.attempt_count),
    available: StandaloneAPI.__pure.projectServiceAvailable(),
  };
}"""


def busy(route):
    return reply(route, 425, "location_dedupe_in_progress",
                 "A nearby report is being consolidated. Retry shortly.", {"retryable": True})


def report_times(central):
    return [call["at"] for call in central.calls if call["path"] == "/v1/potholes/report"]


fails = []
with sync_playwright() as playwright:
    # 1. Busy twice, then free: the capture lands in one go, with growing gaps.
    central = Central(lambda route, request, path, c: path == "/v1/potholes/report"
                      and c.count("/v1/potholes/report") <= 2 and busy(route))
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_timeout(600)
        outcome, text = capture(page, dialogs, cap_s=60)
        if outcome != "detail":
            fails.append(f"busy twice: capture did not finish: {outcome} {text!r}")
        state = page.evaluate(STATE)
        times = report_times(central)
        if len(times) != 3:
            fails.append(f"busy twice: expected 3 POSTs (two busy, one landed), saw {len(times)}")
        else:
            first, second = times[1] - times[0], times[2] - times[1]
            if first < 0.9 or second < 1.8 or second <= first:
                fails.append(f"busy twice: retries did not back off: gaps {first:.2f}s, {second:.2f}s")
        report = (state["reports"] or [{}])[0]
        if state["outbox"] or report.get("pending") or not report.get("pothole"):
            fails.append(f"busy twice: the landed write still reads as queued: {state}")
        if not state["available"]:
            fails.append("busy twice: a 425 marked the service unavailable")
        if errors:
            fails.append(f"busy twice: page errors {errors[:3]}")
    finally:
        browser.close()

    # 2. Busy for longer than the in-place budget: the row goes to the outbox and the
    #    in-session retry lands it once the lock is gone.
    central = Central(lambda route, request, path, c: path == "/v1/potholes/report"
                      and c.count("/v1/potholes/report") <= 4 and busy(route))
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.evaluate("window.__centralRetryDelayMs = 300")
        page.wait_for_timeout(600)
        started = time.time()
        outcome, text = capture(page, dialogs, cap_s=60)
        if outcome != "detail":
            fails.append(f"busy long: capture did not finish: {outcome} {text!r}")
        if time.time() - started > 25:
            fails.append(f"busy long: the capture held the tester for {time.time() - started:.0f} s")
        posts = central.count("/v1/potholes/report")
        if posts != 4:
            fails.append(f"busy long: expected the first attempt and three retries, saw {posts} POSTs")
        state = page.evaluate(STATE)
        report = (state["reports"] or [{}])[0]
        if state["outbox"] != [1] or not report.get("pending"):
            fails.append(f"busy long: a still-busy write was not queued for later: {state}")
        for _ in range(40):
            state = page.evaluate(STATE)
            if not state["outbox"] and state["reports"] and not state["reports"][0]["pending"]:
                break
            page.wait_for_timeout(250)
        else:
            fails.append(f"busy long: the queued write was not retried in-session: {state}")
        if central.count("/v1/potholes/report") != 5:
            fails.append(f"busy long: expected one outbox retry, saw {central.count('/v1/potholes/report')} POSTs")
        if errors:
            fails.append(f"busy long: page errors {errors[:3]}")
    finally:
        browser.close()

if fails:
    print("FAIL central report busy retry")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS central report busy retry")
