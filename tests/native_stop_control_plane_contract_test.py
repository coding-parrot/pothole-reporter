#!/usr/bin/env python3
"""Focused contract for the verified camera-off / durable drive-end Stop phases."""

from pathlib import Path
import hashlib
import sys


ROOT = Path(__file__).resolve().parents[1]
DRIVE = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive"
SERVICE = (DRIVE / "DriveForegroundService.kt").read_text()
NOTIFICATION = (DRIVE / "NotificationHelper.kt").read_text()
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


stop = SERVICE[SERVICE.index("fun stopDriveSession("):
               SERVICE.index("private fun persistSessionTracked")]
camera_task = stop[stop.index("val cameraStopped = async"):
                   stop.index("val cameraFinished = withTimeoutOrNull")]
durable_tail = stop[stop.index("val footageJobs ="):]
finish = WEB[WEB.index("async function finishNativeDrive"):
             WEB.index("async function ensureNativeDriveListeners")]
stopping = WEB[WEB.index("function presentNativeStoppingStatus"):
               WEB.index("async function ensureNativeDriveListeners")]
restore = WEB[WEB.index("async function restoreNativeDrive"):
              WEB.index("// Opening the camera")]
clear_data = PLUGIN[PLUGIN.index("fun clearNativeData(call: PluginCall)"):
                    PLUGIN.index("fun getDrives(call: PluginCall)")]
pending_monitor = PLUGIN[PLUGIN.index("private fun monitorPendingStart"):
                         PLUGIN.index("private fun queuePendingStartStop")]
pending_queue = PLUGIN[PLUGIN.index("private fun queuePendingStartStop"):
                       PLUGIN.index("private data class PendingCancellationResult")]
start_command = SERVICE[SERVICE.index("override fun onStartCommand"):
                        SERVICE.index("private fun failDriveStart")]

