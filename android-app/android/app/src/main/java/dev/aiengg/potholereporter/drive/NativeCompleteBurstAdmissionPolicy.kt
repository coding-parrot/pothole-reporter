package dev.aiengg.potholereporter.drive

/** Pure admission rule for a complete three-view camera window. */
internal object NativeCompleteBurstAdmissionPolicy {
    fun shouldAttempt(
        hasCompleteWindow: Boolean,
        speedMps: Float?,
        accurateFix: Boolean,
        stationaryCadenceDue: Boolean
    ): Boolean {
        if (!hasCompleteWindow) return false
        return !NativeCaptureCadencePolicy.isGenuinelyStationary(speedMps, accurateFix) ||
            stationaryCadenceDue
    }
}
