"""The Android Drive bridge must survive Maps/backgrounding and reconcile on stop."""
import pathlib
import sys

from playwright.sync_api import sync_playwright

APP = "http://localhost:8765/"

INIT = r"""
(() => {
  localStorage.setItem("openai_key", "test-key-never-sent");
  const probe = window.__nativeDriveProbe = {
    listeners: {}, appListeners: {}, start: 0, stop: 0, stopCompleted: 0,
    pause: 0, resume: 0, maps: 0, attach: 0, detach: 0, setVideo: 0,
    exit: 0, ack: 0, lastPreview: null,
    status: {isRunning: false, isPaused: false, isStopping: false,
             sessionId: null, checked: 0, found: 0, already: 0, queued: 0,
             dropped: 0, recordingEnabled: false, isRecording: false,
             videoSupported: true, cameraActive: false, status: "Idle"},
  };
  const emit = (name, data) => (probe.listeners[name] || []).forEach((fn) => fn(data));
  const emitStatus = () => emit("driveStatusChange", {...probe.status});
  const DriveMode = {
    addListener: async (name, fn) => { (probe.listeners[name] ||= []).push(fn); },
    requestDrivePermissions: async () => ({granted: true, notificationsGranted: true}),
    startDrive: async ({recordVideo = false} = {}) => {
      probe.start++;
      probe.status = {...probe.status, isRunning: true, isStopping: false,
        sessionId: "native-test", recordingEnabled: recordVideo,
        isRecording: recordVideo, cameraActive: true, status: "Scanning live"};
      queueMicrotask(emitStatus);
      return {...probe.status};
    },
    getStatus: async () => ({...probe.status}),
    attachPreview: async (rect) => { probe.attach++; probe.lastPreview = rect; },
    detachPreview: async () => { probe.detach++; },
    pauseDrive: async () => {
      probe.pause++;
      probe.status = {...probe.status, isPaused: true, isRecording: false,
        cameraActive: false, status: "Paused"};
      emitStatus();
      return {...probe.status};
    },
    resumeDrive: async () => {
      probe.resume++;
      probe.status = {...probe.status, isPaused: false,
        isRecording: probe.status.recordingEnabled, cameraActive: true, status: "Scanning live"};
      emitStatus();
      return {...probe.status};
    },
    setVideoRecording: async ({enabled}) => {
      probe.setVideo++;
      probe.status = {...probe.status, recordingEnabled: enabled,
        isRecording: enabled && !probe.status.isPaused,
        status: enabled ? "Recording video" : "Scanning live; video not saved"};
      emitStatus();
      return {...probe.status};
    },
    openMaps: async () => { probe.maps++; },
    stopDrive: async () => {
      probe.stop++;
      probe.status = {...probe.status, isStopping: true, status: "Stopping safely"};
      emitStatus();
      return await new Promise((resolve) => {
        probe.completeStop = () => {
          if (probe.stopCompleted) return;
          probe.stopCompleted++;
          const summary = {sessionId: "native-test", checked: 7, found: 1, already: 0};
          probe.status = {...probe.status, isRunning: false, isStopping: false,
            isRecording: false, status: "Stopped"};
          emit("driveEnded", summary);
          resolve(summary);
        };
      });
    },
    syncReports: async () => ({reports: [], count: 0}),
    acknowledgeReports: async ({ids}) => { probe.ack += ids.length; },
    getDrives: async () => ({drives: []}),
    clearNativeData: async () => {},
  };
  const App = {
    addListener: async (name, fn) => { probe.appListeners[name] = fn; },
    exitApp: () => { probe.exit++; },
  };
  Object.defineProperty(window, "Capacitor", {configurable: true, value: {
    isNativePlatform: () => true,
    registerPlugin: (name) => name === "DriveMode" ? DriveMode : {},
    Plugins: {DriveMode, App},
  }});
})();
"""

