#!/usr/bin/env python3
"""Static contract for Android background camera continuity and safe interruption handling."""

from pathlib import Path
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android-app/android/app/src/main"
MANIFEST_PATH = ANDROID / "AndroidManifest.xml"
SERVICE = (ANDROID / "java/dev/aiengg/potholereporter/drive/DriveForegroundService.kt").read_text()
CAMERA = (ANDROID / "java/dev/aiengg/potholereporter/drive/NativeDriveCameraManager.kt").read_text()
START_REGISTRY = (ANDROID / "java/dev/aiengg/potholereporter/drive/DriveStartRegistry.kt").read_text()
LOCATION = (ANDROID / "java/dev/aiengg/potholereporter/drive/NativeDriveLocationProvider.kt").read_text()
RECEIVER = (ANDROID / "java/dev/aiengg/potholereporter/drive/NotificationActionReceiver.kt").read_text()
NOTIFICATION = (ANDROID / "java/dev/aiengg/potholereporter/drive/NotificationHelper.kt").read_text()
PLUGIN = (ANDROID / "java/dev/aiengg/potholereporter/plugin/DriveModePlugin.kt").read_text()
ACTIVITY = (ANDROID / "java/dev/aiengg/potholereporter/MainActivity.java").read_text()
WEB = (ROOT / "static/index.html").read_text()
RUNTIME = (ROOT / "static/standalone.js").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


android = "{http://schemas.android.com/apk/res/android}"
manifest = ET.parse(MANIFEST_PATH).getroot()
drive_service = next(
    node for node in manifest.findall("./application/service")
    if node.get(android + "name") == ".drive.DriveForegroundService"
)
check(
    "Drive is a non-exported source-specific location foreground service that survives task switches",
    drive_service.get(android + "foregroundServiceType") == "camera|connectedDevice|location"
    and drive_service.get(android + "exported") == "false"
    and drive_service.get(android + "stopWithTask") == "false"
    and "FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE" in SERVICE
    and "FOREGROUND_SERVICE_TYPE_CAMERA" in SERVICE
    and "captureSourceKind == NativeFrameSourceKind.DASHCAM" in SERVICE,
)

start_method = PLUGIN[PLUGIN.index("fun startDrive(call: PluginCall)"):
                      PLUGIN.index("fun pauseDrive(call: PluginCall)")]
check(
    "camera FGS creation is gated by a resumed visible Activity immediately before start",
    "activityIsVisibleForDriveStart()" in start_method
    and "Lifecycle.State.RESUMED" in start_method
    and start_method.index("activityIsVisibleForDriveStart()")
        < start_method.index("ContextCompat.startForegroundService"),
)

on_start = SERVICE[SERVICE.index("override fun onStartCommand"):
                   SERVICE.index("private fun handleStopIntent")]
failed_start = SERVICE[SERVICE.index("private fun failDriveStart"):
                       SERVICE.index("private fun handleStopIntent")]
check(
    "service-side foreground promotion failure is recorded and stopped without a process crash",
    "try {" in on_start and "failDriveStart(startId)" in on_start
    and "startRegistry.recordCompletion(startRequestId, summary)" in failed_start
    and "fun recordCompletion(requestId: String?, summary: DriveEndSummary)" in START_REGISTRY
    and "stopForeground(STOP_FOREGROUND_REMOVE)" in failed_start
    and "stopSelf(startId)" in failed_start,
)
check(
    "idle Pause/Resume actions cannot leave an empty started service",
    "ACTION_PAUSE -> if (sessionRunning || isStopping) pauseDrive() else stopSelf(startId)" in on_start
    and "ACTION_RESUME -> if (sessionRunning || isStopping) resumeDrive() else stopSelf(startId)" in on_start,
)

check(
    "notification controls never create a service and are scoped to the active session",
    "context.startService" not in RECEIVER
    and "DriveForegroundService.activeService" in RECEIVER
    and "controlsNotificationSession(expectedSession)" in RECEIVER
    and "EXTRA_SESSION_ID" in NOTIFICATION
    and '.appendPath(sessionId)' in NOTIFICATION
    and "expectedSessionId == sessionId" in SERVICE,
)

