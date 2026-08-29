package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Test

class NativeCaptureCadencePolicyTest {
    @Test
    fun `only a genuinely stationary accurate fix uses the parked interval`() {
        assertEquals(8_000L, NativeCaptureCadencePolicy.intervalMs(0f, accurateFix = true))
        assertEquals(8_000L, NativeCaptureCadencePolicy.intervalMs(0.05f, accurateFix = true))
        assertEquals(650L, NativeCaptureCadencePolicy.intervalMs(0.1f, accurateFix = true))
        assertEquals(650L, NativeCaptureCadencePolicy.intervalMs(0.25f, accurateFix = true))
        assertEquals(400L, NativeCaptureCadencePolicy.intervalMs(20f, accurateFix = true))
    }

    @Test
    fun `crawling traffic is never mistaken for a parked car`() {
        assertEquals(650L, NativeCaptureCadencePolicy.intervalMs(0.5f, accurateFix = true))
        assertEquals(650L, NativeCaptureCadencePolicy.intervalMs(1.0f, accurateFix = true))
    }

    @Test
    fun `missing malformed or coarse speed uses the responsive fallback`() {
        assertEquals(500L, NativeCaptureCadencePolicy.intervalMs(null, accurateFix = true))
        assertEquals(500L, NativeCaptureCadencePolicy.intervalMs(Float.NaN, accurateFix = true))
        assertEquals(500L, NativeCaptureCadencePolicy.intervalMs(-1f, accurateFix = true))
        assertEquals(500L, NativeCaptureCadencePolicy.intervalMs(0f, accurateFix = false))
    }

    @Test
    fun `parked GPS jitter cannot bypass the eight second duplicate-saving interval`() {
        assertEquals(
            false,
            NativeCaptureCadencePolicy.isDue(true, 7_999L, 20.0, 60.0, 0f, true)
        )
        assertEquals(
            true,
            NativeCaptureCadencePolicy.isDue(true, 100L, 20.0, 8.0, 0f, true)
        )
        assertEquals(
            true,
            NativeCaptureCadencePolicy.isDue(true, 8_000L, 0.0, 0.0, 0f, true)
        )
        assertEquals(
            true,
            NativeCaptureCadencePolicy.isDue(false, 0L, 0.0, 0.0, 0f, true)
        )
    }
}
