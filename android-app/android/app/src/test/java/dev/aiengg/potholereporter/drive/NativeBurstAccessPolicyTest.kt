package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeBurstAccessPolicyTest {
    private val primaryAt = 10_000L
    private val fix = GpsFix(19.076, 72.8777, 4f, 8f, 90f, primaryAt + 500L)

    private fun validate(
        before: Long = 4L,
        after: Long = 4L,
        running: Boolean = true,
        paused: Boolean = false,
        stopping: Boolean = false,
        interlock: Boolean = true,
        cameraReady: Boolean = true,
        cameraReleased: Boolean = false,
        capturedAt: Long = primaryAt,
        candidate: GpsFix? = fix
    ) = NativeBurstAccessPolicy.validatedPostBurstFix(
        before, after, running, paused, stopping, interlock, cameraReady,
        cameraReleased, capturedAt, candidate
    )

    @Test
    fun unchangedAccessReturnsTheExactPostBurstFix() {
        assertEquals(fix, validate())
        assertNotNull(validate(candidate = fix.copy(timestampMs = primaryAt - 2_500L)))
        assertNotNull(validate(candidate = fix.copy(timestampMs = primaryAt + 2_500L)))
    }

    @Test
    fun anyAccessOrLifecycleTransitionRejectsTheWholeBurst() {
        assertNull(validate(after = 5L))
        assertNull(validate(running = false))
        assertNull(validate(paused = true))
        assertNull(validate(stopping = true))
        assertNull(validate(interlock = false))
        assertNull(validate(cameraReady = false))
        assertNull(validate(cameraReleased = true))
    }

    @Test
    fun staleFutureOrInvalidCoordinatesNeverReachEvidenceWork() {
        assertNull(validate(candidate = fix.copy(timestampMs = primaryAt - 2_501L)))
        assertNull(validate(candidate = fix.copy(timestampMs = primaryAt + 2_501L)))
        assertNull(validate(candidate = fix.copy(lat = Double.NaN)))
        assertNull(validate(candidate = fix.copy(lat = 91.0)))
        assertNull(validate(candidate = fix.copy(lng = -181.0)))
        assertNull(validate(candidate = fix.copy(accuracy = Float.NaN)))
        assertNull(validate(candidate = fix.copy(accuracy = -1f)))
        assertNull(validate(candidate = null))
    }

    @Test
    fun epochInvalidationIsMonotonic() {
        val epoch = NativeCaptureAccessEpoch()
        val before = epoch.snapshot()
        assertTrue(epoch.invalidate() > before)
        assertTrue(epoch.invalidate() > before)
    }
}
