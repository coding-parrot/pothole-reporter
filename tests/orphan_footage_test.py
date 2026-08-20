# -*- coding: utf-8 -*-
"""Drive shutdown must commit its final footage before publishing history.

This is deliberately independent of a browser's MediaRecorder timing. A fake recorder
emits one final clip on Stop, and the app's own /api/footage POST is held behind a gate.
That gives the test an exact point at which to inspect the half-finished shutdown.
"""
import sys

from playwright.sync_api import sync_playwright

from browser_test_utils import open_app


JS = r"""
async () => {
  const pause = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));
  const video = document.getElementById("driveVideo");
  const realHandle = StandaloneAPI.handle;
  const realPrewarm = StandaloneAPI.prewarm;
  const realPlay = video.play.bind(video);
  const realWatch = navigator.geolocation.watchPosition.bind(navigator.geolocation);
  const realClear = navigator.geolocation.clearWatch.bind(navigator.geolocation);
  const realGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  const mediaRecorderOwn = Object.getOwnPropertyDescriptor(window, "MediaRecorder");
  const wakeOwn = Object.getOwnPropertyDescriptor(navigator, "wakeLock");
  const realSegment = SEGMENT_MS;

  let mediaCalls = 0;
  let delayedPosts = 0;
  let releaseFootage;
  let markFootageEntered;
  const footageGate = new Promise((resolve) => { releaseFootage = resolve; });
  const footageEntered = new Promise((resolve) => { markFootageEntered = resolve; });

  class DeterministicMediaRecorder {
    static isTypeSupported() { return true; }
    constructor(stream, options = {}) {
      this.stream = stream;
      this.mimeType = options.mimeType || "video/webm";
      this.state = "inactive";
      this.ondataavailable = null;
      this.onstop = null;
    }
    start() { this.state = "recording"; }
    stop() {
      if (this.state === "inactive") return;
      this.state = "inactive";
      queueMicrotask(() => {
        const bytes = new Uint8Array(4096);
        bytes.fill(7);
        if (this.ondataavailable) {
          this.ondataavailable({ data: new Blob([bytes], { type: this.mimeType }) });
        }
        if (this.onstop) this.onstop();
      });
    }
  }

  await realHandle("/api/reports", { method: "DELETE" });
  await loadReports();
  localStorage.setItem("record_video", "1");
  SEGMENT_MS = 60000; // only Stop can produce a clip in this test
  StandaloneAPI.prewarm = async () => {};
  Object.defineProperty(window, "MediaRecorder", {
    configurable: true, writable: true, value: DeterministicMediaRecorder,
  });
  Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
    configurable: true,
    value: async (...args) => { mediaCalls++; return realGetUserMedia(...args); },
  });
  navigator.geolocation.watchPosition = () => 77; // never supplies GPS
  navigator.geolocation.clearWatch = () => {};
  Object.defineProperty(navigator, "wakeLock", { configurable: true, value: {
    request: async () => ({ release: async () => {} }),
  }});
  video.play = async () => {};
  window.alert = () => {};
  window.confirm = () => false;

  StandaloneAPI.handle = async (path, opts = {}) => {
    const method = String(opts.method || "GET").toUpperCase();
    if (path === "/api/footage" && method === "POST") {
      delayedPosts++;
      markFootageEntered();
      await footageGate;
    }
    return realHandle(path, opts);
  };

  await startDrive();
  if (!recCtx || !recCtx.recorder || recCtx.recorder.state !== "recording") {
    throw new Error("deterministic recorder did not start");
  }
  const sessionId = drive.sessionId;
  const firstStop = stopDrive();
  const secondStop = stopDrive();
  let stopState = "pending";
  firstStop.then(() => { stopState = "resolved"; }, () => { stopState = "rejected"; });

  // onstop has fired and saveClip has entered the real API boundary, but its IndexedDB
  // transaction cannot begin until this test releases the gate.
  await footageEntered;
  await pause();
  const callsBeforeRestart = mediaCalls;
  await startDrive();
  await pause();
  const backHandled = window.handleAppBack();
  await pause();
  const [drivesBefore, footageBefore] = await Promise.all([
    realHandle("/api/drives"), realHandle("/api/footage"),
  ]);
  const beforeCommit = {
    stopState,
    sameStopPromise: firstStop === secondStop,
    finalizingIsStop: driveFinalizing === firstStop,
    startBlocked: mediaCalls === callsBeforeRestart && drive === null && !driveStarting,
    backBlocked: backHandled === true
      && !document.getElementById("drive").classList.contains("hidden")
      && document.getElementById("home").classList.contains("hidden"),
    delayedPosts,
    drives: drivesBefore.length,
    footage: footageBefore.length,
    history: document.querySelectorAll(`[data-drive="${sessionId}"]`).length,
    driveScreenVisible: !document.getElementById("drive").classList.contains("hidden"),
  };

  releaseFootage();
  await firstStop;
  await pause();
  const [drivesAfter, footageAfter, reportsAfter] = await Promise.all([
    realHandle("/api/drives"), realHandle("/api/footage"), realHandle("/api/reports"),
  ]);
  const driveRec = drivesAfter.find((d) => String(d.id) === String(sessionId));
  const footageRec = footageAfter.find((f) => String(f.drive_id) === String(sessionId));
  const heads = Array.from(document.querySelectorAll("[data-drive]"))
    .filter((el) => el.dataset.drive === String(sessionId));
  if (heads[0]) heads[0].click();
  const footageRow = document.querySelector(`[data-footagerow="${sessionId}"]`);
  const analyse = document.querySelector(`[data-analyse="${sessionId}"]`);
  const remove = document.querySelector(`[data-delfootage="${sessionId}"]`);
  const afterCommit = {
    stopState,
    finalizingCleared: driveFinalizing === null,
    drives: drivesAfter.length,
    reports: reportsAfter.length,
    checked: driveRec && driveRec.checked,
    found: driveRec && driveRec.found,
    gps: driveRec && driveRec.gps_track.length,
    segments: footageRec && footageRec.segments,
    bytes: footageRec && footageRec.bytes,
    startedAt: footageRec && footageRec.started_at,
    endedAt: footageRec && footageRec.ended_at,
    history: heads.length,
    homeVisible: !document.getElementById("home").classList.contains("hidden"),
    emptyMessage: document.getElementById("list").textContent.includes("No reports yet"),
    analyseVisible: !!analyse && !!footageRow && getComputedStyle(footageRow).display !== "none",
    deleteVisible: !!remove && !!footageRow && getComputedStyle(footageRow).display !== "none",
  };

  // Existing footage orphaned by older builds must also be discoverable without a
  // matching drive summary or report.
  StandaloneAPI.handle = realHandle;
  await realHandle("/api/reports", { method: "DELETE" });
  const legacy = new FormData();
  legacy.append("segment", new Blob([new Uint8Array(128)], { type: "video/webm" }), "old.webm");
  legacy.append("drive_id", "legacy-orphan");
  legacy.append("seq", "0");
  await realHandle("/api/footage", { method: "POST", body: legacy });
  await loadReports();
  const legacyHead = document.querySelector('[data-drive="legacy-orphan"]');
  if (legacyHead) legacyHead.click();
  const legacyRow = document.querySelector('[data-footagerow="legacy-orphan"]');
  const legacyState = {
    head: !!legacyHead,
    invalidDate: !!legacyHead && legacyHead.textContent.includes("Invalid"),
    analyseVisible: !!document.querySelector('[data-analyse="legacy-orphan"]')
      && !!legacyRow && getComputedStyle(legacyRow).display !== "none",
    deleteVisible: !!document.querySelector('[data-delfootage="legacy-orphan"]')
      && !!legacyRow && getComputedStyle(legacyRow).display !== "none",
  };

  // Do not solve the orphan problem by adding useless rows for a canceled drive that
  // recorded nothing and assessed nothing.
  await realHandle("/api/reports", { method: "DELETE" });
  localStorage.setItem("record_video", "0");
  await startDrive();
  const emptyId = drive.sessionId;
  await stopDrive();
  await pause();
  const [emptyDrives, emptyFootage, emptyReports] = await Promise.all([
    realHandle("/api/drives"), realHandle("/api/footage"), realHandle("/api/reports"),
  ]);
  const emptyState = {
    drives: emptyDrives.length,
    footage: emptyFootage.length,
    reports: emptyReports.length,
    history: document.querySelectorAll(`[data-drive="${emptyId}"]`).length,
    finalizingCleared: driveFinalizing === null,
  };

  SEGMENT_MS = realSegment;
  StandaloneAPI.prewarm = realPrewarm;
  navigator.geolocation.watchPosition = realWatch;
  navigator.geolocation.clearWatch = realClear;
  Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
    configurable: true, value: realGetUserMedia,
  });
  video.play = realPlay;
  if (mediaRecorderOwn) Object.defineProperty(window, "MediaRecorder", mediaRecorderOwn);
  else delete window.MediaRecorder;
  if (wakeOwn) Object.defineProperty(navigator, "wakeLock", wakeOwn);
  else delete navigator.wakeLock;
  localStorage.setItem("record_video", "1");
  await realHandle("/api/reports", { method: "DELETE" });
  return { beforeCommit, afterCommit, legacyState, emptyState };
}
"""


