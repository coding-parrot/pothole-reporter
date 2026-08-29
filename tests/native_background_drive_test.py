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
    exit: 0, ack: 0, terminalAck: 0, lastPreview: null, terminalSummaries: {},
    status: {isRunning: false, isStarting: false, isPaused: false, isStopping: false,
             captureStopped: false,
             sessionId: null, startRequestId: null,
             checked: 0, found: 0, already: 0, queued: 0,
             dropped: 0, recordingEnabled: false, isRecording: false,
             videoSupported: true, cameraActive: false, status: "Idle"},
  };
  const emit = (name, data) => (probe.listeners[name] || []).forEach((fn) => fn(data));
  const emitStatus = () => emit("driveStatusChange", {...probe.status});
  const DriveMode = {
    addListener: async (name, fn) => { (probe.listeners[name] ||= []).push(fn); },
    requestDrivePermissions: async () => ({granted: true, notificationsGranted: true}),
    startDrive: async ({recordVideo = false, startRequestId = null} = {}) => {
      probe.start++;
      probe.status = {...probe.status, isRunning: true, isStopping: false,
        captureStopped: false,
        sessionId: "native-test", startRequestId, recordingEnabled: recordVideo,
        isRecording: recordVideo, cameraActive: true, status: "Scanning live"};
      queueMicrotask(emitStatus);
      return {...probe.status};
    },
    getStatus: async () => ({...probe.status}),
    getDriveEndSummary: async ({sessionId} = {}) => {
      if (sessionId) return probe.terminalSummaries[String(sessionId)] || {available: false};
      const values = Object.values(probe.terminalSummaries);
      return values.length ? values[values.length - 1] : {available: false};
    },
    acknowledgeDriveEndSummary: async ({sessionId}) => {
      probe.terminalAck++;
      delete probe.terminalSummaries[String(sessionId)];
      return {acknowledged: true};
    },
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
      probe.status = {...probe.status, isRunning: false, isStopping: true,
        status: "Stopping safely"};
      emitStatus();
      return await new Promise((resolve) => {
        probe.completeCameraStop = () => {
          probe.status = {...probe.status, cameraActive: false, isRecording: false,
            captureStopped: true, status: "Camera off · finalizing saved data"};
          emitStatus();
        };
        probe.completeStop = () => {
          if (probe.stopCompleted) return;
          probe.stopCompleted++;
          const summary = {sessionId: "native-test",
            startRequestId: probe.status.startRequestId,
            checked: 7, found: 1, already: 0};
          probe.status = {...probe.status, isRunning: false, isStopping: false,
            captureStopped: true, isRecording: false, status: "Stopped"};
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

    # Before CameraX confirms closure, Stop remains on the transparent Drive screen.
    # In particular, neither a double event nor App.exitApp may imply that capture ended.
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
        failures.append(f"Stop UI returned Home before verified camera closure: {while_stopping}")

    # Camera closure is a separate, earlier control-plane boundary. Home may now be
    # shown, but every mutating control and native sync stays locked until driveEnded.
    page.evaluate("__nativeDriveProbe.completeCameraStop()")
    page.locator("#home").wait_for(state="visible")
    camera_off = page.evaluate("""async () => {
      let syncRejected = false;
      try { await syncNativeData(); } catch (_) { syncRejected = true; }
      return {
        driveVisible: !document.querySelector('#drive').classList.contains('hidden'),
        homeVisible: !document.querySelector('#home').classList.contains('hidden'),
        driveLocked: document.querySelector('#driveBtn').disabled,
        photoLocked: document.querySelector('#captureBtn').disabled,
        settingsLocked: document.querySelector('#gearBtn').disabled,
        wipeLocked: document.querySelector('#wipeBtn').disabled,
        stillOwned: !!(drive && drive.native && drive.stopping && drive.captureStopped),
        banner: document.querySelector('#banner').textContent,
        syncRejected,
        completed: __nativeDriveProbe.stopCompleted,
      };
    }""")
    expected_camera_off = {
        "driveVisible": False, "homeVisible": True, "driveLocked": True,
        "photoLocked": True, "settingsLocked": True, "wipeLocked": True,
        "stillOwned": True, "banner": "Camera off · saving drive data…",
        "syncRejected": True, "completed": 0,
    }
    if camera_off != expected_camera_off:
        failures.append(f"verified camera-off phase was not safely locked: {camera_off}")

    # A recreated WebView obtains the phase from getStatus; it does not depend on having
    # received the original event. A delayed pre-close status is then monotonic and must
    # not bounce the UI back to Drive.
    page.evaluate("""async () => {
      drive = null;
      nativeCaptureFinalizingSessionId = null;
      setNativeFinishLocked(false);
      await restoreNativeDrive({syncWhenIdle: false});
    }""")
    page.locator("#home").wait_for(state="visible")
    page.evaluate("""() => {
      const stale = {...__nativeDriveProbe.status, captureStopped: false,
        status: "Stopping safely"};
      (__nativeDriveProbe.listeners.driveStatusChange || []).forEach((fn) => fn(stale));
    }""")
    monotonic = page.evaluate("""() => ({
      homeVisible: !document.querySelector('#home').classList.contains('hidden'),
      captureStopped: !!(drive && drive.captureStopped),
      locked: document.querySelector('#driveBtn').disabled,
      nativePanelSelected: !document.querySelector('#nativeDrivePanel').classList.contains('hidden') &&
        document.querySelector('#webDrivePanel').classList.contains('hidden'),
    })""")
    if monotonic != {"homeVisible": True, "captureStopped": True, "locked": True,
                     "nativePanelSelected": True}:
        failures.append(f"recreated or stale Stop state regressed camera-off truth: {monotonic}")

    page.evaluate("__nativeDriveProbe.completeStop()")
    page.wait_for_function("__nativeDriveProbe.stopCompleted === 1")
    page.locator("#home").wait_for(state="visible")
    final = page.evaluate("""() => ({
      start: __nativeDriveProbe.start, maps: __nativeDriveProbe.maps,
      stop: __nativeDriveProbe.stop, completed: __nativeDriveProbe.stopCompleted,
      exit: __nativeDriveProbe.exit,
      driveLocked: document.querySelector('#driveBtn').disabled,
      photoLocked: document.querySelector('#captureBtn').disabled,
    })""")
    if final != {"start": 1, "maps": 1, "stop": 1, "completed": 1, "exit": 0,
                 "driveLocked": False, "photoLocked": False}:
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

    # A getStatus call begun before Start must not apply its delayed idle result to the
    # newer camera session.
    stale_idle = page.evaluate("""async () => {
      const plugin = Capacitor.Plugins.DriveMode;
      const originalGetStatus = plugin.getStatus;
      let resolveStatus = null, requested = false;
      plugin.getStatus = () => { requested = true; return new Promise((resolve) => {
        resolveStatus = resolve;
      }); };
      const restoring = restoreNativeDrive({syncWhenIdle: false});
      for (let i = 0; i < 50 && !requested; i++) await new Promise((r) => setTimeout(r, 1));
      bumpNativeDriveControlEpoch();
      drive = {native: true, sessionId: "race-new"};
      setDrivePanels(true); show("drive");
      resolveStatus({isRunning: false, isStopping: false, sessionId: null, status: "Idle"});
      await restoring;
      const result = {sessionId: drive && drive.sessionId,
        driveVisible: !document.querySelector("#drive").classList.contains("hidden")};
      drive = null; setDriveActiveButton(false); show("home");
      plugin.getStatus = originalGetStatus;
      return result;
    }""")
    if stale_idle != {"sessionId": "race-new", "driveVisible": True}:
        failures.append(f"stale idle status erased a newer Drive: {stale_idle}")

    # The inverse race must not resurrect a session after its terminal event won.
    stale_running = page.evaluate("""async () => {
      const plugin = Capacitor.Plugins.DriveMode;
      const originalGetStatus = plugin.getStatus;
      let resolveStatus = null, requested = false;
      drive = {native: true, sessionId: "race-ended"};
      setDrivePanels(true); show("drive");
      plugin.getStatus = () => { requested = true; return new Promise((resolve) => {
        resolveStatus = resolve;
      }); };
      const restoring = restoreNativeDrive({syncWhenIdle: false});
      for (let i = 0; i < 50 && !requested; i++) await new Promise((r) => setTimeout(r, 1));
      await finishNativeDrive({sessionId: "race-ended", reason: "Stopped", discarded: true});
      resolveStatus({isRunning: true, isStopping: false, sessionId: "race-ended",
        cameraActive: true, status: "Scanning live"});
      await restoring;
      const result = {drivePresent: !!drive,
        homeVisible: !document.querySelector("#home").classList.contains("hidden")};
      plugin.getStatus = originalGetStatus;
      return result;
    }""")
    if stale_running != {"drivePresent": False, "homeVisible": True}:
        failures.append(f"stale running status resurrected an ended Drive: {stale_running}")

    # An accepted ACTION_START has a native process-owned Starting state. Neither a
    # same-page Idle read nor a full WebView recreation may hide that pending camera;
    # the eventual running session is re-adopted on the transparent Drive screen.
    starting_races = page.evaluate("""async () => {
      const plugin = Capacitor.Plugins.DriveMode;
      const originalGetStatus = plugin.getStatus;
      driveStarting = true;
      drive = {native: true, sessionId: null, starting: true};
      setDrivePanels(true); show("drive");
      plugin.getStatus = async () => ({isRunning: false, isStarting: false,
        isStopping: false, sessionId: null, status: "Idle"});
      await restoreNativeDrive({syncWhenIdle: false});
      const samePage = {preserved: !!drive,
        visible: !document.querySelector("#drive").classList.contains("hidden")};

      driveStarting = false;
      drive = null;
      plugin.getStatus = async () => ({isRunning: false, isStarting: true,
        isStopping: false, sessionId: null, cameraActive: false,
        startRequestId: "33333333-3333-4333-8333-333333333333",
        status: "Starting camera and GPS"});
      await restoreNativeDrive({syncWhenIdle: false});
      const recreated = {starting: !!(drive && drive.starting),
        visible: !document.querySelector("#drive").classList.contains("hidden")};

      plugin.getStatus = async () => ({isRunning: true, isStarting: false,
        isStopping: false, sessionId: "late-start", cameraActive: true,
        startRequestId: "33333333-3333-4333-8333-333333333333",
        status: "Scanning live"});
      await restoreNativeDrive({syncWhenIdle: false});
      const adopted = {sessionId: drive && drive.sessionId,
        starting: !!(drive && drive.starting),
        visible: !document.querySelector("#drive").classList.contains("hidden")};
      cancelNativeStartStatusPoll();
      drive = null; setDriveActiveButton(false); show("home");
      plugin.getStatus = originalGetStatus;
      return {samePage, recreated, adopted};
    }""")
    expected_starting_races = {
        "samePage": {"preserved": True, "visible": True},
        "recreated": {"starting": True, "visible": True},
        "adopted": {"sessionId": "late-start", "starting": False, "visible": True},
    }
    if starting_races != expected_starting_races:
        failures.append(f"accepted native Start was hidden or detached: {starting_races}")

    # If a control callback temporarily returned the WebView Home while the admitted
    # native start was resolving, success must re-read Android and visibly re-adopt the
    # exact live camera session. A running foreground camera may never stay behind Home.
    resolved_start_readoption = page.evaluate("""async () => {
      const plugin = Capacitor.Plugins.DriveMode;
      const originalStartDrive = plugin.startDrive;
      const originalGetStatus = plugin.getStatus;
      let resolveStart = null, startRequested = false, freshReads = 0;
      let requestedStartId = null;
      const live = {isRunning: true, isStarting: false, isStopping: false,
        sessionId: "resolved-re-adopt", cameraActive: true, checked: 0,
        found: 0, already: 0, queued: 0, dropped: 0, status: "Scanning live"};
      handledNativeEnds.delete(live.sessionId);
      drive = null; setDriveActiveButton(false); show("home");
      plugin.startDrive = async ({startRequestId}) => {
        startRequested = true;
        requestedStartId = startRequestId;
        return await new Promise((resolve) => { resolveStart = resolve; });
      };
      plugin.getStatus = async () => {
        freshReads++;
        return {...live, startRequestId: requestedStartId};
      };
      const pending = startDrive();
      for (let i = 0; i < 100 && !startRequested; i++) {
        await new Promise((resolve) => setTimeout(resolve, 1));
      }
      // Simulate an older terminal/control callback winning the visible UI while the
      // native service is still successfully opening CameraX.
      drive = null; setDriveActiveButton(false); show("home");
      resolveStart({...live, startRequestId: requestedStartId});
      await pending;
      const result = {sessionId: drive && drive.sessionId, freshReads,
        driveVisible: !document.querySelector("#drive").classList.contains("hidden"),
        homeVisible: !document.querySelector("#home").classList.contains("hidden"),
        nativePanelVisible: !document.querySelector("#nativeDrivePanel").classList.contains("hidden"),
        activeLabel: document.querySelector("#driveBtn").getAttribute("aria-label")};
      drive = null; setDriveActiveButton(false); show("home");
      plugin.startDrive = originalStartDrive;
      plugin.getStatus = originalGetStatus;
      return result;
    }""")
    if resolved_start_readoption != {
        "sessionId": "resolved-re-adopt", "freshReads": 1,
        "driveVisible": True, "homeVisible": False, "nativePanelVisible": True,
        "activeLabel": "Drive active — view",
    }:
        failures.append(
            f"resolved native Start stayed hidden behind Home: {resolved_start_readoption}"
        )

    # Delayed status/end events from session A must not claim the blank JS context for
    # newly admitted session B. Ownership is the caller-generated native start token,
    # not the temporary absence of B's timestamp-based session ID.
    cross_session_start = page.evaluate("""async () => {
      const beforeAck = __nativeDriveProbe.terminalAck;
      const requestA = "11111111-1111-4111-8111-111111111111";
      const requestB = "22222222-2222-4222-8222-222222222222";
      driveStarting = true;
      drive = {native: true, sessionId: null, startRequestId: requestB,
        starting: true, stopping: false, captureStopped: false};
      // This is the recreated/fallback state after startDrive's global finally ran.
      driveStarting = false;
      setDrivePanels(true); show("drive");
      document.querySelector("#banner").textContent = "";
      const staleStopAccepted = presentNativeStoppingStatus({isRunning: false,
        isStopping: true, captureStopped: true, sessionId: "old-a",
        startRequestId: requestA, status: "Stopping safely"});
      (__nativeDriveProbe.listeners.driveStatusChange || []).forEach((fn) => fn({
        isRunning: true, isStopping: false, sessionId: "old-a",
        startRequestId: requestA, cameraActive: true, status: "Scanning live"}));
      __nativeDriveProbe.terminalSummaries["old-a"] = {available: true, stopped: true,
        sessionId: "old-a", startRequestId: requestA, checked: 2, found: 0,
        already: 0, discarded: false, error: "Camera A failed", reason: "Stopped"};
      (__nativeDriveProbe.listeners.driveEnded || []).forEach((fn) =>
        fn(__nativeDriveProbe.terminalSummaries["old-a"]));
      for (let i = 0; i < 100 && __nativeDriveProbe.terminalAck === beforeAck; i++) {
        await new Promise((resolve) => setTimeout(resolve, 1));
      }
      const afterA = {sessionId: drive && drive.sessionId,
        requestId: drive && drive.startRequestId, stopping: !!(drive && drive.stopping),
        captureStopped: !!(drive && drive.captureStopped), staleStopAccepted,
        acked: __nativeDriveProbe.terminalAck - beforeAck,
        banner: document.querySelector("#banner").textContent};
      (__nativeDriveProbe.listeners.driveStatusChange || []).forEach((fn) => fn({
        isRunning: true, isStopping: false, sessionId: "new-b",
        startRequestId: requestB, cameraActive: true, status: "Scanning live"}));
      const afterB = {sessionId: drive && drive.sessionId,
        requestId: drive && drive.startRequestId, stopping: !!(drive && drive.stopping),
        captureStopped: !!(drive && drive.captureStopped)};
      driveStarting = false;
      drive = null; setDriveActiveButton(false); show("home");
      return {afterA, afterB};
    }""")
    if cross_session_start != {
        "afterA": {"sessionId": None,
                   "requestId": "22222222-2222-4222-8222-222222222222",
                   "stopping": False, "captureStopped": False,
                   "staleStopAccepted": False, "acked": 1,
                   "banner": "Previous drive: Camera A failed"},
        "afterB": {"sessionId": "new-b",
                   "requestId": "22222222-2222-4222-8222-222222222222",
                   "stopping": False, "captureStopped": False},
    }:
        failures.append(f"old Drive callbacks poisoned a new Start: {cross_session_start}")

    photo_restore_reentry = page.evaluate("""async () => {
      const originalCapture = beginPotholeCapture;
      const originalPromise = nativeInitialRestorePromise;
      let releaseRestore = null, opens = 0;
      nativeInitialRestorePending = true;
      nativeInitialRestorePromise = new Promise((resolve) => { releaseRestore = resolve; });
      beginPotholeCapture = async () => { opens++; };
      photoCaptureStarting = false;
      document.querySelector("#captureBtn").click();
      document.querySelector("#captureBtn").click();
      const claimedBeforeRestore = photoCaptureStarting;
      releaseRestore();
      for (let i = 0; i < 100 && photoCaptureStarting; i++) {
        await new Promise((resolve) => setTimeout(resolve, 1));
      }
      const result = {claimedBeforeRestore, opens, released: !photoCaptureStarting};
      beginPotholeCapture = originalCapture;
      nativeInitialRestorePromise = originalPromise;
      nativeInitialRestorePending = false;
      return result;
    }""")
    if photo_restore_reentry != {
        "claimedBeforeRestore": True, "opens": 1, "released": True,
    }:
        failures.append(
            f"two Photo taps crossed initial restore concurrently: {photo_restore_reentry}"
        )

    camera_mutual_admission = page.evaluate("""async () => {
      const originalDriveAdmitted = startDriveAdmitted;
      const originalCapture = beginPotholeCapture;
      const originalPromise = nativeInitialRestorePromise;
      let driveOpens = 0, photoOpens = 0;
      startDriveAdmitted = async () => {
        await nativeInitialRestorePromise;
        driveOpens++;
      };
      beginPotholeCapture = async () => { photoOpens++; };
      async function round(first, second) {
        let release = null;
        nativeInitialRestorePending = true;
        nativeInitialRestorePromise = new Promise((resolve) => { release = resolve; });
        first.click(); second.click();
        release();
        for (let i = 0; i < 100 && cameraAdmissionOwner; i++) {
          await new Promise((resolve) => setTimeout(resolve, 1));
        }
      }
      const driveButton = document.querySelector("#driveBtn");
      const photoButton = document.querySelector("#captureBtn");
      await round(driveButton, photoButton);
      await round(photoButton, driveButton);
      const result = {driveOpens, photoOpens, owner: cameraAdmissionOwner,
        driveStarting, photoCaptureStarting};
      startDriveAdmitted = originalDriveAdmitted;
      beginPotholeCapture = originalCapture;
      nativeInitialRestorePromise = originalPromise;
      nativeInitialRestorePending = false;
      return result;
    }""")
    if camera_mutual_admission != {
        "driveOpens": 1, "photoOpens": 1, "owner": None,
        "driveStarting": False, "photoCaptureStarting": False,
    }:
        failures.append(
            f"Photo and Drive crossed the shared camera admission: {camera_mutual_admission}"
        )

    # Full recreation after native teardown has no JS session object. The latest exact
    # unacknowledged terminal summary must still be shown once and then acknowledged.
    latest_terminal = page.evaluate("""async () => {
      const beforeAck = __nativeDriveProbe.terminalAck;
      __nativeDriveProbe.status = {isRunning: false, isStarting: false,
        isStopping: false, sessionId: null, status: "Idle"};
      __nativeDriveProbe.terminalSummaries["lost-end"] = {
        available: true, stopped: true, sessionId: "lost-end", checked: 4,
        found: 1, already: 0, discarded: true,
        error: "Camera privacy access was revoked", reason: "Start failed"
      };
      drive = null;
      await restoreNativeDrive({syncWhenIdle: false});
      return {handled: handledNativeEnds.has("lost-end"),
        banner: document.querySelector("#banner").textContent,
        acked: __nativeDriveProbe.terminalAck - beforeAck,
        retained: !!__nativeDriveProbe.terminalSummaries["lost-end"]};
    }""")
    if latest_terminal != {"handled": True,
                           "banner": "Camera privacy access was revoked",
                           "acked": 1, "retained": False}:
        failures.append(f"sessionless terminal result was lost after recreation: {latest_terminal}")

    listener_gap_terminal = page.evaluate("""async () => {
      const beforeAck = __nativeDriveProbe.terminalAck;
      const summary = {available: true, stopped: true, sessionId: "listener-gap-end",
        checked: 0, found: 0, already: 0, discarded: true,
        error: "Camera failed while restoring the app", reason: "Start failed"};
      __nativeDriveProbe.terminalSummaries[summary.sessionId] = summary;
      drive = null;
      (__nativeDriveProbe.listeners.driveEnded || []).forEach((fn) => fn(summary));
      for (let i = 0; i < 100 && (!handledNativeEnds.has(summary.sessionId) || nativeFinishPromise); i++) {
        await new Promise((resolve) => setTimeout(resolve, 2));
      }
      return {handled: handledNativeEnds.has(summary.sessionId),
        banner: document.querySelector("#banner").textContent,
        acked: __nativeDriveProbe.terminalAck - beforeAck,
        drivePresent: !!drive};
    }""")
    if listener_gap_terminal != {"handled": True,
                                 "banner": "Camera failed while restoring the app",
                                 "acked": 1, "drivePresent": False}:
        failures.append(
            f"terminal event between listener registration and status was lost: {listener_gap_terminal}"
        )

    # The running half of the same listener/getStatus race must visibly adopt the native
    # camera. The listener bumps the control epoch, so the delayed Idle result is expected
    # to abort; leaving presentation solely to restoreNativeDrive would strand Home over
    # a still-running foreground camera.
    listener_gap_running = page.evaluate("""async () => {
      const plugin = Capacitor.Plugins.DriveMode;
      const originalGetStatus = plugin.getStatus;
      let resolveStatus = null, requested = false;
      stopNativePreview();
      driveStarting = false;
      drive = null;
      setDriveActiveButton(false);
      show("home");
      plugin.getStatus = () => { requested = true; return new Promise((resolve) => {
        resolveStatus = resolve;
      }); };
      const restoring = restoreNativeDrive({syncWhenIdle: false});
      for (let i = 0; i < 50 && !requested; i++) await new Promise((r) => setTimeout(r, 1));
      const attachBefore = __nativeDriveProbe.attach;
      const running = {isRunning: true, isStarting: false, isStopping: false,
        sessionId: "listener-gap-running", startRequestId: "listener-gap-start",
        cameraActive: true, status: "Scanning live"};
      __nativeDriveProbe.status = {...__nativeDriveProbe.status, ...running};
      (__nativeDriveProbe.listeners.driveStatusChange || []).forEach((fn) => fn(running));
      resolveStatus({isRunning: false, isStarting: false, isStopping: false,
        sessionId: null, status: "Idle"});
      await restoring;
      for (let i = 0; i < 100 && __nativeDriveProbe.attach <= attachBefore; i++) {
        await new Promise((resolve) => setTimeout(resolve, 2));
      }
      const result = {sessionId: drive && drive.sessionId,
        starting: !!(drive && drive.starting),
        driveVisible: !document.querySelector("#drive").classList.contains("hidden"),
        panelVisible: !document.querySelector("#nativeDrivePanel").classList.contains("hidden"),
        previewAttached: __nativeDriveProbe.attach > attachBefore};
      stopNativePreview();
      drive = null;
      setDriveActiveButton(false);
      show("home");
      __nativeDriveProbe.status = {...__nativeDriveProbe.status, isRunning: false,
        isStarting: false, isStopping: false, sessionId: null, cameraActive: false,
        status: "Idle"};
      plugin.getStatus = originalGetStatus;
      return result;
    }""")
    if listener_gap_running != {"sessionId": "listener-gap-running",
                               "starting": False, "driveVisible": True,
                               "panelVisible": True, "previewAttached": True}:
        failures.append(
            f"running event between listener registration and status stayed behind Home: "
            f"{listener_gap_running}"
        )

    # Idle recovery consumes the exact durable summary. The compatibility fallback is
    # deliberately provisional so a later real failure remains visible and authoritative.
    terminal_recovery = page.evaluate("""async () => {
      const plugin = Capacitor.Plugins.DriveMode;
      const originalTerminal = plugin.getDriveEndSummary;
      __nativeDriveProbe.status = {isRunning: false, isStopping: false,
        sessionId: null, status: "Idle"};
      __nativeDriveProbe.terminalSummaries["exact-end"] = {
        available: true, stopped: true, sessionId: "exact-end", checked: 3,
        found: 0, already: 0, discarded: true,
        reason: "Report evidence storage is full"
      };
      drive = {native: true, sessionId: "exact-end", stopping: true, captureStopped: true};
      await restoreNativeDrive({syncWhenIdle: false});
      const exact = {handled: handledNativeEnds.has("exact-end"),
        banner: document.querySelector("#banner").textContent};

      plugin.getDriveEndSummary = undefined;
      drive = {native: true, sessionId: "compat-end", stopping: true, captureStopped: true};
      await restoreNativeDrive({syncWhenIdle: false});
      const provisionalHandled = handledNativeEnds.has("compat-end");
      await finishNativeDrive({sessionId: "compat-end", discarded: true,
        reason: "Camera service failed"});
      const compatibility = {provisionalHandled,
        handled: handledNativeEnds.has("compat-end"),
        banner: document.querySelector("#banner").textContent};
      plugin.getDriveEndSummary = originalTerminal;
      return {exact, compatibility};
    }""")
    if terminal_recovery["exact"] != {
        "handled": True, "banner": "Report evidence storage is full"
    }:
        failures.append(f"durable terminal summary was not recovered exactly: {terminal_recovery}")
    if terminal_recovery["compatibility"] != {
        "provisionalHandled": False, "handled": True, "banner": "Camera service failed"
    }:
        failures.append(f"provisional fallback suppressed the real terminal event: {terminal_recovery}")

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
      // v9/schema7 rows from the previous release remain importable and idempotent.
      const legacyV9 = {...native, id: 98, lat: 28.5921, lng: 77.0460,
        surface_type: "temporary_drivable_surface",
        prompt_version: "pothole-binary-v9", schema_version: 7,
        drive_id: "native-import-v9", source_event_key: "live:native-import-v9:1"};
      const v9First = await api("/api/native-report",
        {method: "POST", body: JSON.stringify(legacyV9)});
      const v9Second = await api("/api/native-report",
        {method: "POST", body: JSON.stringify(legacyV9)});
      // v10/schema7 rows from the previous release remain importable and idempotent.
      const legacyV10 = {...native, id: 99, lat: 28.6550, lng: 77.2300,
        surface_type: "temporary_drivable_surface",
        prompt_version: "pothole-binary-v10", schema_version: 7,
        drive_id: "native-import-v10", source_event_key: "live:native-import-v10:1"};
      const v10First = await api("/api/native-report",
        {method: "POST", body: JSON.stringify(legacyV10)});
      const v10Second = await api("/api/native-report",
        {method: "POST", body: JSON.stringify(legacyV10)});
      // The current v12/schema7 contract follows the same import path.
      const currentV12 = {...native, id: 101, lat: 28.6250, lng: 77.2850,
        surface_type: "temporary_drivable_surface",
        prompt_version: "pothole-binary-v12", schema_version: 7,
        drive_id: "native-import-v12", source_event_key: "live:native-import-v12:1"};
      const v12First = await api("/api/native-report",
        {method: "POST", body: JSON.stringify(currentV12)});
      const v12Second = await api("/api/native-report",
        {method: "POST", body: JSON.stringify(currentV12)});
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
      return {first, second, v8First, v8Second, v9First, v9Second, v10First, v10Second,
        v12First, v12Second, ignored,
        invalidResults, count: reports.length, reports};
    }""")
    if imported["count"] != 5 or not imported["second"]["duplicate"]:
        failures.append(f"native report retry was not idempotent: {imported}")
    for version, first_key, second_key in (
        ("pothole-binary-v8", "v8First", "v8Second"),
        ("pothole-binary-v9", "v9First", "v9Second"),
        ("pothole-binary-v10", "v10First", "v10Second"),
        ("pothole-binary-v12", "v12First", "v12Second"),
    ):
        if imported[first_key].get("duplicate") or not imported[second_key].get("duplicate"):
            failures.append(f"{version} native import was not idempotent: {imported}")
    reports_by_version = {report.get("prompt_version"): report for report in imported["reports"]}
    for version in ("pothole-binary-v6", "pothole-binary-v8", "pothole-binary-v9",
                    "pothole-binary-v10", "pothole-binary-v12"):
        report = reports_by_version.get(version)
        if not report or not report.get("authority_id") or report.get("status") != "draft":
            failures.append(f"{version} native report did not use the existing authority router: {imported}")
    if imported["ignored"].get("ignored") is not True or imported["count"] != 5:
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
