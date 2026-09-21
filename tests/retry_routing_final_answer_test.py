# -*- coding: utf-8 -*-
"""Retry routing only says "still unavailable" when the answer is still retryable.

retryCivicRouting replaces the reason with the new route's. A road_class_unknown report
that now resolves to a national highway got the "try again when the connection is
available" alert, and then a card saying the answer is final with no Retry button.
"""
import os
import sys

from playwright.sync_api import sync_playwright

APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")

SETUP = r"""
(nextReason) => {
  window.__retryCalls = 0;
  const report = {
    id: 90412, status: "unrouted", unrouted_reason: "road_class_unknown",
    unrouted_body: null, lat: 13.00271, lng: 77.58406, created_at: Date.now() / 1000,
    is_pothole: true, assessment: "damaged", size: "medium",
  };
  const realApi = api;
  window.api = async (path, opts) => {
    if (String(path).endsWith("/retry-routing")) {
      window.__retryCalls += 1;
      return { ...report, unrouted_reason: nextReason,
               unrouted_body: nextReason === "national_highway" ? "NH 44" : null };
    }
    return realApi(path, opts);
  };
  openDetail(report, [report]);
  return !!document.getElementById("retryRoutingBtn");
}
"""


def run_case(page, next_reason):
    alerts = []

    def on_dialog(dialog):
        alerts.append(dialog.message)
        dialog.accept()

    page.on("dialog", on_dialog)
    try:
        has_button = page.evaluate(SETUP, next_reason)
        if not has_button:
            return None, alerts
        page.click("#retryRoutingBtn")
        page.wait_for_function("window.__retryCalls === 1", timeout=10000)
        # The handler reloads the list and reopens the card after the call returns.
        page.wait_for_timeout(1500)
    finally:
        page.remove_listener("dialog", on_dialog)
    return True, alerts


def main():
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 412, "height": 915},
                                          is_mobile=True, device_scale_factor=2.625)
            page = context.new_page()
            page.goto(APP)
            page.wait_for_function(
                "typeof openDetail === 'function' && typeof api === 'function'",
                timeout=30000)
            unchanged = page.evaluate("t('retry_routing_unchanged')")

            ok, alerts = run_case(page, "national_highway")
            if not ok:
                failures.append("road_class_unknown detail shows no Retry routing button")
            elif unchanged in alerts:
                failures.append("a retry that resolved to a national highway still alerted "
                                "'Routing is still unavailable'")

            page.goto(APP)
            page.wait_for_function("typeof openDetail === 'function'", timeout=30000)
            ok, alerts = run_case(page, "road_class_unknown")
            if ok and unchanged not in alerts:
                failures.append("a retry that is still road_class_unknown did not say so")
        finally:
            browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("RETRY ROUTING FINAL ANSWER TEST PASS")


if __name__ == "__main__":
    main()
