# -*- coding: utf-8 -*-
"""A history database that cannot open is said out loud, and capture is held.

An older build installed over newer data (IndexedDB at a higher version) or a WebView
without IndexedDB made the first history read reject. Boot surfaced it only as an
unhandled rejection: Home kept the parser's "No reports yet.", and Photo still opened
the camera for a report that could never be saved.
"""
import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import flow_harness  # noqa: E402

SCENARIOS = {
    # Opening at a higher version first queues the app's v8 open behind it; that open
    # then fails with a VersionError, exactly as on a downgraded install.
    "newer database on disk": "indexedDB.open('potholes', 9).onsuccess = (e) => e.target.result.close();",
    "no IndexedDB at all": "Object.defineProperty(window, 'indexedDB', {configurable: true, value: undefined});",
}


def run(playwright, name, sabotage):
    failures = []
    browser = playwright.chromium.launch()
    try:
        context = browser.new_context(viewport={"width": 412, "height": 915},
                                      is_mobile=True, device_scale_factor=2.625)
        context.add_init_script(script="(() => {" + "\n".join([
            f'localStorage.setItem("service_url", {json.dumps(flow_harness.SERVICE)});',
            f'localStorage.setItem("data_notice_version", {json.dumps(flow_harness.DATA_NOTICE_VERSION)});',
            'localStorage.setItem("initial_setup_complete", "1");',
            'localStorage.setItem("vision_provider", "shared");',
            sabotage,
        ]) + "})();")
        context.route(f"{flow_harness.SERVICE}/**", flow_harness.central_service)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(f"uncaught: {error}"))
        page.goto(flow_harness.APP)
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        try:
            page.wait_for_function(
                "() => getComputedStyle(document.getElementById('storageBanner') || document.body)"
                ".display !== 'none' && !!document.getElementById('storageBanner')",
                timeout=5000)
        except Exception:
            failures.append(f"{name}: no storage banner on Home")
        state = page.evaluate("""() => {
          const banner = document.getElementById("storageBanner");
          return {
            banner: banner ? banner.textContent : null,
            want: I18N.en.storage_unavailable,
            drive: document.getElementById("driveBtn").disabled,
            photo: document.getElementById("captureBtn").disabled,
          };
        }""")
        if not state["want"] or not (state["banner"] or "").startswith(state["want"]):
            failures.append(f"{name}: banner does not say storage is unavailable: {state}")
        if not state["drive"] or not state["photo"]:
            failures.append(f"{name}: Drive or Photo still enabled with no storage: {state}")
        page.wait_for_timeout(500)
        failures += [f"{name}: {error}" for error in errors]
    finally:
        browser.close()
    return failures


# The open fails while window.__failOpen is set, as a corrupt backing store or a busy
# disk at launch does, and then succeeds: reports saved earlier must come back.
FAIL_ONCE = r"""
window.__failOpen = !!sessionStorage.getItem("fail_open");
const realOpen = IDBFactory.prototype.open;
IDBFactory.prototype.open = function (name, version) {
  if (!window.__failOpen || name !== "potholes") return realOpen.call(this, name, version);
  const request = { error: new DOMException("Internal error opening backing store", "UnknownError") };
  setTimeout(() => request.onerror && request.onerror({ target: request }), 0);
  return request;
};
"""


def run_retry(playwright):
    name = "open fails, then Retry"
    failures = []
    browser = playwright.chromium.launch()
    try:
        context = browser.new_context(viewport={"width": 412, "height": 915},
                                      is_mobile=True, device_scale_factor=2.625)
        context.add_init_script(script="(() => {" + "\n".join([
            f'localStorage.setItem("service_url", {json.dumps(flow_harness.SERVICE)});',
            f'localStorage.setItem("data_notice_version", {json.dumps(flow_harness.DATA_NOTICE_VERSION)});',
            'localStorage.setItem("initial_setup_complete", "1");',
            'localStorage.setItem("vision_provider", "shared");',
            FAIL_ONCE,
        ]) + "})();")
        context.route(f"{flow_harness.SERVICE}/**", flow_harness.central_service)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(f"uncaught: {error}"))
        page.goto(flow_harness.APP)
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.evaluate(flow_harness.report_form_script(12.9116, 77.6389))
        page.evaluate("sessionStorage.setItem('fail_open', '1')")
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.wait_for_function(
            "() => getComputedStyle(document.getElementById('storageBanner')).display !== 'none'",
            timeout=5000)
        listed = page.evaluate("() => document.getElementById('list').textContent")
        if I18N_EMPTY in listed:
            failures.append(f"{name}: Home says there are no reports")
        retry = page.locator("#storageRetry")
        if not retry.count():
            failures.append(f"{name}: the storage banner has no Retry button")
        else:
            page.evaluate("window.__failOpen = false")
            retry.click()
            try:
                page.wait_for_function(
                    "() => document.querySelectorAll('#list .card').length > 0"
                    " && getComputedStyle(document.getElementById('storageBanner')).display === 'none'"
                    " && !document.getElementById('captureBtn').disabled", timeout=10_000)
            except Exception:
                failures.append(f"{name}: Retry did not bring the saved report back")
        failures += [f"{name}: {error}" for error in errors]
    finally:
        browser.close()
    return failures


I18N_EMPTY = "No reports yet"


def main():
    failures = []
    with sync_playwright() as playwright:
        for name, sabotage in SCENARIOS.items():
            failures += run(playwright, name, sabotage)
        failures += run_retry(playwright)
    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("PASS boot says so when history storage cannot open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