WATCHDOG_JS = r"""
async () => {
  const pause = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));
  const video = document.getElementById("driveVideo");
  const realHandle = StandaloneAPI.handle;
  const realPrewarm = StandaloneAPI.prewarm;
  const realPlay = video.play.bind(video);
  const realWatch = navigator.geolocation.watchPosition.bind(navigator.geolocation);
  const realClear = navigator.geolocation.clearWatch.bind(navigator.geolocation);
  const realGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  const mediaRecorderOwn = Object.getOwnPropertyDescriptor(window, "MediaRecorder");
  const wakeOwn = Object.getOwnPropertyDescriptor(navigator, "wakeLock");
  const realSegment = SEGMENT_MS;
  const realStopTimeout = RECORDER_STOP_TIMEOUT_MS;

  let mode = "silent";
  let footagePosts = 0;
  let drivePosts = 0;
  const instances = [];

  class HangingMediaRecorder {
    static isTypeSupported() { return true; }
    constructor(stream, options = {}) {
      this.stream = stream;
      this.mimeType = options.mimeType || "video/webm";
      this.state = "inactive";
      this.ondataavailable = null;
      this.onstop = null;
      this.onerror = null;
      this.errorDelivered = false;
      instances.push(this);
    }
    start() { this.state = "recording"; }
    stop() {
      this.state = "inactive";
      if (mode === "error") queueMicrotask(() => {
        this.errorDelivered = true;
        if (this.onerror) this.onerror({ error: new Error("synthetic recorder failure") });
      });
      // Both modes deliberately omit onstop. The production watchdog must settle them.
    }
  }

  StandaloneAPI.prewarm = async () => {};
  StandaloneAPI.handle = async (path, opts = {}) => {
    const method = String(opts.method || "GET").toUpperCase();
    if (path === "/api/footage" && method === "POST") footagePosts++;
    if (path === "/api/drives" && method === "POST") drivePosts++;
    return realHandle(path, opts);
  };
  Object.defineProperty(window, "MediaRecorder", {
    configurable: true, writable: true, value: HangingMediaRecorder,
  });
  Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
    configurable: true, value: (...args) => realGetUserMedia(...args),
  });
  navigator.geolocation.watchPosition = () => 77;
  navigator.geolocation.clearWatch = () => {};
  Object.defineProperty(navigator, "wakeLock", { configurable: true, value: {
    request: async () => ({ release: async () => {} }),
  }});
  video.play = async () => {};
  window.alert = () => {};
  window.confirm = () => false;
  localStorage.setItem("record_video", "1");
  SEGMENT_MS = 60000;
  RECORDER_STOP_TIMEOUT_MS = 20;

  const runCase = async (kind) => {
    await realHandle("/api/reports", { method: "DELETE" });
    await loadReports();
    mode = kind;
    instances.length = 0;
    const footageBase = footagePosts;
    const driveBase = drivePosts;
    await startDrive();
    const sessionId = drive.sessionId;
    const recordingCtx = recCtx;
    const recorder = instances[0];
    if (!recorder || recorder.state !== "recording") {
      throw new Error(kind + " recorder did not start");
    }
    // Retain references just as a browser might already have queued the events before
    // production detaches its public handler properties at the watchdog boundary.
    const lateData = recorder.ondataavailable;
    const lateStop = recorder.onstop;
    const lateError = recorder.onerror;
    let stopState = "pending";
    const stopped = stopDrive();
    stopped.then(() => { stopState = "resolved"; }, () => { stopState = "rejected"; });
    await Promise.resolve();
    await Promise.resolve(); // lets the synthetic onerror run, but not the timer
    const pendingBeforeWatchdog = stopState === "pending" && driveFinalizing === stopped;
    const outcome = await Promise.race([
      stopped.then(() => "resolved", () => "rejected"),
      pause(1000).then(() => "timeout"),
    ]);
    await pause();
    const [drivesBeforeLate, footageBeforeLate, reportsBeforeLate] = await Promise.all([
      realHandle("/api/drives"), realHandle("/api/footage"), realHandle("/api/reports"),
    ]);
    const beforeLate = {
      kind,
      outcome,
      pendingBeforeWatchdog,
      errorDelivered: recorder.errorDelivered,
      contextFailed: recordingCtx.failed,
      contextFinished: recordingCtx.finished,
      finalizingCleared: driveFinalizing === null,
      recorderCleared: recCtx === null,
      homeVisible: !document.getElementById("home").classList.contains("hidden"),
      drives: drivesBeforeLate.length,
      footage: footageBeforeLate.length,
      reports: reportsBeforeLate.length,
      history: document.querySelectorAll(`[data-drive="${sessionId}"]`).length,
      footagePosts: footagePosts - footageBase,
      drivePosts: drivePosts - driveBase,
    };

    const lateBytes = new Uint8Array(512);
    lateBytes.fill(9);
    if (lateData) lateData.call(recorder, {
      data: new Blob([lateBytes], { type: recorder.mimeType }),
    });
    if (lateStop) lateStop.call(recorder);
    if (lateError) lateError.call(recorder, { error: new Error("late recorder failure") });
    await pause(RECORDER_STOP_TIMEOUT_MS * 2 + 10);
    const [drivesAfterLate, footageAfterLate, reportsAfterLate] = await Promise.all([
      realHandle("/api/drives"), realHandle("/api/footage"), realHandle("/api/reports"),
    ]);
    const afterLate = {
      drives: drivesAfterLate.length,
      footage: footageAfterLate.length,
      reports: reportsAfterLate.length,
      history: document.querySelectorAll(`[data-drive="${sessionId}"]`).length,
      footagePosts: footagePosts - footageBase,
      drivePosts: drivePosts - driveBase,
      finalizingCleared: driveFinalizing === null,
    };
    return { beforeLate, afterLate };
  };

  const silent = await runCase("silent");
  const error = await runCase("error");

  SEGMENT_MS = realSegment;
  RECORDER_STOP_TIMEOUT_MS = realStopTimeout;
  StandaloneAPI.handle = realHandle;
  StandaloneAPI.prewarm = realPrewarm;
  navigator.geolocation.watchPosition = realWatch;
  navigator.geolocation.clearWatch = realClear;
  Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
    configurable: true, value: realGetUserMedia,
  });
  video.play = realPlay;
  if (mediaRecorderOwn) Object.defineProperty(window, "MediaRecorder", mediaRecorderOwn);
  else delete window.MediaRecorder;
  if (wakeOwn) Object.defineProperty(navigator, "wakeLock", wakeOwn);
  else delete navigator.wakeLock;
  await realHandle("/api/reports", { method: "DELETE" });
  return { silent, error };
}
"""


