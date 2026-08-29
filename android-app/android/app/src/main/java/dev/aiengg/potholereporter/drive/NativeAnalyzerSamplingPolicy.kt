package dev.aiengg.potholereporter.drive

/** Android-free admission gate for expensive ImageProxy-to-Bitmap conversion. */
internal object NativeAnalyzerSamplingPolicy {
    fun shouldConvert(
        enabled: Boolean,
        requested: Boolean,
        destroyed: Boolean,
        cameraReady: Boolean,
        graphCurrent: Boolean,
        windowFull: Boolean,
        sourceTimestampNs: Long,
        lastSampleTimestampNs: Long,
        deliveredFramesSinceLastSample: Int,
        sourceFrameStride: Int,
        minimumGapNs: Long,
        maximumGapNs: Long
    ): Boolean {
        if (!enabled || !requested || destroyed || !cameraReady || !graphCurrent || windowFull) {
            return false
        }
        if (sourceTimestampNs <= 0L || lastSampleTimestampNs < 0L ||
            deliveredFramesSinceLastSample < 0 || sourceFrameStride <= 0 ||
            minimumGapNs <= 0L || maximumGapNs < minimumGapNs
        ) {
            return false
        }
        if (lastSampleTimestampNs == 0L) return true
        val elapsedNs = sourceTimestampNs - lastSampleTimestampNs
        if (elapsedNs < minimumGapNs) return false
        return deliveredFramesSinceLastSample >= sourceFrameStride || elapsedNs >= maximumGapNs
    }
}
