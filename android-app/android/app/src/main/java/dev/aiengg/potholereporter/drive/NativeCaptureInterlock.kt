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
 * Phone-camera prerequisite failures release CameraX so the app cannot keep handset
 * video without usable sensors. An external dashcam transport remains visible and warm
 * through location failures, but no evidence is sampled until a fresh fix returns.
 */
object NativeCaptureInterlock {
    fun evaluate(
        cameraPermissionGranted: Boolean,
        cameraReady: Boolean,
        cameraIssue: String?,
        location: NativeLocationAccess,
        cameraAccessBlocked: Boolean = false,
        sourceKind: NativeFrameSourceKind = NativeFrameSourceKind.PHONE_CAMERA
    ): NativeCaptureDecision {
        val sourceName = if (sourceKind == NativeFrameSourceKind.DASHCAM)
            "Dashcam stream" else "Camera"
        val dashcamKeepsStreaming = sourceKind == NativeFrameSourceKind.DASHCAM
        return when {
            sourceKind.requiresCameraPermission && !cameraPermissionGranted -> blocked(
                NativeCaptureBlocker.CAMERA_PERMISSION,
                "Camera permission was removed. Camera, detection, and video are paused; re-enable Camera in Android Settings.",
                releaseCamera = true
            )
            sourceKind.requiresCameraPermission && cameraAccessBlocked -> blocked(
                NativeCaptureBlocker.CAMERA_PRIVACY,
                "Camera access is off in Android Quick Settings or blocked by device policy. Camera, detection, and video are paused; turn Camera access on to resume.",
                releaseCamera = true
            )
            !location.permissionGranted -> blocked(
                NativeCaptureBlocker.LOCATION_PERMISSION,
                if (dashcamKeepsStreaming)
                    "Location permission was removed. Detection is paused; the dashcam stream stays ready. Re-enable Location in Android Settings."
                else "Location permission was removed. Camera, detection, and video are paused; re-enable Location in Android Settings.",
                releaseCamera = !dashcamKeepsStreaming
            )
            !location.servicesEnabled -> blocked(
                NativeCaptureBlocker.LOCATION_SERVICES,
                if (dashcamKeepsStreaming)
                    "Phone Location is turned off. Detection is paused; the dashcam stream stays ready. Turn Location on to resume."
                else "Phone Location is turned off. Camera, detection, and video are paused; turn Location on to resume.",
                releaseCamera = !dashcamKeepsStreaming
            )
            !location.providerAvailable -> blocked(
                NativeCaptureBlocker.GPS_UNAVAILABLE,
                if (dashcamKeepsStreaming)
                    "GPS is unavailable. Detection is paused; the dashcam stream stays ready. Capture resumes after GPS returns."
                else "GPS is unavailable. Camera, detection, and video are paused; capture resumes after GPS returns.",
                releaseCamera = !dashcamKeepsStreaming
            )
            !location.freshFixAvailable -> blocked(
                NativeCaptureBlocker.WAITING_FOR_FRESH_FIX,
                if (dashcamKeepsStreaming)
                    "Waiting for a fresh, precise GPS fix (30 m or better). Detection is paused; the dashcam stream stays ready."
                else "Waiting for a fresh, precise GPS fix (30 m or better). Detection is paused; camera and local video stay ready.",
                releaseCamera = false
            )
            !cameraReady -> blocked(
                NativeCaptureBlocker.CAMERA_UNAVAILABLE,
                cameraIssue?.takeIf { it.isNotBlank() }
                    ?: "$sourceName is unavailable. Detection is paused; capture resumes automatically when the source returns.",
                releaseCamera = false
            )
            else -> NativeCaptureDecision(
                blocker = NativeCaptureBlocker.NONE,
                canCapture = true,
                shouldReleaseCamera = false,
                message = "Scanning live"
            )
        }
    }

    private fun blocked(
        blocker: NativeCaptureBlocker,
        message: String,
        releaseCamera: Boolean
    ) = NativeCaptureDecision(blocker, false, releaseCamera, message)
}
