package dev.aiengg.potholereporter.drive

import java.io.File
import java.io.IOException
import java.util.regex.Pattern

class NativeInferenceException(
    message: String,
    val fatal: Boolean = false,
    val suspendInference: Boolean = false,
    val retryAfterMs: Long? = null,
    cause: Throwable? = null
) : IOException(message, cause)

internal enum class NativeDetectionCompletionMode {
    COMPLETE,
    EARLY_MODEL_NO,
    EARLY_SPEED_BREAKER,
    EARLY_GATE_VETO
}

internal data class NativeDetectionStreamVerdict(
    val assessment: AssessmentResult,
    val rawVerdict: DetectionModelVerdict?,
    val completionMode: NativeDetectionCompletionMode,
    val observedFields: Set<String>,
    val rejectionReasons: List<DetectionRejectionReason>
)

internal data class NativeEarlyReject(
    val assessment: AssessmentResult,
    val completionMode: NativeDetectionCompletionMode,
    val observedFields: Set<String>,
    val rejectionReasons: List<DetectionRejectionReason>
)

/**
 * Reads only fields that have actually arrived in the streamed JSON. Synthetic fallback values
 * remain confined to the legacy [AssessmentResult]; [observedFields] records the model truth.
 */
internal object NativePartialVerdictScanner {
    private val isPotholePattern = Pattern.compile("\"is_pothole\"\\s*:\\s*(true|false)")
    private val speedBreakerPattern = Pattern.compile("\"looks_like_speed_breaker\"\\s*:\\s*(true|false)")
    private val qualityPattern = Pattern.compile("\"image_quality\"\\s*:\\s*\"(usable|unusable)\"")
    private val surfacePattern = Pattern.compile(
        "\"surface_type\"\\s*:\\s*\"(bituminous_asphalt|cement_concrete|mastic_asphalt|" +
            "paver_blocks|temporary_drivable_surface|unpaved_or_nonroad|unknown)\""
    )
    private val roadPattern = Pattern.compile("\"on_drivable_surface\"\\s*:\\s*(true|false)")
    private val cavityPattern = Pattern.compile("\"has_localized_cavity\"\\s*:\\s*(true|false)")
    private val edgePattern = Pattern.compile("\"has_broken_edge_or_rim\"\\s*:\\s*(true|false)")
    private val depthPattern = Pattern.compile("\"has_depth_or_surface_loss\"\\s*:\\s*(true|false)")
    private val temporalPattern = Pattern.compile(
        "\"temporal_consistency\"\\s*:\\s*\"(consistent|single_view|inconsistent|not_applicable)\""
    )
    private val sizePattern = Pattern.compile("\"size\"\\s*:\\s*(?:\"(small|medium|large)\"|null)")

    private data class Partial(
        val isPothole: Boolean?,
        val looksLikeSpeedBreaker: Boolean?,
        val imageQuality: String?,
        val surfaceType: String?,
        val onDrivableSurface: Boolean?,
        val hasLocalizedCavity: Boolean?,
        val hasBrokenEdgeOrRim: Boolean?,
        val hasDepthOrSurfaceLoss: Boolean?,
        val temporalConsistency: String?,
        val sizeSeen: Boolean,
        val size: String?,
        val observedFields: Set<String>
    )

    fun earlyReject(text: String): NativeEarlyReject? {
        val partial = scan(text)
        val modelIsPothole = partial.isPothole ?: return null
        val mode = when {
            !modelIsPothole -> NativeDetectionCompletionMode.EARLY_MODEL_NO
            partial.looksLikeSpeedBreaker == true -> NativeDetectionCompletionMode.EARLY_SPEED_BREAKER
            partial.isCompleteForGateDecision() && partial.toPolicyVerdict()
                ?.let(NativePotholeDecisionPolicy::evaluate)?.accepted == false ->
                NativeDetectionCompletionMode.EARLY_GATE_VETO
            else -> return null
        }
        val reasons = when (mode) {
            NativeDetectionCompletionMode.EARLY_MODEL_NO -> listOf(DetectionRejectionReason.MODEL_NO)
            NativeDetectionCompletionMode.EARLY_SPEED_BREAKER -> listOf(DetectionRejectionReason.SPEED_BREAKER)
            NativeDetectionCompletionMode.EARLY_GATE_VETO -> partial.toPolicyVerdict()
                ?.let(NativePotholeDecisionPolicy::evaluate)?.rejectionReasons.orEmpty()
            NativeDetectionCompletionMode.COMPLETE -> emptyList()
        }
        return NativeEarlyReject(
            assessment = partial.toLegacyRejectedAssessment(),
            completionMode = mode,
            observedFields = partial.observedFields,
            rejectionReasons = reasons
        )
    }

