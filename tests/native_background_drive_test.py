"""The Android Drive bridge must survive Maps/backgrounding and reconcile on stop."""
import pathlib
import sys

from playwright.sync_api import sync_playwright

APP = "http://localhost:8765/"

INIT = r"""
(() => {
  localStorage.setItem("openai_key", "test-key-never-sent");
  const probe = window.__nativeDriveProbe = {
    listeners: {}, start: 0, stop: 0, pause: 0, resume: 0, maps: 0, ack: 0,
    status: {isRunning: false, isPaused: false, sessionId: null, checked: 0,
             found: 0, already: 0, queued: 0, dropped: 0, status: "Idle"},
  };
  const emit = (name, data) => (probe.listeners[name] || []).forEach((fn) => fn(data));
  const DriveMode = {
    addListener: async (name, fn) => { (probe.listeners[name] ||= []).push(fn); },
    requestDrivePermissions: async () => ({granted: true, notificationsGranted: true}),
    startDrive: async () => {
      probe.start++;
      probe.status = {...probe.status, isRunning: true, sessionId: "native-test", status: "Scanning live"};
      queueMicrotask(() => emit("driveStatusChange", probe.status));
      return probe.status;
    },
    getStatus: async () => probe.status,
    pauseDrive: async () => { probe.pause++; probe.status = {...probe.status, isPaused: true, status: "Paused"}; return probe.status; },
    resumeDrive: async () => { probe.resume++; probe.status = {...probe.status, isPaused: false, status: "Scanning live"}; return probe.status; },
    openMaps: async () => { probe.maps++; },
    stopDrive: async () => {
      probe.stop++;
      const summary = {sessionId: "native-test", checked: 7, found: 1, already: 0};
      probe.status = {...probe.status, isRunning: false, status: "Stopped"};
      queueMicrotask(() => emit("driveEnded", summary));
      return {stopped: true};
    },
    syncReports: async () => ({reports: [], count: 0}),
    acknowledgeReports: async ({ids}) => { probe.ack += ids.length; },
    getDrives: async () => ({drives: []}),
    clearNativeData: async () => {},
  };
  Object.defineProperty(window, "Capacitor", {configurable: true, value: {
    isNativePlatform: () => true,
    registerPlugin: (name) => name === "DriveMode" ? DriveMode : {},
    Plugins: {DriveMode, App: {addListener: async () => {}}},
  }});
})();
"""

failures = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--disable-web-security"])
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(INIT)
    page = context.new_page()
    page.route("**/nominatim.openstreetmap.org/reverse**", lambda route: route.abort())
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.evaluate("localStorage.setItem('data_notice_version', DATA_NOTICE_VERSION); window.alert = () => {}")
    page.locator("#driveBtn").click()
    page.locator("#nativeDrivePanel").wait_for(state="visible")

    page.evaluate("""() => {
      let state = "hidden";
      Object.defineProperty(document, "visibilityState", {configurable: true, get: () => state});
      document.dispatchEvent(new Event("visibilitychange"));
    }""")
    page.wait_for_timeout(50)
    before = page.evaluate("__nativeDriveProbe")
    if before["stop"] != 0 or not before["status"]["isRunning"]:
        failures.append(f"backgrounding stopped native Drive: {before}")

    page.locator("#openMapsBtn").click()
    page.locator("#nativePauseBtn").click()
    page.wait_for_function("__nativeDriveProbe.pause === 1")
    page.locator("#nativePauseBtn").click()
    page.wait_for_function("__nativeDriveProbe.resume === 1")
    page.locator("#nativeDriveStop").click()
    page.wait_for_function("__nativeDriveProbe.stop === 1")
    page.locator("#home").wait_for(state="visible")
    final = page.evaluate("__nativeDriveProbe")
    if final["start"] != 1 or final["maps"] != 1 or final["stop"] != 1:
        failures.append(f"native controls did not call the bridge exactly once: {final}")
    if page.evaluate("!!drive"):
        failures.append("native Drive state remained after driveEnded")

    imported = page.evaluate(r"""async () => {
      const native = {
        id: 91, created_at: Date.now() / 1000, lat: 28.6129, lng: 77.2295,
        photo_data_url: "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
        photo_full_data_url: "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
        is_reportable: 1, damage_type: "pothole_cavity", assessment: "clear",
        image_quality: "usable", on_drivable_surface: true,
        has_broken_edge_or_rim: true, has_depth_or_surface_loss: true,
        temporal_consistency: "consistent", size: "medium", decision: "accept",
        description: "Test native Drive detection", detection_model: "gpt-5-mini",
        image_detail: "high", prompt_version: "road-damage-v3", schema_version: 3,
        evidence_count: 4, drive_id: "native-import", capture_source: "drive_live",
        source_event_key: "live:native-import:1", captured_at: Date.now() / 1000,
        source_offset_s: 4, gps_accuracy: 5, speed_mps: 8, heading: 0,
      };
      const first = await api("/api/native-report", {method: "POST", body: JSON.stringify(native)});
      const second = await api("/api/native-report", {method: "POST", body: JSON.stringify(native)});
      const reports = await api("/api/reports");
      return {first, second, count: reports.length, report: reports[0]};
    }""")
    if imported["count"] != 1 or not imported["second"]["duplicate"]:
        failures.append(f"native report retry was not idempotent: {imported}")
    if not imported["report"].get("authority_id") or imported["report"].get("status") != "draft":
        failures.append(f"native report did not use the existing authority router: {imported}")
    context.close()
    browser.close()

if failures:
    print("FAIL")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)
print("NATIVE BACKGROUND DRIVE TEST PASS")
