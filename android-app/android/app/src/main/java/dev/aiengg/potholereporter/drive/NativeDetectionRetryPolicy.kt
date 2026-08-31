package dev.aiengg.potholereporter.drive

/** Conservative bounded voting for visually ambiguous temporary traffic surfaces. */
internal object NativeDetectionRetryPolicy {
    const val MAX_ATTEMPTS = 3

    fun isVoteEligible(assessment: AssessmentResult): Boolean =
            !assessment.looksLikeSpeedBreaker &&
            assessment.imageQuality == "usable" &&
            assessment.surfaceType == "temporary_drivable_surface" &&
            assessment.onDrivableSurface &&
            assessment.temporalConsistency == "consistent"

    fun shouldRetry(attempts: List<AssessmentResult>): Boolean {
        if (attempts.isEmpty() || attempts.size >= MAX_ATTEMPTS ||
            attempts.any { !isVoteEligible(it) }
        ) return false
        val accepts = attempts.count { it.decision == "accept" }
        val rejects = attempts.size - accepts
        return accepts < 2 && rejects < 2
    }

    fun acceptedByMajority(attempts: List<AssessmentResult>): Boolean =
        attempts.size >= 2 && attempts.count { it.decision == "accept" } >= 2
}

/**
 * Eligible temporary-surface results need two matching YES votes out of at most three complete
 * requests. A first complete NO is allowed a bounded reconsideration because wet/rubble-filled
 * cavities are visually stochastic, while two NOs, any safety-gate contradiction, or a failed
 * request remains fail-closed. Ordinary paved-road and early-stream NO decisions stay single-shot.
 */
internal suspend fun runBoundedDetectionAttempts(
    detect: suspend () -> AssessmentResult
): AssessmentResult {
    val attempts = mutableListOf(detect())
    if (!NativeDetectionRetryPolicy.isVoteEligible(attempts.first())) return attempts.first()

    while (NativeDetectionRetryPolicy.shouldRetry(attempts)) {
        val next = try {
            detect()
        } catch (_: NativeInferenceException) {
            return attempts.lastOrNull { it.decision == "reject" }
                ?: attempts.first().asUnconfirmedTemporarySurface()
        }
        if (!NativeDetectionRetryPolicy.isVoteEligible(next)) {
            return if (next.decision == "reject") next
            else next.asUnconfirmedTemporarySurface()
        }
        attempts += next
    }
    if (NativeDetectionRetryPolicy.acceptedByMajority(attempts)) {
        return attempts.last { it.decision == "accept" }
    }
    return attempts.lastOrNull { it.decision == "reject" }
        ?: attempts.last().asUnconfirmedTemporarySurface()
}

private fun AssessmentResult.asUnconfirmedTemporarySurface(): AssessmentResult = copy(
    isPothole = false,
    reportable = false,
    assessment = "absent",
    damageType = "none",
    defectType = "not_pothole",
    measurementProvenance = "not_applicable",
    measurementConfidence = "not_applicable",
    size = null,
    description = "Temporary-surface pothole was not independently confirmed.",
    decision = "reject"
)
