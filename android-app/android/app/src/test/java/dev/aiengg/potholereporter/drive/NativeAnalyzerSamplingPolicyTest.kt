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
        sourceTimestampNs: Long = 1_000_000_000L,
        lastSampleTimestampNs: Long = 750_000_000L,
        minimumSpacingNs: Long = 250_000_000L
    ) = NativeAnalyzerSamplingPolicy.shouldConvert(
        enabled,
        requested,
        destroyed,
        cameraReady,
        graphCurrent,
        sourceTimestampNs,
        lastSampleTimestampNs,
        minimumSpacingNs
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
    }

    @Test
    fun `first frame is immediate and later frames obey bounded spacing`() {
        assertTrue(shouldConvert(lastSampleTimestampNs = 0L))
        assertFalse(shouldConvert(sourceTimestampNs = 999_999_999L))
        assertTrue(shouldConvert(sourceTimestampNs = 1_000_000_000L))
        assertFalse(shouldConvert(sourceTimestampNs = 700_000_000L))
    }
}
