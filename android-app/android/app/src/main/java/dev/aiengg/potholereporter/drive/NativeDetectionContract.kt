package dev.aiengg.potholereporter.drive

/** Single source of truth for the native Drive detection prompt and structured schema. */
internal object NativeDetectionContract {
    const val PROMPT_VERSION = "pothole-binary-v13"
    const val SCHEMA_VERSION = 7
    const val MAX_OUTPUT_TOKENS = 1_536

    // Keep byte-for-byte equivalent (after trimIndent) to DETECT_PROMPT in standalone.js.
    val DETECT_PROMPT =
        """
        You are a strict binary pothole detector for a civic complaint app. Inspect the supplied road views in chronological order and return only the structured fields in the schema. False positives are more harmful than false negatives: ambiguous geometry is NO.

        A pothole is a localized wheel-dropping depression caused by missing, displaced, or disintegrated road-surface material. It does not need to be round, deep, dark, fully enclosed, or surrounded by intact pavement.

        Return is_pothole true only when all are visible:
        1. The damaged footprint lies on the surface the recording vehicle is actually traversing.
        2. The footprint is lower than the adjacent wheel path and has localized material loss.
        3. At least one side has an irregular broken lip, eroded edge, or abrupt material-height drop.
        4. With multiple views, the same lower footprint moves or grows predictably as the vehicle approaches.
        5. It is not an intentional raised speed breaker, hump, or rumble strip.

        "Localized" means a stable, limited footprint within the traffic path; it does not mean a closed circular rim. On an unfinished, gravel-covered, failed, or construction-stage traffic lane, a jagged eroded depression or connected cavity cluster is YES when it occupies only part of the wheel path, is visibly lower than the adjacent path, and keeps at least one abrupt lip across the approach. Loose rubble within or beside that lower footprint and one boundary blending into surrounding failed material do not turn it into general roughness. A water-filled depression is YES when its stable edge and lower opening remain visible; the floor need not be visible. Do not reject such a feature merely because the rest of the lane is also rough.

        General gravel texture, road-wide grading, corrugation, broad roughness with no local lower footprint, a wheel rut with smooth sides, loose debris resting on a level surface, a stain, shadow, puddle with no visible depressed boundary, intact patch, crack, seam, manhole, drain, shoulder erosion, construction obstacle, or damage outside the wheel-traversed surface is NO. A darker or rougher strip at a paved-to-loose-material transition is also NO when no interior is visibly lower than both adjacent surfaces; persistence of that flat transition across views is not depth evidence.

        Speed-breaker hard veto: set looks_like_speed_breaker true and is_pothole false whenever the feature is or could reasonably be a raised transverse ridge. Painted bands or rectangles, reflectors, parallel leading/trailing edges, a vehicle jolt, and camera pitch support a breaker. A separate cavity beside a breaker is YES only when clearly distinct from the raised ridge; raised-versus-concave ambiguity is NO.

        Surface type:
        - bituminous_asphalt, cement_concrete, mastic_asphalt, or paver_blocks when identifiable;
        - temporary_drivable_surface for an unsealed or construction-stage path that the recording vehicle traverses. In a forward-facing Drive burst, coherent forward motion along a continuous wheel path proves this use even when no second vehicle is visible;
        - unpaved_or_nonroad for a shoulder, construction bed, work area, service path, or roadside ground not being traversed; otherwise unknown.
        unpaved_or_nonroad and unknown are always NO.

        A cavity at a road edge may be YES when its opening removes part of the flat traffic surface or creates a wheel-reachable drop, even if rubble extends beneath a raised roadside slab. It is NO when an intact kerb or gutter separates the entire opening from traffic.

        Set has_localized_cavity, has_broken_edge_or_rim, and has_depth_or_surface_loss true when the physical evidence above is present. Set image_quality unusable only when blur, darkness, glare, obstruction, or distance prevents a defensible judgment. For multiple views set temporal_consistency consistent when at least two show the same footprint; a feature leaving the final full frame is not disagreement. Use single_view for one user-framed photo.

        After YES, set size to small below 30 cm, medium from 30 to 60 cm, or large above 60 cm or for a connected cavity cluster. For NO, size is null. These are visual app estimates, not official measurements. Keep description factual and never output confidence or probability.
        """.trimIndent()

    val SCHEMA_JSON =
        """
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["is_pothole", "looks_like_speed_breaker", "image_quality", "surface_type", "on_drivable_surface", "has_localized_cavity", "has_broken_edge_or_rim", "has_depth_or_surface_loss", "temporal_consistency", "size", "description"],
          "properties": {
            "is_pothole": { "type": "boolean" },
            "looks_like_speed_breaker": { "type": "boolean" },
            "image_quality": { "type": "string", "enum": ["usable", "unusable"] },
            "surface_type": { "type": "string", "enum": ["bituminous_asphalt", "cement_concrete", "mastic_asphalt", "paver_blocks", "temporary_drivable_surface", "unpaved_or_nonroad", "unknown"] },
            "on_drivable_surface": { "type": "boolean" },
            "has_localized_cavity": { "type": "boolean" },
            "has_broken_edge_or_rim": { "type": "boolean" },
            "has_depth_or_surface_loss": { "type": "boolean" },
            "temporal_consistency": { "type": "string", "enum": ["consistent", "single_view", "inconsistent", "not_applicable"] },
            "size": { "type": ["string", "null"], "enum": ["small", "medium", "large", null] },
            "description": { "type": "string" }
          }
        }
        """.trimIndent()

    fun buildPrompt(language: String, imageCount: Int, primaryIndex: Int): String {
        require(imageCount >= 3) { "Detection requires context plus at least two complete frames" }
        val languageSuffix = when (language) {
            "kn" -> "\n- Write the description field in formal Kannada (ಕನ್ನಡ ಭಾಷೆಯಲ್ಲಿ ಬರೆಯಿರಿ)."
            "mr" -> "\n- Write the description field in clear formal Marathi (मराठी भाषेत लिहा)."
            "bn" -> "\n- Write the description field in clear formal Bengali (পরিষ্কার, প্রমিত বাংলায় লিখুন)."
            else -> ""
        }
        val layout = "\n- Capture layout: image 1 is downscaled full-frame context from the " +
            "sharpest burst frame. Images 2-$imageCount are complete camera frames in " +
            "chronological order; chronological frame ${primaryIndex + 1} is the sharpest. " +
            "No image is cropped, tiled, masked, or limited to a region of interest."
        return DETECT_PROMPT + layout + languageSuffix
    }
}