check(
    "Activity backgrounding detaches only Preview while service-owned CameraX stays bound",
    "public void onPause()" not in ACTIVITY
    and "public void onDestroy()" in ACTIVITY
    and "hideDrivePreview();" in ACTIVITY[ACTIVITY.index("public void onDestroy()") :]
    and "stopDrive" not in ACTIVITY
    and "private val lifecycleOwner: LifecycleOwner" in CAMERA
    and "provider.bindToLifecycle(" in CAMERA
    and "if (host != null) host.hideDrivePreview()" in PLUGIN
    and 'call.getBoolean("preserveGraph") != true' not in PLUGIN
    and "host.hasWindowFocus()" not in PLUGIN
    and "stopNativePreview(true)" in WEB,
)

bind_camera = CAMERA[
    CAMERA.index("private fun bindCameraUseCases"):
    CAMERA.index("private fun handleCameraState")
]
detach_preview = CAMERA[
    CAMERA.index("fun detachPreview(expectedSurfaceProvider"):
    CAMERA.index('@SuppressLint("MissingPermission")', CAMERA.index("fun detachPreview(expectedSurfaceProvider"))
]
reconcile_preview = CAMERA[
    CAMERA.index("private fun reconcilePreviewSurfaceProvider"):
    CAMERA.index('@SuppressLint("MissingPermission")', CAMERA.index("private fun reconcilePreviewSurfaceProvider"))
]
check(
    "true preview detach unbinds only Preview while Analysis and Video remain service-bound",
    "ImageReader" not in CAMERA
    and "backgroundPreviewSurfaceProvider" not in CAMERA
    and "desiredPreviewProvider != null" in bind_camera
    and "scheduleLatestPreviewReconciliation()" in bind_camera
    and "previewSurfaceProvider !== expectedSurfaceProvider" in detach_preview
    and "previewSurfaceProvider = null" in detach_preview
    and "runOnMain" in detach_preview
    and "private fun scheduleLatestPreviewReconciliation()" in detach_preview
    and "previewStateGeneration" in reconcile_preview
    and "generation != previewStateGeneration" in reconcile_preview
    and "provider.unbind(preview)" in reconcile_preview
    and reconcile_preview.index("provider.unbind(preview)")
        < reconcile_preview.index("preview.setSurfaceProvider(null)")
    and "provider.bindToLifecycle(" in reconcile_preview
    and "CameraSelector.DEFAULT_BACK_CAMERA,\n                preview" in reconcile_preview
    and "if (attached) return true" in reconcile_preview
    and "bindCameraUseCases()" not in reconcile_preview
    and "publishState(false, reason)" in reconcile_preview
    and "NativeCameraRecoveryAction.RELEASE_AND_RETRY" in reconcile_preview
    and "detection and video continue" not in reconcile_preview
    and "unbindAll()" not in reconcile_preview
    and "imageAnalysis =" not in reconcile_preview
    and "videoCapture =" not in reconcile_preview,
)

plugin_attach_preview = PLUGIN[
    PLUGIN.index("fun attachPreview(call: PluginCall)"):
    PLUGIN.index("fun detachPreview(call: PluginCall)")
]
check(
    "Preview surface is registered before the first CameraX graph bind",
    "status.cameraActive" not in plugin_attach_preview
    and "!status.isRunning" in plugin_attach_preview
    and "host.showDrivePreview" in plugin_attach_preview
)

show_preview = ACTIVITY[
    ACTIVITY.index("public void showDrivePreview("):
    ACTIVITY.index("public void hideDrivePreview()")
]
check(
    "Preview host is visible and laid out before CameraX receives its surface provider",
    "drivePreviewHost.setVisibility(View.VISIBLE);" in show_preview
    and "drivePreviewHost.requestLayout();" in show_preview
    and "attachDrivePreviewWhenLaidOut" in show_preview
    and "drivePreviewHost.isAttachedToWindow()" in show_preview
    and "drivePreviewHost.isLaidOut()" in show_preview
    and "service.attachPreview(drivePreview.getSurfaceProvider())" in show_preview
    and show_preview.index("drivePreviewHost.setVisibility(View.VISIBLE);")
        < show_preview.index("service.attachPreview(drivePreview.getSurfaceProvider())")
    and "if (Boolean.FALSE.equals(attachment))" in show_preview
    and "callback.onComplete(Boolean.TRUE.equals(attachment));" in show_preview,
)

