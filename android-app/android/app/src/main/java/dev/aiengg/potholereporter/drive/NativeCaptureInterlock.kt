package dev.aiengg.potholereporter.drive

/** Inputs that must be true before a camera frame can be associated with a road point. */
data class NativeLocationAccess(
    val permissionGranted: Boolean,
    val servicesEnabled: Boolean,
    val providerAvailable: Boolean,
    val freshFixAvailable: Boolean
)

enum class NativeCaptureBlocker {
    NONE,
    CAMERA_PERMISSION,
    CAMERA_PRIVACY,
    LOCATION_PERMISSION,
    LOCATION_SERVICES,
    GPS_UNAVAILABLE,
    WAITING_FOR_FRESH_FIX,
    CAMERA_UNAVAILABLE
}

data class NativeCaptureDecision(
    val blocker: NativeCaptureBlocker,
    val canCapture: Boolean,
    /** CameraX should be unbound for prerequisite failures instead of recording unusable footage. */
    val shouldReleaseCamera: Boolean,
    val message: String
)

/**
 * Fail-closed sensor policy shared by the service callbacks and its polling loop.
 *
 * Permission, Android Camera-access, and hard GPS failures release CameraX so the app
 * cannot keep video without usable sensors. A transiently old fix blocks evidence but
 * keeps the graph warm; CameraX availability errors also remain bound for recovery.
 */
object NativeCaptureInterlock {
    fun evaluate(
        cameraPermissionGranted: Boolean,
        cameraReady: Boolean,
        cameraIssue: String?,
        location: NativeLocationAccess,
        cameraAccessBlocked: Boolean = false
    ): NativeCaptureDecision = when {
        !cameraPermissionGranted -> blocked(
            NativeCaptureBlocker.CAMERA_PERMISSION,
            "Camera permission was removed. Camera, detection, and video are paused; re-enable Camera in Android Settings.",
            releaseCamera = true
        )
        cameraAccessBlocked -> blocked(
            NativeCaptureBlocker.CAMERA_PRIVACY,
            "Camera access is off in Android Quick Settings or blocked by device policy. Camera, detection, and video are paused; turn Camera access on to resume.",
            releaseCamera = true
        )
        !location.permissionGranted -> blocked(
            NativeCaptureBlocker.LOCATION_PERMISSION,
            "Location permission was removed. Camera, detection, and video are paused; re-enable Location in Android Settings.",
            releaseCamera = true
        )
        !location.servicesEnabled -> blocked(
            NativeCaptureBlocker.LOCATION_SERVICES,
            "Phone Location is turned off. Camera, detection, and video are paused; turn Location on to resume.",
            releaseCamera = true
        )
        !location.providerAvailable -> blocked(
            NativeCaptureBlocker.GPS_UNAVAILABLE,
            "GPS is unavailable. Camera, detection, and video are paused; capture resumes after GPS returns.",
            releaseCamera = true
        )
        !location.freshFixAvailable -> blocked(
            NativeCaptureBlocker.WAITING_FOR_FRESH_FIX,
            "Waiting for a fresh, precise GPS fix (30 m or better). Detection is paused; camera and local video stay ready.",
            releaseCamera = false
        )
        !cameraReady -> blocked(
            NativeCaptureBlocker.CAMERA_UNAVAILABLE,
            cameraIssue?.takeIf { it.isNotBlank() }
                ?: "Camera access is unavailable. Detection and video are paused; capture resumes automatically when access returns.",
            releaseCamera = false
        )
        else -> NativeCaptureDecision(
            blocker = NativeCaptureBlocker.NONE,
            canCapture = true,
            shouldReleaseCamera = false,
            message = "Scanning live"
        )
    }

    private fun blocked(
        blocker: NativeCaptureBlocker,
        message: String,
        releaseCamera: Boolean
    ) = NativeCaptureDecision(blocker, false, releaseCamera, message)
}
