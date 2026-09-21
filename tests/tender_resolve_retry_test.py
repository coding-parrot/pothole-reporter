# -*- coding: utf-8 -*-
"""A retryable road-ownership outage is asked once more before the photo is filed unrouted.

One slow KGIS layer makes /v1/tenders/resolve answer 503 road_ownership_unavailable with
retryable: true. The phone took that as final and filed the accepted photo "unrouted",
although the same lookup a few seconds later answered 200 municipal. A terminal answer
or a second outage must still end the capture without a third call.
"""

import sys

from playwright.sync_api import sync_playwright

import flow_harness as fh
from central_stub_harness import Central, capture, open_central, reply

MUNICIPAL = {"address": "Test Road, Kalaburagi", "lgd": "248127", "town": "Kalaburagi",
             "source": "kgis", "address_source": "nominatim", "road_ownership": "municipal"}


def resolver(failures, retryable=True):
    def script(route, request, path, central):
        if path != "/v1/tenders/resolve":
            return False
        if central.count(path) <= failures:
            return reply(route, 503, "road_ownership_unavailable",
                         "Road ownership could not be verified. Retry later.",
                         {"retryable": retryable})
        fh.envelope(route, {"jurisdiction": MUNICIPAL, "tender": None,
                            "reason": "no_tender_match"})
        return True
    return script


def run(playwright, failures, retryable, want_status, want_calls):
    central = Central(resolver(failures, retryable))
    browser, page, dialogs, errors = open_central(playwright, central)
    label = f"{failures} failure(s), retryable={retryable}"
    try:
        page.wait_for_timeout(600)
        outcome, text = capture(page, dialogs)
        if outcome != "detail":
            return [f"{label}: capture ended in {outcome} {text!r}"]
        stored = page.evaluate(
            "async () => (await StandaloneAPI.handle('/api/reports')).map((r) => r.status)")
        found = []
        if stored != [want_status]:
            found.append(f"{label}: stored {stored}, want [{want_status!r}]")
        keys = {call["headers"].get("idempotency-key") for call in central.calls
                if call["path"] == "/v1/tenders/resolve"}
        if central.count("/v1/tenders/resolve") != want_calls:
            found.append(f"{label}: {central.count('/v1/tenders/resolve')} resolve calls,"
                         f" want {want_calls}")
        if len(keys) != 1:
            found.append(f"{label}: the retry used a new idempotency key: {keys}")
        if errors:
            found.append(f"{label}: page errors {errors[:3]}")
        return found
    finally:
        browser.close()


fails = []
with sync_playwright() as playwright:
    fails += run(playwright, 1, True, "draft", 2)
    fails += run(playwright, 3, True, "unrouted", 2)
    fails += run(playwright, 1, False, "unrouted", 1)

if fails:
    print("FAIL tender resolve retry")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS tender resolve retry")