check(
    "captureStopped is reset per session and exported by both plugin status paths",
    "@Volatile private var captureStopped = false" in SERVICE
    and "captureStopped = false; sessionRunning = true" in SERVICE
    and stop.index("captureStopped = false") < stop.index("val cameraStopped = async")
    and PLUGIN.count('put("captureStopped", status.captureStopped)') == 2,
)
check(
    "only successful normal or emergency frame-source closure publishes capture-off",
    "manager?.stopSafely()" in camera_task
    and "cameraStoppedCleanly = true" in camera_task
    and "manager?.closeImmediately()" in camera_task
    and "cameraStoppedCleanly = closed.isSuccess" in camera_task
    and "if (cameraStoppedCleanly)" in camera_task
    and camera_task.index("if (cameraStoppedCleanly)")
        < camera_task.index("captureStopped = true")
    and camera_task.index("captureStopped = true")
        < camera_task.index('publish(sourceOffStatus("finalizing saved data"))'),
)
check(
    "camera-off status precedes persistence while Stop callbacks remain after durability",
    stop.index("captureStopped = true") < stop.index("val footageJobs =")
    and "finishTrackedJobs(footageJobs, \"Video\")" in durable_tail
    and "finishTrackedJobs(sessionJobs, \"Drive summary\")" in durable_tail
    and durable_tail.index("persistSession(\"stopped\"")
        < durable_tail.index("onDriveEndedListener?.invoke(completion.summary)")
    and durable_tail.index("onDriveEndedListener?.invoke(completion.summary)")
        < durable_tail.index("completion.callbacks.forEach"),
)
check(
    "Stop time limits never release destructive callbacks before producer ownership ends",
    "stopTeardownJob = abnormalTeardownScope.launch(" in stop
    and stop.count("waiting for saved data") >= 2
    and "jobs.joinAll()" in stop
    and "scanningJob?.join()" in stop
    and "workerJob?.join()" in stop
    and stop.index("workerJob?.join()") < stop.index("claimStopCompletion(terminalCandidate)"),
)
check(
    "transient inference cancellation is retried after worker ownership ends",
    "var inferenceCloseFailure: Throwable? = null" in stop
    and "fun closeInference(): Boolean" in stop
    and '.onFailure { recordStopError("Could not close the detection engine", it) }'
        not in stop[stop.index("fun closeInference(): Boolean"):
                    stop.index("suspend fun stopWorkerWithinLimit")]
    and stop.index("workerJob?.join()") < stop.index("if (!inferenceClosed) closeInference()")
    and stop.count("if (!inferenceClosed) closeInference()") >= 3
    and 'recordStopError("Could not close the detection engine", it)' in stop,
)
check(
    "terminal result includes failures discovered by the emergency teardown pass",
    "val currentStopError = synchronized(stopErrors)" in stop
    and "val terminalError = when" in stop
    and "currentStopError.startsWith(\"${baseSummary.error}; \")" in stop
    and "error = terminalError" in stop
    and stop.index("val terminalError = when")
        < stop.index("NativeDriveEndSummaryStore.record(applicationContext, terminalCandidate)")
        < stop.index("onDriveEndedListener?.invoke(completion.summary)"),
)
check(
    "foreground notification removal is part of the durable terminal result",
    "if (cameraStoppedCleanly) {\n                            runCatching { stopForeground(STOP_FOREGROUND_REMOVE) }" in stop
    and stop.index("stopForeground(STOP_FOREGROUND_REMOVE)")
        < stop.index("val currentStopError = synchronized(stopErrors)")
        < stop.index("NativeDriveEndSummaryStore.record(applicationContext, terminalCandidate)")
    and '.onFailure { recordStopError("Could not remove the Drive Mode notification", it) }'
        in stop
    and 'recordStopError("Drive-ended listener failed"' not in stop
    and 'recordStopError("A Stop callback failed"' not in stop,
)
check(
    "a failed frame-source close remains reachable and keeps foreground disclosure visible",
    "cameraStoppedCleanly = emergencyClosed.isSuccess" in stop
    and stop.count("cameraStoppedCleanly = emergencyClosed.isSuccess") >= 2
    and stop.count("if (cameraStoppedCleanly) {\n                        frameSource = null") >= 1
    and "if (!cameraStoppedCleanly) {\n                            val emergencyClosed" in stop
    and "if (cameraManager != null)" not in stop[
        stop.index("unexpected failure in the normal NonCancellable cleanup"):
        stop.index("val baseSummary = completedSummary")
    ]
    and stop.index("if (cameraStoppedCleanly) {\n                            runCatching { stopForeground")
        < stop.index("val currentStopError = synchronized(stopErrors)"),
)
check(
    "clearNativeData still waits on the durable Stop callback before deletion",
    'service.stopDriveSession("Data cleared", discardData = true) { clearData() }' in clear_data,
)
check(
    "Start cancellation and destructive wipe survive plugin/WebView recreation",
    "val pendingStartLock = Any()" in PLUGIN
    and "var pendingStart: PendingDriveStart? = null" in PLUGIN
    and "var nativeClearInProgress = false" in PLUGIN
    and "val driveControlScope" in PLUGIN
    and "driveControlScope.launch" in pending_monitor
    and "DriveForegroundService.cancelStartAdmission" in pending_queue
    and "cancelledStartDisposition(startRequestId)" in start_command
    and "completeCancelledStart(startId, cancelledDiscard)" in start_command
    and "NativeDriveEndSummaryStore.record(applicationContext, summary)" in start_command,
)
check(
    "notification stays foreground and truthfully distinguishes both Stop phases",
    '"Drive Mode · Dashcam Off · Saving"' in NOTIFICATION
    and '"Drive Mode · Camera Off · Saving"' in NOTIFICATION
    and '"Dashcam off · finalizing saved data" else "Camera off · finalizing saved data"' in NOTIFICATION
    and "if (!isStopping && !isPausing)" in NOTIFICATION
    and 'builder.addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop"' in NOTIFICATION,
)
check(
    "pre-close stays on Drive; verified camera-off returns Home monotonically",
    "drive.captureStopped = Boolean(drive.captureStopped || status.captureStopped)" in stopping
    and stopping.index("if (drive.captureStopped)") < stopping.index('show("home")')
    and 'show("drive")' in stopping
    and "handledNativeEnds.has(incomingSession)" in stopping,
)
check(
    "Home remains mutation- and native-sync-locked until driveEnded",
    all(token in WEB for token in (
        '$("driveBtn").disabled = disabled',
        '$("captureBtn").disabled = disabled',
        '$("gearBtn").disabled = disabled',
        '$("wipeBtn").disabled = disabled',
        '$("setSave").disabled = disabled',
    ))
    and WEB.count("nativeWipeInProgress || nativeCaptureFinalizingSessionId") >= 4
    and "!nativeCaptureFinalizingSessionId && !drive && !driveStarting" in WEB
    and finish.index("releaseNativeCaptureFinalization(sessionId)")
        < finish.index("hydrateNativeDriveEndWithinDeadline()")
    and finish.index("hydrateNativeDriveEndWithinDeadline()")
        < finish.index("setNativeFinishLocked(false)"),
)
check(
    "Activity recreation restores camera-off Home and polls a missed final event safely",
    "presentNativeStoppingStatus(status)" in restore
    and 'if (!status)' in restore
    and "scheduleNativeStopStatusPoll()" in restore
    and "if (drive.stopping)" in restore
    and 'typeof plugin.getDriveEndSummary === "function"' in restore
    and "result && result.available === true" in restore
    and "{ provisional: !terminal }" in restore,
)
check(
    "early Home cannot be reached through Back or another stale screen callback",
    "drive && drive.native && drive.stopping && !drive.captureStopped" in WEB[
        WEB.index("function show(screen)"):WEB.index("function fmtDate")]
    and "if (drive && drive.native && drive.stopping && !drive.captureStopped) return true" in WEB,
)
check(
    "all four shipped web copies are byte-identical",
    len({hashlib.sha256(path.read_bytes()).digest() for path in WEB_PATHS}) == 1,
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native Stop control-plane contract tests passed")
