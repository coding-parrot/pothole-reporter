package dev.aiengg.potholereporter.drive

import org.json.JSONArray
import org.json.JSONObject

internal fun buildDetectionRequest(
    model: String,
    detail: String,
    imageUrls: List<String>,
    prompt: String
): JSONObject = buildVisionRequest(
    model = model,
    detail = detail,
    imageUrls = imageUrls,
    prompt = "$prompt\n\nThe ${imageUrls.size} supplied image(s) are ordered exactly as labelled by the capture pipeline.",
    schemaName = "pothole_binary_assessment",
    schemaJson = NativeDetectionContract.SCHEMA_JSON,
    maxOutputTokens = NativeDetectionContract.MAX_OUTPUT_TOKENS,
    reasoningEffort = if (model == "gpt-5.6") "low" else "minimal"
)

internal fun buildRepairRequest(
    model: String,
    detail: String,
    imageUrls: List<String>,
    prompt: String
): JSONObject = buildVisionRequest(
    model = model,
    detail = detail,
    imageUrls = imageUrls,
    prompt = prompt,
    schemaName = "road_repair_assessment",
    schemaJson = NativeRepairContract.SCHEMA_JSON,
    maxOutputTokens = NativeRepairContract.MAX_OUTPUT_TOKENS,
    reasoningEffort = if (model == "gpt-5.6") "none" else "minimal"
)

private fun buildVisionRequest(
    model: String,
    detail: String,
    imageUrls: List<String>,
    prompt: String,
    schemaName: String,
    schemaJson: String,
    maxOutputTokens: Int,
    reasoningEffort: String
): JSONObject {
    val content = JSONArray()
    imageUrls.forEach { imageUrl ->
        content.put(JSONObject().apply {
            put("type", "input_image")
            put("image_url", imageUrl)
            put("detail", detail)
        })
    }
    content.put(JSONObject().apply {
        put("type", "input_text")
        put("text", prompt)
    })

    val format = JSONObject().apply {
        put("type", "json_schema")
        put("name", schemaName)
        put("strict", true)
        put("schema", JSONObject(schemaJson))
    }
    return JSONObject().apply {
        put("model", model)
        put("input", JSONArray().put(JSONObject().apply {
            put("role", "user")
            put("content", content)
        }))
        put("text", JSONObject().apply {
            put("format", format)
            put("verbosity", "low")
        })
        put("stream", true)
        put("store", false)
        put("max_output_tokens", maxOutputTokens)
        put("reasoning", JSONObject().put("effort", reasoningEffort))
    }
}
