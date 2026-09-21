# -*- coding: utf-8 -*-
"""The "shared checking is unavailable" banner clears when connectivity returns.

Health was only re-read on a Photo or Drive tap, so a phone that booted without
signal kept telling the tester the service was down long after it came back.
"""
import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import flow_harness  # noqa: E402

BANNER_SHOWN = "() => getComputedStyle(document.getElementById('banner')).display !== 'none'"
BANNER_HIDDEN = "() => getComputedStyle(document.getElementById('banner')).display === 'none'"


def main():
    failures = []
    online = {"value": False}
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
            ]) + "})();")

            def service(route, request):
                if online["value"]:
                    flow_harness.central_service(route, request)
                else:
                    route.abort("internetdisconnected")

            context.route(f"{flow_harness.SERVICE}/**", service)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(f"uncaught: {error}"))
            page.goto(flow_harness.APP)
            page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
            try:
                page.wait_for_function(BANNER_SHOWN, timeout=8000)
            except Exception:
                failures.append("offline boot did not show the unavailable banner")

            online["value"] = True
            page.evaluate("() => window.dispatchEvent(new Event('online'))")
            try:
                page.wait_for_function(BANNER_HIDDEN, timeout=5000)
            except Exception:
                failures.append("banner still shown 5 s after connectivity returned")
            failures += errors
        finally:
            browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("PASS unavailable banner clears when the phone comes back online")
    return 0


if __name__ == "__main__":
    sys.exit(main())
