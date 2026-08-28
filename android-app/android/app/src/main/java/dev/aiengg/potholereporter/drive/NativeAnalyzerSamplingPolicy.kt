package dev.aiengg.potholereporter.drive

/** Android-free admission gate for expensive ImageProxy-to-Bitmap conversion. */
internal object NativeAnalyzerSamplingPolicy {
    fun shouldConvert(
        enabled: Boolean,
        requested: Boolean,
        destroyed: Boolean,
        cameraReady: Boolean,
        graphCurrent: Boolean,
        sourceTimestampNs: Long,
        lastSampleTimestampNs: Long,
        minimumSpacingNs: Long
    ): Boolean {
        if (!enabled || !requested || destroyed || !cameraReady || !graphCurrent) return false
        if (sourceTimestampNs <= 0L || lastSampleTimestampNs < 0L || minimumSpacingNs <= 0L) {
            return false
        }
        return lastSampleTimestampNs == 0L ||
            sourceTimestampNs - lastSampleTimestampNs >= minimumSpacingNs
    }
}
