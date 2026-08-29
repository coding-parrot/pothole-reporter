package dev.aiengg.potholereporter.drive

import org.json.JSONObject

data class RepairVerificationResult(
    val currentCondition: String,
    val assessment: String,
    val imageQuality: String,
    val sameLocationVisible: Boolean,
    val completedRepairVisible: Boolean,
    val description: String,
    val currentPhotoPath: String,
    val detectionModel: String,
    val imageDetail: String,
    val promptVersion: String,
    val schemaVersion: Int
)

internal data class RepairModelAssessment(
    val currentCondition: String,
    val assessment: String,
    val imageQuality: String,
    val sameLocationVisible: Boolean,
    val completedRepairVisible: Boolean,
    val description: String
)

internal object NativeRepairContract {
    const val PROMPT_VERSION = "road-repair-v1"
    const val SCHEMA_VERSION = 1
    const val MAX_OUTPUT_TOKENS = 768

    const val PROMPT =
        "You are comparing a previously accepted road-damage report with a new " +
            "dashcam pass. Image 1 is the historical defect evidence. The remaining " +
            "images are current views from one short burst. A nearby clean-looking road " +
            "is not proof of repair. Set same_location_visible true only when permanent " +
            "road geometry, lane position, curbs, markings, or fixed surroundings make " +
            "the exact old defect footprint visible in the current views. Set " +
            "completed_repair_visible true only when that exact footprint visibly has a " +
            "completed, intact fill or resurfacing with no open cavity or failed material. " +
            "A different lane, changed viewpoint, blur, glare, traffic obstruction, water, " +
            "or the old defect merely being absent from view must be not_visible or " +
            "uncertain, never repaired. Use assessment clear only for unambiguous visual " +
            "alignment and repair evidence."

    val SCHEMA_JSON =
        """
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["current_condition", "assessment", "image_quality", "same_location_visible", "completed_repair_visible", "description"],
          "properties": {
            "current_condition": { "type": "string", "enum": ["repaired", "still_damaged", "not_visible", "uncertain"] },
            "assessment": { "type": "string", "enum": ["clear", "probable", "uncertain"] },
            "image_quality": { "type": "string", "enum": ["usable", "degraded", "unusable"] },
            "same_location_visible": { "type": "boolean" },
            "completed_repair_visible": { "type": "boolean" },
            "description": { "type": "string" }
          }
        }
        """.trimIndent()

    fun buildPrompt(language: String, imageCount: Int, primaryIndex: Int): String {
        require(imageCount >= 3) { "Repair verification requires historical and current evidence" }
        val layout = "\n- Input layout: image 1 is historical evidence; image 2 is " +
            "current full-frame context; images 3-$imageCount are the complete current " +
            "lower-road burst in chronological order; its sharpest crop is image ${primaryIndex + 3}."
        val languageNote = when (language) {
            "kn" -> "\n- Write description in formal Kannada."
            "mr" -> "\n- Write description in clear formal Marathi."
            "bn" -> "\n- Write description in clear formal Bengali."
            else -> ""
        }
        return PROMPT + layout + languageNote
    }

    fun parseAssessment(text: String): RepairModelAssessment {
        try {
            val json = JSONObject(text)
            val condition = json.getString("current_condition")
            val assessment = json.getString("assessment")
            val imageQuality = json.getString("image_quality")
            if (condition !in CONDITIONS || assessment !in ASSESSMENTS ||
                imageQuality !in IMAGE_QUALITIES
            ) throw IllegalArgumentException("Unexpected repair assessment value")

            return RepairModelAssessment(
                currentCondition = condition,
                assessment = assessment,
                imageQuality = imageQuality,
                sameLocationVisible = json.getBoolean("same_location_visible"),
                completedRepairVisible = json.getBoolean("completed_repair_visible"),
                description = json.getString("description").take(1000)
            )
        } catch (error: Exception) {
            throw NativeInferenceException(
                "OpenAI returned an invalid completed repair assessment",
                suspendInference = true,
                cause = error
            )
        }
    }

    private val CONDITIONS = setOf("repaired", "still_damaged", "not_visible", "uncertain")
    private val ASSESSMENTS = setOf("clear", "probable", "uncertain")
    private val IMAGE_QUALITIES = setOf("usable", "degraded", "unusable")
}
