# -*- coding: utf-8 -*-
"""In the public web build, Email complaint opens the visitor's mail app.

The web build has no Capacitor composer. Its branch only logged a line to the console,
then marked the report "Opened in email" although nothing had opened. It now follows a
mailto: link with the routed recipient, the subject and the body.
"""

import sys
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, capture, open_central, routed

# A capture-phase listener sees the link the app follows, and stops the browser from
# handing mailto: to an external program on the test machine.
WATCH_MAILTO = """() => {
  window.__mailto = [];
  document.addEventListener("click", (event) => {
    const link = event.target && event.target.closest && event.target.closest("a[href^='mailto:']");
    if (link) { window.__mailto.push(link.href); event.preventDefault(); }
  }, true);
}"""

fails = []
with sync_playwright() as playwright:
    central = Central(routed)
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        if page.evaluate("() => !!(window.Capacitor && Capacitor.isNativePlatform && Capacitor.isNativePlatform())"):
            fails.append("the web build test is running with a native bridge")
        page.evaluate(WATCH_MAILTO)
        page.wait_for_timeout(600)
        outcome, text = capture(page, dialogs)
        if outcome != "detail" or not page.locator("#detail #sendBtn").count():
            fails.append(f"no sendable draft to email: {outcome} {text!r}")
        else:
            page.locator("#detail #sendBtn").click()
            page.wait_for_timeout(1500)
            links = page.evaluate("() => window.__mailto")
            if len(links) != 1:
                fails.append(f"Email complaint followed {len(links)} mailto links")
            else:
                parsed = urlparse(links[0])
                query = parse_qs(parsed.query)
                if unquote(parsed.path) != "ka.kalaburagi.cc@gmail.com":
                    fails.append(f"mailto recipient was {unquote(parsed.path)!r}")
                subject = (query.get("subject") or [""])[0]
                body = (query.get("body") or [""])[0]
                if "Pothole complaint" not in subject:
                    fails.append(f"mailto subject was {subject!r}")
                if "Coordinates: 12.971600, 77.594600" not in body:
                    fails.append("mailto body lost the complaint text")
                if "cannot attach" not in body:
                    fails.append("mailto body does not say the photo is not attached")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    finally:
        browser.close()

if fails:
    print("FAIL web mailto fallback")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS web mailto fallback")
