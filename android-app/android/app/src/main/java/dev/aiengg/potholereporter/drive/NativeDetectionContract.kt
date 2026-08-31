package dev.aiengg.potholereporter.drive

/** Single source of truth for the native Drive detection prompt and structured schema. */
internal object NativeDetectionContract {
    const val PROMPT_VERSION = "pothole-binary-v19"
    const val SCHEMA_VERSION = 9
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

        "Localized" means a stable, limited footprint within the traffic path; it does not mean a closed circular rim. Apply a stricter rule on an unfinished, gravel-covered, failed, or construction-stage lane. YES only when a compact depression or connected cavity cluster occupies part of a wheel path, has an abrupt irregular boundary against the adjacent traffic surface, and the same lower opening grows coherently in at least two chronological views. The floor may contain rubble or loose aggregate. It need not be fully exposed when a persistent broken lip or occlusion boundary together with visibly higher adjacent traffic surface proves the opening is lower. Set has_unambiguous_lower_interior true for that repeated geometric proof, never for colour, texture, stones, shadow, or coherent growth alone.

        Temporary-surface decision clarification: A connected eroded cavity cluster occupying only part of a wheel path is localized. When a persistent broken or occluding near edge, a rubble-filled lower footprint, and visibly higher adjacent running surfaces move and enlarge together, return YES even if loose aggregate visually bridges or partly hides the floor. Do not relabel that bounded wheel-dropping cluster as distributed roughness merely because the lane is unfinished. This does not relax the NO rule for road-wide gravel, uniform grading, corrugation, scattered stones, or a construction scar with no bounded lower footprint.

        Wet connected-cluster rule: Do not require a connected cavity cluster to be compact across the lane. A finite transverse band on the traversed temporary lane is localized when the same dark, wet, or rubble-filled concave footprint is bounded along the direction of travel by persistent irregular eroded height breaks and enlarges coherently. In that case mark all four physical evidence fields true even when water hides the floor and the cluster spans much of the lane; transverse concavity is not a raised speed breaker. Keep all four false for a flat wet band, stain, reflection, or puddle whose boundaries are only colour and have no higher approach/exit surface or eroded occluding lip.

        Return NO for distributed gravel texture, scattered stones, gradual grading, exposed soil, a rolling wave or smooth rut across most of the lane, or a construction scar with no compact repeatable downward opening. Roughness, rubble, and a boundary line are never sufficient without both a localized footprint and a persistent abrupt drop from higher adjacent surface.

        A compact water-filled opening may be YES only when a stable irregular eroded shoreline or broken lip encloses a bounded wet interior, the immediately adjacent traffic surface is visibly higher, and the same opening grows coherently through the approach. A flat puddle, reflection, wet stain, or broad water-covered low area without that stable broken boundary is NO. Water by itself never proves depth or material loss.

        A shallow pothole is still YES when an irregular, compact area has a visibly lower interior relative to the immediate surrounding traffic surface and that geometry persists through the approach. A worn or rounded eroded lip or exposed aggregate may support that visible depression, but neither can replace it. Do not demand a steep wall, deep dark interior, fully visible floor, or sharp closed rim. A flat patch remains NO: colour, texture, a repair outline, or a height transition without an unambiguous lower interior is not a pothole.

        General gravel texture, road-wide grading, corrugation, broad roughness with no local lower footprint, a wheel rut with smooth sides, loose debris resting on a level surface, a stain, shadow, puddle with no visible depressed boundary, intact patch, crack, seam, manhole, drain, shoulder erosion, construction obstacle, or damage outside the wheel-traversed surface is NO. A darker or rougher strip at a paved-to-loose-material transition is also NO when no interior is visibly lower than both adjacent surfaces; persistence of that flat transition across views is not depth evidence.

        Speed-breaker hard veto: set looks_like_speed_breaker true and is_pothole false whenever the feature is or could reasonably be a raised transverse ridge. Painted bands or rectangles, reflectors, parallel leading/trailing edges, a vehicle jolt, and camera pitch support a breaker. A separate cavity beside a breaker is YES only when clearly distinct from the raised ridge; raised-versus-concave ambiguity is NO.

        Utility-reinstatement veto: a circular manhole or utility-cover ring, collar, rectangular trench patch, or linear reinstatement around an intact cover is NO even when its repair material is rough, cracked, or slightly sunken. Return YES only for a separate irregular wheel-dropping cavity that clearly extends beyond the utility repair footprint and independently satisfies every pothole condition.

        Surface type:
        - bituminous_asphalt, cement_concrete, mastic_asphalt, or paver_blocks when identifiable;
        - temporary_drivable_surface for an unsealed or construction-stage path that the recording vehicle traverses. In a forward-facing Drive burst, coherent forward motion along a continuous wheel path proves this use even when no second vehicle is visible;
        - unpaved_or_nonroad for a shoulder, construction bed, work area, service path, or roadside ground not being traversed; otherwise unknown.
        unpaved_or_nonroad and unknown are always NO.

        A cavity at a road edge may be YES when its opening removes part of the flat traffic surface or creates a wheel-reachable drop, even if rubble extends beneath a raised roadside slab. It is NO when an intact kerb or gutter separates the entire opening from traffic.

        Set has_localized_cavity, has_unambiguous_lower_interior, has_broken_edge_or_rim, and has_depth_or_surface_loss true only when the corresponding physical evidence above is present. has_unambiguous_lower_interior means the opening is demonstrably below its immediate surroundings, either by a visible lower interior or by a persistent broken lip or occlusion boundary beside visibly higher traffic surface. Texture, loose stones, shadow, a boundary line alone, or a raised ridge cannot satisfy it. Set image_quality unusable only when blur, darkness, glare, obstruction, or distance prevents a defensible judgment. For multiple views set temporal_consistency consistent when at least two show the same footprint; a feature leaving the final full frame is not disagreement. Use single_view for one user-framed photo.

        After YES, set size to small below 30 cm, medium from 30 to 60 cm, or large above 60 cm or for a connected cavity cluster. For NO, size is null. These are visual app estimates, not official measurements. Keep description factual and never output confidence or probability.
        """.trimIndent()

    val SCHEMA_JSON =
        """
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["image_quality", "surface_type", "on_drivable_surface", "temporal_consistency", "looks_like_speed_breaker", "is_pothole", "has_localized_cavity", "has_unambiguous_lower_interior", "has_broken_edge_or_rim", "has_depth_or_surface_loss", "size", "description"],
          "properties": {
            "image_quality": { "type": "string", "enum": ["usable", "unusable"] },
            "surface_type": { "type": "string", "enum": ["bituminous_asphalt", "cement_concrete", "mastic_asphalt", "paver_blocks", "temporary_drivable_surface", "unpaved_or_nonroad", "unknown"] },
            "on_drivable_surface": { "type": "boolean" },
            "temporal_consistency": { "type": "string", "enum": ["consistent", "single_view", "inconsistent", "not_applicable"] },
            "looks_like_speed_breaker": { "type": "boolean" },
            "is_pothole": { "type": "boolean" },
            "has_localized_cavity": { "type": "boolean" },
            "has_unambiguous_lower_interior": { "type": "boolean" },
            "has_broken_edge_or_rim": { "type": "boolean" },
            "has_depth_or_surface_loss": { "type": "boolean" },
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
