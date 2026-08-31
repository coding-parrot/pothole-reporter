#!/usr/bin/env node

/**
 * Real-device/emulator smoke test for the native Media3 RTSP path.
 *
 * Prerequisites: the debug app is installed and visible, an H.264 RTSP-over-TCP stream is
 * reachable from the device, and Location + Notification permissions are granted. Optional
 * --publisher-pid enables a controlled publisher-loss/recovery check using SIGSTOP/SIGCONT.
 * This test deliberately uses a fake API key; it verifies capture transport/lifecycle, not AI.
 */

import { execFileSync, spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import process from "node:process";

const PACKAGE = "dev.aiengg.potholereporter";
const ACTIVITY = `${PACKAGE}/.MainActivity`;

function option(name, fallback = null) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

function findAdb() {
  const candidates = [
    process.env.ADB,
    process.env.ANDROID_HOME && `${process.env.ANDROID_HOME}/platform-tools/adb`,
    `${process.env.HOME}/Library/Android/sdk/platform-tools/adb`,
    "/opt/homebrew/share/android-commandlinetools/platform-tools/adb",
  ].filter(Boolean);
  const found = candidates.find(existsSync);
  if (!found) throw new Error("adb was not found; set ADB or ANDROID_HOME");
  return found;
}

const adbPath = findAdb();
const serial = option("--serial", process.env.ANDROID_SERIAL || "emulator-5554");
const streamUrl = option("--stream", process.env.DASHCAM_RTSP_URL);
const publisherPid = Number(option("--publisher-pid", "0"));
const fixtureVideo = option("--fixture-video", null);
const publishUrl = option("--publish-url", null);
const devtoolsPort = Number(option("--devtools-port", "9222"));
const soakSeconds = Math.max(0, Number(option("--soak-seconds", "0")) || 0);
const outageSeconds = Math.max(0, Number(option("--outage-seconds", "0")) || 0);
const minimumChecked = Math.max(0, Number(option("--minimum-checked", "0")) || 0);
const minimumFound = Math.max(0, Number(option("--minimum-found", "0")) || 0);
const measureMemory = option("--measure-memory", "true") !== "false";
const liveApiKey = String(process.env.DASHCAM_TEST_API_KEY || "").trim();
if (!streamUrl?.startsWith("rtsp://")) {
  throw new Error("Pass an H.264 RTSP URL with --stream or DASHCAM_RTSP_URL");
}
if (fixtureVideo && !publishUrl?.startsWith("rtsp://")) {
  throw new Error("--fixture-video also requires an RTSP --publish-url");
}
if ((minimumChecked > 0 || minimumFound > 0) && !liveApiKey) {
  throw new Error(
    "DASHCAM_TEST_API_KEY must be set when detector-result minimums are requested",
  );
}

function adb(...args) {
  return execFileSync(adbPath, ["-s", serial, ...args], {
    encoding: "utf8",
    timeout: 45_000,
  }).trim();
}

function screenshotHash() {
  const image = execFileSync(adbPath, ["-s", serial, "exec-out", "screencap", "-p"], {
    encoding: null,
    timeout: 15_000,
  });
  return createHash("sha256").update(image).digest("hex");
}

function totalPssKib() {
  const memory = adb("shell", "dumpsys", "meminfo", PACKAGE);
  const match = memory.match(/TOTAL PSS:\s+(\d+)/);
  return match ? Number(match[1]) : null;
}

function assertNoPackageAnr(stage) {
  const events = adb("shell", "logcat", "-d", "-b", "events", "-v", "brief");
  const packageAnr = events.split("\n").find(
    (line) => line.includes("am_anr") && line.includes(PACKAGE),
  );
  if (packageAnr) throw new Error(`${stage}: app ANR recorded: ${packageAnr.trim()}`);
}

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

let ownedPublisher = null;

async function startOwnedPublisher() {
  ownedPublisher = spawn("ffmpeg", [
    "-hide_banner", "-loglevel", "error", "-re", "-stream_loop", "-1",
    "-i", fixtureVideo, "-map", "0:v:0", "-an", "-c:v", "copy",
    "-rtsp_transport", "tcp", "-f", "rtsp", publishUrl,
  ], { stdio: "ignore" });
  await sleep(1_000);
  if (ownedPublisher.exitCode != null) {
    throw new Error(`ffmpeg publisher exited with code ${ownedPublisher.exitCode}`);
  }
}

async function stopOwnedPublisher() {
  const publisher = ownedPublisher;
  ownedPublisher = null;
  if (!publisher || publisher.exitCode != null) return;
  publisher.kill("SIGTERM");
  await Promise.race([
    new Promise((resolve) => publisher.once("exit", resolve)),
    sleep(3_000),
  ]);
  if (publisher.exitCode == null && publisher.signalCode == null) publisher.kill("SIGKILL");
}

class CdpPage {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
  }

  async connect() {
    await new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", () => reject(new Error("CDP connection failed")), {
        once: true,
      });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (!message.id) return;
      const waiter = this.pending.get(message.id);
      if (!waiter) return;
      this.pending.delete(message.id);
      if (message.error) waiter.reject(new Error(message.error.message));
      else waiter.resolve(message.result);
    });
  }

  command(method, params = {}) {
    const id = this.nextId++;
    const result = new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  async evaluate(expression) {
    const result = await this.command("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description || "WebView evaluation failed");
    }
    return result.result?.value;
  }

  close() {
    this.socket.close();
  }
}

