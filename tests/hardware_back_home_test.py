# -*- coding: utf-8 -*-
"""Hardware Back from Home closes the app, and from the Pothole map it returns Home.

The back handler once walked a screen list that named a "review" screen the app does
not have. $("review") is null, so the loop threw on Home and on the map, the
backButton listener died, and the phone's Back key did nothing on either screen.
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import flow_harness  # noqa: E402


def visible(page, element_id):
    return page.evaluate(
        "(id) => !document.getElementById(id).classList.contains('hidden')", element_id)


def fire_back(page, failures):
    thrown = page.evaluate("""() => {
      try { window.__fireNative('backButton', {canGoBack: false}); return null; }
      catch (error) { return String(error); }
    }""")
    if thrown:
        failures.append(f"backButton listener threw: {thrown}")


def main():
    failures = []
    with sync_playwright() as playwright:
        browser, page, errors = flow_harness.open_flow(playwright, native=True)
        try:
            page.wait_for_function("() => !document.getElementById('home').classList.contains('hidden')")

            # Home: the handler reports "not handled" so the listener exits the app.
            try:
                handled = page.evaluate("() => window.handleAppBack()")
            except Exception as error:  # the old code threw here
                handled = f"threw: {error}"
            if handled is not False:
                failures.append(f"Home: handleAppBack() returned {handled!r}, want false")
            fire_back(page, failures)
            page.wait_for_timeout(200)
            exits = page.evaluate("() => window.__exitAppCalls")
            if exits != 1:
                failures.append(f"Home: Back called exitApp {exits} times, want 1")

            # Pothole map: Back returns to Home.
            page.click("#dashBtn")
            page.wait_for_function("() => !document.getElementById('dash').classList.contains('hidden')")
            fire_back(page, failures)
            page.wait_for_timeout(200)
            if not visible(page, "home") or visible(page, "dash"):
                failures.append("map: Back did not return to Home")
            exits = page.evaluate("() => window.__exitAppCalls")
            if exits != 1:
                failures.append(f"map: Back called exitApp (total {exits}), want no new call")

            failures += flow_harness.error_failures(errors, "hardware back")
        finally:
            browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("PASS hardware back on Home and the Pothole map")
    return 0


if __name__ == "__main__":
    sys.exit(main())
