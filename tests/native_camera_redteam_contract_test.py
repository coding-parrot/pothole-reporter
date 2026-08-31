#!/usr/bin/env python3
"""Static integration guard for camera/GPS/notification races found by red-team audit."""

from pathlib import Path
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "android-app/android/app/src/main"
DRIVE = SRC / "java/dev/aiengg/potholereporter/drive"
CAMERA = (DRIVE / "NativeDriveCameraManager.kt").read_text()
LOCATION = (DRIVE / "NativeDriveLocationProvider.kt").read_text()
SERVICE = (DRIVE / "DriveForegroundService.kt").read_text()
INFERENCE = "\n".join(
    (DRIVE / name).read_text()
    for name in ("NativeInferenceEngine.kt", "NativeInferenceRequest.kt", "NativeInferenceTransport.kt")
)
NOTIFICATION = (DRIVE / "NotificationHelper.kt").read_text()
RECEIVER = (DRIVE / "NotificationActionReceiver.kt").read_text()
WEB = (ROOT / "static/index.html").read_text()
MANIFEST = ET.parse(SRC / "AndroidManifest.xml").getroot()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


bind = CAMERA[CAMERA.index("private fun bindCameraUseCases"):
              CAMERA.index("private fun handleCameraState")]
process = CAMERA[CAMERA.index("private fun processImageProxy"):
                 CAMERA.index("suspend fun captureBurst")]
release = CAMERA[CAMERA.index("private fun releaseCameraUseCases"):
                 CAMERA.index("private fun clearRollingFrames")]
stop = SERVICE[SERVICE.index("fun stopDriveSession("):
               SERVICE.index("private fun persistSessionTracked")]
video_toggle = CAMERA[CAMERA.index("suspend fun setVideoRecordingEnabled"):
                      CAMERA.index("fun attachPreview")]
pause_resume = SERVICE[SERVICE.index("fun pauseDrive("):
                       SERVICE.index("fun attachPreview")]
service_destroy = SERVICE[SERVICE.index("override fun onDestroy"):]

