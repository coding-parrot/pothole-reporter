# -*- coding: utf-8 -*-
"""An uncaught error on a tester's phone can reach the team through Feedback.

There was no window error or unhandledrejection handler, and feedback carried no error
context, so a field crash like v1.38.1's "trimmedKey is not defined" left no trace
anywhere. The app now keeps the last few error messages on the phone, and Feedback offers
to include them. Nothing is sent unless the tester ticks the box.
"""

import json
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import flow_harness as fh

sent = []


def service(route, request):
    if urlparse(request.url).path == "/v1/feedback":
        sent.append(json.loads(request.post_data or "{}"))
    return fh.central_service(route, request)


def send_feedback(page, text, include):
    page.evaluate("openFeedback('home')")
    page.locator("#feedback").wait_for(state="visible")
    page.locator("#feedbackText").fill(text)
    box = page.locator("#feedbackErrors")
    if include:
        box.check()
    count = len(sent)
    page.locator("#feedbackSend").click()
    page.wait_for_function("() => /Thank|Sent|sent/i.test("
                           "document.getElementById('feedbackStatus').textContent)",
                           timeout=15_000)
    return sent[count] if len(sent) > count else None


fails = []
with sync_playwright() as playwright:
    browser, page, errors = fh.open_flow(playwright, native=False)
    try:
        page.context.route(f"{fh.SERVICE}/**", service)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.evaluate("""() => {
          setTimeout(() => { throw new Error("boom-timeout-7731"); }, 0);
          Promise.reject(new Error("boom-rejection-7732"));
        }""")
        page.wait_for_timeout(500)

        plain = send_feedback(page, "First message, no errors attached.", include=False)
        if plain is None:
            fails.append("the unticked feedback never reached /v1/feedback")
        elif "boom-" in json.dumps(plain):
            fails.append("errors were sent without the tester ticking the box")

        page.wait_for_timeout(1700)  # the screen returns home after a send
        body = send_feedback(page, "Second message, errors attached.", include=True)
        payload = json.dumps(body or {})
        if body is None:
            fails.append("the ticked feedback never reached /v1/feedback")
        else:
            for marker in ("boom-timeout-7731", "boom-rejection-7732"):
                if marker not in payload:
                    fails.append(f"{marker} is not in the feedback payload")
            if len(body.get("text") or "") > 2000:
                fails.append("the feedback text is over the service's 2000 characters")
            if "data:image" in payload:
                fails.append("image data leaked into the feedback")
        real = [e for e in fh.error_failures(errors, "client errors") if "boom-" not in e]
        fails += real
    finally:
        browser.close()

if fails:
    print("FAIL client error capture")
    for fail in fails:
        print("  -", fail)
    sys.exit(1)
print("PASS client error capture")
