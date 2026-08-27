#!/usr/bin/env python3
"""Offline contract for bounded native Drive capture and saved-frame replay."""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
CAMERA = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeDriveCameraManager.kt").read_text()
SERVICE = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/DriveForegroundService.kt").read_text()
INTERLOCK = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeCaptureInterlock.kt").read_text()
CAMERA_ACCESS = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeCameraAccessPolicy.kt").read_text()
QUOTA = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeMediaStorageQuota.kt").read_text()
DISCARDED_MEDIA = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeDiscardedMediaCleanup.kt").read_text()
KEYFRAME_FILES = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeKeyframeFiles.kt").read_text()
IMAGE_POLICY = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeStoredImagePolicy.kt").read_text()
QUALITY = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/FrameQualityEvaluator.kt").read_text()
INFERENCE = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeInferenceEngine.kt").read_text()
POLICY = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/DriveSessionLimitPolicy.kt").read_text()
PLUGIN = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/plugin/DriveModePlugin.kt").read_text()
DATABASE = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/db/PotholeDatabase.kt").read_text()
WEB = (ROOT / "static/index.html").read_text()
GRADLE = (ROOT / "android-app/android/app/build.gradle").read_text()
RELEASE = (ROOT / "tools/build-play-release.sh").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


check("continuous video requests SD, never HD",
      "Quality.SD" in CAMERA and "Quality.HD" not in CAMERA)
check("only one raw burst may wait for inference",
      re.search(r"jobChannel\s*=\s*Channel\([\s\S]{0,500}?capacity\s*=\s*1\b", SERVICE))
check("camera privacy uses public AppOps and releases capture until access returns",
      "startWatchingMode(AppOpsManager.OPSTR_CAMERA" in SERVICE
      and "unsafeCheckOpNoThrow(" in SERVICE
      and "checkOpNoThrow(" in SERVICE
      and "stopWatchingMode(listener)" in SERVICE
      and "SensorPrivacyManager" not in SERVICE
      and "MODE_IGNORED" in CAMERA_ACCESS and "MODE_ERRORED" in CAMERA_ACCESS
      and "cameraAccessBlocked -> blocked(" in INTERLOCK
      and "releaseCamera = true" in INTERLOCK[INTERLOCK.index("cameraAccessBlocked -> blocked("):])
start_session = SERVICE[SERVICE.index("private fun startDriveSession("):
                        SERVICE.index("private fun startForegroundNow()")]
check("startup waits for a fresh GPS fix before CameraX is opened",
      "manager.startCamera" not in start_session
      and "accessCameraReleased = true" in start_session
      and start_session.index("cameraManager = NativeDriveCameraManager(")
          < start_session.index("refreshCaptureInterlock()"))
check("Android 10 never receives the Android 11 camera foreground-service bit",
      "Build.VERSION.SDK_INT >= Build.VERSION_CODES.R" in SERVICE
      and "Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q" in SERVICE
      and SERVICE.index("Build.VERSION.SDK_INT >= Build.VERSION_CODES.R")
          < SERVICE.index("FOREGROUND_SERVICE_TYPE_CAMERA")
      and SERVICE.index("FOREGROUND_SERVICE_TYPE_CAMERA")
          < SERVICE.index("Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q"))
check("native detection requires at least two real source frames",
      "MIN_DETECTION_SOURCE_FRAMES = 2" in CAMERA
      and "frames.size < MIN_DETECTION_SOURCE_FRAMES" in CAMERA
      and "burstFrames.size < NativeDriveCameraManager.MIN_DETECTION_SOURCE_FRAMES" in INFERENCE)
check("30-minute active-time limit is bounded to 15..90 minutes",
      all(value in POLICY for value in (
          "MIN_LIMIT_MINUTES = 15", "MAX_LIMIT_MINUTES = 90",
          "DEFAULT_LIMIT_MINUTES = 30", "fun pause(", "fun resume(",
          "fun remainingMs(", "fun expired("))
      and "SystemClock.elapsedRealtime()" in SERVICE
      and all(value in SERVICE for value in (
          "startSessionLimitLoop()", "pauseSessionLimit()", "resumeSessionLimit()")))
check("sparse keyframes are durable and unique per drive capture",
      "KEYFRAME_INTERVAL_MS = 2_000L" in SERVICE
      and "persistSparseKeyframe(baseItem)" in SERVICE
      and "if (!recordingEnabled" not in SERVICE[SERVICE.index("private suspend fun persistSparseKeyframe"):SERVICE.index("private fun startInferenceWorker")]
      and SERVICE.index("return withContext(Dispatchers.IO)", SERVICE.index("private suspend fun persistSparseKeyframe"))
          < SERVICE.index("FrameQualityEvaluator.bitmapToBoundedJpegBytes", SERVICE.index("private suspend fun persistSparseKeyframe"))
      and "selectTemporalCompanionIndex" in SERVICE
      and "reserveMediaBytes(expectedBytes)" in SERVICE
      and "MAX_KEYFRAME_IMAGE_BYTES = 900L * 1024L" in IMAGE_POLICY
      and "MAX_KEYFRAME_PAIR_BYTES = 2L * MAX_KEYFRAME_IMAGE_BYTES" in IMAGE_POLICY
      and "_context.jpg" in KEYFRAME_FILES
      and SERVICE.index("persistSparseKeyframe(baseItem)") < SERVICE.index("jobChannel?.trySend(item)")
      and "MAX_TOTAL_BYTES = 4L * 1024 * 1024 * 1024" in QUOTA
      and "VIDEO_SEGMENT_RESERVATION_BYTES" in QUOTA
      and "MIGRATION_3_4" in DATABASE
      and "index_drive_keyframes_sessionId_captureSeq" in DATABASE)
