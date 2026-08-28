package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DrivePauseTimeoutPolicyTest {
    @Test
    fun `pause has an independent bounded lifetime`() {
        val policy = DrivePauseTimeoutPolicy(timeoutMs = 1_000L)
        policy.beginPause(5_000L)

        assertEquals(1_000L, policy.remainingMs(5_000L))
        assertEquals(1L, policy.remainingMs(5_999L))
        assertFalse(policy.expired(5_999L))
        assertTrue(policy.expired(6_000L))
    }

    @Test
    fun `resume clears the deadline and a later pause gets a new one`() {
        val policy = DrivePauseTimeoutPolicy(timeoutMs = 1_000L)
        policy.beginPause(10L)
        policy.clearPause(500L)
        assertNull(policy.remainingMs(5_000L))

        policy.beginPause(6_000L)
        assertEquals(1_000L, policy.remainingMs(6_000L))
        assertTrue(policy.expired(7_000L))
    }

    @Test(expected = IllegalArgumentException::class)
    fun `wall clock rollback cannot silently extend a pause`() {
        val policy = DrivePauseTimeoutPolicy(timeoutMs = 1_000L)
        policy.beginPause(5_000L)
        policy.remainingMs(4_999L)
    }
}
