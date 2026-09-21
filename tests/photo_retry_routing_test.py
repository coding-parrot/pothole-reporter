# -*- coding: utf-8 -*-
"""Retry routing in shared mode asks the central resolver again, and its draft can be emailed.

A shared-mode photo whose road-ownership lookup failed was filed unrouted. Retry routing
then routed it with the phone's own geocoder and no central jurisdiction, so it became a
Draft with no road_ownership_source. The Email tap requires central ownership proof, so
it alerted "could not check whether this road is a national, state, or district
highway" and fell back to unrouted: a Draft whose only action always failed.
"""

import sys

from playwright.sync_api import sync_playwright

import flow_harness as fh
from central_stub_harness import Central, capture, open_central, reply

OWNERSHIP_DOWN = {"on": True}
# A body the bundled Karnataka routing pack has an address for.
MUNICIPAL = {"address": "Test Road, Kalaburagi", "lgd": "248127", "town": "Kalaburagi",
             "source": "kgis", "address_source": "nominatim", "road_ownership": "municipal"}


def resolver_outage(route, request, path, central):
    if path != "/v1/tenders/resolve":
        return False
    if OWNERSHIP_DOWN["on"]:
        return reply(route, 503, "road_ownership_unavailable",
                     "Road ownership could not be verified. Retry later.",
                     {"retryable": True})
    fh.envelope(route, {"jurisdiction": MUNICIPAL, "tender": None, "reason": "no_tender_match"})
    return True


REPORT = """async () => (await StandaloneAPI.handle('/api/reports')).map((r) => ({
  id: r.id, status: r.status, reason: r.unrouted_reason,
  owner: r.road_ownership, source: r.road_ownership_source,
  email: r.officer_email, checked: r.tender_resolution_checked_at }))"""


def still_down(playwright):
    """The resolver is still unreachable on retry: the report stays unrouted."""
    OWNERSHIP_DOWN["on"] = True
    central = Central(resolver_outage)
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_timeout(600)
        capture(page, dialogs)
        captured = page.evaluate(REPORT)[0]
        page.locator("#retryRoutingBtn").wait_for(state="visible", timeout=10_000)
        page.locator("#retryRoutingBtn").click()
        page.wait_for_function(
            "() => { const b = document.getElementById('retryRoutingBtn');"
            " return !b || !b.disabled; }", timeout=30_000)
        page.wait_for_timeout(400)
        report = page.evaluate(REPORT)[0]
        if report["status"] != "unrouted" or report["reason"] != captured["reason"] \
                or report["source"] is not None:
            fails.append(f"a retry with the resolver still down became {report}")
        if errors:
            fails.append(f"still down: page errors {errors[:3]}")
    finally:
        browser.close()


fails = []
with sync_playwright() as playwright:
    still_down(playwright)
    OWNERSHIP_DOWN["on"] = True
    central = Central(resolver_outage)
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_timeout(600)
        outcome, text = capture(page, dialogs)
        stored = page.evaluate(REPORT)
        if outcome != "detail" or [r["status"] for r in stored] != ["unrouted"]:
            fails.append(f"setup: the outage did not file an unrouted report: {outcome} {text!r} {stored}")
        else:
            page.locator("#retryRoutingBtn").wait_for(state="visible", timeout=10_000)
            before = central.count("/v1/tenders/resolve")
            OWNERSHIP_DOWN["on"] = False
            page.locator("#retryRoutingBtn").click()
            page.wait_for_function(
                "() => { const b = document.getElementById('retryRoutingBtn');"
                " return !b || !b.disabled; }", timeout=30_000)
            page.wait_for_timeout(400)
            if central.count("/v1/tenders/resolve") <= before:
                fails.append("retry routing never asked the central resolver again")
            report = page.evaluate(REPORT)[0]
            if report["status"] != "draft" or report["source"] != "central_v1" \
                    or report["owner"] != "municipal" or not report["email"] \
                    or report["checked"] is None:
                fails.append(f"retry did not produce a centrally proven draft: {report}")
            elif page.locator("#detail #sendBtn").count() == 1:
                seen = len(dialogs)
                page.locator("#detail #sendBtn").click()
                page.wait_for_timeout(2500)
                for message in dialogs[seen:]:
                    if "highway" in message or "could not" in message.lower():
                        fails.append(f"Email on the retried draft failed: {message!r}")
                after = page.evaluate(REPORT)[0]
                if after["status"] == "unrouted":
                    fails.append(f"Email fell back to unrouted: {after}")
            else:
                fails.append("the retried draft offers no Email action")

        if errors:
            fails.append(f"page errors {errors[:3]}")
    finally:
        browser.close()

if fails:
    print("FAIL photo retry routing")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS photo retry routing")
