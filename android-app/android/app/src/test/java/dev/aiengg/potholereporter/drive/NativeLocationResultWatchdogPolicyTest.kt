package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeLocationResultWatchdogPolicyTest {
    @Test
    fun `availability-only callbacks never advance last real result time`() {
        val registeredAtMs = 1_000L
        val afterAvailability = NativeLocationResultWatchdogPolicy.noteRealResult(
            previousResultElapsedMs = registeredAtMs,
            callbackElapsedMs = 29_000L,
            deliveredLocation = false
        )

        assertEquals(registeredAtMs, afterAvailability)
        assertTrue(
            NativeLocationResultWatchdogPolicy.isStalled(
                lastResultElapsedMs = afterAvailability,
                nowMs = 31_000L,
                timeoutMs = 30_000L
            )
        )
    }

    @Test
    fun `real location result resets the bounded no-fix timeout`() {
        val afterResult = NativeLocationResultWatchdogPolicy.noteRealResult(
            previousResultElapsedMs = 1_000L,
            callbackElapsedMs = 29_000L,
            deliveredLocation = true
        )

        assertEquals(29_000L, afterResult)
        assertFalse(
            NativeLocationResultWatchdogPolicy.isStalled(
                lastResultElapsedMs = afterResult,
                nowMs = 31_000L,
                timeoutMs = 30_000L
            )
        )
        assertTrue(
            NativeLocationResultWatchdogPolicy.isStalled(
                lastResultElapsedMs = afterResult,
                nowMs = 59_000L,
                timeoutMs = 30_000L
            )
        )
    }
}
