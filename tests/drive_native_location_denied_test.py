# -*- coding: utf-8 -*-
"""On the phone, a refused location permission is read once and not asked again.

Drive threw away Android's answer to the location request and started the WebView's
watchPosition anyway, which asks Android a second time for a permission the tester has
just refused. Drive now keeps the answer: no second request, the status line says
location is off, and the app's settings page is offered.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow

# Registered after the native stub, so it overrides the stub's granted Geolocation.
DENY_NATIVE_LOCATION = """
(() => {
  window.__watchCalls = 0;
  window.__settingsCalls = 0;
  const proto = Object.getPrototypeOf(navigator.geolocation);
  proto.watchPosition = function () { window.__watchCalls++; return 9; };
  proto.clearWatch = function () {};
  const plugins = window.Capacitor && window.Capacitor.Plugins;
  if (!plugins) return;
  plugins.Geolocation.checkPermissions = async () => ({ location: "denied", coarseLocation: "denied" });
  plugins.Geolocation.requestPermissions = async () => ({ location: "denied", coarseLocation: "denied" });
  plugins.DriveMode.openAppSettings = async () => { window.__settingsCalls++; return { opened: true }; };
})();
"""

fails = []
with sync_playwright() as playwright:
    browser, page, errors = open_flow(playwright, native=True)
    try:
        page.context.add_init_script(script=DENY_NATIVE_LOCATION)
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        confirms = []
        # Dismissed, so the drive stays up and its status line can be read.
        page.on("dialog", lambda dialog: (confirms.append(dialog.message), dialog.dismiss()))
        page.locator("#driveBtn").click()
        page.wait_for_function("() => drive && !driveStarting", timeout=30_000)
        expected = page.evaluate("t('location_off')")
        try:
            page.wait_for_function("(text) => document.getElementById('driveStatus').textContent"
                                   " === text", arg=expected, timeout=5_000)
        except Exception:
            fails.append("location refused, but the line reads: "
                         + repr(page.locator("#driveStatus").text_content()))
        page.wait_for_timeout(2000)
        if page.locator("#driveStatus").text_content() != expected:
            fails.append("the location-off line was replaced by: "
                         + repr(page.locator("#driveStatus").text_content()))
        watches = page.evaluate("window.__watchCalls")
        if watches:
            fails.append(f"the WebView was asked for location {watches} time(s) after the refusal")
        if len(confirms) != 1 or expected not in confirms[0]:
            fails.append(f"expected one offer of the settings page, got {confirms}")
        page.locator("#driveStop").click()
        page.wait_for_function("() => !drive && !driveFinalizing", timeout=30_000)
        fails += error_failures(errors, "native location refused")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive native location denied")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive native location denied")
