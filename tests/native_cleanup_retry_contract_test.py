#!/usr/bin/env python3
"""Static integration contract for retryable native-media cleanup and quota truth."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DRIVE = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive"
PLUGIN = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/plugin"
SERVICE = (DRIVE / "DriveForegroundService.kt").read_text()
CAMERA = (DRIVE / "NativeDriveCameraManager.kt").read_text()
INFERENCE = (DRIVE / "NativeInferenceEngine.kt").read_text()
REPORT_STORAGE = (DRIVE / "NativeReportEvidenceStorage.kt").read_text()
DISCARDED = (DRIVE / "NativeDiscardedMediaCleanup.kt").read_text()
RETRY = (DRIVE / "NativeRetryableFileCleanup.kt").read_text()
EPOCH = (DRIVE / "NativeMediaReconciliationEpoch.kt").read_text()
BRIDGE = (PLUGIN / "DriveModePlugin.kt").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


check(
    "reconciliation uses a monotonic epoch instead of a racy Boolean",
    "AtomicLong" in EPOCH
    and "fun invalidate()" in EPOCH
    and "NativeMediaReconciliationEpoch.isCurrent(passEpoch)" in BRIDGE
    and "nativeStateReconciled =" not in BRIDGE,
)
check(
    "every drive opens reconciliation at start and before drive-ended callbacks",
    SERVICE.count("NativeMediaReconciliationEpoch.invalidate()") >= 2
    and SERVICE.index("NativeMediaReconciliationEpoch.invalidate()", SERVICE.index("private fun startDriveSession"))
    < SERVICE.index("startedAtMs =", SERVICE.index("private fun startDriveSession"))
    and SERVICE.index("NativeMediaReconciliationEpoch.invalidate()", SERVICE.index("val completion = claimStopCompletion(terminalCandidate)"))
    < SERVICE.index("onDriveEndedListener?.invoke(completion.summary)"),
)
check(
    "failed report and repair evidence deletion uses verified retry invalidation",
    "uncommittedReportPhoto?.let" in SERVICE
    and "uncommittedRepairPhoto?.let" in SERVICE
    and SERVICE.count("NativeReportEvidenceStorage.deleteVerified(File(it))") >= 2
    and "NativeReportEvidenceStorage.deleteVerified(file)" in INFERENCE
    and REPORT_STORAGE.count("NativeRetryableFileCleanup.deleteVerified(") >= 2,
)
check(
    "discarded and unindexed videos keep failed deletion discoverable",
    "NativeMediaReconciliationEpoch.invalidate()" in DISCARDED
    and "NativeRetryableFileCleanup.deleteVerified(File(segment.filePath))" in SERVICE,
)
check(
    "active report, video and keyframe deferrals leave reconciliation incomplete",
    BRIDGE.count("progress.markCleanupIncomplete()") >= 8
    and "unindexedVideoFiles.any" in BRIDGE
    and "wasActiveAtInventory" in BRIDGE
    and "becameActiveDuringInventory" in BRIDGE
    and "if (protectedNow)" in BRIDGE
    and "fun protectedByReportProducer(candidate: File)" in BRIDGE
    and "val liveStatus = DriveForegroundService.status()" in BRIDGE
    and "!protectedByReportProducer(directory)" in BRIDGE,
)
check(
    "keyframe rollback owns temporary files and separates quota classes",
    "val temporaryFiles = plan.files.map { File(directory, \".${it.name}.tmp\") }" in SERVICE
    and "temporaryFiles.drop(1).zip(plan.files.drop(1))" in SERVICE
    and "NativeKeyframeFailureCleanup.cleanup(" in SERVICE
    and "remainingUnaccountedBytes" in SERVICE
    and "removedAccountedBytes" in SERVICE
    and "NativeRetryableFileCleanup.deleteVerified(temporary) && destination.isFile" in SERVICE
    and "remainingUnaccountedBytes" in RETRY,
)
check(
    "Room ownership hand-offs cannot be cancelled into evidence deletion",
    SERVICE.count("withContext(NonCancellable)") >= 4
    and "var rowCommitted = false" in SERVICE
    and "if (!rowCommitted) cleanFailedKeyframeWrite(" in SERVICE,
)
video_prepare = CAMERA[CAMERA.index("private fun startRecordingSegment()"):
                       CAMERA.index("private fun blockRecordingForStorage(")]
check(
    "video session directory creation cannot race a stale reconciliation snapshot",
    video_prepare.index("reserveMediaBytes(")
    < video_prepare.index("NativeMediaFilesystemMutation.mutex.withLock")
    < video_prepare.index("footageRoot.mkdirs()")
    and "if (!directoryReady) reservation?.let(storageQuota::release)" in video_prepare
    and "reservation.takeIf { directoryReady }" in video_prepare,
)
start_drive = BRIDGE[BRIDGE.index("fun startDrive(call: PluginCall)"):
                     BRIDGE.index("private fun activityIsVisibleForDriveStart")]
clear_data = BRIDGE[BRIDGE.index("fun clearNativeData(call: PluginCall)"):
                    BRIDGE.index("fun getDrives(call: PluginCall)")]
destroy = SERVICE[SERVICE.index("override fun onDestroy()"):
                  SERVICE.index("private fun finalizeAbnormalTeardown")]
abnormal_finalize = SERVICE[SERVICE.index("private fun finalizeAbnormalTeardown"):]
interrupted_persist = abnormal_finalize.index('persistSession("interrupted", endedAt)')
pure_abnormal_unlock = abnormal_finalize.index("activeService = null", interrupted_persist)
check(
    "abnormal service destruction protects media until ownership and interruption are durable",
    "val abnormalTeardown = wasActive && completedStopSummary == null" in destroy
    and "jobsToJoin" in destroy
    and "stopTeardownJob" in destroy
    and destroy.index("jobsToJoin") < destroy.index("criticalCameraRecoveryJob?.cancel()")
    and "isStopping = abnormalTeardown" in destroy
    and "finalizeAbnormalTeardown(" in destroy
    and "jobsToJoin.joinAll()" in abnormal_finalize
    and abnormal_finalize.count("jobsToJoin.joinAll()") >= 2
    and abnormal_finalize.index("if (completedStopSummary != null)")
        < abnormal_finalize.index('persistSession("interrupted", endedAt)')
    and 'persistSession("interrupted", endedAt)' in abnormal_finalize
    and interrupted_persist
        < pure_abnormal_unlock
        < abnormal_finalize.index("onDriveEndedListener?.invoke(completion.summary)")
    and pure_abnormal_unlock
        < abnormal_finalize.index("NativeMediaReconciliationEpoch.invalidate()", pure_abnormal_unlock)
    and "discarded = discardDataOnStop" in abnormal_finalize,
)
check(
    "native destructive wipe and Drive admission share one process-global interlock",
    "private companion object" in BRIDGE
    and "val pendingStartLock = Any()" in BRIDGE
    and "var pendingStart: PendingDriveStart? = null" in BRIDGE
    and "var nativeClearInProgress = false" in BRIDGE
    and "val driveControlScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)" in BRIDGE
    and "synchronized(pendingStartLock) { nativeClearInProgress }" in start_drive
    and "if (nativeClearInProgress)" in start_drive
    and start_drive.index("if (nativeClearInProgress)")
        < start_drive.index("pendingStart = pending")
    and "nativeClearInProgress = true" in clear_data
    and clear_data.index("nativeClearInProgress = true")
        < clear_data.index("pendingStart?.takeIf")
    and "synchronized(pendingStartLock) { nativeClearInProgress = false }" in clear_data
    and "driveControlScope.launch" in clear_data
    and clear_data.index("finishClear()") < clear_data.index("call.resolve"),
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native cleanup retry contract tests passed")
