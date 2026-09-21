# -*- coding: utf-8 -*-
"""Shared-map writes: a refused one stops for good, a failed one is retried in-session.

A 400 (an expired or mismatched receipt) will be refused identically forever, yet the
outbox row stayed and was re-sent at every launch and every online event, with the
report stuck on "sync pending". A 503 is the opposite: worth retrying, but in shared
mode nothing retried it until the app restarted or the network toggled.
"""

import sys

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, capture, open_central, reply

STATE = """async () => {
  const reports = await StandaloneAPI.handle('/api/reports');
  const outbox = await StandaloneAPI.__pure.allCentralOutbox();
  return {
    reports: reports.map((r) => ({ pending: !!r.central_sync_pending,
      terminal: !!r.server_sync_terminal, err: r.server_sync_error || null,
      pothole: r.server_pothole_id || null })),
    outbox: outbox.map((row) => row.attempt_count),
  };
}"""

fails = []
with sync_playwright() as playwright:
    # A refused write is final.
    central = Central(lambda route, request, path, c: path == "/v1/potholes/report" and reply(
        route, 400, "bad_receipt", "The detection receipt does not match this observation."))
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_timeout(600)
        outcome, text = capture(page, dialogs)
        if outcome != "detail":
            fails.append(f"refused write: capture did not finish: {outcome} {text!r}")
        state = page.evaluate(STATE)
        if state["outbox"]:
            fails.append(f"refused write was queued for retry: {state}")
        report = (state["reports"] or [{}])[0]
        if report.get("pending") or not report.get("terminal") or not report.get("err"):
            fails.append(f"refused write still reads as pending or lost its reason: {report}")
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.evaluate("window.dispatchEvent(new Event('online'))")
        page.wait_for_timeout(2500)
        if central.count("/v1/potholes/report") != 1:
            fails.append(f"refused write was re-sent: {central.count('/v1/potholes/report')} POSTs")
    finally:
        browser.close()

    # A refused write already sitting in an outbox (from an older build) is cleared too.
    central = Central(lambda route, request, path, c: path == "/v1/potholes/report" and (
        reply(route, 503, "service_temporarily_unavailable", "Down.", {"retryable": True})
        if c.count("/v1/potholes/report") == 1 else
        reply(route, 400, "bad_receipt", "The detection receipt does not match this observation.")))
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_timeout(600)
        capture(page, dialogs)
        if page.evaluate(STATE)["outbox"] != [1]:
            fails.append(f"a 503 did not leave one queued row: {page.evaluate(STATE)}")
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.wait_for_timeout(2500)
        state = page.evaluate(STATE)
        if state["outbox"] or (state["reports"] or [{}])[0].get("pending"):
            fails.append(f"a queued row refused on retry was kept: {state}")
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.wait_for_timeout(1500)
        if central.count("/v1/potholes/report") != 2:
            fails.append(f"refused retry was re-sent: {central.count('/v1/potholes/report')} POSTs")
    finally:
        browser.close()

    # A failed write is retried while the app stays open.
    central = Central(lambda route, request, path, c: path == "/v1/potholes/report"
                      and c.count("/v1/potholes/report") == 1 and reply(
                          route, 503, "service_temporarily_unavailable",
                          "The shared map is temporarily unavailable.", {"retryable": True}))
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.evaluate("window.__centralRetryDelayMs = 300")
        page.wait_for_timeout(600)
        outcome, text = capture(page, dialogs)
        if outcome != "detail":
            fails.append(f"failed write: capture did not finish: {outcome} {text!r}")
        state = None
        for _ in range(40):
            state = page.evaluate(STATE)
            if not state["outbox"] and state["reports"] and not state["reports"][0]["pending"]:
                break
            page.wait_for_timeout(250)
        else:
            fails.append(f"failed write was not retried in-session: "
                         f"{central.count('/v1/potholes/report')} POSTs, {state}")
        if central.count("/v1/potholes/report") != 2:
            fails.append(f"expected exactly one retry, saw {central.count('/v1/potholes/report')} POSTs")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    finally:
        browser.close()

if fails:
    print("FAIL central outbox retry")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS central outbox retry")