fails = []
page_errors = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=[
        "--disable-web-security", "--allow-running-insecure-content",
        "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
    ])
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, permissions=["camera"])
    page = context.new_page()
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    open_app(page, "test-only")
    result = page.evaluate(JS)
    watchdog = page.evaluate(WATCHDOG_JS)
    browser.close()

before = result["beforeCommit"]
after = result["afterCommit"]
legacy = result["legacyState"]
empty = result["emptyState"]

if before["stopState"] != "pending":
    fails.append(f"Stop settled before the final footage commit: {before}")
if not before["sameStopPromise"] or not before["finalizingIsStop"]:
    fails.append(f"repeated Stop did not return the one finalization promise: {before}")
if not before["startBlocked"]:
    fails.append(f"a new Drive started while the old one was finalizing: {before}")
if not before["backBlocked"]:
    fails.append(f"Android Back exposed Home while the drive was finalizing: {before}")
if before["delayedPosts"] != 1:
    fails.append(f"expected exactly one gated final footage POST: {before}")
if before["drives"] or before["footage"] or before["history"]:
    fails.append(f"drive/history became visible before footage committed: {before}")
if not before["driveScreenVisible"]:
    fails.append(f"Drive screen disappeared while Stop was still pending: {before}")

if after["stopState"] != "resolved" or not after["finalizingCleared"]:
    fails.append(f"Stop did not resolve and release its startup guard: {after}")
