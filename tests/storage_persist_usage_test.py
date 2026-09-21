#!/usr/bin/env python3
"""The app asks to keep its storage and says how much it uses.

It never called navigator.storage.persist(), so the browser may evict a tester's
reports under pressure, and nothing showed the total it stores (about 4 MB per accepted
Photo) until a write failed.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow, report_form_script


SPY = """() => {
  window.__persistCalls = 0;
  navigator.storage.persist = async () => { window.__persistCalls += 1; return true; };
}"""

failures = []
with sync_playwright() as playwright:
    browser, page, errors = open_flow(playwright, native=False)
    try:
        page.evaluate(SPY)
        page.evaluate(report_form_script())
        page.evaluate(report_form_script())
        page.wait_for_timeout(200)
        persist_calls = page.evaluate("() => window.__persistCalls")
        page.evaluate("() => openSettings()")
        page.wait_for_function("() => /\\d/.test(($('storageUsage') || {}).textContent || '')",
                               timeout=5000)
        shown = page.evaluate("() => $('storageUsage').textContent")
        usage_mb = page.evaluate("async () => (await navigator.storage.estimate()).usage / 1048576")
    finally:
        browser.close()

if persist_calls != 1:
    failures.append(f"persist() was called {persist_calls} times after two saved reports, expected once")
import re
match = re.search(r"([\d.]+)\s*MB", shown or "")
if not match:
    failures.append(f"Settings shows no storage figure: {shown!r}")
elif abs(float(match.group(1)) - usage_mb) > max(0.1, usage_mb * 0.1):
    failures.append(f"Settings shows {match.group(1)} MB but estimate() says {usage_mb:.2f} MB")
failures += error_failures(errors, "storage persist and usage")

if failures:
    print("STORAGE PERSIST USAGE TEST FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("STORAGE PERSIST USAGE TEST PASS")
