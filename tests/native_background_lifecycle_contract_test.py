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
    "Drive is a non-exported camera/location foreground service that survives task switches",
    drive_service.get(android + "foregroundServiceType") == "camera|location"
    and drive_service.get(android + "exported") == "false"
    and drive_service.get(android + "stopWithTask") == "false",
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
    and "startCompletionLedger.record(startRequestId, summary)" in failed_start
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
    "@Override\n    public void onPause()" in ACTIVITY
    and "hideDrivePreview();" in ACTIVITY
    and "stopDrive" not in ACTIVITY
    and "private val lifecycleOwner: LifecycleOwner" in CAMERA
    and "provider.bindToLifecycle(" in CAMERA,
)

check(
    "native app-state events detach and reliably restore the transparent preview",
    'App.addListener("appStateChange"' in RUNTIME
    and "window.handleNativeAppStateChange" in RUNTIME
    and "window.handleNativeAppStateChange = async (isActive)" in WEB
    and "nativeHostAppActive = !!isActive" in WEB
    and "stopNativePreview();" in WEB[WEB.index("window.handleNativeAppStateChange"):]
    and "await restoreNativeDrive({ syncWhenIdle: false })" in WEB,
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
    and "latestFix?.timestampMs" in availability_callback
    and availability_callback.index("providerIsUsable(")
        < availability_callback.index("if (!providerUsable) latestFix = null")
    and "NativeLocationAvailabilityPolicy.isFreshFix(" in capture_access
    and "NativeLocationAvailabilityPolicy.providerIsUsable(" in capture_access
    and "reportedAvailable = reportedProviderAvailable" in capture_access
    and "providerAvailable = providerUsable" in capture_access
    and "GPS_MAX_AGE_MS" in capture_access
    and "fun stopUpdates()" in LOCATION
    and "reportedProviderAvailable = false\n        latestFix = null" in LOCATION,
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native background lifecycle contract tests passed")