if after["drives"] != 1 or after["reports"] != 0:
    fails.append(f"zero-frame drive stores are wrong after commit: {after}")
if after["checked"] != 0 or after["found"] != 0 or after["gps"] != 0:
    fails.append(f"zero-frame drive summary is wrong: {after}")
if after["segments"] != 1 or after["bytes"] != 4096:
    fails.append(f"final footage was not committed exactly once: {after}")
if not after["startedAt"] or not after["endedAt"]:
    fails.append(f"footage summary has no usable history timestamp: {after}")
if after["history"] != 1 or after["emptyMessage"] or not after["homeVisible"]:
    fails.append(f"committed drive is not represented once in history: {after}")
if not after["analyseVisible"] or not after["deleteVisible"]:
    fails.append(f"footage actions are not visible after expansion: {after}")

if not legacy["head"] or legacy["invalidDate"]:
    fails.append(f"legacy orphan footage has no valid history row: {legacy}")
if not legacy["analyseVisible"] or not legacy["deleteVisible"]:
    fails.append(f"legacy orphan footage actions are inaccessible: {legacy}")
if empty["drives"] or empty["footage"] or empty["reports"] or empty["history"]:
    fails.append(f"recording-disabled empty cancel created history: {empty}")
if not empty["finalizingCleared"]:
    fails.append(f"empty cancel left Drive startup blocked: {empty}")

