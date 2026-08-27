#!/usr/bin/env python3
"""Static integration contract for retryable native-media cleanup and quota truth."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DRIVE = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive"
PLUGIN = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/plugin"
SERVICE = (DRIVE / "DriveForegroundService.kt").read_text()
INFERENCE = (DRIVE / "NativeInferenceEngine.kt").read_text()
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
    and SERVICE.index("NativeMediaReconciliationEpoch.invalidate()", SERVICE.index("val summary = synchronized(stopCallbacks)"))
    < SERVICE.index("onDriveEndedListener?.invoke(summary)"),
)
check(
    "failed report and repair evidence deletion uses verified retry invalidation",
    "uncommittedReportPhoto?.let" in SERVICE
    and "uncommittedRepairPhoto?.let" in SERVICE
    and SERVICE.count("NativeRetryableFileCleanup.deleteVerified(File(it))") >= 2
    and INFERENCE.count("NativeRetryableFileCleanup.deleteVerified(") >= 3,
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
    and "unownedKeyframeFiles.any" in BRIDGE
    and "if (protectedNow)" in BRIDGE,
)
check(
    "keyframe rollback owns temporary files and separates quota classes",
    "val temporaryFiles = listOf(primaryTemporary, companionTemporary)" in SERVICE
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

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native cleanup retry contract tests passed")
