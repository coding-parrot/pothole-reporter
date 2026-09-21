# -*- coding: utf-8 -*-
"""A photo taken while Android killed the app behind the camera is still checked.

Photo hands off to the system camera. On a 2 to 4 GB phone (and always with "Don't keep
activities") Android reclaims the WebView while the camera is in front, the getPhoto
promise dies with the old page, and Capacitor delivers the photo only as the App
plugin's appRestoredResult event on the relaunched page. Nothing listened for it, so
the app came back on Home with no report. The relaunched page now checks that photo as
a camera capture.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import APP, error_failures, open_flow

fails = []
with sync_playwright() as playwright:
    browser, page, errors = open_flow(playwright)
    try:
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.wait_for_timeout(1500)
        alerts = []
        page.on("dialog", lambda dialog: (
            alerts.append(dialog.message) if dialog.type == "alert" else None, dialog.accept()))
        before = page.evaluate("async () => (await StandaloneAPI.handle('/api/reports')).length")
        page.evaluate("""(url) => window.__fireNative("appRestoredResult", {
          pluginId: "Camera", methodName: "getPhoto", success: true,
          data: { webPath: url, format: "jpeg", saved: false } })""",
                      APP + "example-pothole.jpg")
        try:
            page.locator("#detail").wait_for(state="visible", timeout=30_000)
        except Exception:
            fails.append("the restored camera photo never reached a report: "
                         + page.evaluate("document.querySelector('.screen.active, section:not([hidden])')?.id || ''"))
        reports = page.evaluate("async () => await StandaloneAPI.handle('/api/reports')")
        if len(reports) != before + 1:
            fails.append(f"expected one new report, found {len(reports) - before}")
        elif reports[0].get("capture_source") != "manual_camera":
            fails.append(f"the restored photo was saved as {reports[0].get('capture_source')!r}")

        # A cancelled or failed camera call, or another plugin's result, starts nothing.
        page.evaluate("() => show('home')")
        page.evaluate("""() => {
          window.__fireNative("appRestoredResult", { pluginId: "Camera", methodName: "getPhoto",
            success: false, error: { message: "User cancelled photos app" } });
          window.__fireNative("appRestoredResult", { pluginId: "Filesystem",
            methodName: "readFile", success: true, data: {} });
        }""")
        page.wait_for_timeout(1500)
        if not page.locator("#home").is_visible():
            fails.append("a cancelled restored camera call left Home")
        if alerts:
            fails.append(f"a cancelled restored camera call raised {alerts}")

        # A camera call that failed for any other reason lost the photo: say so, and how
        # to get it back, rather than landing on Home as if nothing had been taken.
        page.evaluate("""() => window.__fireNative("appRestoredResult", { pluginId: "Camera",
          methodName: "getPhoto", success: false, error: { message: "Unable to process image" } })""")
        page.wait_for_timeout(1000)
        expected = page.evaluate("t('photo_not_recovered')")
        if alerts != [expected]:
            fails.append(f"a failed restored photo did not ask for a retake: {alerts}")
        fails += error_failures(errors, "restored camera result")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL camera restored result")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS camera restored result")
