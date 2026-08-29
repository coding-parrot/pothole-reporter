package dev.aiengg.potholereporter.drive

import org.json.JSONObject
import java.util.regex.Pattern

/** The model response before the app applies its strict pothole rules. */
internal data class DetectionModelVerdict(
    val isPothole: Boolean,
    val looksLikeSpeedBreaker: Boolean,
    val imageQuality: String,
    val surfaceType: String,
    val onDrivableSurface: Boolean,
    val hasLocalizedCavity: Boolean,
    val hasBrokenEdgeOrRim: Boolean,
    val hasDepthOrSurfaceLoss: Boolean,
    val temporalConsistency: String,
    val size: String?,
    val description: String
)

internal enum class DetectionRejectionReason {
    MODEL_NO,
    SPEED_BREAKER,
    UNSUPPORTED_SURFACE,
    IMAGE_UNUSABLE,
    OFF_DRIVABLE_SURFACE,
    NO_LOCALIZED_CAVITY,
    NO_BROKEN_EDGE_OR_RIM,
    NO_DEPTH_OR_SURFACE_LOSS,
    TEMPORAL_NOT_CONSISTENT,
    SIZE_MISSING_OR_INVALID
}

data class AssessmentResult(
    val isPothole: Boolean,
    val looksLikeSpeedBreaker: Boolean,
    val reportable: Boolean,
    val assessment: String,
    val imageQuality: String,
    val damageType: String,
    val surfaceType: String,
    val defectType: String,
    val measurementProvenance: String,
    val measurementConfidence: String,
    val onDrivableSurface: Boolean,
    val hasLocalizedCavity: Boolean,
    val hasBrokenEdgeOrRim: Boolean,
    val hasDepthOrSurfaceLoss: Boolean,
    val temporalConsistency: String,
    val size: String?,
    val description: String,
    val decision: String
)

/** Pure application policy: every returned item is an explicit reason to reject. */
internal fun DetectionModelVerdict.rejectionReasons(): List<DetectionRejectionReason> = buildList {
    if (!isPothole) add(DetectionRejectionReason.MODEL_NO)
    if (looksLikeSpeedBreaker) add(DetectionRejectionReason.SPEED_BREAKER)
    if (surfaceType !in REPORTABLE_SURFACES) {
        add(DetectionRejectionReason.UNSUPPORTED_SURFACE)
    }
    if (imageQuality != "usable") add(DetectionRejectionReason.IMAGE_UNUSABLE)
    if (!onDrivableSurface) add(DetectionRejectionReason.OFF_DRIVABLE_SURFACE)
    if (!hasLocalizedCavity) add(DetectionRejectionReason.NO_LOCALIZED_CAVITY)
    if (!hasBrokenEdgeOrRim) add(DetectionRejectionReason.NO_BROKEN_EDGE_OR_RIM)
    if (!hasDepthOrSurfaceLoss) add(DetectionRejectionReason.NO_DEPTH_OR_SURFACE_LOSS)
    if (temporalConsistency != "consistent") {
        add(DetectionRejectionReason.TEMPORAL_NOT_CONSISTENT)
    }
    if (this@rejectionReasons.size == null || this@rejectionReasons.size !in VALID_SIZES) {
        add(DetectionRejectionReason.SIZE_MISSING_OR_INVALID)
    }
}

internal fun DetectionModelVerdict.toAssessment(): AssessmentResult {
    val accepted = rejectionReasons().isEmpty()
    return AssessmentResult(
        isPothole = accepted,
        looksLikeSpeedBreaker = looksLikeSpeedBreaker,
        reportable = accepted,
        assessment = if (accepted) "clear" else "absent",
        imageQuality = imageQuality,
        damageType = if (accepted) "pothole_cavity" else "none",
        surfaceType = surfaceType,
        defectType = if (accepted) "pothole" else "not_pothole",
        measurementProvenance = if (accepted) "visual_estimate_no_scale" else "not_applicable",
        measurementConfidence = if (accepted) "low" else "not_applicable",
        onDrivableSurface = onDrivableSurface,
        hasLocalizedCavity = hasLocalizedCavity,
        hasBrokenEdgeOrRim = hasBrokenEdgeOrRim,
        hasDepthOrSurfaceLoss = hasDepthOrSurfaceLoss,
        temporalConsistency = temporalConsistency,
        size = if (accepted) size else null,
        description = description,
        decision = if (accepted) "accept" else "reject"
    )
}

