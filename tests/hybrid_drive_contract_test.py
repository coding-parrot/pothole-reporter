#!/usr/bin/env python3
"""Offline contract for bounded native Drive capture and saved-frame replay."""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
CAMERA = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeDriveCameraManager.kt").read_text()
ANALYZER_POLICY = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeAnalyzerSamplingPolicy.kt").read_text()
LOCATION = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeDriveLocationProvider.kt").read_text()
SERVICE = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/DriveForegroundService.kt").read_text()
INTERLOCK = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeCaptureInterlock.kt").read_text()
CAMERA_ACCESS = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeCameraAccessPolicy.kt").read_text()
QUOTA = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeMediaStorageQuota.kt").read_text()
DISCARDED_MEDIA = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeDiscardedMediaCleanup.kt").read_text()
KEYFRAME_FILES = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeKeyframeFiles.kt").read_text()
IMAGE_POLICY = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeStoredImagePolicy.kt").read_text()
QUALITY = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/FrameQualityEvaluator.kt").read_text()
INFERENCE = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeInferenceEngine.kt").read_text()
REPORT_STORAGE = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeReportEvidenceStorage.kt").read_text()
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
check("raw inference never buffers a second bitmap burst",
      re.search(r"jobChannel\s*=\s*Channel\([\s\S]{0,500}?capacity\s*=\s*Channel\.RENDEZVOUS\b", SERVICE)
      and "onBufferOverflow" not in SERVICE)
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
      and "NativeRollingBurstWindow.CAPACITY" in CAMERA
      and "NativeRollingBurstWindow.selectSourceIndexes(" in CAMERA
      and "burstFrames.size < MIN_DETECTION_SOURCE_FRAMES" in CAMERA
      and "burstFrames.size !in NativeDriveCameraManager.MIN_DETECTION_SOURCE_FRAMES.." in INFERENCE
      and "NativeRollingBurstWindow.OUTPUT_COUNT" in INFERENCE)
