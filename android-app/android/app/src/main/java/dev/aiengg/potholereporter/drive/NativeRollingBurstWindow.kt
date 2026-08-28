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
    const val CAPACITY = 5
    const val OUTPUT_COUNT = 3
    const val SAMPLE_SPACING_NS = 180_000_000L
    const val MAX_OLDEST_AGE_MS = 1_500L
    const val MIN_WINDOW_SPAN_MS = 500L

    data class Sample(
        val capturedAtMs: Long,
        val sourceTimestampNs: Long,
        val generation: Long
    )

    /** Selects oldest/middle/newest from one fresh five-sample camera generation. */
    fun selectSourceIndexes(
        samples: List<Sample>,
        nowMs: Long,
        expectedGeneration: Long
    ): List<Int>? {
        if (samples.size != CAPACITY || nowMs <= 0L) return null
        if (samples.any {
                it.generation != expectedGeneration ||
                    it.capturedAtMs <= 0L ||
                    it.sourceTimestampNs <= 0L ||
                    it.capturedAtMs > nowMs
            }
        ) return null
        for (index in 1 until samples.size) {
            if (samples[index].capturedAtMs <= samples[index - 1].capturedAtMs ||
                samples[index].sourceTimestampNs <= samples[index - 1].sourceTimestampNs
            ) return null
        }
        val oldestAgeMs = nowMs - samples.first().capturedAtMs
        val spanMs = samples.last().capturedAtMs - samples.first().capturedAtMs
        if (oldestAgeMs > MAX_OLDEST_AGE_MS || spanMs < MIN_WINDOW_SPAN_MS) return null
        return listOf(0, samples.size / 2, samples.lastIndex)
    }
}
