package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeLocationAvailabilityPolicyTest {
    private val nowMs = 1_000_000L
    private val maxAgeMs = NativeDriveLocationProvider.GPS_MAX_AGE_MS

    @Test
    fun `fresh delivered fix survives a pessimistic availability callback`() {
        assertTrue(
            NativeLocationAvailabilityPolicy.providerIsUsable(
                reportedAvailable = false,
                latestFixTimestampMs = nowMs - 1_000L,
                nowMs = nowMs,
                maxAgeMs = maxAgeMs
            )
        )
    }

    @Test
    fun `stale fix never overrides unavailable provider`() {
        assertFalse(
            NativeLocationAvailabilityPolicy.providerIsUsable(
                reportedAvailable = false,
                latestFixTimestampMs = nowMs - maxAgeMs - 1L,
                nowMs = nowMs,
                maxAgeMs = maxAgeMs
            )
        )
    }

    @Test
    fun `repeated pessimistic hints do not flap camera before fix expires`() {
        val fixTimestampMs = nowMs
        assertTrue(
            NativeLocationAvailabilityPolicy.providerIsUsable(
                false, fixTimestampMs, nowMs + 1_000L, maxAgeMs
            )
        )
        assertTrue(
            NativeLocationAvailabilityPolicy.providerIsUsable(
                false, fixTimestampMs, nowMs + 4_000L, maxAgeMs
            )
        )
        assertFalse(
            NativeLocationAvailabilityPolicy.providerIsUsable(
                false, fixTimestampMs, nowMs + maxAgeMs + 1L, maxAgeMs
            )
        )
    }

    @Test
    fun `freshness boundary remains fail closed after ten seconds`() {
        assertTrue(NativeLocationAvailabilityPolicy.isFreshFix(nowMs - maxAgeMs, nowMs, maxAgeMs))
        assertFalse(NativeLocationAvailabilityPolicy.isFreshFix(nowMs - maxAgeMs - 1L, nowMs, maxAgeMs))
    }

    @Test
    fun `only bounded future clock skew is accepted`() {
        assertTrue(NativeLocationAvailabilityPolicy.isFreshFix(nowMs + 60_000L, nowMs, maxAgeMs))
        assertFalse(NativeLocationAvailabilityPolicy.isFreshFix(nowMs + 60_001L, nowMs, maxAgeMs))
    }

    @Test
    fun `reported available does not invent a fresh fix`() {
        assertTrue(
            NativeLocationAvailabilityPolicy.providerIsUsable(
                reportedAvailable = true,
                latestFixTimestampMs = null,
                nowMs = nowMs,
                maxAgeMs = maxAgeMs
            )
        )
        assertFalse(NativeLocationAvailabilityPolicy.isFreshFix(null, nowMs, maxAgeMs))
    }
}
