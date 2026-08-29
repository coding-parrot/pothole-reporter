# -*- coding: utf-8 -*-
"""Photo stays a one-tap, pothole-only capture path on Web and Android."""
import os
import pathlib
import sys

from playwright.sync_api import sync_playwright


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
CLIENT = (ROOT / "static" / "standalone.js").read_text(encoding="utf-8")

INIT_NATIVE_CAMERA_FAILURE = r"""
(() => {
  localStorage.setItem("openai_key", "test-key-never-sent");
  localStorage.setItem("initial_setup_complete", "1");
  const probe = window.__photoProbe = {events: [], fileClicks: 0, alerts: []};
  Object.defineProperty(window, "Capacitor", {configurable: true, writable: true, value: {
    isNativePlatform: () => true,
    Plugins: {
      Camera: {
        requestPermissions: async () => probe.events.push("camera_permission"),
        getPhoto: async () => {
          probe.events.push("get_photo");
          throw new Error("native camera test failure");
        },
      },
      Geolocation: {
        requestPermissions: async () => probe.events.push("location_permission"),
      },
    },
  }});
})();
"""


def check(condition, message, failures):
    if not condition:
        failures.append(message)


failures = []

# The chooser itself must be gone. Legacy civic-report rendering and storage support may
# remain so an update can still display reports made by an older app version.
for removed_id in ("issuePicker", "issueRoad", "issueGarbage", "issueManhole", "issueBack"):
    check(f'id="{removed_id}"' not in INDEX,
          f"removed Photo category control is still shipped: {removed_id}", failures)
check('fd.append("issue_type", "road_damage")' in INDEX,
      "Photo FormData is not pinned to road_damage", failures)
check('api("/api/report", { method: "POST", body: fd })' in INDEX,
      "Photo no longer submits through the verified pothole endpoint", failures)
check('api("/api/civic-report"' not in INDEX,
      "the current UI still exposes the unverified civic-report endpoint", failures)

# A separate version makes the single-photo instruction auditable without changing the
# Drive prompt contract used by the native closed-test build.
check('const PHOTO_PROMPT_VERSION = "pothole-photo-only-v4";' in CLIENT,
      "Photo prompt contract/version is missing or changed without updating this test", failures)
check("const promptVersion = driveMode ? PROMPT_VERSION : PHOTO_PROMPT_VERSION;" in CLIENT,
      "single-photo reports do not record the Photo-only prompt version", failures)
check('DETECT_PROMPT + (driveMode ? "" : PHOTO_ONLY_PROMPT_SUFFIX)' in CLIENT,
      "Photo-only rules are not attached exclusively to single-photo inference", failures)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--disable-web-security"])

    # One tap must go directly through native permissions into the native camera. A native
    # camera failure must stop there; silently opening the WebView file picker would turn a
    # cancellation/error into a second, confusing capture UI.
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(INIT_NATIVE_CAMERA_FAILURE)
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        "typeof StandaloneAPI !== 'undefined' && typeof DATA_NOTICE_VERSION === 'string'"
    )
    page.evaluate(
        """() => {
          localStorage.setItem("data_notice_version", DATA_NOTICE_VERSION);
          window.alert = (message) => window.__photoProbe.alerts.push(String(message));
          document.getElementById("fileInput").click = () => {
            window.__photoProbe.fileClicks++;
            window.__photoProbe.events.push("file_picker");
          };
          StandaloneAPI.prewarm = () => window.__photoProbe.events.push("prewarm");
        }"""
    )
    page.locator("#captureBtn").click()
    page.wait_for_function("window.__photoProbe.events.includes('get_photo')")
    page.wait_for_timeout(100)
    native = page.evaluate(
        """() => ({
          events: window.__photoProbe.events,
          fileClicks: window.__photoProbe.fileClicks,
          alerts: window.__photoProbe.alerts,
          chooserPresent: ["issuePicker", "issueRoad", "issueGarbage", "issueManhole"]
            .some((id) => document.getElementById(id)),
        })"""
    )
    check(native["events"] == [
        "camera_permission", "location_permission", "prewarm", "get_photo"
    ], f"one Photo tap did not go straight to the native camera: {native}", failures)
    check(native["fileClicks"] == 0,
          f"native camera error incorrectly fell back to a file picker: {native}", failures)
    check(not native["chooserPresent"], "a Photo category chooser remains in the DOM", failures)
    check("native camera test failure" in native["alerts"][0],
          f"native camera failure was not surfaced clearly: {native}", failures)
    context.close()

    # Exercise the submission boundary itself without contacting OpenAI. The request is
    # deliberately left pending after inspection so no fake detector response is needed.
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(
        """() => {
          localStorage.setItem("openai_key", "test-key-never-sent");
          localStorage.setItem("initial_setup_complete", "1");
        }"""
    )
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function("typeof handleFile === 'function'")
    submitted = page.evaluate(
        """async () => {
          const captured = [];
          StandaloneAPI.handle = async (path, options = {}) => {
            if (path === "/api/report" || path === "/api/civic-report") {
              const body = options.body;
              captured.push({
                path,
                method: options.method,
                issueType: body && body.get("issue_type"),
                captureSource: body && body.get("capture_source"),
              });
              return new Promise(() => {});
            }
            throw new Error(`unexpected test API call: ${path}`);
          };
          handleFile(new File(["fake-jpeg"], "pothole.jpg", {type: "image/jpeg"}), {
            captureSource: "manual_import",
            locationConfirmed: false,
          });
          await Promise.resolve();
          return captured;
        }"""
    )
    check(submitted == [{
        "path": "/api/report", "method": "POST",
        "issueType": "road_damage", "captureSource": "manual_import",
    }], f"Photo submission escaped the pothole-only endpoint/issue type: {submitted}", failures)

    prompt = page.evaluate(
        """() => ({
          version: StandaloneAPI.__pure.PHOTO_PROMPT_VERSION,
          suffix: StandaloneAPI.__pure.PHOTO_ONLY_PROMPT_SUFFIX,
        })"""
    )
    suffix = prompt["suffix"].lower()
    check(prompt["version"] == "pothole-photo-only-v4",
          f"runtime Photo prompt version is not the audited contract: {prompt['version']}", failures)
    check(all(term in suffix for term in (
        "detect potholes only", "garbage", "open or damaged manholes",
        "every other civic issue", "is_pothole false", "never reinterpret",
    )), "runtime Photo prompt does not reject every removed civic category", failures)
    context.close()
    browser.close()

if failures:
    print("FAIL")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)

print("PHOTO POTHOLE-ONLY TEST PASS")