failures = []
with sync_playwright() as playwright:
    launch_options = {"args": ["--disable-web-security"]}
    system_chrome = pathlib.Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if system_chrome.is_file():
        launch_options["executable_path"] = str(system_chrome)
    browser = playwright.chromium.launch(**launch_options)
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(INIT)
    page = context.new_page()
    page.route("**/nominatim.openstreetmap.org/reverse**", lambda route: route.abort())
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.evaluate("localStorage.setItem('data_notice_version', DATA_NOTICE_VERSION); window.alert = () => {}")
    page.locator("#driveBtn").click()
    page.locator("#nativeDrivePanel").wait_for(state="visible")
    page.wait_for_function("__nativeDriveProbe.attach >= 1")

    initial = page.evaluate("""() => ({
      start: __nativeDriveProbe.start,
      attach: __nativeDriveProbe.attach,
      running: __nativeDriveProbe.status.isRunning,
      preview: __nativeDriveProbe.lastPreview,
      badge: document.querySelector('#nativeCameraBadge').textContent,
    })""")
    if initial["start"] != 1 or not initial["running"]:
        failures.append(f"native Drive did not start exactly one service session: {initial}")
    preview = initial["preview"] or {}
    if preview.get("width", 0) <= 0 or preview.get("height", 0) <= 0:
        failures.append(f"native Preview was not attached to a visible slot: {initial}")
    if "SAVING FRAMES" not in initial["badge"]:
        failures.append(f"initial camera badge did not disclose frame capture: {initial}")

    # GPS/privacy interruptions are a stopped capture, not a camera startup. Keep the
    # full actionable native reason visible while making the badge unambiguous.
    blocked_issue = "GPS is unavailable. Camera, detection, and video are paused; capture resumes after GPS returns."
    page.evaluate("""(issue) => {
      __nativeDriveProbe.status = {...__nativeDriveProbe.status,
        cameraActive: false, captureBlocked: true, captureIssue: issue,
        status: "Waiting for a fresh GPS fix"};
      (__nativeDriveProbe.listeners.driveStatusChange || []).forEach((fn) =>
        fn({...__nativeDriveProbe.status}));
    }""", blocked_issue)
    blocked_hud = page.evaluate("""() => ({
      badge: document.querySelector('#nativeCameraBadge').textContent,
      status: document.querySelector('#nativeDriveStatus').textContent,
    })""")
    if blocked_hud["badge"] != "CAPTURE PAUSED · CAMERA/GPS UNAVAILABLE" or blocked_hud["status"] != blocked_issue:
        failures.append(f"capture blocker was rendered as camera startup: {blocked_hud}")
    page.evaluate("""() => {
      __nativeDriveProbe.status = {...__nativeDriveProbe.status,
        cameraActive: true, captureBlocked: false, captureIssue: null,
        status: "Scanning live"};
      (__nativeDriveProbe.listeners.driveStatusChange || []).forEach((fn) =>
        fn({...__nativeDriveProbe.status}));
    }""")

    page.evaluate("""() => {
      window.__testVisibility = "hidden";
      Object.defineProperty(document, "visibilityState", {
        configurable: true, get: () => window.__testVisibility,
      });
      document.dispatchEvent(new Event("visibilitychange"));
    }""")
    page.wait_for_function("__nativeDriveProbe.detach >= 1")
    background = page.evaluate("""() => ({
      stop: __nativeDriveProbe.stop, detach: __nativeDriveProbe.detach,
      running: __nativeDriveProbe.status.isRunning,
    })""")
    if background["stop"] != 0 or not background["running"]:
        failures.append(f"backgrounding stopped the native capture service: {background}")

    page.evaluate("""() => {
      window.__testVisibility = "visible";
      document.dispatchEvent(new Event("visibilitychange"));
    }""")
    page.wait_for_function(f"__nativeDriveProbe.attach > {initial['attach']}")
    page.locator("#nativeDrivePanel").wait_for(state="visible")

    # Capacitor's native Activity signal is the authoritative fallback on WebViews that
    # omit or delay document.visibilityState transitions. It detaches only the preview;
    # the same foreground camera session continues while Maps/chat owns the screen.
    page.wait_for_function("!!__nativeDriveProbe.appListeners.appStateChange")
    native_detach_before = page.evaluate("__nativeDriveProbe.detach")
    page.evaluate("__nativeDriveProbe.appListeners.appStateChange({isActive: false})")
    page.wait_for_function(f"__nativeDriveProbe.detach > {native_detach_before}")
    inactive = page.evaluate("""() => ({
      start: __nativeDriveProbe.start, stop: __nativeDriveProbe.stop,
      running: __nativeDriveProbe.status.isRunning,
      sessionId: __nativeDriveProbe.status.sessionId,
    })""")
    if inactive != {"start": 1, "stop": 0, "running": True, "sessionId": "native-test"}:
        failures.append(f"native app backgrounding changed the camera session: {inactive}")
    native_attach_before = page.evaluate("__nativeDriveProbe.attach")
    page.evaluate("__nativeDriveProbe.appListeners.appStateChange({isActive: true})")
    page.wait_for_function(f"__nativeDriveProbe.attach > {native_attach_before}")

    # Hardware Back is navigation, not Stop: hide the preview but keep the foreground
    # capture service alive so Maps/calls can remain the foreground app.
    page.wait_for_function("!!__nativeDriveProbe.appListeners.backButton")
    detach_before_back = page.evaluate("__nativeDriveProbe.detach")
    page.evaluate("__nativeDriveProbe.appListeners.backButton()")
    page.locator("#home").wait_for(state="visible")
    page.wait_for_function(f"__nativeDriveProbe.detach > {detach_before_back}")
    after_back = page.evaluate("""() => ({
      start: __nativeDriveProbe.start, stop: __nativeDriveProbe.stop,
      exit: __nativeDriveProbe.exit, running: __nativeDriveProbe.status.isRunning,
    })""")
    if after_back != {"start": 1, "stop": 0, "exit": 0, "running": True}:
        failures.append(f"Back stopped or exited instead of returning Home: {after_back}")

    # Tapping Drive while that service is active reopens its transparent live UI. It
    # must not launch a second camera/service session.
    attach_before_reentry = page.evaluate("__nativeDriveProbe.attach")
    page.locator("#driveBtn").click()
    page.locator("#nativeDrivePanel").wait_for(state="visible")
    page.wait_for_function(f"__nativeDriveProbe.attach > {attach_before_reentry}")
    reentered = page.evaluate("""() => ({
      start: __nativeDriveProbe.start, stop: __nativeDriveProbe.stop,
      running: __nativeDriveProbe.status.isRunning,
    })""")
    if reentered != {"start": 1, "stop": 0, "running": True}:
        failures.append(f"Drive re-entry started a duplicate native session: {reentered}")

    # The opt-in is explicit, and every visible label must follow native recording
    # truth: video is opt-in, while sparse evidence frames are always saved.
    page.locator("#nativeRecordBtn").click()
    page.wait_for_function("__nativeDriveProbe.status.isRecording === true")
    video_on = page.evaluate("""() => ({
      calls: __nativeDriveProbe.setVideo,
      enabled: __nativeDriveProbe.status.recordingEnabled,
      recording: __nativeDriveProbe.status.isRecording,
      button: document.querySelector('#nativeRecordBtn').textContent,
      badge: document.querySelector('#nativeCameraBadge').textContent,
      saved: localStorage.getItem('record_video'),
    })""")
    if (video_on["calls"] != 1 or not video_on["enabled"] or not video_on["recording"]
            or video_on["button"] != "Video: On" or "RECORDING VIDEO" not in video_on["badge"]
            or video_on["saved"] != "1"):
        failures.append(f"video-on UI diverged from native recording truth: {video_on}")

    # A foreground video-call/camera app may temporarily pre-empt this camera. Drive must
    # stay alive, disclose the interruption, and resume the user's video preference when
    # CameraX reports OPEN again—without creating a second service session.
    page.evaluate("__nativeDriveProbe.appListeners.appStateChange({isActive: false})")
    interruption = "Camera is in use by another app. Detection and video are paused; capture resumes automatically when access returns."
    page.evaluate("""(issue) => {
      __nativeDriveProbe.status = {...__nativeDriveProbe.status,
        cameraActive: false, isRecording: false, captureBlocked: true,
        captureIssue: issue, status: issue};
      (__nativeDriveProbe.listeners.driveStatusChange || []).forEach((fn) =>
        fn({...__nativeDriveProbe.status}));
    }""", interruption)
    attach_before_interrupted_return = page.evaluate("__nativeDriveProbe.attach")
    page.evaluate("__nativeDriveProbe.appListeners.appStateChange({isActive: true})")
    page.wait_for_function(f"__nativeDriveProbe.attach > {attach_before_interrupted_return}")
    interrupted_ui = page.evaluate("""() => ({
      start: __nativeDriveProbe.start, stop: __nativeDriveProbe.stop,
      running: __nativeDriveProbe.status.isRunning,
      enabled: __nativeDriveProbe.status.recordingEnabled,
      badge: document.querySelector('#nativeCameraBadge').textContent,
      status: document.querySelector('#nativeDriveStatus').textContent,
    })""")
    if (
        interrupted_ui["start"] != 1
        or interrupted_ui["stop"] != 0
        or not interrupted_ui["running"]
        or not interrupted_ui["enabled"]
        or interrupted_ui["badge"] != "CAPTURE PAUSED · CAMERA/GPS UNAVAILABLE"
        or interrupted_ui["status"] != interruption
    ):
        failures.append(f"camera contention did not preserve a truthful live session: {interrupted_ui}")
    page.evaluate("""() => {
      __nativeDriveProbe.status = {...__nativeDriveProbe.status,
        cameraActive: true, isRecording: true, captureBlocked: false,
        captureIssue: null, status: "Scanning live"};
      (__nativeDriveProbe.listeners.driveStatusChange || []).forEach((fn) =>
        fn({...__nativeDriveProbe.status}));
    }""")
    page.wait_for_function("__nativeDriveProbe.status.isRecording === true")
    recovered = page.evaluate("""() => ({
      start: __nativeDriveProbe.start, stop: __nativeDriveProbe.stop,
      sessionId: __nativeDriveProbe.status.sessionId,
      badge: document.querySelector('#nativeCameraBadge').textContent,
    })""")
    if (
        recovered["start"] != 1
        or recovered["stop"] != 0
        or recovered["sessionId"] != "native-test"
        or "RECORDING VIDEO" not in recovered["badge"]
    ):
        failures.append(f"camera did not resume in the same Drive session: {recovered}")

    page.locator("#nativeRecordBtn").click()
    page.wait_for_function("__nativeDriveProbe.status.recordingEnabled === false")
    video_off = page.evaluate("""() => ({
      calls: __nativeDriveProbe.setVideo,
      recording: __nativeDriveProbe.status.isRecording,
      button: document.querySelector('#nativeRecordBtn').textContent,
      badge: document.querySelector('#nativeCameraBadge').textContent,
      saved: localStorage.getItem('record_video'),
    })""")
    if (video_off["calls"] != 2 or video_off["recording"]
            or video_off["button"] != "Video: Off" or "SAVING FRAMES" not in video_off["badge"]
            or video_off["saved"] != "0"):
        failures.append(f"video-off UI diverged from native recording truth: {video_off}")

    page.locator("#openMapsBtn").click()
    page.locator("#nativePauseBtn").click()
    page.wait_for_function("__nativeDriveProbe.pause === 1")
    page.locator("#nativePauseBtn").click()
    page.wait_for_function("__nativeDriveProbe.resume === 1")
    page.locator("#nativeDriveStop").click()
    page.wait_for_function("__nativeDriveProbe.stop === 1")

    # Stop remains on the transparent Drive screen until native finalization and
    # persistence complete. In particular, neither a double event nor App.exitApp may
    # close the Activity during this barrier.
    page.wait_for_timeout(100)
    while_stopping = page.evaluate("""() => ({
      driveVisible: !document.querySelector('#drive').classList.contains('hidden'),
      homeVisible: !document.querySelector('#home').classList.contains('hidden'),
      disabled: document.querySelector('#nativeDriveStop').disabled,
      stopping: __nativeDriveProbe.status.isStopping,
      completed: __nativeDriveProbe.stopCompleted,
      exit: __nativeDriveProbe.exit,
    })""")
    if while_stopping != {"driveVisible": True, "homeVisible": False, "disabled": True,
                          "stopping": True, "completed": 0, "exit": 0}:
        failures.append(f"Stop UI did not await durable native completion: {while_stopping}")

    page.evaluate("__nativeDriveProbe.completeStop()")
    page.wait_for_function("__nativeDriveProbe.stopCompleted === 1")
    page.locator("#home").wait_for(state="visible")
    final = page.evaluate("""() => ({
      start: __nativeDriveProbe.start, maps: __nativeDriveProbe.maps,
      stop: __nativeDriveProbe.stop, completed: __nativeDriveProbe.stopCompleted,
      exit: __nativeDriveProbe.exit,
    })""")
    if final != {"start": 1, "maps": 1, "stop": 1, "completed": 1, "exit": 0}:
        failures.append(f"native controls did not call the bridge exactly once: {final}")
    if page.evaluate("!!drive"):
        failures.append("native Drive state remained after driveEnded")

    # A data wipe ends the native service before deleting Room/files. Its end event
    # must never re-import the data that the user is in the process of deleting.
    discarded = page.evaluate("""async () => {
      const originalSync = window.syncNativeData;
      const originalLoad = window.loadReports;
      let syncCalls = 0, loadCalls = 0;
      window.syncNativeData = async () => { syncCalls++; };
      window.loadReports = async () => { loadCalls++; };
      try {
        await finishNativeDrive({sessionId: "discard-test", discarded: true});
        return {syncCalls, loadCalls};
      } finally {
        window.syncNativeData = originalSync;
        window.loadReports = originalLoad;
      }
    }""")
    if discarded != {"syncCalls": 0, "loadCalls": 0}:
        failures.append(f"discarded native data was synced back during wipe: {discarded}")

    imported = page.evaluate(r"""async () => {
      const native = {
        id: 91, created_at: Date.now() / 1000, lat: 28.6129, lng: 77.2295,
        photo_data_url: "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
        photo_full_data_url: "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
        is_reportable: 1, is_pothole: 1, damage_type: "pothole_cavity", assessment: "clear",
        looks_like_speed_breaker: false, image_quality: "usable",
        surface_type: "bituminous_asphalt", defect_type: "pothole",
        measurement_provenance: "visual_estimate_no_scale", measurement_confidence: "low",
        on_drivable_surface: true,
        has_localized_cavity: true,
        has_broken_edge_or_rim: true, has_depth_or_surface_loss: true,
        temporal_consistency: "consistent", size: "medium", decision: "accept",
        description: "Test native Drive detection", detection_model: "gpt-5-mini",
        // A strict v6 row can remain unsynced in Room across an app update. It must be
        // imported once under its paved-surface vocabulary, not discarded or retried.
        image_detail: "high", prompt_version: "pothole-binary-v6", schema_version: 6,
        evidence_count: 4, drive_id: "native-import", capture_source: "drive_live",
        source_event_key: "live:native-import:1", captured_at: Date.now() / 1000,
        source_offset_s: 4, gps_accuracy: 5, speed_mps: 8, heading: 0,
      };
      const first = await api("/api/native-report", {method: "POST", body: JSON.stringify(native)});
      const second = await api("/api/native-report", {method: "POST", body: JSON.stringify(native)});
      // v8/schema7 rows can remain unsynced in Room after an app update. This contract
      // introduced temporary drivable surfaces and must import once, then dedupe retries.
      const legacyV8 = {...native, id: 97, lat: 28.7495, lng: 77.0565,
        surface_type: "temporary_drivable_surface",
        prompt_version: "pothole-binary-v8", schema_version: 7,
        drive_id: "native-import-v8", source_event_key: "live:native-import-v8:1"};
      const v8First = await api("/api/native-report",
        {method: "POST", body: JSON.stringify(legacyV8)});
      const v8Second = await api("/api/native-report",
        {method: "POST", body: JSON.stringify(legacyV8)});
      // The currently shipped v9/schema7 contract follows the same import and retry path.
      const currentV9 = {...native, id: 98, lat: 28.5921, lng: 77.0460,
        surface_type: "temporary_drivable_surface",
        prompt_version: "pothole-binary-v9", schema_version: 7,
        drive_id: "native-import-v9", source_event_key: "live:native-import-v9:1"};
      const v9First = await api("/api/native-report",
        {method: "POST", body: JSON.stringify(currentV9)});
      const v9Second = await api("/api/native-report",
        {method: "POST", body: JSON.stringify(currentV9)});
      const obsolete = {...native, id: 92, prompt_version: "road-damage-v4", schema_version: 4,
        source_event_key: "live:native-import:obsolete"};
      const ignored = await api("/api/native-report", {method: "POST", body: JSON.stringify(obsolete)});
      const invalids = [
        {...native, id: 93, looks_like_speed_breaker: true,
          source_event_key: "live:native-import:breaker"},
        {...native, id: 94, has_localized_cavity: false,
          source_event_key: "live:native-import:no-cavity"},
        {...native, id: 95, temporal_consistency: "single_view",
          source_event_key: "live:native-import:single-view"},
        {...native, id: 96, surface_type: "unknown",
          source_event_key: "live:native-import:unknown-surface"},
      ];
      const invalidResults = [];
      for (const value of invalids) invalidResults.push(await api("/api/native-report",
        {method: "POST", body: JSON.stringify(value)}));
      const reports = await api("/api/reports");
      return {first, second, v8First, v8Second, v9First, v9Second, ignored,
        invalidResults, count: reports.length, reports};
    }""")
    if imported["count"] != 3 or not imported["second"]["duplicate"]:
        failures.append(f"native report retry was not idempotent: {imported}")
    for version, first_key, second_key in (
        ("pothole-binary-v8", "v8First", "v8Second"),
        ("pothole-binary-v9", "v9First", "v9Second"),
    ):
        if imported[first_key].get("duplicate") or not imported[second_key].get("duplicate"):
            failures.append(f"{version} native import was not idempotent: {imported}")
    reports_by_version = {report.get("prompt_version"): report for report in imported["reports"]}
    for version in ("pothole-binary-v6", "pothole-binary-v8", "pothole-binary-v9"):
        report = reports_by_version.get(version)
        if not report or not report.get("authority_id") or report.get("status") != "draft":
            failures.append(f"{version} native report did not use the existing authority router: {imported}")
    if imported["ignored"].get("ignored") is not True or imported["count"] != 3:
        failures.append(f"obsolete non-binary native report was imported: {imported}")
    if any(result.get("ignored") is not True for result in imported["invalidResults"]):
        failures.append(f"native report bypassed a persisted binary gate: {imported}")
    context.close()
    browser.close()

if failures:
    print("FAIL")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)
print("NATIVE BACKGROUND DRIVE TEST PASS")