check(
    "native app-state events detach and reliably restore the transparent preview",
    'Capacitor.registerPlugin("App")' in RUNTIME
    and 'App.addListener("appStateChange"' in RUNTIME
    and "window.handleNativeAppStateChange" in RUNTIME
    and "window.handleNativeAppStateChange = async (isActive)" in WEB
    and "nativeHostAppActive = !!isActive" in WEB
    and "stopNativePreview();" in WEB[WEB.index("window.handleNativeAppStateChange"):]
    and "await restoreNativeDrive({ syncWhenIdle: false })" in WEB,
)

finish_native_drive = WEB[
    WEB.index("async function finishNativeDrive"):
    WEB.index("async function ensureNativeDriveListeners")
]
check(
    "unexpected native termination reasons remain visible after teardown",
    "const terminalNotice" in finish_native_drive
    and "!/^stopped$/i.test(endReason)" in finish_native_drive
    and 'banner.textContent = terminalNotice' in finish_native_drive,
)
check(
    "Stop history hydration is bounded and camera start cancels idle-only cache work",
    "NATIVE_FINISH_HYDRATE_TIMEOUT_MS = 8000" in WEB
    and "hydrateNativeDriveEndWithinDeadline()" in finish_native_drive
    and "invalidateNativeBackgroundSyncs()" in finish_native_drive
    and "cancelNativeBackgroundSyncsForDriveStart(nativePlugin)" in WEB
    and "refreshNativeRepairData(nativePlugin).catch" not in WEB[
        WEB.index("async function startDrive()"):
        WEB.index('$("driveBtn").onclick = startDrive')]
)

availability_callback = LOCATION[
    LOCATION.index("override fun onLocationAvailability"):
    LOCATION.index("override fun onLocationResult")
]
capture_access = LOCATION[
    LOCATION.index("fun captureAccess("):
    LOCATION.index("private fun hasLocationPermission")
]
check(
    "a pessimistic fused-location hint cannot discard a still-fresh bounded GPS fix",
    "NativeLocationAvailabilityPolicy.providerIsUsable(" in availability_callback
    and "latestFix?.elapsedRealtimeMs" in availability_callback
    and availability_callback.index("providerIsUsable(")
        < availability_callback.index("if (!providerUsable) latestFix = null")
    and "NativeCaptureFixQualityPolicy.isUsable(" in capture_access
    and "NativeLocationAvailabilityPolicy.providerIsUsable(" in capture_access
    and "reportedAvailable = reportedProviderAvailable" in capture_access
    and "providerAvailable = providerUsable" in capture_access
    and "GPS_MAX_AGE_MS" in capture_access
    and "NativeBurstAccessPolicy.MAX_FIX_PRIMARY_DELTA_MS" in capture_access
    and "fun stopUpdates()" in LOCATION
    and "reportedProviderAvailable = false\n        latestFix = null" in LOCATION,
)

dispatch_status = SERVICE[
    SERVICE.index("private fun dispatchStatus()"):
    SERVICE.index("private fun recycle(item: BurstJob)")
]
check(
    "unchanged status callbacks do not repost the foreground notification every second",
    "private data class DriveNotificationState(" in SERVICE
    and "private var lastNotificationState: DriveNotificationState? = null" in SERVICE
    and "lastNotificationState = null" in SERVICE[
        SERVICE.index("private fun startDriveSession("):
        SERVICE.index("private fun startForegroundNow()")
    ]
    and "val notificationState = DriveNotificationState(" in dispatch_status
    and "Looper.myLooper() != Looper.getMainLooper()" in dispatch_status
    and dispatch_status.index("Looper.myLooper() != Looper.getMainLooper()")
        < dispatch_status.index("val notificationState = DriveNotificationState(")
    and "if (notificationState != lastNotificationState)" in dispatch_status
    and dispatch_status.index(".notify(NotificationHelper.NOTIFICATION_ID, notification)")
        < dispatch_status.index("lastNotificationState = notificationState"),
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native background lifecycle contract tests passed")