    private fun scan(text: String): Partial {
        val observed = linkedSetOf<String>()
        fun boolean(pattern: Pattern, field: String): Boolean? {
            val matcher = pattern.matcher(text)
            if (!matcher.find()) return null
            observed += field
            return matcher.group(1) == "true"
        }
        fun string(pattern: Pattern, field: String): String? {
            val matcher = pattern.matcher(text)
            if (!matcher.find()) return null
            observed += field
            return matcher.group(1)
        }

        val sizeMatcher = sizePattern.matcher(text)
        val sizeSeen = sizeMatcher.find()
        if (sizeSeen) observed += "size"
        return Partial(
            isPothole = boolean(isPotholePattern, "is_pothole"),
            looksLikeSpeedBreaker = boolean(speedBreakerPattern, "looks_like_speed_breaker"),
            imageQuality = string(qualityPattern, "image_quality"),
            surfaceType = string(surfacePattern, "surface_type"),
            onDrivableSurface = boolean(roadPattern, "on_drivable_surface"),
            hasLocalizedCavity = boolean(cavityPattern, "has_localized_cavity"),
            hasBrokenEdgeOrRim = boolean(edgePattern, "has_broken_edge_or_rim"),
            hasDepthOrSurfaceLoss = boolean(depthPattern, "has_depth_or_surface_loss"),
            temporalConsistency = string(temporalPattern, "temporal_consistency"),
            sizeSeen = sizeSeen,
            size = if (sizeSeen) sizeMatcher.group(1) else null,
            observedFields = observed
        )
    }

    private fun Partial.isCompleteForGateDecision(): Boolean =
        isPothole != null && looksLikeSpeedBreaker != null && imageQuality != null &&
            surfaceType != null && onDrivableSurface != null && hasLocalizedCavity != null &&
            hasBrokenEdgeOrRim != null && hasDepthOrSurfaceLoss != null &&
            temporalConsistency != null && sizeSeen

    private fun Partial.toPolicyVerdict(): DetectionModelVerdict? {
        if (!isCompleteForGateDecision()) return null
        return DetectionModelVerdict(
            isPothole = isPothole!!,
            looksLikeSpeedBreaker = looksLikeSpeedBreaker!!,
            imageQuality = imageQuality!!,
            surfaceType = surfaceType!!,
            onDrivableSurface = onDrivableSurface!!,
            hasLocalizedCavity = hasLocalizedCavity!!,
            hasBrokenEdgeOrRim = hasBrokenEdgeOrRim!!,
            hasDepthOrSurfaceLoss = hasDepthOrSurfaceLoss!!,
            temporalConsistency = temporalConsistency!!,
            size = size,
            description = ""
        )
    }

    private fun Partial.toLegacyRejectedAssessment(): AssessmentResult {
        val modelSaidPothole = isPothole == true
        return AssessmentResult(
            isPothole = false,
            looksLikeSpeedBreaker = looksLikeSpeedBreaker ?: true,
            reportable = false,
            assessment = "absent",
            imageQuality = imageQuality ?: "unusable",
            damageType = "none",
            surfaceType = surfaceType ?: "unknown",
            defectType = "not_pothole",
            measurementProvenance = "not_applicable",
            measurementConfidence = "not_applicable",
            onDrivableSurface = onDrivableSurface ?: false,
            hasLocalizedCavity = hasLocalizedCavity ?: false,
            hasBrokenEdgeOrRim = hasBrokenEdgeOrRim ?: false,
            hasDepthOrSurfaceLoss = hasDepthOrSurfaceLoss ?: false,
            temporalConsistency = temporalConsistency ?: "not_applicable",
            size = null,
            description = if (modelSaidPothole) {
                "Pothole evidence failed a required physical gate."
            } else {
                "No pothole detected."
            },
            decision = "reject"
        )
    }
}

