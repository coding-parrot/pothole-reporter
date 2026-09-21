# -*- coding: utf-8 -*-
"""A photo check that the server never answers can be cancelled from the spinner.

The shared detector is allowed 100 s. On a dead link the tester sat on "AI checking
for road damage..." for all of it with no button, and Android's Back did not stop the
upload. The progress screen now offers Cancel for a photo check, which aborts the
request and returns Home without an alert, and after a while says it is still waiting.
"""

import sys

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, PHOTO, open_central

held = []


def script(route, request, path, central):
    if path == "/v1/vision/detect":
        held.append(route)   # never answered: only the app can end this request
        return True
    return False


fails = []
with sync_playwright() as playwright:
    central = Central(script)
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.evaluate("window.__stillCheckingAfterMs = 1500")
        page.wait_for_timeout(600)
        aborted = []
        page.on("requestfailed", lambda request: aborted.append(request.url)
                if request.url.endswith("/v1/vision/detect") else None)
        page.set_input_files("#fileInput", str(PHOTO))
        for _ in range(200):
            if held:
                break
            page.wait_for_timeout(100)
        if not held:
            raise RuntimeError("the photo never reached the detector")
        cancel = page.locator("#progressCancel")
        if not cancel.is_visible():
            fails.append("no Cancel button while the photo is being checked")
        page.wait_for_function("() => document.getElementById('progressText').textContent"
                               " === t('still_checking')", timeout=5_000)
        if cancel.is_visible():
            if cancel.inner_text() != page.evaluate("t('manual_cancel')"):
                fails.append(f"the Cancel button reads {cancel.inner_text()!r}")
            cancel.click()
            page.locator("#home").wait_for(state="visible", timeout=1_000)
            page.wait_for_timeout(500)
            if not aborted:
                fails.append("Cancel returned Home but left the upload running")
            if dialogs:
                fails.append(f"Cancel raised an alert: {dialogs}")
            if page.locator("#progressCancel").is_visible():
                fails.append("the Cancel button stayed visible after the check ended")
            reports = page.evaluate("async () => (await StandaloneAPI.handle('/api/reports')).length")
            if reports:
                fails.append(f"a cancelled check saved {reports} report(s)")
        # A second photo after a cancel is checked normally.
        central.script = None
        page.set_input_files("#fileInput", str(PHOTO))
        page.locator("#detail").wait_for(state="visible", timeout=30_000)
        if page.locator("#progressCancel").is_visible():
            fails.append("the Cancel button leaked onto the next screen")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        for route in held:
            try:
                route.abort()
            except Exception:
                pass   # the app already cancelled it
        browser.close()

if fails:
    print("FAIL manual capture cancel")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS manual capture cancel")
