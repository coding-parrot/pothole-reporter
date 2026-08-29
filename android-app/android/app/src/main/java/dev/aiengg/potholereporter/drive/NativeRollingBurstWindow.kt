package dev.aiengg.potholereporter.drive

/**
 * Pure selection policy for one bounded CameraX source burst.
 *
 * Camera ownership stays in [NativeDriveCameraManager]; this object validates only the
 * timestamps/generation and returns the three chronological source indexes that become
 * one detection burst. Keeping this policy Android-free makes spacing, stale-frame and
 * privacy-generation behaviour deterministic in JVM tests.
 */
internal object NativeRollingBurstWindow {
    const val CAPACITY = 3
    const val OUTPUT_COUNT = 3
    // At a typical 30 fps, retaining every fifth delivered frame gives three genuinely
    // different road views.
    // A time fallback keeps sparse/irregular camera streams moving instead of waiting
    // forever for frame 5. The time bounds also admit common 60 fps camera streams.
    const val SOURCE_FRAME_STRIDE = 5
    const val MAX_SAMPLE_GAP_MS = 166L
    const val MAX_SAMPLE_GAP_NS = MAX_SAMPLE_GAP_MS * 1_000_000L
    const val SAMPLE_SPACING_MS = 140L
    const val SAMPLE_SPACING_NS = SAMPLE_SPACING_MS * 1_000_000L
    const val MAX_OLDEST_AGE_MS = 900L
    const val MIN_WINDOW_SPAN_MS = 280L

    enum class Disposition { WAIT, READY, DISCARD }

    data class Sample(
        val capturedAtElapsedMs: Long,
        val sourceTimestampNs: Long,
        val generation: Long
    )

    /**
     * Incomplete windows wait. A valid complete window is ready. A complete invalid or
     * stale window must be discarded so it cannot permanently block future camera input.
     */
    fun disposition(
        samples: List<Sample>,
        nowElapsedMs: Long,
        expectedGeneration: Long
    ): Disposition {
        if (samples.size < CAPACITY) return Disposition.WAIT
        if (samples.size > CAPACITY || nowElapsedMs <= 0L) return Disposition.DISCARD
        if (samples.any {
                it.generation != expectedGeneration ||
                    it.capturedAtElapsedMs <= 0L ||
                    it.sourceTimestampNs <= 0L ||
                    it.capturedAtElapsedMs > nowElapsedMs
            }
        ) return Disposition.DISCARD
        for (index in 1 until samples.size) {
            if (samples[index].capturedAtElapsedMs <= samples[index - 1].capturedAtElapsedMs ||
                samples[index].sourceTimestampNs - samples[index - 1].sourceTimestampNs <
                    SAMPLE_SPACING_NS
            ) return Disposition.DISCARD
        }
        val oldestAgeMs = nowElapsedMs - samples.first().capturedAtElapsedMs
        val spanMs = samples.last().capturedAtElapsedMs - samples.first().capturedAtElapsedMs
        if (oldestAgeMs > MAX_OLDEST_AGE_MS || spanMs < MIN_WINDOW_SPAN_MS) {
            return Disposition.DISCARD
        }
        return Disposition.READY
    }

    /** Selects the complete chronological three-view window from one camera generation. */
    fun selectSourceIndexes(
        samples: List<Sample>,
        nowElapsedMs: Long,
        expectedGeneration: Long
    ): List<Int>? = if (disposition(samples, nowElapsedMs, expectedGeneration) == Disposition.READY) {
        samples.indices.toList()
    } else {
        null
    }
}
