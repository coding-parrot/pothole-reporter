package dev.aiengg.potholereporter.drive

import androidx.camera.core.CameraState

/** Explicit service action for a CameraX state error. */
enum class NativeCameraRecoveryAction {
    /** CameraX documents these errors as recoverable while the use cases stay bound. */
    WAIT_FOR_CAMERAX,

    /** Release the failed graph and make a bounded attempt to bind a fresh one. */
    RELEASE_AND_RETRY,

    /** The current drive cannot safely continue and must surface a terminal reason. */
    STOP_TERMINALLY
}

internal object NativeCameraRecoveryPolicy {
    fun actionFor(
        errorCode: Int,
        errorType: CameraState.ErrorType
    ): NativeCameraRecoveryAction = when (errorCode) {
        CameraState.ERROR_CAMERA_IN_USE,
        CameraState.ERROR_MAX_CAMERAS_IN_USE,
        CameraState.ERROR_OTHER_RECOVERABLE_ERROR ->
            NativeCameraRecoveryAction.WAIT_FOR_CAMERAX

        CameraState.ERROR_CAMERA_DISABLED,
        CameraState.ERROR_DO_NOT_DISTURB_MODE_ENABLED ->
            NativeCameraRecoveryAction.RELEASE_AND_RETRY

        CameraState.ERROR_STREAM_CONFIG,
        CameraState.ERROR_CAMERA_FATAL_ERROR ->
            NativeCameraRecoveryAction.STOP_TERMINALLY

        else -> if (errorType == CameraState.ErrorType.CRITICAL) {
            NativeCameraRecoveryAction.STOP_TERMINALLY
        } else {
            NativeCameraRecoveryAction.WAIT_FOR_CAMERAX
        }
    }
}

/** Main-thread retry ledger kept separate so exhaustion and reset are deterministic. */
internal class NativeCameraRetryBudget(private val maxAttempts: Int) {
    init {
        require(maxAttempts > 0)
    }

    private var attempts = 0

    fun nextAttemptOrNull(): Int? {
        if (attempts >= maxAttempts) return null
        attempts += 1
        return attempts
    }

    fun reset() {
        attempts = 0
    }
}
