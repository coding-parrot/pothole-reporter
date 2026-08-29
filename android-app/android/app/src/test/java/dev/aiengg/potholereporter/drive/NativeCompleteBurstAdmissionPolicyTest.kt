package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeCompleteBurstAdmissionPolicyTest {
    @Test
    fun `moving and unknown-speed windows are admitted as soon as complete`() {
        assertTrue(NativeCompleteBurstAdmissionPolicy.shouldAttempt(true, 8f, true, false))
        assertTrue(NativeCompleteBurstAdmissionPolicy.shouldAttempt(true, 0.1f, true, false))
        assertTrue(NativeCompleteBurstAdmissionPolicy.shouldAttempt(true, 0.25f, true, false))
        assertTrue(NativeCompleteBurstAdmissionPolicy.shouldAttempt(true, null, true, false))
        assertTrue(NativeCompleteBurstAdmissionPolicy.shouldAttempt(true, 0f, false, false))
    }

    @Test
    fun `only a complete accurately parked window waits for parked cadence`() {
        assertFalse(NativeCompleteBurstAdmissionPolicy.shouldAttempt(false, 8f, true, true))
        assertFalse(NativeCompleteBurstAdmissionPolicy.shouldAttempt(true, 0f, true, false))
        val displacedZeroSpeedIsDue = NativeCaptureCadencePolicy.isDue(
            hasPreviousCapture = true,
            sinceLastMs = 100L,
            movedMeters = 20.0,
            displacementUncertaintyM = 8.0,
            speedMps = 0f,
            accurateFix = true
        )
        assertTrue(
            NativeCompleteBurstAdmissionPolicy.shouldAttempt(
                true,
                0f,
                true,
                displacedZeroSpeedIsDue
            )
        )
    }
}
