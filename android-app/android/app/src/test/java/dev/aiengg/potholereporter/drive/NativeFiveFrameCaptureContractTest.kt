package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeFiveFrameCaptureContractTest {
    private fun selectedFrameIndexes(fps: Int, firstSourceFrame: Int): List<Int> {
        val framePeriodNs = 1_000_000_000L / fps
        var lastSampleTimestampNs = 0L
        var deliveredSinceSample = 0
        val selected = mutableListOf<Int>()

        for (sourceFrame in firstSourceFrame..(firstSourceFrame + 30)) {
            val timestampNs = 1_000_000_000L + sourceFrame * framePeriodNs
            val eligibleCount = if (lastSampleTimestampNs == 0L) 0 else {
                (deliveredSinceSample + 1)
                    .coerceAtMost(NativeRollingBurstWindow.SOURCE_FRAME_STRIDE)
            }
            val retain = NativeAnalyzerSamplingPolicy.shouldConvert(
                enabled = true,
                requested = true,
                destroyed = false,
                cameraReady = true,
                graphCurrent = true,
                windowFull = false,
                sourceTimestampNs = timestampNs,
                lastSampleTimestampNs = lastSampleTimestampNs,
                deliveredFramesSinceLastSample = eligibleCount,
                sourceFrameStride = NativeRollingBurstWindow.SOURCE_FRAME_STRIDE,
                minimumGapNs = NativeRollingBurstWindow.SAMPLE_SPACING_NS,
                maximumGapNs = NativeRollingBurstWindow.MAX_SAMPLE_GAP_NS
            )
            if (retain) {
                selected += sourceFrame
                lastSampleTimestampNs = timestampNs
                deliveredSinceSample = 0
            } else {
                deliveredSinceSample = eligibleCount
            }
        }
        return selected
    }

    @Test
    fun `all five source phases retain at least every fifth frame at 30 fps`() {
        for (phase in 0 until NativeRollingBurstWindow.SOURCE_FRAME_STRIDE) {
            val selected = selectedFrameIndexes(fps = 30, firstSourceFrame = phase)
            assertTrue(selected.size >= 6)
            assertEquals(phase, selected.first())
            assertTrue(selected.zipWithNext().all { (left, right) -> right - left <= 5 })
        }
    }

    @Test
    fun `sparse camera fallback is never less frequent than every fifth callback`() {
        for (fps in listOf(15, 24, 30)) {
            val selected = selectedFrameIndexes(fps = fps, firstSourceFrame = 0)
            assertTrue(selected.zipWithNext().all { (left, right) -> right - left <= 5 })
        }
    }

    @Test
    fun `dropped callbacks trigger the bounded timestamp fallback`() {
        val deliveredSourceIndexes = listOf(0, 3, 5, 8, 10, 13, 15)
        val framePeriodNs = 1_000_000_000L / 30L
        var lastSampleTimestampNs = 0L
        var deliveredSinceSample = 0
        val selected = mutableListOf<Int>()
        for (sourceIndex in deliveredSourceIndexes) {
            val timestampNs = 1_000_000_000L + sourceIndex * framePeriodNs
            val eligibleCount = if (lastSampleTimestampNs == 0L) 0 else deliveredSinceSample + 1
            val retain = NativeAnalyzerSamplingPolicy.shouldConvert(
                true, true, false, true, true, false,
                timestampNs, lastSampleTimestampNs, eligibleCount,
                NativeRollingBurstWindow.SOURCE_FRAME_STRIDE,
                NativeRollingBurstWindow.SAMPLE_SPACING_NS,
                NativeRollingBurstWindow.MAX_SAMPLE_GAP_NS
            )
            if (retain) {
                selected += sourceIndex
                lastSampleTimestampNs = timestampNs
                deliveredSinceSample = 0
            } else deliveredSinceSample = eligibleCount
        }
        assertEquals(listOf(0, 5, 10, 15), selected)
    }

    @Test
    fun `three retained 30 fps views form a valid production window`() {
        val framePeriodMs = 1_000.0 / 30.0
        val times = listOf(0, 5, 10).map { 1_000L + (it * framePeriodMs).toLong() }
        val samples = times.map { elapsedMs ->
            NativeRollingBurstWindow.Sample(
                capturedAtElapsedMs = elapsedMs,
                sourceTimestampNs = elapsedMs * 1_000_000L,
                generation = 4L
            )
        }
        assertNotNull(
            NativeRollingBurstWindow.selectSourceIndexes(
                samples,
                nowElapsedMs = times.last() + 50L,
                expectedGeneration = 4L
            )
        )
    }

    @Test
    fun `every nonstationary cadence is bounded to 650 milliseconds`() {
        for (speedMps in listOf(0.26f, 0.5f, 1f, 5f, 10f, 30f)) {
            assertTrue(NativeCaptureCadencePolicy.intervalMs(speedMps, true) <= 650L)
        }
        assertEquals(500L, NativeCaptureCadencePolicy.intervalMs(null, false))
    }
}
