package dev.aiengg.potholereporter.drive

import org.json.JSONArray
import org.json.JSONObject

/** Single source of truth for the native Drive detection prompt and structured schema. */
internal object NativeDetectionContract {
    const val PROMPT_VERSION = "pothole-binary-v10"
    const val SCHEMA_VERSION = 7
    const val MAX_OUTPUT_TOKENS = 1_536

    // Keep byte-for-byte equivalent (after trimIndent) to DETECT_PROMPT in standalone.js.
    val DETECT_PROMPT =
        """
        You are a high-precision binary pothole detector inspecting one or more chronologically ordered road views for a civic complaint app.

        Return one decision only: is_pothole true (YES) or false (NO). There is no confidence score, probability, probable result, review result, or general road-damage category. False positives are more harmful than false negatives, so any ambiguity must be NO.

        A pothole is a localized concave open cavity in the surface currently used by moving road traffic, with surface material visibly missing, displaced, or disintegrated. A YES requires all of these:
        - the feature is on the drivable surface used by moving traffic;
        - it has a distinct local edge, lip, or abrupt height discontinuity enclosing a depressed opening;
        - it has visible depth or localized material loss; and
        - when several chronological views are supplied, they consistently show the same concave geometry as the vehicle approaches.

        Position near the side of a lane is not itself a rejection. A localized cavity whose opening intrudes into the drivable surface actually used by moving traffic may be YES when every physical gate above is satisfied, including when the cavity adjoins a raised kerb or roadside slab. This exception is only for the cavity footprint inside the active traffic surface: damage confined to a kerb, gutter, drain, footpath, shoulder, verge, roadside ground, or a broken outer edge that vehicles do not traverse is NO.

        Return NO for a speed breaker, road hump, rumble strip, shadow, stain, glare, dust, loose debris, lane marking, intact patch, crack, broad surface breakup without a distinct cavity, wheel rut, smooth depression, manhole, drain, expansion joint, shoulder erosion, construction obstacle, or broken edge outside the active traffic surface. A failed patch is YES only when it now contains a distinct cavity satisfying every rule.

        Speed-breaker rule:
        - Set looks_like_speed_breaker true whenever the feature is or could reasonably be an intentional raised speed breaker, hump, or rumble strip. Painted rectangles or stripes, reflectors, a transverse ridge across the lane, parallel leading/trailing edges, camera pitch, and a vehicle jolt support NO, not YES.
        - A separate cavity on or beside a breaker is YES only when it is visually unambiguous and distinct from the raised ridge. If raised-versus-concave geometry is uncertain, return NO.

        Classify surface_type as:
        - bituminous_asphalt for conventional asphalt or blacktop;
        - cement_concrete for a concrete slab;
        - mastic_asphalt only when that pavement is visually identifiable;
        - paver_blocks for interlocking paved blocks;
        - temporary_drivable_surface only for an unsealed, unfinished, or construction-stage lane that the chronological views clearly show is currently carrying road traffic;
        - unpaved_or_nonroad for a dirt or gravel shoulder, construction bed, work area, service path, roadside ground, or other non-carriageway surface; or
        - unknown whenever the material or road use is uncertain.

        Camera position alone does not prove that an unsealed surface is a traffic lane. The four named paved surfaces may be YES only when every physical gate is satisfied. For temporary_drivable_surface, distinguish a local cavity from the surrounding unfinished texture:
        - A pothole can exist inside a generally rough, failed, or gravel-covered traffic lane. Do not reject a discrete cavity merely because nearby surface is also damaged or unfinished.
        - On this surface, a broken edge or rim can be an eroded lip or abrupt localized material-height change; it need not be a fractured asphalt edge.
        - A water-filled cavity can be YES when a localized enclosing lip and depressed opening remain visible and preserve their geometry across the approach. Water or a dark patch without that independent boundary evidence is NO; the cavity floor need not be visible through opaque water.
        - Do not require dramatic depth, a black interior, or an exposed cavity floor. A shallow opening is YES only when an irregular eroded lip or abrupt material-height change bounds a visibly lower local interior and that same footprint remains coherent as it grows across the approach views. A flat discoloration, intact repair, soft shadow, loose-gravel texture, or broad unevenness without that bounded lower interior is NO.
        - A long or open-ended eroded edge, a seam or step between paver blocks and loose aggregate, missing edge blocks, and a transition between paved and unfinished material are NO. Do not reinterpret one of these boundaries as a cavity cluster merely because the same rough edge persists across the approach.
        - On loose gravel, changes in colour, aggregate density, wheel-track texture, or grading do not prove a cavity. Require a separate compact concave opening with its own localized enclosing lip and visibly lower interior; never infer either feature from aggregate texture alone.
        - Two or more adjacent discrete bowl-like material-loss openings are one connected cavity-cluster event. Do not relabel them as broad breakup when their local boundaries remain distinct.
        - Two adjacent compact oval material-loss openings may be one shallow cavity-cluster even when their floors are similar in colour to the surrounding lane. This is YES only when each opening has a stable irregular boundary and visibly lower interior across the approach; patch outlines and stains remain NO.
        - General roughness, corrugation, wheel ruts, broad breakup, loose aggregate, normal gravel texture, grading, and smooth depressions are NO.

        Road-edge boundary interpretation:
        - A cavity at the meeting line of a flat roadway foreground and a raised roadside slab is not confined to the footpath or gutter when its broken opening removes part of that flat road edge or creates an abrupt open drop reachable by a vehicle wheel. In that case set on_drivable_surface true even when much of the visible void or rubble extends beside or underneath the slab. Reject it as confined outside the road only when an intact continuous kerb or gutter clearly separates the entire cavity opening from the traffic surface.

        unpaved_or_nonroad and unknown must always be NO.

        Set image_quality unusable when blur, darkness, glare, obstruction, or distance prevents a defensible judgment. For multiple views use temporal_consistency consistent only when they agree; use inconsistent when they do not. For a single user-framed image use single_view.

        Only after YES, classify approximate visual size using the app's simple bands:
        - small: maximum visible opening width below 30 cm;
        - medium: 30 to 60 cm;
        - large: above 60 cm or a connected cavity cluster.
        For NO, size must be null. These are app visual classes only, not measured dimensions and not BMC, BDA, GBA, or any other authority's official categories.

        description must be one or two factual sentences. For YES, name the visible cavity evidence, position, and road-user hazard. For NO, briefly name the disqualifying feature. Never output a confidence percentage.
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
}

internal object NativeDetectionPromptFactory {
    fun build(language: String, imageCount: Int, primaryIndex: Int): String {
        require(imageCount >= 2) { "Detection requires context plus at least one road crop" }
        val languageSuffix = when (language) {
            "kn" -> "\n- Write the description field in formal Kannada (ಕನ್ನಡ ಭಾಷೆಯಲ್ಲಿ ಬರೆಯಿರಿ)."
            "mr" -> "\n- Write the description field in clear formal Marathi (मराठी भाषेत लिहा)."
            "bn" -> "\n- Write the description field in clear formal Bengali (পরিষ্কার, প্রমিত বাংলায় লিখুন)."
            else -> ""
        }
        val layout = "\n- Capture layout: image 1 is full-frame context from the sharpest burst frame. " +
            "Images 2-$imageCount are orientation-aware road-region crops in chronological order; " +
            "the sharpest crop is chronological frame ${primaryIndex + 1}."
        return NativeDetectionContract.DETECT_PROMPT + layout + languageSuffix
    }
}

internal class NativeDetectionRequestFactory(
    private val model: String,
    private val detail: String
) {
    internal data class Spec(
        val model: String,
        val detail: String,
        val imageUrls: List<String>,
        val prompt: String,
        val schemaName: String,
        val schemaJson: String,
        val stream: Boolean,
        val store: Boolean,
        val maxOutputTokens: Int,
        val reasoningEffort: String
    )

    fun spec(imageUrls: List<String>, prompt: String): Spec = Spec(
        model = model,
        detail = detail,
        imageUrls = imageUrls.toList(),
        prompt = prompt,
        schemaName = "pothole_binary_assessment",
        schemaJson = NativeDetectionContract.SCHEMA_JSON,
        stream = true,
        store = false,
        maxOutputTokens = NativeDetectionContract.MAX_OUTPUT_TOKENS,
        reasoningEffort = if (model == "gpt-5.6") "low" else "minimal"
    )

    fun build(imageUrls: List<String>, prompt: String): JSONObject {
        val spec = spec(imageUrls, prompt)
        val content = JSONArray()
        spec.imageUrls.forEach { url ->
            content.put(JSONObject().apply {
                put("type", "input_image")
                put("image_url", url)
                put("detail", spec.detail)
            })
        }
        content.put(JSONObject().apply {
            put("type", "input_text")
            put(
                "text",
                "${spec.prompt}\n\nThe ${spec.imageUrls.size} supplied image(s) are ordered exactly as labelled by the capture pipeline."
            )
        })

        val format = JSONObject().apply {
            put("type", "json_schema")
            put("name", spec.schemaName)
            put("strict", true)
            put("schema", JSONObject(spec.schemaJson))
        }
        return JSONObject().apply {
            put("model", spec.model)
            put("input", JSONArray().put(JSONObject().apply {
                put("role", "user")
                put("content", content)
            }))
            put("text", JSONObject().apply {
                put("format", format)
                put("verbosity", "low")
            })
            put("stream", spec.stream)
            put("store", spec.store)
            put("max_output_tokens", spec.maxOutputTokens)
            put("reasoning", JSONObject().put("effort", spec.reasoningEffort))
        }
    }
}
