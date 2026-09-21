# -*- coding: utf-8 -*-
"""A photo whose shared-map write failed can be retried from its card, and a retry that
lands later unlocks Email on the open card without backing out.

The card used to read "Waiting for the shared map" with only Back and Delete. The
engine did retry later, but nothing told the page: the list and the open card were
drawn from the old row, so Email stayed hidden until the tester left and came back.
"""

import sys

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, capture, open_central, reply, routed


def fail_first_write(route, request, path, central):
    if routed(route, request, path, central):
        return True
    return path == "/v1/potholes/report" and central.count(path) == 1 and reply(
        route, 500, "internal_error", "The shared map is temporarily unavailable.")


def buttons(page):
    return page.evaluate("() => [...document.querySelectorAll('#detail button')].map((b) => b.id)")


fails = []
with sync_playwright() as playwright:
    # The tester retries by hand.
    central = Central(fail_first_write)
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_timeout(600)
        outcome, text = capture(page, dialogs)
        if outcome != "detail":
            fails.append(f"manual retry: capture did not finish: {outcome} {text!r}")
        before = buttons(page)
        if "sendBtn" in before or "syncRetryBtn" not in before:
            fails.append(f"a failed shared-map write offers no retry: {before}")
        else:
            page.locator("#syncRetryBtn").click()
            try:
                page.wait_for_selector("#detail #sendBtn", timeout=10_000)
            except Exception:
                fails.append(f"retry did not unlock Email: {buttons(page)}, "
                             f"{central.count('/v1/potholes/report')} POSTs, alerts {dialogs}")
        if central.count("/v1/potholes/report") != 2:
            fails.append(f"expected one retry, saw {central.count('/v1/potholes/report')} POSTs")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    finally:
        browser.close()

    # The engine's own retry lands while the card is open.
    central = Central(fail_first_write)
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.evaluate("window.__centralRetryDelayMs = 500")
        page.wait_for_timeout(600)
        capture(page, dialogs)
        try:
            page.wait_for_selector("#detail #sendBtn", timeout=10_000)
        except Exception:
            fails.append(f"a background retry left the open card stale: {buttons(page)}, "
                         f"{central.count('/v1/potholes/report')} POSTs")
    finally:
        browser.close()

if fails:
    print("FAIL photo outbox retry")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS photo outbox retry")
