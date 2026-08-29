package dev.aiengg.potholereporter.drive

import org.json.JSONObject

/** The exact fields emitted by the structured-output model, before app policy is applied. */
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

internal data class DetectionPolicyResult(
    val accepted: Boolean,
    val rejectionReasons: List<DetectionRejectionReason>
) {
    val decision: String get() = if (accepted) "accept" else "reject"
}

/** Pure, deterministic application policy. It never parses JSON and never performs I/O. */
internal object NativePotholeDecisionPolicy {
    internal val imageQualities = setOf("usable", "unusable")
    internal val temporalValues = setOf("consistent", "single_view", "inconsistent", "not_applicable")
    internal val sizes = setOf("small", "medium", "large")
    internal val surfaceTypes = setOf(
        "bituminous_asphalt", "cement_concrete", "mastic_asphalt", "paver_blocks",
        "temporary_drivable_surface", "unpaved_or_nonroad", "unknown"
    )
    private val reportableSurfaceTypes = surfaceTypes - setOf("unknown", "unpaved_or_nonroad")

    fun evaluate(verdict: DetectionModelVerdict): DetectionPolicyResult {
        val reasons = buildList {
            if (!verdict.isPothole) add(DetectionRejectionReason.MODEL_NO)
            if (verdict.looksLikeSpeedBreaker) add(DetectionRejectionReason.SPEED_BREAKER)
            if (verdict.surfaceType !in reportableSurfaceTypes) {
                add(DetectionRejectionReason.UNSUPPORTED_SURFACE)
            }
            if (verdict.imageQuality != "usable") add(DetectionRejectionReason.IMAGE_UNUSABLE)
            if (!verdict.onDrivableSurface) add(DetectionRejectionReason.OFF_DRIVABLE_SURFACE)
            if (!verdict.hasLocalizedCavity) add(DetectionRejectionReason.NO_LOCALIZED_CAVITY)
            if (!verdict.hasBrokenEdgeOrRim) add(DetectionRejectionReason.NO_BROKEN_EDGE_OR_RIM)
            if (!verdict.hasDepthOrSurfaceLoss) add(DetectionRejectionReason.NO_DEPTH_OR_SURFACE_LOSS)
            // Native Drive always supplies a chronological multi-frame burst.
            if (verdict.temporalConsistency != "consistent") {
                add(DetectionRejectionReason.TEMPORAL_NOT_CONSISTENT)
            }
            if (verdict.size !in sizes) add(DetectionRejectionReason.SIZE_MISSING_OR_INVALID)
        }
        return DetectionPolicyResult(accepted = reasons.isEmpty(), rejectionReasons = reasons)
    }

    fun decisionFor(
        isPothole: Boolean,
        looksLikeSpeedBreaker: Boolean,
        imageQuality: String,
        surfaceType: String,
        onDrivableSurface: Boolean,
        hasLocalizedCavity: Boolean,
        hasBrokenEdgeOrRim: Boolean,
        hasDepthOrSurfaceLoss: Boolean,
        temporalConsistency: String,
        size: String?
    ): String = evaluate(
        DetectionModelVerdict(
            isPothole = isPothole,
            looksLikeSpeedBreaker = looksLikeSpeedBreaker,
            imageQuality = imageQuality,
            surfaceType = surfaceType,
            onDrivableSurface = onDrivableSurface,
            hasLocalizedCavity = hasLocalizedCavity,
            hasBrokenEdgeOrRim = hasBrokenEdgeOrRim,
            hasDepthOrSurfaceLoss = hasDepthOrSurfaceLoss,
            temporalConsistency = temporalConsistency,
            size = size,
            description = ""
        )
    ).decision
}

/** Parses and validates the model-owned fields without overwriting them with app policy. */
internal object NativeDetectionVerdictJsonParser {
    private val requiredFields = setOf(
        "is_pothole", "looks_like_speed_breaker", "image_quality", "surface_type",
        "on_drivable_surface", "has_localized_cavity", "has_broken_edge_or_rim",
        "has_depth_or_surface_loss", "temporal_consistency", "size", "description"
    )

    fun parse(text: String): DetectionModelVerdict? = try {
        val json = JSONObject(text)
        val sizeValue = json.get("size")
        fromFields(
            mapOf(
                "is_pothole" to json.get("is_pothole"),
                "looks_like_speed_breaker" to json.get("looks_like_speed_breaker"),
                "image_quality" to json.get("image_quality"),
                "surface_type" to json.get("surface_type"),
                "on_drivable_surface" to json.get("on_drivable_surface"),
                "has_localized_cavity" to json.get("has_localized_cavity"),
                "has_broken_edge_or_rim" to json.get("has_broken_edge_or_rim"),
                "has_depth_or_surface_loss" to json.get("has_depth_or_surface_loss"),
                "temporal_consistency" to json.get("temporal_consistency"),
                "size" to if (sizeValue === JSONObject.NULL) null else sizeValue,
                "description" to json.get("description")
            )
        )
    } catch (_: Exception) {
        null
    }

