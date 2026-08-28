package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Test

class NativeCaptureCadencePolicyTest {
    @Test
    fun `only a genuinely stationary accurate fix uses the parked interval`() {
        assertEquals(8_000L, NativeCaptureCadencePolicy.intervalMs(0f, accurateFix = true))
        assertEquals(8_000L, NativeCaptureCadencePolicy.intervalMs(0.25f, accurateFix = true))
        assertEquals(1_500L, NativeCaptureCadencePolicy.intervalMs(0.26f, accurateFix = true))
        assertEquals(500L, NativeCaptureCadencePolicy.intervalMs(20f, accurateFix = true))
    }

    @Test
    fun `crawling traffic is never mistaken for a parked car`() {
        assertEquals(1_500L, NativeCaptureCadencePolicy.intervalMs(0.5f, accurateFix = true))
        assertEquals(1_500L, NativeCaptureCadencePolicy.intervalMs(1.0f, accurateFix = true))
    }

    @Test
    fun `missing malformed or coarse speed uses the responsive fallback`() {
        assertEquals(750L, NativeCaptureCadencePolicy.intervalMs(null, accurateFix = true))
        assertEquals(750L, NativeCaptureCadencePolicy.intervalMs(Float.NaN, accurateFix = true))
        assertEquals(750L, NativeCaptureCadencePolicy.intervalMs(-1f, accurateFix = true))
        assertEquals(750L, NativeCaptureCadencePolicy.intervalMs(0f, accurateFix = false))
    }
}