/** Parses the real strict JSON response; tests use this same entry point. */
internal fun parseDetectionVerdict(text: String): DetectionModelVerdict? {
    return try {
        val json = JSONObject(text)
        val imageQuality = json.get("image_quality") as? String ?: return null
        val surfaceType = json.get("surface_type") as? String ?: return null
        val temporalConsistency = json.get("temporal_consistency") as? String ?: return null
        val rawSize = json.get("size")
        val size = if (rawSize === JSONObject.NULL) null else rawSize as? String ?: return null

        if (imageQuality !in VALID_IMAGE_QUALITIES || surfaceType !in VALID_SURFACES ||
            temporalConsistency !in VALID_TEMPORAL_VALUES || size != null && size !in VALID_SIZES
        ) return null

        DetectionModelVerdict(
            isPothole = json.get("is_pothole") as? Boolean ?: return null,
            looksLikeSpeedBreaker =
                json.get("looks_like_speed_breaker") as? Boolean ?: return null,
            imageQuality = imageQuality,
            surfaceType = surfaceType,
            onDrivableSurface = json.get("on_drivable_surface") as? Boolean ?: return null,
            hasLocalizedCavity = json.get("has_localized_cavity") as? Boolean ?: return null,
            hasBrokenEdgeOrRim = json.get("has_broken_edge_or_rim") as? Boolean ?: return null,
            hasDepthOrSurfaceLoss =
                json.get("has_depth_or_surface_loss") as? Boolean ?: return null,
            temporalConsistency = temporalConsistency,
            size = size,
            description = json.get("description") as? String ?: return null
        )
    } catch (_: Exception) {
        null
    }
}

/**
 * The two cheap hard-negative checks worth cancelling early. All other responses finish and use
 * the normal JSON parser, keeping the streaming path understandable.
 */
internal fun findEarlyRejection(text: String): DetectionRejectionReason? {
    val modelDecision = IS_POTHOLE_PATTERN.matcher(text)
    if (!modelDecision.find()) return null
    if (modelDecision.group(1) == "false") return DetectionRejectionReason.MODEL_NO

    val speedBreaker = SPEED_BREAKER_PATTERN.matcher(text)
    return if (speedBreaker.find() && speedBreaker.group(1) == "true") {
        DetectionRejectionReason.SPEED_BREAKER
    } else {
        null
    }
}

internal fun completeDetectionAssessment(
    text: String,
    streamCompleted: Boolean,
    earlyRejection: DetectionRejectionReason?
): AssessmentResult {
    if (earlyRejection != null) return earlyRejectedAssessment(earlyRejection)
    if (!streamCompleted) throw NativeInferenceException("OpenAI detection stream was interrupted")
    return parseDetectionVerdict(text)?.toAssessment() ?: throw NativeInferenceException(
        "OpenAI returned an invalid completed detection assessment",
        suspendInference = true
    )
}

private fun earlyRejectedAssessment(reason: DetectionRejectionReason): AssessmentResult =
    AssessmentResult(
        isPothole = false,
        looksLikeSpeedBreaker = reason == DetectionRejectionReason.SPEED_BREAKER,
        reportable = false,
        assessment = "absent",
        imageQuality = "unusable",
        damageType = "none",
        surfaceType = "unknown",
        defectType = "not_pothole",
        measurementProvenance = "not_applicable",
        measurementConfidence = "not_applicable",
        onDrivableSurface = false,
        hasLocalizedCavity = false,
        hasBrokenEdgeOrRim = false,
        hasDepthOrSurfaceLoss = false,
        temporalConsistency = "not_applicable",
        size = null,
        description = if (reason == DetectionRejectionReason.SPEED_BREAKER) {
            "Raised speed-breaker geometry is not a pothole."
        } else {
            "No pothole detected."
        },
        decision = "reject"
    )

private val VALID_IMAGE_QUALITIES = setOf("usable", "unusable")
private val VALID_TEMPORAL_VALUES = setOf(
    "consistent", "single_view", "inconsistent", "not_applicable"
)
private val VALID_SIZES = setOf("small", "medium", "large")
private val VALID_SURFACES = setOf(
    "bituminous_asphalt", "cement_concrete", "mastic_asphalt", "paver_blocks",
    "temporary_drivable_surface", "unpaved_or_nonroad", "unknown"
)
private val REPORTABLE_SURFACES = VALID_SURFACES - setOf("unknown", "unpaved_or_nonroad")
private val IS_POTHOLE_PATTERN = Pattern.compile("\"is_pothole\"\\s*:\\s*(true|false)")
private val SPEED_BREAKER_PATTERN =
    Pattern.compile("\"looks_like_speed_breaker\"\\s*:\\s*(true|false)")