    fun fromFields(fields: Map<String, Any?>): DetectionModelVerdict? {
        if (!fields.keys.containsAll(requiredFields)) return null
        val isPothole = fields["is_pothole"] as? Boolean ?: return null
        val looksLikeSpeedBreaker = fields["looks_like_speed_breaker"] as? Boolean ?: return null
        val imageQuality = fields["image_quality"] as? String ?: return null
        val surfaceType = fields["surface_type"] as? String ?: return null
        val onDrivableSurface = fields["on_drivable_surface"] as? Boolean ?: return null
        val hasLocalizedCavity = fields["has_localized_cavity"] as? Boolean ?: return null
        val hasBrokenEdgeOrRim = fields["has_broken_edge_or_rim"] as? Boolean ?: return null
        val hasDepthOrSurfaceLoss = fields["has_depth_or_surface_loss"] as? Boolean ?: return null
        val temporalConsistency = fields["temporal_consistency"] as? String ?: return null
        val description = fields["description"] as? String ?: return null
        if (imageQuality !in NativePotholeDecisionPolicy.imageQualities ||
            surfaceType !in NativePotholeDecisionPolicy.surfaceTypes ||
            temporalConsistency !in NativePotholeDecisionPolicy.temporalValues
        ) return null
        val sizeValue = fields["size"]
        if (sizeValue != null &&
            (sizeValue !is String || sizeValue !in NativePotholeDecisionPolicy.sizes)
        ) return null

        return DetectionModelVerdict(
            isPothole = isPothole,
            looksLikeSpeedBreaker = looksLikeSpeedBreaker,
            imageQuality = imageQuality,
            surfaceType = surfaceType,
            onDrivableSurface = onDrivableSurface,
            hasLocalizedCavity = hasLocalizedCavity,
            hasBrokenEdgeOrRim = hasBrokenEdgeOrRim,
            hasDepthOrSurfaceLoss = hasDepthOrSurfaceLoss,
            temporalConsistency = temporalConsistency,
            size = sizeValue as? String,
            description = description
        )
    }
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

internal object NativeDetectionAssessmentMapper {
    fun toAssessment(
        verdict: DetectionModelVerdict,
        policy: DetectionPolicyResult = NativePotholeDecisionPolicy.evaluate(verdict)
    ): AssessmentResult {
        val accepted = policy.accepted
        return AssessmentResult(
            isPothole = accepted,
            looksLikeSpeedBreaker = verdict.looksLikeSpeedBreaker,
            reportable = accepted,
            assessment = if (accepted) "clear" else "absent",
            imageQuality = verdict.imageQuality,
            damageType = if (accepted) "pothole_cavity" else "none",
            surfaceType = verdict.surfaceType,
            defectType = if (accepted) "pothole" else "not_pothole",
            measurementProvenance = if (accepted) "visual_estimate_no_scale" else "not_applicable",
            measurementConfidence = if (accepted) "low" else "not_applicable",
            onDrivableSurface = verdict.onDrivableSurface,
            hasLocalizedCavity = verdict.hasLocalizedCavity,
            hasBrokenEdgeOrRim = verdict.hasBrokenEdgeOrRim,
            hasDepthOrSurfaceLoss = verdict.hasDepthOrSurfaceLoss,
            temporalConsistency = verdict.temporalConsistency,
            size = if (accepted) verdict.size else null,
            description = verdict.description,
            decision = policy.decision
        )
    }
}

/** Compatibility facade retained for callers while parsing and policy are tested separately. */
internal object NativeCompleteVerdictParser {
    fun parse(text: String): AssessmentResult? =
        NativeDetectionVerdictJsonParser.parse(text)?.let(NativeDetectionAssessmentMapper::toAssessment)

    internal fun fromFields(fields: Map<String, Any?>): AssessmentResult? =
        NativeDetectionVerdictJsonParser.fromFields(fields)?.let(NativeDetectionAssessmentMapper::toAssessment)

    internal fun decisionFor(
        isPothole: Boolean,
        looksLikeSpeedBreaker: Boolean,
        imageQuality: String,
        surfaceType: String,
        onDrivableSurface: Boolean,
        hasLocalizedCavity: Boolean,
        hasBrokenEdgeOrRim: Boolean,
        hasDepthOrSurfaceLoss: Boolean,
        temporalConsistency: String,
        size: String?
    ): String = NativePotholeDecisionPolicy.decisionFor(
        isPothole,
        looksLikeSpeedBreaker,
        imageQuality,
        surfaceType,
        onDrivableSurface,
        hasLocalizedCavity,
        hasBrokenEdgeOrRim,
        hasDepthOrSurfaceLoss,
        temporalConsistency,
        size
    )
}
