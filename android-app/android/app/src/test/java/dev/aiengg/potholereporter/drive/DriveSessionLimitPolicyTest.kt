package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class DriveSessionLimitPolicyTest {
    @Test
    fun defaultLimitExpiresAfterThirtyActiveMinutes() {
        val policy = DriveSessionLimitPolicy(startedAtElapsedMs = 1_000L)

        assertEquals(minutes(30), policy.remainingMs(1_000L))
        assertFalse(policy.expired(1_000L + minutes(29)))
        assertEquals(minutes(1), policy.remainingMs(1_000L + minutes(29)))
        assertTrue(policy.expired(1_000L + minutes(30)))
        assertEquals(0L, policy.remainingMs(1_000L + minutes(90)))
    }

    @Test
    fun pauseExcludesTimeAndRepeatedTransitionsDoNotResetBudget() {
        val start = 5_000L
        val policy = DriveSessionLimitPolicy(start, limitMinutes = 15)

        policy.pause(start + minutes(5))
        policy.pause(start + minutes(20))
        assertEquals(minutes(10), policy.remainingMs(start + minutes(60)))

        policy.resume(start + minutes(60))
        policy.resume(start + minutes(65))
        assertEquals(minutes(5), policy.remainingMs(start + minutes(65)))

        policy.pause(start + minutes(67))
        policy.resume(start + minutes(80))
        assertFalse(policy.expired(start + minutes(82)))
        assertEquals(minutes(1), policy.remainingMs(start + minutes(82)))
        assertTrue(policy.expired(start + minutes(83)))

        policy.pause(start + minutes(90))
        policy.resume(start + minutes(100))
        assertTrue(policy.expired(start + minutes(100)))
    }

    @Test
    fun acceptsBoundaryLimitsAndRejectsInvalidConfigurationOrClock() {
        assertEquals(minutes(15), DriveSessionLimitPolicy(0L, 15).remainingMs(0L))
        assertEquals(minutes(90), DriveSessionLimitPolicy(0L, 90).remainingMs(0L))
        assertThrows(IllegalArgumentException::class.java) { DriveSessionLimitPolicy(0L, 14) }
        assertThrows(IllegalArgumentException::class.java) { DriveSessionLimitPolicy(0L, 91) }
        assertThrows(IllegalArgumentException::class.java) { DriveSessionLimitPolicy(-1L) }

        val policy = DriveSessionLimitPolicy(100L)
        policy.remainingMs(200L)
        assertThrows(IllegalArgumentException::class.java) { policy.pause(199L) }
    }

    private fun minutes(value: Long): Long = value * 60_000L
}
