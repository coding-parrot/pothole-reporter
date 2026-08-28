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

    @Test
    fun `capture accepts only a fresh road-level fix`() {
        val precise = GpsFix(19.1, 72.9, 8f, 6f, 90f, nowMs - 500L)
        assertTrue(
            NativeCaptureFixQualityPolicy.isUsable(precise, nowMs, maxAgeMs, 30f)
        )
        assertFalse(
            NativeCaptureFixQualityPolicy.isUsable(
                precise.copy(accuracy = 30.1f), nowMs, maxAgeMs, 30f
            )
        )
        assertFalse(
            NativeCaptureFixQualityPolicy.isUsable(
                precise.copy(accuracy = null), nowMs, maxAgeMs, 30f
            )
        )
        assertFalse(
            NativeCaptureFixQualityPolicy.isUsable(
                precise.copy(timestampMs = nowMs - maxAgeMs - 1L),
                nowMs,
                maxAgeMs,
                30f
            )
        )
    }

    @Test
    fun `capture rejects malformed coordinates or accuracy`() {
        val base = GpsFix(19.1, 72.9, 8f, 6f, 90f, nowMs)
        assertFalse(
            NativeCaptureFixQualityPolicy.isUsable(
                base.copy(lat = Double.NaN), nowMs, maxAgeMs, 30f
            )
        )
        assertFalse(
            NativeCaptureFixQualityPolicy.isUsable(
                base.copy(lng = 181.0), nowMs, maxAgeMs, 30f
            )
        )
        assertFalse(
            NativeCaptureFixQualityPolicy.isUsable(
                base.copy(accuracy = -1f), nowMs, maxAgeMs, 30f
            )
        )
    }
}
