# -*- coding: utf-8 -*-
"""Boot builds the History list once and asks the service for its health once.

Two costs used to repeat on every start. The list was rebuilt a second time after the
native sync even when there was nothing native to merge (130 ms on a 300 report
history), and /v1/health was requested twice: once by Home's banner check and again by
the standalone warm-up probe. A Photo tap then probed a third time although the answer
was already known. Every probe is a Lambda call against the service's overall cap.
"""
import json
import pathlib
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import flow_harness  # noqa: E402

LIST_OBSERVER = r"""
(() => {
  window.__listRebuilds = 0;
  document.addEventListener("DOMContentLoaded", () => {
    const list = document.getElementById("list");
    new MutationObserver((records) => {
      window.__listRebuilds += records.filter((record) => record.target === list
        && record.type === "childList").length;
    }).observe(list, { childList: true });
  });
})();
"""


def main():
    failures = []
    health_calls = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 412, "height": 915},
                                          is_mobile=True, device_scale_factor=2.625)
            context.add_init_script(script="(() => {" + "\n".join([
                f'localStorage.setItem("service_url", {json.dumps(flow_harness.SERVICE)});',
                f'localStorage.setItem("data_notice_version", {json.dumps(flow_harness.DATA_NOTICE_VERSION)});',
                'localStorage.setItem("initial_setup_complete", "1");',
                'localStorage.setItem("vision_provider", "shared");',
            ]) + "})();" + LIST_OBSERVER)

            def service(route, request):
                if urlparse(request.url).path == "/v1/health":
                    health_calls.append(request.url)
                flow_harness.central_service(route, request)

            context.route(f"{flow_harness.SERVICE}/**", service)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(f"uncaught: {error}"))
            page.goto(flow_harness.APP)
            page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
            page.wait_for_timeout(2000)

            rebuilds = page.evaluate("() => window.__listRebuilds")
            if rebuilds != 1:
                failures.append(f"boot rebuilt the History list {rebuilds} times, want 1")
            if len(health_calls) != 1:
                failures.append(f"boot sent {len(health_calls)} /v1/health probes, want 1")

            # Photo with a known-good service: straight to the picker, no new probe.
            page.evaluate("""() => {
              window.alert = () => {};
              document.getElementById("fileInput").click = () => {};
            }""")
            before = len(health_calls)
            page.click("#captureBtn")
            page.wait_for_timeout(1000)
            if len(health_calls) != before:
                failures.append(
                    f"a Photo tap sent {len(health_calls) - before} /v1/health probes "
                    "although the service was just confirmed healthy")
            failures += errors
        finally:
            browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("PASS boot renders History once and probes health once")
    return 0


if __name__ == "__main__":
    sys.exit(main())
