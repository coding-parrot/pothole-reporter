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
        assertTrue(NativeLocationAvailabilityPolicy.isFreshFix(nowMs + 2_500L, nowMs, maxAgeMs))
        assertFalse(NativeLocationAvailabilityPolicy.isFreshFix(nowMs + 2_501L, nowMs, maxAgeMs))
        assertFalse(NativeLocationAvailabilityPolicy.isFreshFix(nowMs + 60_000L, nowMs, maxAgeMs))
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
        val captureMaxAgeMs = NativeBurstAccessPolicy.MAX_FIX_PRIMARY_DELTA_MS
        assertTrue(
            NativeCaptureFixQualityPolicy.isUsable(precise, nowMs, captureMaxAgeMs, 30f)
        )
        assertFalse(
            NativeCaptureFixQualityPolicy.isUsable(
                precise.copy(accuracy = 30.1f), nowMs, captureMaxAgeMs, 30f
            )
        )
        assertFalse(
            NativeCaptureFixQualityPolicy.isUsable(
                precise.copy(accuracy = null), nowMs, captureMaxAgeMs, 30f
            )
        )
        assertFalse(
            NativeCaptureFixQualityPolicy.isUsable(
                precise.copy(elapsedRealtimeMs = nowMs - captureMaxAgeMs - 1L),
                nowMs,
                captureMaxAgeMs,
                30f
            )
        )
    }

    @Test
    fun `capture freshness closes before provider grace expires`() {
        val captureMaxAgeMs = NativeBurstAccessPolicy.MAX_FIX_PRIMARY_DELTA_MS
        val providerStillRecent = GpsFix(
            19.1, 72.9, 8f, 6f, 90f,
            nowMs - captureMaxAgeMs - 1L
        )

        assertTrue(
            NativeLocationAvailabilityPolicy.providerIsUsable(
                reportedAvailable = false,
                latestFixTimestampMs = providerStillRecent.elapsedRealtimeMs,
                nowMs = nowMs,
                maxAgeMs = maxAgeMs
            )
        )
        assertFalse(
            NativeCaptureFixQualityPolicy.isUsable(
                providerStillRecent,
                nowMs,
                captureMaxAgeMs,
                30f
            )
        )
    }

    @Test
    fun `wall clock changes do not alter monotonic capture freshness`() {
        val captureMaxAgeMs = NativeBurstAccessPolicy.MAX_FIX_PRIMARY_DELTA_MS
        val fix = GpsFix(
            19.1, 72.9, 8f, 6f, 90f,
            timestampMs = 1_700_000_000_000L,
            elapsedRealtimeMs = nowMs - 500L
        )
        assertTrue(
            NativeCaptureFixQualityPolicy.isUsable(fix, nowMs, captureMaxAgeMs, 30f)
        )
        assertTrue(
            NativeCaptureFixQualityPolicy.isUsable(
                fix.copy(timestampMs = 1_900_000_000_000L),
                nowMs,
                captureMaxAgeMs,
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
