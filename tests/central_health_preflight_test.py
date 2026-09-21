# -*- coding: utf-8 -*-
"""A slow health check does not refuse a photo the detector could have checked.

The shared-mode preflight gave /v1/health 4 s (and the background probe 1.5 s) while
the detection it guards is allowed 100 s. A cold Lambda or a 3G handshake answering in
5 s refused the capture with the outage text and put the outage banner on Home. A
server that says it is down must still be refused.
"""

import sys
import time

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, capture, open_central, reply
import flow_harness as fh

fails = []
with sync_playwright() as playwright:
    def slow_health(route, request, path, central):
        if path == "/v1/health":
            time.sleep(5)
            fh.envelope(route, {"ok": True, "shared_vision_configured": True})
            return True
        return False

    central = Central(slow_health)
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_timeout(600)
        outcome, text = capture(page, dialogs)
        if outcome != "detail":
            fails.append(f"a 5 s health answer refused the capture: {outcome} {text!r}")
        if central.count("/v1/vision/detect") != 1:
            fails.append(f"expected one detect, saw {central.count('/v1/vision/detect')}")
        banner = page.evaluate("() => { const b = document.getElementById('banner');"
                               " return b.style.display !== 'none' ? b.textContent : null; }")
        if banner:
            fails.append(f"a slow health answer showed the outage banner: {banner!r}")
    finally:
        browser.close()

    central = Central(lambda route, request, path, c: path == "/v1/health" and reply(
        route, 503, "unhealthy", "Service is warming up."))
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_timeout(600)
        outcome, text = capture(page, dialogs)
        expected = page.evaluate("t('shared_unavailable')")
        if outcome != "alert" or not (text or "").startswith(expected):
            fails.append(f"a down server did not refuse the capture: {outcome} {text!r}")
        if central.count("/v1/vision/detect"):
            fails.append("a photo was sent to a server that said it was down")
    finally:
        browser.close()

    # The outage banner says the shared service is down in every language; Marathi and
    # Bengali used to tell a shared-mode tester to add an OpenAI key instead.
    for lang, shared_word in (("mr", "सामायिक"), ("bn", "শেয়ার-করা")):
        central = Central(lambda route, request, path, c: path == "/v1/health" and reply(
            route, 503, "unhealthy", "Service is warming up."))
        browser, page, dialogs, errors = open_central(playwright, central, lang=lang)
        try:
            page.wait_for_function("() => document.getElementById('banner').style.display"
                                   " === 'block'", timeout=15_000)
            banner = page.inner_text("#banner")
            if shared_word not in banner:
                fails.append(f"{lang} outage banner does not name the shared service: {banner!r}")
        finally:
            browser.close()

if fails:
    print("FAIL central health preflight")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS central health preflight")
