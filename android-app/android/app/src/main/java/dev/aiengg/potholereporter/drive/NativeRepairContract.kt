package dev.aiengg.potholereporter.drive

import org.json.JSONArray
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
}

internal object NativeRepairPromptFactory {
    fun build(language: String, imageCount: Int, primaryIndex: Int): String {
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
        return NativeRepairContract.PROMPT + layout + languageNote
    }
}

internal class NativeRepairRequestFactory(
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
        schemaName = "road_repair_assessment",
        schemaJson = NativeRepairContract.SCHEMA_JSON,
        stream = true,
        store = false,
        maxOutputTokens = NativeRepairContract.MAX_OUTPUT_TOKENS,
        reasoningEffort = if (model == "gpt-5.6") "none" else "minimal"
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
            put("text", spec.prompt)
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

internal object NativeRepairAssessmentParser {
    private val conditions = setOf("repaired", "still_damaged", "not_visible", "uncertain")
    private val assessments = setOf("clear", "probable", "uncertain")
    private val imageQualities = setOf("usable", "degraded", "unusable")

    fun parse(text: String): RepairModelAssessment {
        try {
            val json = JSONObject(text)
            val condition = json.getString("current_condition")
            val assessment = json.getString("assessment")
            val imageQuality = json.getString("image_quality")
            if (condition !in conditions || assessment !in assessments ||
                imageQuality !in imageQualities
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
}