/** Turns terminal/early stream state into a durable verdict without hiding truncation. */
internal object NativeDetectionStreamCompletionPolicy {
    fun requireVerdict(
        completeVerdict: AssessmentResult?,
        intentionalEarlyReject: AssessmentResult?,
        transportCompleted: Boolean
    ): AssessmentResult {
        if (intentionalEarlyReject != null) return intentionalEarlyReject
        NativeStreamCompletionPolicy.requireCompleted(
            transportCompleted,
            "OpenAI detection stream was interrupted"
        )
        return completeVerdict ?: throw NativeInferenceException(
            "OpenAI returned an invalid completed detection assessment",
            suspendInference = true
        )
    }
}

internal object NativeStreamCompletionPolicy {
    fun requireCompleted(transportCompleted: Boolean, incompleteMessage: String) {
        if (!transportCompleted) throw NativeInferenceException(incompleteMessage)
    }
}

/** A small bounded UTF-8 accumulator for untrusted streamed output text. */
internal class NativeSseTextAccumulator(
    private val maxUtf8Bytes: Int = MAX_UTF8_BYTES
) {
    private val text = StringBuilder(minOf(maxUtf8Bytes.coerceAtLeast(1), 4 * 1024))
    private var utf8Bytes = 0

    init {
        require(maxUtf8Bytes > 0) { "SSE text limit must be positive" }
    }

    fun append(delta: String): Boolean {
        val deltaBytes = utf8LengthAtMost(delta, maxUtf8Bytes - utf8Bytes) ?: return false
        text.append(delta)
        utf8Bytes += deltaBytes
        return true
    }

    fun snapshot(): String = text.toString()

    private fun utf8LengthAtMost(value: String, remaining: Int): Int? {
        var total = 0
        var index = 0
        while (index < value.length) {
            val char = value[index]
            val bytes = when {
                char.code < 0x80 -> 1
                char.code < 0x800 -> 2
                Character.isHighSurrogate(char) && index + 1 < value.length &&
                    Character.isLowSurrogate(value[index + 1]) -> {
                    index++
                    4
                }
                else -> 3
            }
            if (total > remaining - bytes) return null
            total += bytes
            index++
        }
        return total
    }

    companion object {
        const val MAX_UTF8_BYTES = 64 * 1024
    }
}

internal object NativeInferenceHttpFailurePolicy {
    private const val MAX_BACKOFF_MS = 60_000L
    private val TRANSIENT_HTTP_CODES = setOf(408, 409, 425, 429)

    fun isTransient(code: Int): Boolean =
        code == 0 || code in TRANSIENT_HTTP_CODES || code in 500..599

    fun shouldSuspendInference(code: Int): Boolean = !isTransient(code)

    fun retryDelayMs(code: Int, retryAfterHeader: String?, consecutiveFailure: Int): Long? {
        if (!isTransient(code)) return null
        val headerDelay = retryAfterHeader?.trim()?.toLongOrNull()
            ?.takeIf { it >= 0L }
            ?.let { seconds ->
                if (seconds > MAX_BACKOFF_MS / 1_000L) MAX_BACKOFF_MS else seconds * 1_000L
            }
        val base = when (code) {
            429 -> 5_000L
            0 -> 10_000L
            else -> 2_000L
        }
        val shift = consecutiveFailure.coerceIn(0, 5)
        val exponential = (base * (1L shl shift)).coerceAtMost(MAX_BACKOFF_MS)
        return maxOf(headerDelay ?: 0L, exponential)
    }
}

internal object NativeInferenceEvidenceOwnership {
    fun handOff(file: File, receiver: (String) -> Unit) {
        try {
            receiver(file.absolutePath)
        } catch (error: Throwable) {
            NativeReportEvidenceStorage.deleteVerified(file)
            throw error
        }
    }
}
