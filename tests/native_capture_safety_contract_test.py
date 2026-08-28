#!/usr/bin/env python3
"""Static integration contract for critical-camera recovery and burst access races."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DRIVE = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive"
SERVICE = (DRIVE / "DriveForegroundService.kt").read_text()
CAMERA = (DRIVE / "NativeDriveCameraManager.kt").read_text()
RECOVERY = (DRIVE / "NativeCameraRecoveryPolicy.kt").read_text()
ACCESS = (DRIVE / "NativeBurstAccessPolicy.kt").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


scan_start = SERVICE.index("private fun startScanLoop()")
scan_end = SERVICE.index("private fun hasCameraPermission()", scan_start)
scan = SERVICE[scan_start:scan_end]
keyframe_start = SERVICE.index("private suspend fun persistSelectedBurst(")
keyframe_end = SERVICE.index("private fun commitKeyframeFile", keyframe_start)
keyframe = SERVICE[keyframe_start:keyframe_end]
worker_start = SERVICE.index("private fun startInferenceWorker()")
worker_end = SERVICE.index("private fun startSessionLimitLoop()", worker_start)
worker = SERVICE[worker_start:worker_end]
destroy_start = SERVICE.index("override fun onDestroy()")
destroy = SERVICE[destroy_start:SERVICE.index("super.onDestroy()", destroy_start)]

check(
    "CameraX errors are explicitly classified and critical codes signal the service once",
    "ERROR_CAMERA_IN_USE" in RECOVERY
    and "ERROR_MAX_CAMERAS_IN_USE" in RECOVERY
    and "ERROR_OTHER_RECOVERABLE_ERROR" in RECOVERY
    and "RELEASE_AND_RETRY" in RECOVERY
    and "STOP_TERMINALLY" in RECOVERY
    and "onCameraRecoveryRequired(action, reason)" in CAMERA
    and "lastSignalledRecoveryErrorCode" in CAMERA,
)
check(
    "critical rebind retries are bounded and exhaustion stops with an explicit reason",
    "NativeCameraRetryBudget(MAX_CRITICAL_CAMERA_RETRIES)" in SERVICE
    and "nextAttemptOrNull()" in SERVICE
    and "Camera remained unavailable after" in SERVICE
    and 'stopDriveSession("Stopped because $terminal")' in SERVICE
    and "criticalCameraRecoveryJob?.isActive != true" in SERVICE,
)
check(
    "the access epoch is captured before CameraX and checked before persistence",
    scan.index("captureAccessEpoch.snapshot()") < scan.index("cam.captureBurst()")
    < scan.index("validatedPostBurstFix(") < scan.index("persistSelectedBurst(baseItem)")
    and "recycleFrames(burst.first)" in scan,
)
check(
    "GPS and downstream timestamps are anchored to the selected primary frame",
    "val primaryFrame = burst.first[primaryIndex]" in scan
    and "val primaryCapturedAt = primaryFrame.capturedAtMs" in scan
    and "primaryFrame.capturedAtElapsedMs" in scan
    and "sourceOffset = (primaryCapturedAt - startedAtMs).coerceAtLeast(0L)" in scan
    and "fix = validatedFix" in scan
    and "captureSeq = sequence" in scan
    and "capturedAtMs = primaryCapturedAt" in scan
    and "capturedAtElapsedMs = primaryFrame.capturedAtElapsedMs" in scan,
)
check(
    "keyframe encoding and Room indexing each have an access gate",
    keyframe.index("if (!isBurstAccessStillValid(item)) return null")
    < keyframe.index("bitmapToBoundedJpegBytes(")
    and keyframe.index('IllegalStateException("Capture access changed')
    < keyframe.index("insertKeyframe("),
)
check(
    "queued inference rechecks access before local lookup or network analysis",
    worker.index("if (!isBurstAccessStillValid(item))")
    < worker.index("repairEngine?.findCandidate(")
    < worker.index("inferenceEngine?.analyzeBurst("),
)
check(
    "policy rejects changed epochs, invalid lifecycle, camera release, and distant fixes",
    "epochBeforeCapture != epochImmediatelyBeforeWork" in ACCESS
    and "!sessionRunning || paused || stopping" in ACCESS
    and "!cameraReady || cameraReleased" in ACCESS
    and "deltaMs <= MAX_FIX_PRIMARY_DELTA_MS" in ACCESS,
)
check(
    "abnormal service teardown invalidates capture and media reconciliation epochs",
    "captureAccessEpoch.invalidate()" in destroy
    and "NativeMediaReconciliationEpoch.invalidate()" in destroy
    and "criticalCameraRecoveryJob?.cancel()" in destroy,
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native capture safety contract tests passed")
