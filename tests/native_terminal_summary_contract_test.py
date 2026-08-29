#!/usr/bin/env python3
"""Static contract for stale-status rejection and exact terminal Drive recovery."""

from pathlib import Path
import hashlib
import sys


ROOT = Path(__file__).resolve().parents[1]
DRIVE = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive"
SERVICE = (DRIVE / "DriveForegroundService.kt").read_text()
START_REGISTRY = (DRIVE / "DriveStartRegistry.kt").read_text()
STORE = (DRIVE / "NativeDriveEndSummaryStore.kt").read_text()
PLUGIN = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/plugin/DriveModePlugin.kt").read_text()
WEB_PATHS = [
    ROOT / "static/index.html",
    ROOT / "docs/index.html",
    ROOT / "android-app/www/index.html",
    ROOT / "android-app/android/app/src/main/assets/public/index.html",
]
WEB = WEB_PATHS[0].read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


restore = WEB[WEB.index("async function restoreNativeDrive"):
              WEB.index("// Opening the camera")]
finish = WEB[WEB.index("async function finishNativeDrive"):
             WEB.index("function presentNativeStoppingStatus")]
normal_completion = SERVICE[SERVICE.index("var terminalCandidate = baseSummary.copy"):
                            SERVICE.index("locationProvider = null")]
abnormal_completion = SERVICE[SERVICE.index("var summary = DriveEndSummary(", SERVICE.index("private fun finalizeAbnormalTeardown")):]
start_failure = SERVICE[SERVICE.index("private fun failDriveStart"):
                        SERVICE.index("private fun handleStopIntent")]
terminal_method = PLUGIN[PLUGIN.index("fun getDriveEndSummary"):
                         PLUGIN.index("fun attachPreview")]
clear_data = PLUGIN[PLUGIN.index("fun clearNativeData"):
                    PLUGIN.index("fun getDrives")]
photo_click = WEB[WEB.index('$("captureBtn").onclick'):
                  WEB.index("async function beginPotholeCapture")]

