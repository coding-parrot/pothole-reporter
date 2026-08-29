#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/DriveForegroundService.kt").read_text()
LOCATION = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeDriveLocationProvider.kt").read_text()
INTERLOCK = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeCaptureInterlock.kt").read_text()


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)


availability = LOCATION[
    LOCATION.index("override fun onLocationAvailability"):
    LOCATION.index("override fun onLocationResult")
]
location_result = LOCATION[
    LOCATION.index("override fun onLocationResult"):
    LOCATION.index("locationCallback = callback")
]
accept_callback = LOCATION[
    LOCATION.index("private fun acceptRegistrationCallback"):
    LOCATION.index("private fun scheduleRegistrationRetry")
]
watchdog = LOCATION[
    LOCATION.index("private fun ensureRegistrationWatchdog"):
    LOCATION.index("private fun cancelRegistrationWatchdog")
]

check(
    "availability proves registration without resetting or postponing the no-fix watchdog",
    "acceptRegistrationCallback(generation, callback)" in availability
    and "lastLocationResultElapsedMs" not in availability
    and "lastLocationResultElapsedMs" not in accept_callback
    and "if (registrationWatchdog != null) return" in watchdog
    and "NativeLocationResultWatchdogPolicy.isStalled(" in watchdog,
)
check(
    "only a non-null location result advances the watchdog clock",
    location_result.index("result.lastLocation ?: return")
    < location_result.index("lastLocationResultElapsedMs =")
    and "deliveredLocation = true" in location_result,
)

scan = SERVICE[
    SERVICE.index("private fun startScanLoop()"):
    SERVICE.index("private fun validatedPostBurstFix")
]
decision = SERVICE[
    SERVICE.index("private fun captureInterlockDecision"):
    SERVICE.index("private fun requestCaptureInterlockRefresh")
]
refresh = SERVICE[
    SERVICE.index("private fun refreshPrerequisiteAccessState"):
    SERVICE.index("private fun noteCameraState")
]
check(
    "50 ms frame scheduler polls Binder-backed prerequisites no faster than once per second",
    "delay(50)" in scan
    and "PREREQUISITE_ACCESS_RECHECK_MS = 1_000L" in SERVICE
    and "mainHandler.post(::refreshPrerequisiteAccessState)" in scan
    and "captureAccess()" not in scan
    and "hasCameraPermission()" not in scan,
)
check(
    "hot interlock decisions consume callback cache instead of querying Android services",
    "lastLocationAccess ?: NativeLocationAccess(" in decision
    and "cameraPermissionGranted = cameraPermissionGranted" in decision
    and "captureAccess()" not in decision
    and "hasCameraPermission()" not in decision
    and "locationProvider?.captureAccess()" in refresh
    and "hasCameraPermission()" in refresh,
)
check(
    "high-accuracy fixes are requested at the dense capture pairing cadence",
    "LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 500L)" in LOCATION
    and ".setMinUpdateIntervalMillis(250L)" in LOCATION
    and ".setMinUpdateDistanceMeters(1.0f)" in LOCATION,
)

stale_fix = INTERLOCK[
    INTERLOCK.index("!location.freshFixAvailable"):
    INTERLOCK.index("!cameraReady")
]
check(
    "a transiently stale fix blocks evidence without tearing down CameraX",
    "NativeCaptureBlocker.WAITING_FOR_FRESH_FIX" in stale_fix
    and "releaseCamera = false" in stale_fix
    and "local video stay ready" in stale_fix
    and "cameraActive && capture.canCapture" not in SERVICE
    and "DETECTION WAITING FOR GPS" in
        (ROOT / "static/index.html").read_text(),
)

print("NATIVE LOCATION CONTROL-PLANE CONTRACT PASS")