check(
    "provider and graph callbacks carry exact generation tokens",
    "cameraStartGeneration.issue()" in CAMERA
    and "cameraStartGeneration.isCurrent(startToken)" in CAMERA
    and "cameraGraphGeneration.issue()" in bind
    and "processImageProxy(imageProxy, graphToken)" in bind
    and "cameraGraphGeneration.isCurrent(graphToken)" in bind
    and "cameraGraphGeneration.isCurrent(sourceGraphGeneration)" in process
    and "sourceGraphGeneration" in CAMERA[
        CAMERA.index("val frame = CapturedFrame("):
        CAMERA.index("val retained = synchronized(rollingFrameLock)")]
    and "sourceGraphGeneration" in process,
)
check(
    "old analyzer is cleared before CameraX unbind and failed close remains retryable",
    release.index("analysis?.clearAnalyzer()") < release.index("cameraProvider?.unbindAll()")
    and "if (released)" in CAMERA
    and CAMERA.index("if (released)") < CAMERA.index("fullyClosed = true", CAMERA.index("if (released)")),
)
check(
    "Stop joins a cancelled GPS privacy source-release transition",
    "val accessTransition = accessTransitionJob" in stop
    and "accessTransition?.cancel()" in stop
    and "accessTransition?.join()" in stop
    and stop.index("accessTransition?.join()") < stop.index("manager?.stopSafely()"),
)
preview_reconcile = CAMERA[
    CAMERA.index("private fun reconcilePreviewSurfaceProvider"):
    CAMERA.index('@SuppressLint("MissingPermission")', CAMERA.index("private fun reconcilePreviewSurfaceProvider"))
]
check(
    "visible Preview failure closes capture and enters serialized camera recovery",
    "bindCameraUseCases()" not in preview_reconcile
    and "publishState(false, reason)" in preview_reconcile
    and "NativeCameraRecoveryAction.RELEASE_AND_RETRY" in preview_reconcile
    and "detection and video continue" not in preview_reconcile
    and "previewSurfaceProvider !== expectedSurfaceProvider" in CAMERA,
)
check(
    "rapid Video Off then On cannot cancel bounded Finalize cleanup",
    "withContext(NonCancellable) { awaitFinalization(transition.first) }" in video_toggle
    and video_toggle.index("stopActiveRecording()")
        < video_toggle.index("withContext(NonCancellable) { awaitFinalization(transition.first) }"),
)
check(
    "VideoCapture is absent by default and a toggle publishes the black-frame transition",
    "if (!isVideoRecordingEnabled)" in bind
    and bind.index("if (!isVideoRecordingEnabled)") < bind.index("bindWithVideo(QualitySelector.from(")
    and "bindWithoutVideo()" in bind
    and "videoCapture = null" in bind
    and 'bindCameraUseCases("Applying video setting")' in video_toggle
    and "transitionReason?.let { publishState(false, it) }" in bind,
)
interlock = (DRIVE / "NativeCaptureInterlock.kt").read_text()
stale_fix = interlock[
    interlock.index("!location.freshFixAvailable"):
    interlock.index("!cameraReady")
]
check(
    "transient stale GPS blocks evidence without tearing down the camera graph",
    "releaseCamera = false" in stale_fix
    and "camera and local video stay ready" in stale_fix,
)
check(
    "Pause and Resume completion is independent of Room and preserves the latest intent",
    "queuePauseTransitionIntent(paused = true" in pause_resume
    and "queuePauseTransitionIntent(paused = false" in pause_resume
    and "val desiredPaused = pauseTransitionIntent.take()" in pause_resume
    and "persistSessionTracked(\"paused\", null)" in pause_resume
    and "persistSessionTracked(\"active\", null)" in pause_resume
    and "previous?.join()" in SERVICE[SERVICE.index("private fun persistSessionTracked"):]
    and "sessionPersistTail = job" in SERVICE[SERVICE.index("private fun persistSessionTracked"):]
    and "withContext(Dispatchers.IO) { persistSession(\"paused\"" not in pause_resume
    and "withContext(Dispatchers.IO) { persistSession(\"active\"" not in pause_resume,
)
check(
    "late persistence and old-session callbacks cannot revive a stopped Drive UI",
    SERVICE.count("terminalStatusSealed || activeService !== this") >= 2
    and "terminalStatusSealed = true" in stop
    and "notificationStopRequested = true" in service_destroy
    and "if (activeService === this) activeService = null" in service_destroy
    and "currentSession && incomingSession && currentSession !== incomingSession" in WEB
    and "nativePauseControlPending" in WEB
    and "nativeVideoControlPending" in WEB,
)
check(
    "abnormal destruction keeps disclosure until frame-source closure succeeds",
    service_destroy.index("frameSource?.closeImmediately()")
        < service_destroy.index("scheduleForegroundNotificationRemoval()")
    and "if (cameraClosed)" in service_destroy[
        service_destroy.index("frameSource?.closeImmediately()"):
        service_destroy.index("scheduleForegroundNotificationRemoval()")
    ],
)
check(
    "location registration retries and every async callback is identity plus generation guarded",
    "registrationGeneration.issue()" in LOCATION
    and LOCATION.count("registrationGeneration.isCurrent(generation)") >= 3
    and LOCATION.count("locationCallback !== callback") >= 3
    and "scheduleRegistrationRetry()" in LOCATION
    and "registrationGeneration.invalidate()" in LOCATION
    and "cancelRegistrationRetry()" in LOCATION,
)
check(
    "a callback-before-Task and a never-completing location Task cannot leak registration",
    LOCATION.count("acceptRegistrationCallback(generation, callback)") == 2
    and "cancelRegistrationTaskTimeout()" in LOCATION[
        LOCATION.index("private fun acceptRegistrationCallback"):
        LOCATION.index("private fun scheduleRegistrationRetry")
    ]
    and "ensureRegistrationWatchdog(generation, callback)" in LOCATION[
        LOCATION.index("private fun acceptRegistrationCallback"):
        LOCATION.index("private fun scheduleRegistrationRetry")
    ]
    and "removeRegistrationBestEffort(callback)" in LOCATION[
        LOCATION.index("private fun handleRegistrationFailure"):
        LOCATION.index("private fun acceptRegistrationCallback")
    ]
    and "REMOVE_RETRY_LIMIT = 4" in LOCATION,
)
check(
    "future and coarse GPS cannot cause useless burst or graph flapping",
    "MAX_FUTURE_SKEW_MS = NativeBurstAccessPolicy.MAX_FIX_PRIMARY_DELTA_MS" in LOCATION
    and "NativeLocationAvailabilityPolicy.isFreshFix(" in LOCATION[
        LOCATION.index("fun shouldTriggerCapture"):]
    and "if (NativeCaptureFixQualityPolicy.isUsable(" in LOCATION,
)
check(
    "notification loss is fail-safe while active or paused",
    ".setDeleteIntent(dismissPendingIntent)" in NOTIFICATION
    and "ACTION_DISMISS" in RECEIVER
    and "if (!notificationStillVisible()) continue" in SERVICE[
        SERVICE.index("private fun startSessionLimitLoop"):
        SERVICE.index("fun pauseDrive")]
    and "activeNotifications.any" in SERVICE
    and "notificationMissingChecks >= 2" in SERVICE
    and "requestNotificationStop(" in SERVICE
    and ".notify(stateNotificationId, notification)" in SERVICE,
)
notification_claim = SERVICE[
    SERVICE.index("private fun claimNotificationStop"):
    SERVICE.index("private fun requestNotificationStop")
]
check(
    "notification Stop closes burst admission before main-thread teardown",
    "notificationStopRequested = true" in notification_claim
    and "captureAccessEpoch.invalidate()" in notification_claim
    and "if (notificationStopRequested) return null" in SERVICE[
        SERVICE.index("private fun validatedPostBurstFix"):
        SERVICE.index("private fun isBurstAccessStillValid")
    ]
    and "&& !notificationStopRequested" in SERVICE[
        SERVICE.index("private fun dispatchStatus"):
        SERVICE.index("private fun recycle(")
    ],
)
check(
    "null process restart intent cannot silently reopen camera",
    "if (intent == null)" in SERVICE
    and SERVICE.index("if (intent == null)") < SERVICE.index("when (intent.action")
    and "return START_NOT_STICKY" in SERVICE[
        SERVICE.index("if (intent == null)"):SERVICE.index("when (intent.action")],
)
check(
    "transient API and transport failures defer while deterministic requests suspend detection only",
    "code == 0 || code in setOf(408, 409, 425, 429) || code in 500..599" in INFERENCE
    and "if (!isTransientInferenceFailure(code)) return null" in INFERENCE
    and "retryAfterMs = retryDelay" in INFERENCE
    and 'throw retryableFailure("OpenAI detection connection failed", error)' in INFERENCE
    and 'throw retryableFailure("OpenAI repair-check connection failed", error)' in INFERENCE
    and "AtomicInteger(0)" in INFERENCE
    and INFERENCE.count("consecutiveRetryableFailures.set(0)") >= 2
    and "consecutiveRetryableFailures.getAndIncrement()" in INFERENCE
    and "frame saved for later" in SERVICE
    and "if (error.suspendInference)" in SERVICE
    and "camera and local video continue" in SERVICE
    and "error.retryAfterMs?.let { delay(it) }" in SERVICE
    and "error.fatal" not in SERVICE,
)

android = "{http://schemas.android.com/apk/res/android}"
rear_camera = next(
    node for node in MANIFEST.findall("uses-feature")
    if node.get(android + "name") == "android.hardware.camera"
)
check(
    "dashcam-only devices remain installable while phone capture rejects a missing rear camera",
    rear_camera.get(android + "required") == "false"
    and "provider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA)" in CAMERA,
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native camera red-team contract tests passed")