check("native capture samples a bounded three-frame source burst only when due",
      "const val CAPACITY = 3" in (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeRollingBurstWindow.kt").read_text()
      and "return samples.indices.toList()" in
          (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeRollingBurstWindow.kt").read_text()
      and "ArrayDeque<CapturedFrame>(NativeRollingBurstWindow.CAPACITY)" in CAMERA
      and "NativeAnalyzerSamplingPolicy.shouldConvert(" in CAMERA
      and "sourceTimestampNs - lastSampleTimestampNs >= minimumSpacingNs" in ANALYZER_POLICY
      and "while (rollingFrames.size > NativeRollingBurstWindow.CAPACITY)" in CAMERA
      and "NativeRollingBurstWindow.selectSourceIndexes(" in CAMERA
      and "pendingFrameRequest" not in CAMERA
      and "ArrayDeque<BurstFrame>" not in CAMERA)
capture_burst = CAMERA[CAMERA.index("suspend fun captureBurst()"):
                       CAMERA.index("private fun Bitmap.recycleSafely()")]
check("native capture transfers a complete fresh rolling window without waiting or cloning",
      "synchronized(rollingFrameLock)" in capture_burst
      and "val transferred = sourceIndexes.map(sourceFrames::get)" in capture_burst
      and "rollingFrames.clear()" in capture_burst
      and "lastRollingSourceTimestampNs = 0L" in capture_burst
      and ".copy(Bitmap.Config" not in capture_burst
      and "delay(" not in capture_burst
      and "withTimeout" not in capture_burst
      and "clearRollingFrames()" in CAMERA)
process_proxy = CAMERA[CAMERA.index("private fun processImageProxy"):
                       CAMERA.index("suspend fun captureBurst()")]
check("analyzer allocation failure reaches the service instead of killing capture silently",
      "catch (error: OutOfMemoryError)" in process_proxy
      and "analyzerFatalError = error" in process_proxy
      and "NativeCameraRecoveryAction.STOP_TERMINALLY" in process_proxy
      and "ownedBitmap?.recycleSafely()" in process_proxy)
check("native Drive refuses approximate-only location before camera startup",
      "Camera and Precise Location permission are required" in PLUGIN
      and "private fun hasDrivePermissions()" in PLUGIN
      and "Manifest.permission.ACCESS_FINE_LOCATION" in
          PLUGIN[PLUGIN.index("private fun hasDrivePermissions()"):
                 PLUGIN.index("private fun hasNotificationPermission()")]
      and "Manifest.permission.ACCESS_COARSE_LOCATION" not in
          PLUGIN[PLUGIN.index("private fun hasDrivePermissions()"):
                 PLUGIN.index("private fun hasNotificationPermission()")])
runtime_location_permission = LOCATION[LOCATION.index("private fun hasLocationPermission()"):
                                       LOCATION.index("private fun locationServicesEnabled()")]
check("mid-drive downgrade to Approximate Location closes capture",
      "Manifest.permission.ACCESS_FINE_LOCATION" in runtime_location_permission
      and "Manifest.permission.ACCESS_COARSE_LOCATION" not in runtime_location_permission
      and "fixNearestToElapsed(elapsedRealtimeMs" in LOCATION
      and "nearestCaptureReady(elapsedRealtimeMs, GPS_COARSE_M)" in LOCATION
      and "elapsedRealtimeMs" in LOCATION)
check("30-minute active-time limit is bounded to 15..90 minutes",
      all(value in POLICY for value in (
          "MIN_LIMIT_MINUTES = 15", "MAX_LIMIT_MINUTES = 90",
          "DEFAULT_LIMIT_MINUTES = 30", "fun pause(", "fun resume(",
          "fun remainingMs(", "fun expired("))
      and "SystemClock.elapsedRealtime()" in SERVICE
      and all(value in SERVICE for value in (
          "startSessionLimitLoop()", "pauseSessionLimit()", "resumeSessionLimit()")))
pause_policy = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/DrivePauseTimeoutPolicy.kt").read_text()
check("a forgotten paused foreground service auto-stops after 15 minutes",
      "DEFAULT_TIMEOUT_MINUTES = 15" in pause_policy
      and "startPauseTimeout()" in SERVICE
      and "clearPauseTimeout()" in SERVICE
      and "pauseTimeoutJob?.cancel()" in SERVICE
      and "Stopped after ${DrivePauseTimeoutPolicy.DEFAULT_TIMEOUT_MINUTES} minutes paused" in SERVICE)
check("every selected burst is durable and unique before live inference",
      "KEYFRAME_INTERVAL_MS" not in SERVICE
      and "persistSelectedBurst(baseItem)" in SERVICE
      and "if (!recordingEnabled" not in SERVICE[SERVICE.index("private suspend fun persistSelectedBurst"):SERVICE.index("private fun startInferenceWorker")]
      and SERVICE.index("return withContext(Dispatchers.IO)", SERVICE.index("private suspend fun persistSelectedBurst"))
          < SERVICE.index("FrameQualityEvaluator.bitmapToBoundedJpegBytes", SERVICE.index("private suspend fun persistSelectedBurst"))
      and "selectTemporalContextIndexes" in SERVICE
      and "reserveMediaBytes(expectedBytes)" in SERVICE
      and "MAX_KEYFRAME_IMAGE_BYTES = 900L * 1024L" in IMAGE_POLICY
      and "MAX_KEYFRAME_BURST_BYTES = 3L * MAX_KEYFRAME_IMAGE_BYTES" in IMAGE_POLICY
      and "_context_" in KEYFRAME_FILES
      and SERVICE.index("persistSelectedBurst(baseItem)") < SERVICE.index("jobChannel?.trySend(item)")
      and 'if (keyframeId == null)' in SERVICE
      and 'stopDriveSession(' in SERVICE[SERVICE.index('if (keyframeId == null)'):SERVICE.index('val item = baseItem.copy')]
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
check("video quota charges exact disk bytes and shares the free-space floor with evidence",
      "val bytes = segment.file.length().coerceAtLeast(0L)" in CAMERA
      and "maxOf(segment.file.length(), event.recordingStats.numBytesRecorded)" not in CAMERA
      and "private val NativeProcessFreeSpaceReservations" in QUOTA
      and "freeSpaceReservations.tryReserve(" in QUOTA
      and "freeSpaceReservations.release(entry.freeSpaceReservation)" in QUOTA
      and "actualBytes > entry.bytes" in QUOTA
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
      and "NativeReportEvidenceStorage.saveJpegAtomically(" in INFERENCE
      and "out.write(jpeg)" in REPORT_STORAGE
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

analysis = WEB[WEB.index("function nativeAutomaticReplayAllowed"):]
analysis = analysis[:analysis.index("function gpsAt")]
check("UI wires timer and idempotent saved-frame analysis",
      "const DRIVE_LIMIT_OPTIONS = [15, 30, 60, 90]" in WEB
      and "maxDurationMinutes: driveLimitMinutes()" in WEB
      and 'data-analysenative="${driveId}"' in WEB
      and "pendingOnly: true" in analysis
      and "`live:${state.driveId}:${saved.captureSeq}`" in analysis
      and "scheduleNativeKeyframeReplay(sessionId)" in WEB
      and "scheduleNativeKeyframeReplay();" in WEB
      and "result.analyzed !== true" in analysis
      and analysis.index('await api("/api/frame"')
          < analysis.index("() => plugin.markKeyframeAnalyzed"))
check("native Drive uses the live-regression model independent of Photo settings",
      'const NATIVE_DRIVE_DETECTION_MODEL = "gpt-5.6"' in WEB
      and 'const NATIVE_DRIVE_DETECTION_DETAIL = "high"' in WEB
      and "model: NATIVE_DRIVE_DETECTION_MODEL" in WEB
      and "detail: NATIVE_DRIVE_DETECTION_DETAIL" in WEB
      and 'private val model: String = "gpt-5.6"' in INFERENCE
      and 'call.getString("model") ?: "gpt-5.6"' in PLUGIN
      and 'intent?.getStringExtra(EXTRA_MODEL) ?: "gpt-5.6"' in SERVICE)
check("hybrid status is explicit and all shipped web copies match",
      "RECORDING VIDEO" in WEB and '"Video: On"' in WEB
      and "SAVING FRAMES" in WEB and "saved frame" in WEB and "min left" in WEB
      and (ROOT / "android-app/www/index.html").read_bytes()
          == (ROOT / "static/index.html").read_bytes()
      and (ROOT / "docs/index.html").read_bytes()
          == (ROOT / "static/index.html").read_bytes()
      and (ROOT / "android-app/android/app/src/main/assets/public/index.html").read_bytes()
          == (ROOT / "static/index.html").read_bytes())
check("Android release identity is 1.36.5 code 60 everywhere",
      re.search(r"versionCode\s+60\b", GRADLE)
      and re.search(r'versionName\s+"1\.36\.5"', GRADLE)
      and 'android:versionCode="60"' in RELEASE
      and 'android:versionName="1.36.5"' in RELEASE)

if failures:
    print(f"\nFAIL: {len(failures)} hybrid Drive contract check(s) failed")
    sys.exit(1)
print("\nHYBRID DRIVE CONTRACT TEST PASS")
