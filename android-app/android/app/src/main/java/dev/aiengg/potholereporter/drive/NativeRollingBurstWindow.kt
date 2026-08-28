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
    const val SAMPLE_SPACING_MS = 250L
    const val SAMPLE_SPACING_NS = SAMPLE_SPACING_MS * 1_000_000L
    const val MAX_OLDEST_AGE_MS = 1_250L
    const val MIN_WINDOW_SPAN_MS = 400L

    data class Sample(
        val capturedAtElapsedMs: Long,
        val sourceTimestampNs: Long,
        val generation: Long
    )

    /** Selects the complete chronological three-view window from one camera generation. */
    fun selectSourceIndexes(
        samples: List<Sample>,
        nowElapsedMs: Long,
        expectedGeneration: Long
    ): List<Int>? {
        if (samples.size != CAPACITY || nowElapsedMs <= 0L) return null
        if (samples.any {
                it.generation != expectedGeneration ||
                    it.capturedAtElapsedMs <= 0L ||
                    it.sourceTimestampNs <= 0L ||
                    it.capturedAtElapsedMs > nowElapsedMs
            }
        ) return null
        for (index in 1 until samples.size) {
            if (samples[index].capturedAtElapsedMs <= samples[index - 1].capturedAtElapsedMs ||
                samples[index].sourceTimestampNs <= samples[index - 1].sourceTimestampNs
            ) return null
        }
        val oldestAgeMs = nowElapsedMs - samples.first().capturedAtElapsedMs
        val spanMs = samples.last().capturedAtElapsedMs - samples.first().capturedAtElapsedMs
        if (oldestAgeMs > MAX_OLDEST_AGE_MS || spanMs < MIN_WINDOW_SPAN_MS) return null
        return samples.indices.toList()
    }
}