check("manual old-drive deletion reconciles the active shared media quota",
      "NativeMediaFilesystemMutation.mutex.withLock" in CAMERA
      and "NativeMediaFilesystemMutation.mutex.withLock" in PLUGIN
      and "externalMediaDeletionRecorderIfReconciled" in SERVICE
      and "mediaBytesBefore - directoryBytes(sessionRoot)" in PLUGIN
      and "ledgerDeletion?.invoke(removedBytes)" in PLUGIN)
check("video quota charges the same on-disk bytes that deletion later credits",
      "val bytes = segment.file.length().coerceAtLeast(0L)" in CAMERA
      and "maxOf(segment.file.length(), event.recordingStats.numBytesRecorded)" not in CAMERA
      and "actualBytes > reserved" in QUOTA
      and "if (commitReservedVideoFile(segment, bytes))" in CAMERA
      and "clip exceeded its 80 MB storage reservation" in CAMERA
      and CAMERA.count(".delete()") == 0
      and DISCARDED_MEDIA.count("candidate.delete()") == 1
      and CAMERA.count("discardReservedVideoFile(") >= 6
      and "segment.terminalState == VideoSegmentTerminalState.DISCARDED" in CAMERA
      and "segment.discardedMediaCleanup.reconcile(segment.file)" in CAMERA
      and "noteUnexpectedExistingFile(result.addedBytes)" in CAMERA
      and "noteDeletion(result.removedBytes)" in CAMERA)
check("saved report and repair evidence is bridge-size bounded before disk write",
      "fun bitmapToBoundedJpegBytes(" in QUALITY
      and "maxBytes = NativeStoredImagePolicy.MAX_BRIDGE_IMAGE_BYTES" in INFERENCE
      and "out.write(jpeg)" in INFERENCE
      and "bitmap.compress(Bitmap.CompressFormat.JPEG, quality, out)" not in INFERENCE)
check("binary detector evidence has a non-destructive Room migration",
      "version = 6" in DATABASE and "MIGRATION_4_5" in DATABASE and "MIGRATION_5_6" in DATABASE
      and "ADD COLUMN `looksLikeSpeedBreaker` INTEGER NOT NULL DEFAULT 1" in DATABASE
      and "ADD COLUMN `hasLocalizedCavity` INTEGER NOT NULL DEFAULT 0" in DATABASE
      and "ADD COLUMN `surfaceType` TEXT NOT NULL DEFAULT 'unknown'" in DATABASE)
check("native bridge exposes pending keyframe replay",
      all(f"fun {method}(" in PLUGIN for method in
          ("listKeyframes", "readKeyframe", "markKeyframeAnalyzed"))
      and 'getInt("maxDurationMinutes")' in PLUGIN
      and "EXTRA_MAX_DRIVE_MINUTES" in PLUGIN)

analysis = WEB[WEB.index("async function analyseNativeKeyframes"):]
analysis = analysis[:analysis.index("function gpsAt")]
check("UI wires timer and idempotent saved-frame analysis",
      "const DRIVE_LIMIT_OPTIONS = [15, 30, 60, 90]" in WEB
      and "maxDurationMinutes: driveLimitMinutes()" in WEB
      and 'data-analysenative="${driveId}"' in WEB
      and "pendingOnly: true" in analysis
      and "`live:${state.driveId}:${saved.captureSeq}`" in analysis
      and analysis.index('await api("/api/frame"')
          < analysis.index("await plugin.markKeyframeAnalyzed"))
check("hybrid status is explicit and all shipped web copies match",
      "RECORDING VIDEO" in WEB and '"Video: On"' in WEB
      and "SAVING FRAMES" in WEB and "saved frame" in WEB and "min left" in WEB
      and (ROOT / "android-app/www/index.html").read_bytes()
          == (ROOT / "static/index.html").read_bytes()
      and (ROOT / "docs/index.html").read_bytes()
          == (ROOT / "static/index.html").read_bytes()
      and (ROOT / "android-app/android/app/src/main/assets/public/index.html").read_bytes()
          == (ROOT / "static/index.html").read_bytes())
check("Android release identity is 1.36.1 code 56 everywhere",
      re.search(r"versionCode\s+56\b", GRADLE)
      and re.search(r'versionName\s+"1\.36\.1"', GRADLE)
      and 'android:versionCode="56"' in RELEASE
      and 'android:versionName="1.36.1"' in RELEASE)

if failures:
    print(f"\nFAIL: {len(failures)} hybrid Drive contract check(s) failed")
    sys.exit(1)
print("\nHYBRID DRIVE CONTRACT TEST PASS")