for name in ("silent", "error"):
    case = watchdog[name]
    before_late = case["beforeLate"]
    after_late = case["afterLate"]
    if not before_late["pendingBeforeWatchdog"]:
        fails.append(f"{name}: recorder settled before its watchdog: {before_late}")
    if before_late["outcome"] != "resolved":
        fails.append(f"{name}: watchdog did not resolve Stop: {before_late}")
    if not before_late["contextFailed"] or not before_late["contextFinished"]:
        fails.append(f"{name}: watchdog did not mark/finish the recorder: {before_late}")
    if not before_late["finalizingCleared"] or not before_late["recorderCleared"]:
        fails.append(f"{name}: watchdog left Drive finalization locked: {before_late}")
    if not before_late["homeVisible"]:
        fails.append(f"{name}: watchdog did not return the UI home: {before_late}")
    if any(before_late[key] for key in
           ("drives", "footage", "reports", "history", "footagePosts", "drivePosts")):
        fails.append(f"{name}: empty recorder tail created data/history: {before_late}")
    if (name == "error") != bool(before_late["errorDelivered"]):
        fails.append(f"{name}: synthetic onerror delivery was not exercised: {before_late}")
    if any(after_late[key] for key in
           ("drives", "footage", "reports", "history", "footagePosts", "drivePosts")):
        fails.append(f"{name}: retained late callbacks wrote after finalization: {after_late}")
    if not after_late["finalizingCleared"]:
        fails.append(f"{name}: late callbacks re-locked finalization: {after_late}")
if page_errors:
    fails.append(f"browser errors: {page_errors}")

if fails:
    print("FAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("ORPHAN FOOTAGE TEST PASS")