check(
    "every async restore is invalidated by newer native control-plane state",
    "let nativeDriveControlEpoch = 0" in WEB
    and "function bumpNativeDriveControlEpoch()" in WEB
    and "const requestEpoch = nativeDriveControlEpoch" in restore
    and "const observedDrive = drive && drive.native ? drive : null" in restore
    and "requestEpoch !== nativeDriveControlEpoch" in restore
    and "currentDrive !== observedDrive" in restore
    and "handledNativeEnds.has(incomingSession)" in restore,
)
check(
    "Start, Stop, terminal completion and wipe all invalidate stale status reads",
    WEB.count("bumpNativeDriveControlEpoch();") >= 7
    and WEB.index("bumpNativeDriveControlEpoch();", WEB.index("async function startDrive"))
        < WEB.index("await ensureDataConsent()", WEB.index("async function startDrive"))
    and "bumpNativeDriveControlEpoch();\n    ctx.stopping = true" in WEB
    and "nativeWipeInProgress = true;\n  bumpNativeDriveControlEpoch();" in WEB
    and "bumpNativeDriveControlEpoch();" in finish,
)
check(
    "terminal summaries are committed before listener and Stop callbacks",
    "NativeDriveEndSummaryStore.record(applicationContext, terminalCandidate)" in normal_completion
    and normal_completion.index("NativeDriveEndSummaryStore.record")
        < normal_completion.index("onDriveEndedListener?.invoke")
        < normal_completion.index("completion.callbacks.forEach")
    and "NativeDriveEndSummaryStore.record(applicationContext, summary)" in abnormal_completion
    and abnormal_completion.index("NativeDriveEndSummaryStore.record")
        < abnormal_completion.index("onDriveEndedListener?.invoke"),
)
check(
    "startup failure is durable and visible after plugin recreation",
    "NativeDriveEndSummaryStore.record(applicationContext, summary)" in start_failure
    and start_failure.index("NativeDriveEndSummaryStore.record")
        < start_failure.index("clearStartAdmission(startRequestId)")
        < start_failure.index("onDriveEndedListener?.invoke(summary)")
    and "NativeMediaReconciliationEpoch.invalidate()" in start_failure,
)
check(
    "restored idle Stop reads exact durable result instead of fabricating handled success",
    "fun getDriveEndSummary(call: PluginCall)" in PLUGIN
    and "NativeDriveEndSummaryStore.read(context, sessionId)" in terminal_method
    and "database.sessionDao().getSession(sessionId)" in terminal_method
    and "typeof plugin.getDriveEndSummary === \"function\"" in restore
    and "result && result.available === true" in restore
    and "{ provisional: !terminal }" in restore
    and "else if (sessionId) handledNativeEnds.add(sessionId)" in finish
    and "provisionalNativeEnds.delete(sessionId)" in finish,
)
check(
    "accepted native Start remains observable across plugin and WebView recreation",
    "val isStarting: Boolean = false" in SERVICE
    and "fun admitStart(requestId: String)" in SERVICE
    and "private val admissionTtlMs: Long = 30_000L" in START_REGISTRY
    and "nowMs() - current.admittedAtMs > admissionTtlMs" in START_REGISTRY
    and "DriveForegroundService.admitStart(pending.requestId)" in PLUGIN
    and 'put("isStarting", status.isStarting)' in PLUGIN
    and "if (status && status.isStarting)" in restore
    and "driveStarting && currentDrive && !currentDrive.stopping && !observedSession" in restore
    and "scheduleNativeStartStatusPoll()" in restore,
)
check(
    "sessionless recreation consumes and acknowledges the latest exact terminal result",
    "fun readLatest(context: Context)" in STORE
    and "fun acknowledge(context: Context, sessionId: String)" in STORE
    and "NativeDriveEndSummaryStore.readLatest(context)" in terminal_method
    and "fun acknowledgeDriveEndSummary(call: PluginCall)" in PLUGIN
    and 'plugin.getDriveEndSummary({})' in restore
    and 'plugin.acknowledgeDriveEndSummary({ sessionId })' in finish,
)
check(
    "terminal event between listener registration and status reconstruction is presented",
    'plugin.addListener("driveEnded", (summary)' in WEB
    and "if (!drive && !nativeFinishPromise && sessionId" in WEB
    and "startRequestId: summary && summary.startRequestId || null" in WEB
    and "stopping: true, captureStopped: true" in WEB
    and "finishNativeDrive(summary);" in WEB,
)
check(
    "new starts and delayed old callbacks have exact cross-layer ownership",
    "val startRequestId: String? = null" in SERVICE
    and 'put("startRequestId", status.startRequestId)' in PLUGIN
    and 'put("startRequestId", summary.startRequestId)' in PLUGIN
    and 'put("startRequestId", summary.startRequestId ?: JSONObject.NULL)' in STORE
    and 'call.getString("startRequestId")' in PLUGIN
    and "startRequestId: newNativeStartRequestId()" in WEB
    and "function nativeStatusOwnsPendingStart" in WEB
    and "driveStarting || (candidate && candidate.starting)" in WEB
    and "pendingAdmittedStartRequestId()" in SERVICE
    and "startRequestId ?: pendingAdmittedStartRequestId()" in SERVICE
    and "endedStartRequestId !== liveStartRequestId" in finish
    and "Previous drive: ${previousNotice}" in finish
    and "const latest = await nativePlugin.getStatus()" in WEB
    and "committedDrive.stopping = false" in WEB
    and "committedDrive.captureStopped = false" in WEB,
)
check(
    "Photo claims its reentry guard before waiting for initial restore",
    photo_click.index("photoCaptureStarting = true")
        < photo_click.index("await nativeInitialRestorePromise.catch")
    and "finally" in photo_click
    and "photoCaptureStarting = false" in photo_click,
)
check(
    "Drive and Photo share one synchronous camera admission",
    "let cameraAdmissionOwner = null" in WEB
    and "function claimCameraAdmission(owner)" in WEB
    and "function releaseCameraAdmission(owner)" in WEB
    and 'claimCameraAdmission("drive")' in WEB
    and 'releaseCameraAdmission("drive")' in WEB
    and 'claimCameraAdmission("photo")' in photo_click
    and 'releaseCameraAdmission("photo")' in photo_click
    and "if (photoCaptureStarting) return" in WEB
    and "if (drive || driveStarting || nativeFinishLocked" in photo_click,
)
check(
    "initial native restore gates new Drive and Photo camera actions",
    "let nativeInitialRestorePending" in WEB
    and "let nativeInitialRestorePromise" in WEB
    and WEB.count("await nativeInitialRestorePromise.catch") >= 2
    and "nativeInitialRestorePending = false" in WEB
    and "await Promise.all([loadHealth(), nativeInitialRestorePromise])" in WEB,
)
check(
    "terminal result store is bounded, exact, private and included in full wipe",
    "MAX_ENTRIES = 32" in STORE
    and "Context.MODE_PRIVATE" in STORE
    and "discarded" in STORE and "reason" in STORE and "error" in STORE
    and "editor.commit()" in STORE
    and "NativeDriveEndSummaryStore.clear(context)" in clear_data,
)
check(
    "all four shipped web copies are byte-identical",
    len({hashlib.sha256(path.read_bytes()).digest() for path in WEB_PATHS}) == 1,
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native terminal summary contract tests passed")
