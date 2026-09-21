# -*- coding: utf-8 -*-
"""An accepted photo survives the app being killed while it is being routed.

Nothing was written until /v1/tenders/resolve answered, so a kill in that gap (the live
resolver has taken over six seconds during a KGIS stall) lost the photo and the verdict,
although the detection had already counted against the install's 50 a day. Here the
resolver is held open, the page is closed without lifecycle events, and the same profile
is relaunched: the report must be there, and its routing and shared-map sync must finish.
"""

import json
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import flow_harness as fh

held = []
calls = []
hold = {"on": True}


def service(route, request):
    path = urlparse(request.url).path
    calls.append(path)
    if path == "/v1/tenders/resolve" and hold["on"]:
        held.append(route)  # never answered: the app is killed while waiting
        return
    if path == "/v1/tenders/resolve":
        body = json.loads(request.post_data or "{}")
        return fh.envelope(route, {
            "jurisdiction": {"lat": body.get("lat"), "lng": body.get("lng"),
                             "address": None, "lgd": "305852",
                             "town": "Bengaluru South City Corporation", "source": "kgis",
                             "address_source": "unresolved", "road_ownership": "municipal"},
            "tender": None, "reason": "no_tenders_for_jurisdiction",
        })
    return fh.central_service(route, request)


STATE = r"""
async () => ({
  reports: await StandaloneAPI.__pure.allReports(),
  outbox: await StandaloneAPI.__pure.allCentralOutbox(),
})
"""

fails = []
with sync_playwright() as playwright:
    browser, page, errors = fh.open_flow(playwright, native=False)
    try:
        context = page.context
        context.route(f"{fh.SERVICE}/**", service)
        page.evaluate("() => { (" + fh.report_form_script(12.9116, 77.6389)
                      + ")().catch(() => {}); return true; }")
        page.wait_for_function("() => true")
        for _ in range(100):
            if held:
                break
            page.wait_for_timeout(100)
        if not held:
            fails.append("the capture never reached the resolver")
        page.wait_for_timeout(500)
        # A kill: no pagehide, no visibilitychange, no chance to finish anything.
        page.close(run_before_unload=False)
        # Settle the held requests so the driver does not report them as cancelled.
        for route in held:
            try:
                route.abort()
            except Exception:
                pass

        hold["on"] = False
        calls.clear()
        relaunched = context.new_page()
        relaunched.on("pageerror", lambda error: errors.append(f"uncaught: {error}"))
        relaunched.goto(fh.APP)
        relaunched.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        state = None
        for _ in range(100):
            state = relaunched.evaluate(STATE)
            reports = state["reports"]
            if (len(reports) == 1 and reports[0].get("status") == "draft"
                    and reports[0].get("server_pothole_id")):
                break
            relaunched.wait_for_timeout(200)
        reports = state["reports"]
        if len(reports) != 1:
            fails.append(f"expected the one accepted report after relaunch, found {len(reports)}")
        else:
            report = reports[0]
            print("  after relaunch:", report.get("status"), report.get("unrouted_reason"),
                  report.get("server_pothole_id"), report.get("routing_pending"))
            if report.get("status") != "draft":
                fails.append(f"routing was not resumed: {report.get('status')} "
                             f"{report.get('unrouted_reason')}")
            if not report.get("email_body"):
                fails.append("the resumed report has no complaint")
            if not report.get("server_pothole_id"):
                fails.append("the resumed report never reached the shared map")
            if report.get("routing_pending"):
                fails.append("the report is still marked as waiting for routing")
        if "/v1/tenders/resolve" not in calls:
            fails.append("the relaunch did not retry routing")
        if calls.count("/v1/vision/detect"):
            fails.append("the relaunch spent another detection")
        if state and state["outbox"]:
            fails.append(f"the shared-map outbox did not drain: {state['outbox']}")
        fails += fh.error_failures(errors, "kill mid routing")
    finally:
        browser.close()

if fails:
    print("FAIL kill mid routing")
    for fail in fails:
        print("  -", fail)
    sys.exit(1)
print("PASS kill mid routing")
