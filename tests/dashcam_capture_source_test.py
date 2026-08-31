#!/usr/bin/env python3
"""Settings and native Drive must keep phone and RTSP capture sources isolated."""

from __future__ import annotations

import pathlib
import sys

from playwright.sync_api import sync_playwright


APP = "http://localhost:8765/"

NATIVE_MOCK = r"""
(() => {
  const probe = window.__captureSourceProbe = {
    listeners: {}, permissionArgs: [], startArgs: [], attach: 0,
    status: {
      isRunning: false, isStarting: false, isPaused: false, isStopping: false,
      captureStopped: false, sessionId: null, startRequestId: null,
      checked: 0, found: 0, already: 0, queued: 0, dropped: 0,
      recordingEnabled: false, isRecording: false, videoSupported: true,
      cameraActive: false, sourceActive: false, sourceState: "idle",
      captureSource: "phone_camera", status: "Idle",
    },
  };
  const emit = (name, value) => (probe.listeners[name] || []).forEach((fn) => fn(value));
  probe.emitStatus = (changes) => {
    probe.status = {...probe.status, ...changes};
    emit("driveStatusChange", {...probe.status});
  };
  const DriveMode = {
    addListener: async (name, fn) => { (probe.listeners[name] ||= []).push(fn); },
    requestDrivePermissions: async (options = {}) => {
      probe.permissionArgs.push({...options});
      return {granted: true, notificationsGranted: true};
    },
    startDrive: async (options = {}) => {
      probe.startArgs.push({...options});
      probe.status = {...probe.status,
        isRunning: true, sessionId: "capture-source-test",
        startRequestId: options.startRequestId || null,
        captureSource: options.captureSource || "phone_camera",
        sourceActive: true, sourceState: "streaming", sourceIssue: null,
        cameraActive: true, status: options.captureSource === "dashcam"
          ? "Scanning dashcam stream" : "Scanning live"};
      queueMicrotask(() => emit("driveStatusChange", {...probe.status}));
      return {...probe.status};
    },
    getStatus: async () => ({...probe.status}),
    getDriveEndSummary: async () => ({available: false}),
    attachPreview: async () => { probe.attach++; },
    detachPreview: async () => {},
    syncReports: async () => ({reports: [], count: 0}),
    syncRepairObservations: async () => ({observations: [], count: 0}),
    acknowledgeReports: async ({ids = []} = {}) => ({acknowledged: ids.length}),
    getDrives: async () => ({drives: []}),
    beginRepairTargetSync: async ({ids = []} = {}) => {
      probe.repairIds = ids;
      return {token: "capture-source-repair"};
    },
    appendRepairTargetBatch: async () => ({appended: true}),
    commitRepairTargetSync: async () => ({replaced: (probe.repairIds || []).length}),
    abortRepairTargetSync: async () => ({aborted: true}),
  };
  const App = {addListener: async () => {}};
  Object.defineProperty(window, "Capacitor", {configurable: true, value: {
    isNativePlatform: () => true,
    registerPlugin: (name) => name === "DriveMode" ? DriveMode : {},
    Plugins: {DriveMode, App},
  }});
})();
"""


def make_context(browser, init: str):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(NATIVE_MOCK)
    context.add_init_script(init)
    return context


def wait_ready(page):
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        "typeof validateDashcamRtspUrl === 'function' && !nativeInitialRestorePending",
        timeout=30_000,
    )
    page.locator("#home").wait_for(state="visible")
    page.evaluate("localStorage.setItem('data_notice_version', DATA_NOTICE_VERSION)")


