package dev.aiengg.potholereporter.drive

import androidx.camera.core.CameraState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NativeCameraRecoveryPolicyTest {
    @Test
    fun ordinaryContentionStaysBoundForCameraXRecovery() {
        listOf(
            CameraState.ERROR_CAMERA_IN_USE,
            CameraState.ERROR_MAX_CAMERAS_IN_USE,
            CameraState.ERROR_OTHER_RECOVERABLE_ERROR
        ).forEach { code ->
            assertEquals(
                NativeCameraRecoveryAction.WAIT_FOR_CAMERAX,
                NativeCameraRecoveryPolicy.actionFor(code, CameraState.ErrorType.RECOVERABLE)
            )
        }
    }

    @Test
    fun recoverableByUserStateCriticalErrorsRequireAFreshGraph() {
        listOf(
            CameraState.ERROR_CAMERA_DISABLED,
            CameraState.ERROR_DO_NOT_DISTURB_MODE_ENABLED
        ).forEach { code ->
            assertEquals(
                NativeCameraRecoveryAction.RELEASE_AND_RETRY_USER_STATE,
                NativeCameraRecoveryPolicy.actionFor(code, CameraState.ErrorType.CRITICAL)
            )
        }
    }

    @Test
    fun brokenGraphAndUnknownCriticalErrorsStopTerminally() {
        listOf(
            CameraState.ERROR_STREAM_CONFIG,
            CameraState.ERROR_CAMERA_FATAL_ERROR,
            Int.MAX_VALUE
        ).forEach { code ->
            assertEquals(
                NativeCameraRecoveryAction.STOP_TERMINALLY,
                NativeCameraRecoveryPolicy.actionFor(code, CameraState.ErrorType.CRITICAL)
            )
        }
        assertEquals(
            NativeCameraRecoveryAction.WAIT_FOR_CAMERAX,
            NativeCameraRecoveryPolicy.actionFor(Int.MAX_VALUE, CameraState.ErrorType.RECOVERABLE)
        )
    }

    @Test
    fun retryBudgetIsBoundedAndResettable() {
        val budget = NativeCameraRetryBudget(3)
        assertEquals(1, budget.nextAttemptOrNull())
        assertEquals(2, budget.nextAttemptOrNull())
        assertEquals(3, budget.nextAttemptOrNull())
        assertNull(budget.nextAttemptOrNull())
        budget.reset()
        assertEquals(1, budget.nextAttemptOrNull())
    }
}