const pluginExpression = `
  (window.Capacitor && Capacitor.registerPlugin)
    ? Capacitor.registerPlugin("DriveMode")
    : window.Capacitor?.Plugins?.DriveMode
`;

async function pluginCall(page, method, options = {}) {
  return page.evaluate(`(async () => {
    const plugin = ${pluginExpression};
    if (!plugin || typeof plugin.${method} !== "function") {
      throw new Error("DriveMode.${method} is unavailable");
    }
    return await plugin.${method}(${JSON.stringify(options)});
  })()`);
}

async function waitForStatus(page, predicate, description, timeoutMs = 25_000) {
  const deadline = Date.now() + timeoutMs;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await pluginCall(page, "getStatus");
    if (predicate(latest)) return latest;
    await sleep(500);
  }
  throw new Error(`${description}; latest status=${JSON.stringify(latest)}`);
}

let page = null;
let publisherPaused = false;
let driveStarted = false;
try {
  // The ANR marker lives in the events buffer. Clearing only the default buffers can
  // falsely attribute an older run's ANR to this smoke test.
  adb("shell", "logcat", "-b", "all", "-c");
  if (fixtureVideo) await startOwnedPublisher();
  const pid = adb("shell", "pidof", PACKAGE);
  if (!pid) throw new Error("Pothole Reporter is not running in the foreground");
  adb("forward", `tcp:${devtoolsPort}`, `localabstract:webview_devtools_remote_${pid}`);
  const targets = await (await fetch(`http://127.0.0.1:${devtoolsPort}/json/list`)).json();
  const target = targets.find((candidate) => candidate.type === "page" && candidate.webSocketDebuggerUrl);
  if (!target) throw new Error("No debuggable Pothole Reporter WebView was found");
  page = new CdpPage(target.webSocketDebuggerUrl);
  await page.connect();
  await page.command("Runtime.enable");

  const existing = await pluginCall(page, "getStatus");
  if (existing?.isRunning || existing?.isStopping || existing?.isStarting) {
    await pluginCall(page, "stopDrive");
    await waitForStatus(page, (status) => !status.isRunning && !status.isStopping,
      "Existing Drive session did not stop", 35_000);
  }

  const start = await pluginCall(page, "startDrive", {
    startRequestId: randomUUID(),
    // The default deliberately exercises transport without making a paid request.
    // A live gate receives its secret only through the process environment; never a
    // command-line argument, repository file, output field, or error message.
    apiKey: liveApiKey || "sk-test-placeholder-not-a-real-secret",
    model: "gpt-5.6",
    detail: "original",
    language: "en",
    debug: false,
    recordVideo: false,
    maxDurationMinutes: 15,
    captureSource: "dashcam",
    dashcamRtspUrl: streamUrl,
  });
  driveStarted = true;
  const streaming = await waitForStatus(
    page,
    (status) => status.captureSource === "dashcam" && status.sourceActive === true &&
      status.sourceState === "streaming",
    "Dashcam never reached streaming state",
  );

  await page.evaluate(`(() => {
    drive = {
      native: true,
      sessionId: ${JSON.stringify(streaming.sessionId)},
      startRequestId: ${JSON.stringify(streaming.startRequestId)},
      captureSource: "dashcam",
      stopping: false,
      captureStopped: false,
    };
    setDrivePanels(true, "dashcam");
    updateNativeDriveHud(${JSON.stringify(streaming)});
    show("drive");
    startNativePreview();
    return true;
  })()`);
  const previewAttached = await page.evaluate(`(async () => {
    const deadline = Date.now() + 8000;
    while (Date.now() < deadline) {
      if (nativePreviewAttached) return true;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return false;
  })()`);
  if (!previewAttached) throw new Error("Dashcam preview did not attach");
  await sleep(700);
  const firstPreviewHash = screenshotHash();
  await sleep(900);
  const secondPreviewHash = screenshotHash();
  if (firstPreviewHash === secondPreviewHash) {
    throw new Error("Visible dashcam preview did not change between live samples");
  }

  adb("shell", "input", "keyevent", "3");
  await sleep(5_000);
  const packageServices = adb("shell", "dumpsys", "activity", "services", PACKAGE);
  if (!packageServices.includes("DriveForegroundService")) {
    throw new Error("Drive foreground service disappeared after Home");
  }
  // The package-filtered view omits runtime foreground-type fields on Android 14, so
  // inspect the complete service records and isolate this component for the type check.
  const allBackgroundServices = adb("shell", "dumpsys", "activity", "services");
  const serviceStart = allBackgroundServices.indexOf(
    `${PACKAGE}/.drive.DriveForegroundService`,
  );
  const nextService = allBackgroundServices.indexOf("\n  * ServiceRecord", serviceStart + 1);
  const backgroundService = serviceStart < 0 ? "" : allBackgroundServices.slice(
    serviceStart,
    nextService < 0 ? undefined : nextService,
  );
  const hasDashcamForegroundTypes = /foregroundServiceType=(?:0x18|24)\b/.test(backgroundService) ||
    /\btypes=(?:0x)?0*18\b/i.test(backgroundService) ||
    /(connectedDevice.*location|location.*connectedDevice)/i.test(backgroundService);
  if (!hasDashcamForegroundTypes) {
    const relevant = backgroundService.split("\n")
      .filter((line) => /foreground|DriveForegroundService/i.test(line)).join(" | ");
    throw new Error(
      `Dashcam foreground service did not retain connected-device + location types: ${relevant}`,
    );
  }

  const resumedActivity = adb("shell", "am", "start", "-W", "-n", ACTIVITY);
  const resumeTimeMs = Number(resumedActivity.match(/TotalTime:\s+(\d+)/)?.[1] || 0);
  if (resumeTimeMs >= 5_000) {
    throw new Error(`Activity return took ${resumeTimeMs}ms after backgrounding`);
  }
  await sleep(2_000);
  await waitForStatus(page, (status) => status.sourceActive === true &&
    status.sourceState === "streaming", "Dashcam did not survive background/return");
  const previewRestored = await page.evaluate(`(async () => {
    if (!nativePreviewAttached) startNativePreview();
    const deadline = Date.now() + 8000;
    while (Date.now() < deadline) {
      if (nativePreviewAttached) return true;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return false;
  })()`);
  if (!previewRestored) throw new Error("Dashcam preview did not restore after background/return");
  assertNoPackageAnr("background/return");

  // dumpsys meminfo requests an explicit ART collection. Accuracy timing runs can disable
  // that observer effect; memory soaks leave it enabled and retain the peak-PSS assertion data.
  let peakPssKib = measureMemory ? totalPssKib() : null;
  let detectorStatus = streaming;
  const soakDeadline = Date.now() + soakSeconds * 1_000;
  while (Date.now() < soakDeadline) {
    await sleep(Math.min(5_000, soakDeadline - Date.now()));
    const soakStatus = await pluginCall(page, "getStatus");
    detectorStatus = soakStatus;
    if (!soakStatus.sourceActive || soakStatus.sourceState !== "streaming") {
      throw new Error(`Dashcam left streaming state during soak: ${JSON.stringify(soakStatus)}`);
    }
    if (measureMemory) {
      const currentPssKib = totalPssKib();
      if (currentPssKib != null) peakPssKib = Math.max(peakPssKib || 0, currentPssKib);
    }
  }
  detectorStatus = await pluginCall(page, "getStatus");
  if (detectorStatus.checked < minimumChecked) {
    throw new Error(
      `Detector completed ${detectorStatus.checked} checks; required ${minimumChecked}`,
    );
  }
  if (detectorStatus.found < minimumFound) {
    throw new Error(
      `Detector found ${detectorStatus.found} potholes; required ${minimumFound}`,
    );
  }

  if (ownedPublisher) {
    await stopOwnedPublisher();
    await waitForStatus(page, (status) => ["reconnecting", "connecting"].includes(status.sourceState),
      "Publisher loss was not detected", 18_000);
    if (outageSeconds > 0) {
      await sleep(outageSeconds * 1_000);
      const outageStatus = await pluginCall(page, "getStatus");
      if (!outageStatus.isRunning ||
          !["reconnecting", "connecting"].includes(outageStatus.sourceState)) {
        throw new Error(
          `Dashcam exhausted reconnects or escaped backoff during outage: ${JSON.stringify(outageStatus)}`,
        );
      }
    }
    await startOwnedPublisher();
    await waitForStatus(page, (status) => status.sourceActive === true &&
      status.sourceState === "streaming", "Dashcam did not recover after publisher restart", 45_000);
  } else if (Number.isInteger(publisherPid) && publisherPid > 1) {
    process.kill(publisherPid, "SIGSTOP");
    publisherPaused = true;
    await waitForStatus(page, (status) => ["reconnecting", "connecting"].includes(status.sourceState),
      "Publisher loss was not detected", 18_000);
    process.kill(publisherPid, "SIGCONT");
    publisherPaused = false;
    await waitForStatus(page, (status) => status.sourceActive === true &&
      status.sourceState === "streaming", "Dashcam did not recover after publisher return", 30_000);
  }

  await pluginCall(page, "stopDrive");
  driveStarted = false;
  const stopped = await waitForStatus(page, (status) => !status.isRunning && !status.isStopping,
    "Drive did not stop cleanly", 40_000);
  const finalServices = adb("shell", "dumpsys", "activity", "services", PACKAGE);
  if (finalServices.includes("DriveForegroundService")) {
    throw new Error("Drive foreground service remained after verified Stop");
  }
  if (!adb("shell", "pidof", PACKAGE)) {
    throw new Error("Stopping Drive incorrectly killed the app process");
  }
  assertNoPackageAnr("completed dashcam smoke test");

  console.log(JSON.stringify({
    passed: true,
    source: streaming.captureSource,
    state: streaming.sourceState,
    previewChanged: true,
    backgroundServiceType: "connectedDevice|location",
    soakSeconds,
    outageSeconds,
    peakPssKib,
    checked: detectorStatus.checked,
    found: detectorStatus.found,
    already: detectorStatus.already,
    liveInferenceTested: Boolean(liveApiKey),
    publisherRecoveryTested: Boolean(fixtureVideo) || publisherPid > 1,
    stopped: !stopped.isRunning,
  }, null, 2));
} finally {
  if (publisherPaused) process.kill(publisherPid, "SIGCONT");
  await stopOwnedPublisher();
  if (page && driveStarted) {
    try { await pluginCall(page, "stopDrive"); } catch (_) {}
  }
  page?.close();
}