failures: list[str] = []
with sync_playwright() as playwright:
    launch_options = {"args": ["--disable-web-security"]}
    system_chrome = pathlib.Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if system_chrome.is_file():
        launch_options["executable_path"] = str(system_chrome)
    browser = playwright.chromium.launch(**launch_options)

    # Dashcam stays opt-in, validates before persistence, and passes only the trimmed
    # secret URL across the native bridge. The URL never appears in visible status copy.
    dashcam_context = make_context(
        browser,
        """localStorage.setItem('openai_key', 'test-key-never-sent');
        localStorage.setItem('initial_setup_complete', '1');""",
    )
    page = dashcam_context.new_page()
    wait_ready(page)
    page.locator("#gearBtn").click()
    initial = page.evaluate(
        """() => ({
          source: document.querySelector('#setCaptureSource').value,
          dashcamVisible: !document.querySelector('#dashcamSettings').classList.contains('hidden'),
          disabled: document.querySelector('#setDashcamRtspUrl').disabled,
          type: document.querySelector('#setDashcamRtspUrl').type,
          autocomplete: document.querySelector('#setDashcamRtspUrl').autocomplete,
        })"""
    )
    if initial != {
        "source": "phone_camera", "dashcamVisible": False, "disabled": True,
        "type": "password", "autocomplete": "off",
    }:
        failures.append(f"default Settings did not keep Dashcam opt-in and secret: {initial}")

    page.locator("#setCaptureSource").select_option("dashcam")
    conditional = page.evaluate(
        """() => ({
          visible: !document.querySelector('#dashcamSettings').classList.contains('hidden'),
          enabled: !document.querySelector('#setDashcamRtspUrl').disabled,
          note: document.querySelector('#dashcamRtspNote').textContent,
        })"""
    )
    if not conditional["visible"] or not conditional["enabled"]:
        failures.append(f"Dashcam selection did not reveal its only URL field: {conditional}")
    if "not its camera or audio" not in conditional["note"]:
        failures.append(f"Dashcam Settings did not disclose silent capture: {conditional}")

    validation = page.evaluate(
        """() => ({
          good: !!validateDashcamRtspUrl('rtsp://user:pass@192.168.1.1:554/live?channel=1'),
          http: validateDashcamRtspUrl('https://192.168.1.1/live'),
          relative: validateDashcamRtspUrl('/live'),
          blank: validateDashcamRtspUrl('   '),
          whitespace: validateDashcamRtspUrl('rtsp://192.168.1.1/live stream'),
          fragment: validateDashcamRtspUrl('rtsp://192.168.1.1/live#secret'),
          zeroPort: validateDashcamRtspUrl('rtsp://192.168.1.1:00000/live'),
          largePort: validateDashcamRtspUrl('rtsp://192.168.1.1:65536/live'),
          backslash: validateDashcamRtspUrl(String.raw`rtsp://192.168.1.1\\live`),
          ipv6: !!validateDashcamRtspUrl('rtsp://[fe80::1]:554/live'),
        })"""
    )
    if validation != {
        "good": True, "http": None, "relative": None, "blank": None,
        "whitespace": None, "fragment": None, "zeroPort": None,
        "largePort": None, "backslash": None, "ipv6": True,
    }:
        failures.append(f"RTSP validation accepted an unsafe or unsupported address: {validation}")

    page.locator("#setDashcamRtspUrl").fill("https://192.168.1.1/live")
    page.locator("#setSave").click()
    page.wait_for_function("document.querySelector('#setDashcamRtspUrl').getAttribute('aria-invalid') === 'true'")
    rejected = page.evaluate(
        """() => ({
          settingsVisible: !document.querySelector('#settings').classList.contains('hidden'),
          sourceSaved: localStorage.getItem(CAPTURE_SOURCE_KEY),
          message: document.querySelector('#dashcamRtspNote').textContent,
        })"""
    )
    if not rejected["settingsVisible"] or rejected["sourceSaved"] is not None:
        failures.append(f"invalid Dashcam URL was persisted or dismissed Settings: {rejected}")
    if "rtsp://" not in rejected["message"]:
        failures.append(f"invalid Dashcam URL did not get actionable inline feedback: {rejected}")

    secret_rtsp = "rtsp://dash-user:dash-pass@192.168.1.1:554/live?channel=1"
    page.locator("#setDashcamRtspUrl").fill(f"  {secret_rtsp}  ")
    page.locator("#setSave").click()
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    saved = page.evaluate(
        """() => ({
          source: localStorage.getItem(CAPTURE_SOURCE_KEY),
          urlTrimmed: localStorage.getItem(DASHCAM_RTSP_URL_KEY) ===
            'rtsp://dash-user:dash-pass@192.168.1.1:554/live?channel=1',
        })"""
    )
    if saved != {"source": "dashcam", "urlTrimmed": True}:
        failures.append(f"valid Dashcam Settings were not saved atomically: {saved}")

    page.locator("#driveBtn").click()
    page.locator("#nativeDrivePanel").wait_for(state="visible")
    page.wait_for_function("__captureSourceProbe.startArgs.length === 1 && __captureSourceProbe.attach > 0")
    started = page.evaluate(
        """() => {
          const permission = __captureSourceProbe.permissionArgs[0] || {};
          const start = __captureSourceProbe.startArgs[0] || {};
          return {
            permissionSource: permission.captureSource,
            startSource: start.captureSource,
            urlExact: start.dashcamRtspUrl ===
              'rtsp://dash-user:dash-pass@192.168.1.1:554/live?channel=1',
            badge: document.querySelector('#nativeCameraBadge').textContent,
            previewLabel: document.querySelector('#nativePreviewSlot').getAttribute('aria-label'),
            placeholderHidden: document.querySelector('#nativeSourceMessage').classList.contains('hidden'),
            recordControlHidden: document.querySelector('#nativeRecordBtn').classList.contains('hidden'),
            tip: document.querySelector('#driveTip').textContent,
            secretVisible: document.body.innerText.includes('dash-user:dash-pass'),
          };
        }"""
    )
    if started["permissionSource"] != "dashcam" or started["startSource"] != "dashcam" or not started["urlExact"]:
        failures.append(f"Dashcam native start contract was incomplete: {started}")
    if "DASHCAM ACTIVE" not in started["badge"] or started["previewLabel"] != "Live dashcam preview":
        failures.append(f"Dashcam active state was not explicit in the preview: {started}")
    if (not started["placeholderHidden"] or not started["recordControlHidden"]
            or "Wi-Fi" not in started["tip"] or started["secretVisible"]):
        failures.append(f"Dashcam preview copy hid video or exposed the saved URL: {started}")

    page.evaluate(
        """__captureSourceProbe.emitStatus({
          sourceActive: false, cameraActive: false, sourceState: 'reconnecting',
          sourceIssue: 'Could not open rtsp://dash-user:dash-pass@192.168.1.1/live. Reconnecting safely.',
          status: 'Reconnecting to dashcam',
        })"""
    )
    reconnecting = page.evaluate(
        """() => ({
          badge: document.querySelector('#nativeCameraBadge').textContent,
          status: document.querySelector('#nativeDriveStatus').textContent,
          preview: document.querySelector('#nativeSourceMessage').textContent,
          previewVisible: !document.querySelector('#nativeSourceMessage').classList.contains('hidden'),
          secretVisible: document.body.innerText.includes('dash-user:dash-pass'),
        })"""
    )
    if "DASHCAM INTERRUPTED" not in reconnecting["badge"]:
        failures.append(f"Dashcam reconnecting badge was ambiguous: {reconnecting}")
    if (not reconnecting["previewVisible"] or "Reconnecting safely" not in reconnecting["status"]
            or "saved RTSP address" not in reconnecting["preview"]):
        failures.append(f"Dashcam reconnecting reason was not visible: {reconnecting}")
    if reconnecting["secretVisible"]:
        failures.append("Dashcam credentials appeared in Drive status or preview copy")
    dashcam_context.close()

    # Existing installs and the browser/native phone path remain phone-camera-first.
    phone_context = make_context(
        browser,
        """localStorage.setItem('openai_key', 'test-key-never-sent');
        localStorage.setItem('initial_setup_complete', '1');
        localStorage.setItem('drive_capture_source', 'phone_camera');
        localStorage.setItem('dashcam_rtsp_url', 'rtsp://stale-user:stale-pass@10.0.0.1/live');""",
    )
    page = phone_context.new_page()
    wait_ready(page)
    page.locator("#driveBtn").click()
    page.locator("#nativeDrivePanel").wait_for(state="visible")
    page.wait_for_function("__captureSourceProbe.startArgs.length === 1 && __captureSourceProbe.attach > 0")
    phone = page.evaluate(
        """() => {
          const permission = __captureSourceProbe.permissionArgs[0] || {};
          const start = __captureSourceProbe.startArgs[0] || {};
          return {
            permissionSource: permission.captureSource,
            startSource: start.captureSource,
            dashcamUrlEmpty: start.dashcamRtspUrl === '',
            badge: document.querySelector('#nativeCameraBadge').textContent,
            previewLabel: document.querySelector('#nativePreviewSlot').getAttribute('aria-label'),
            placeholderHidden: document.querySelector('#nativeSourceMessage').classList.contains('hidden'),
            recordControlHidden: document.querySelector('#nativeRecordBtn').classList.contains('hidden'),
            staleSecretVisible: document.body.innerText.includes('stale-user:stale-pass'),
          };
        }"""
    )
    expected_phone = {
        "permissionSource": "phone_camera", "startSource": "phone_camera",
        "dashcamUrlEmpty": True, "badge": "● CAMERA ACTIVE · SAVING FRAMES",
        "previewLabel": "Live phone camera preview", "placeholderHidden": True,
        "recordControlHidden": False,
        "staleSecretVisible": False,
    }
    if phone != expected_phone:
        failures.append(f"phone-camera Drive contract regressed: {phone}")
    phone_context.close()
    browser.close()

if failures:
    print("FAIL")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)

print("DASHCAM CAPTURE SOURCE TEST PASS")
