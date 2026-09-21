# -*- coding: utf-8 -*-
"""A shared-service refusal tells the tester what happened and what to do next.

The per-phone daily cap, the project-wide rate limit and budget, an outdated app, a
refused phone and a server fault all used to reach the alert as the server's English
wording: "The configured shared-vision limit has been reached." for three different
limits, "Forbidden", "This server requires road-damage-v6.". A tester cannot act on
any of those. Each case here stubs one refusal and reads the alert a tester gets.
"""

import sys

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, capture, open_central, reply

LIMIT_TEXT = "The configured shared-vision limit has been reached."
CASES = [
    # (name, status, code, server message, details, must contain, must not contain)
    ("daily per-phone cap", 503, "daily_vision_limit", LIMIT_TEXT,
     {"retryable": False, "limit": 50}, ["50", "05:30 IST", "OpenAI"], [LIMIT_TEXT]),
    ("project rate limit", 429, "shared_rate_limit", LIMIT_TEXT,
     {"retryable": True, "limit": 60}, ["minute"], [LIMIT_TEXT, "05:30"]),
    ("project daily budget", 503, "shared_daily_budget_reached", LIMIT_TEXT,
     {"retryable": False, "limit": 2000}, ["05:30 IST", "OpenAI"], [LIMIT_TEXT]),
    ("outdated prompt", 409, "prompt_version_mismatch", "This server requires road-damage-v6.",
     None, ["Update"], ["road-damage-v6"]),
    ("refused phone", 403, "forbidden", "Forbidden", None, ["feedback"], ["Forbidden"]),
    ("still in progress", 425, "idempotency_in_progress",
     "This operation is still in progress; retry shortly.", {"retryable": True},
     ["still being checked"], ["retry shortly"]),
    ("server fault", 500, "internal_error", "The service could not complete this request.",
     None, ["minute"], ["could not complete this request"]),
    ("skewed clock, no server time", 401, "stale_request", "The signed request is too old.",
     None, ["clock", "automatic"], ["too old"]),
]

fails = []
alerts = {}
with sync_playwright() as playwright:
    for name, status, code, message, details, wanted, banned in CASES:
        def script(route, request, path, central, status=status, code=code, message=message,
                   details=details):
            if path == "/v1/vision/detect":
                return reply(route, status, code, message, details)
            return False

        central = Central(script)
        browser, page, dialogs, errors = open_central(playwright, central)
        try:
            page.wait_for_timeout(600)
            outcome, text = capture(page, dialogs)
            alerts[name] = text or ""
            if outcome != "alert":
                fails.append(f"{name}: expected an alert, got {outcome}")
                continue
            for token in wanted:
                if token not in text:
                    fails.append(f"{name}: alert does not mention {token!r}: {text!r}")
            for token in banned:
                if token in text:
                    fails.append(f"{name}: alert still shows raw server text {token!r}: {text!r}")
            if errors:
                fails.append(f"{name}: page errors {errors[:3]}")
        finally:
            browser.close()

    if alerts.get("daily per-phone cap") and \
            alerts.get("daily per-phone cap") == alerts.get("project rate limit"):
        fails.append("the per-phone cap and the project rate limit read the same")

    # The copy is the tester's language, not the server's.
    central = Central(lambda route, request, path, c: path == "/v1/vision/detect" and reply(
        route, 503, "daily_vision_limit", LIMIT_TEXT, {"retryable": False, "limit": 50}))
    browser, page, dialogs, errors = open_central(playwright, central, lang="kn")
    try:
        page.wait_for_timeout(600)
        outcome, text = capture(page, dialogs)
        expected = page.evaluate("t('svc_daily_limit', { limit: '50' })")
        if outcome != "alert" or text != expected:
            fails.append(f"Kannada tester did not get the Kannada cap text: {outcome} {text!r}")
    finally:
        browser.close()

    # A phone clock 10 minutes behind: the server refuses the stamp and says its own
    # time, and the app re-signs against that time instead of failing every call.
    skew = {"server_now": None, "stamps": []}

    def skewed(route, request, path, central):
        if path != "/v1/vision/detect":
            return False
        stamp = int(central.calls[-1]["headers"].get("x-timestamp") or 0)
        skew["stamps"].append(stamp)
        if skew["server_now"] is None:
            skew["server_now"] = stamp + 10 * 60_000
            return reply(route, 401, "stale_request", "The signed request is too old.",
                         {"server_time": skew["server_now"], "retryable": True})
        if abs(stamp - skew["server_now"]) > 5_000:
            return reply(route, 401, "stale_request", "The signed request is too old.",
                         {"server_time": skew["server_now"], "retryable": True})
        return False

    central = Central(skewed)
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_timeout(600)
        outcome, text = capture(page, dialogs)
        if outcome != "detail":
            fails.append(f"skewed clock: capture did not re-sign on the server's time: "
                         f"{outcome} {text!r}, stamps {skew['stamps']}")
        if len(skew["stamps"]) != 2:
            fails.append(f"skewed clock: expected one re-signed retry, saw {skew['stamps']}")
    finally:
        browser.close()

if fails:
    print("FAIL central cap messaging")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS central cap messaging")
