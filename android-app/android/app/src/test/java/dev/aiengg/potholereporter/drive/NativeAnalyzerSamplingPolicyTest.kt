package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeAnalyzerSamplingPolicyTest {
    private fun shouldConvert(
        enabled: Boolean = true,
        requested: Boolean = true,
        destroyed: Boolean = false,
        cameraReady: Boolean = true,
        graphCurrent: Boolean = true,
        windowFull: Boolean = false,
        sourceTimestampNs: Long = 1_000_000_000L,
        lastSampleTimestampNs: Long = 833_333_335L,
        deliveredFramesSinceLastSample: Int = 5,
        sourceFrameStride: Int = 5,
        minimumGapNs: Long = 140_000_000L,
        maximumGapNs: Long = 166_000_000L
    ) = NativeAnalyzerSamplingPolicy.shouldConvert(
        enabled,
        requested,
        destroyed,
        cameraReady,
        graphCurrent,
        windowFull,
        sourceTimestampNs,
        lastSampleTimestampNs,
        deliveredFramesSinceLastSample,
        sourceFrameStride,
        minimumGapNs,
        maximumGapNs
    )

    @Test
    fun `disabled sampling rejects conversion while camera graph can remain live`() {
        assertFalse(shouldConvert(enabled = false))
        assertTrue(shouldConvert(enabled = true))
    }

    @Test
    fun `camera lifecycle and exact graph generation remain mandatory`() {
        assertFalse(shouldConvert(requested = false))
        assertFalse(shouldConvert(destroyed = true))
        assertFalse(shouldConvert(cameraReady = false))
        assertFalse(shouldConvert(graphCurrent = false))
        assertFalse(shouldConvert(windowFull = true))
    }

    @Test
    fun `first frame is immediate then every fifth delivered frame is retained`() {
        assertTrue(shouldConvert(lastSampleTimestampNs = 0L))
        assertFalse(shouldConvert(
            lastSampleTimestampNs = 850_000_000L,
            deliveredFramesSinceLastSample = 4
        ))
        assertTrue(shouldConvert(
            lastSampleTimestampNs = 850_000_000L,
            deliveredFramesSinceLastSample = 5
        ))
        assertFalse(shouldConvert(sourceTimestampNs = 800_000_000L))
    }

    @Test
    fun `maximum time fallback handles sparse or dropped camera callbacks`() {
        assertFalse(shouldConvert(
            sourceTimestampNs = 1_165_999_999L,
            lastSampleTimestampNs = 1_000_000_000L,
            deliveredFramesSinceLastSample = 2
        ))
        assertTrue(shouldConvert(
            sourceTimestampNs = 1_166_000_000L,
            lastSampleTimestampNs = 1_000_000_000L,
            deliveredFramesSinceLastSample = 2
        ))
    }

    @Test
    fun `minimum temporal separation applies even after five callbacks`() {
        assertFalse(shouldConvert(
            sourceTimestampNs = 1_139_999_999L,
            lastSampleTimestampNs = 1_000_000_000L,
            deliveredFramesSinceLastSample = 5
        ))
        assertTrue(shouldConvert(
            sourceTimestampNs = 1_140_000_000L,
            lastSampleTimestampNs = 1_000_000_000L,
            deliveredFramesSinceLastSample = 5
        ))
    }
}
