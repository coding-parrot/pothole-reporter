package dev.aiengg.potholereporter.drive

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeInferenceRequestTest {
    @Test
    fun detectionRequestIsTheExactJsonSentToOpenAi() {
        val urls = listOf("context", "early", "primary", "late")
        val prompt = NativeDetectionContract.buildPrompt("mr", urls.size, primaryIndex = 1)
        val request = buildDetectionRequest("gpt-5.6", "high", urls, prompt)
        val content = request.content()

        assertEquals("gpt-5.6", request.getString("model"))
        assertEquals(urls, (0 until 4).map { content.getJSONObject(it).getString("image_url") })
        assertEquals("high", content.getJSONObject(0).getString("detail"))
        assertTrue(content.getJSONObject(4).getString("text").contains("formal Marathi"))
        assertTrue(content.getJSONObject(4).getString("text").contains("4 supplied image(s)"))
        assertEquals("pothole_binary_assessment", request.schema().getString("name"))
        assertTrue(request.schema().getBoolean("strict"))
        assertEquals(JSONObject(NativeDetectionContract.SCHEMA_JSON).toString(),
            request.schema().getJSONObject("schema").toString())
        assertTrue(request.getBoolean("stream"))
        assertFalse(request.getBoolean("store"))
        assertEquals(1_536, request.getInt("max_output_tokens"))
        assertEquals("low", request.getJSONObject("reasoning").getString("effort"))
        assertEquals(
            "minimal",
            buildDetectionRequest("another-model", "low", listOf("one"), "prompt")
                .getJSONObject("reasoning")
                .getString("effort")
        )
    }

    @Test
    fun repairRequestHasItsOwnPromptSchemaAndReasoning() {
        val urls = listOf("historical", "context", "one", "two", "three")
        val prompt = NativeRepairContract.buildPrompt("en", urls.size, primaryIndex = 1)
        val request = buildRepairRequest("gpt-5.6", "high", urls, prompt)
        val content = request.content()

        assertEquals(urls, (0 until 5).map { content.getJSONObject(it).getString("image_url") })
        assertTrue(content.getJSONObject(5).getString("text").contains(
            "image 1 is historical evidence"
        ))
        assertTrue(content.getJSONObject(5).getString("text").contains(
            "sharpest crop is image 4"
        ))
        assertEquals("road_repair_assessment", request.schema().getString("name"))
        assertEquals(768, request.getInt("max_output_tokens"))
        assertEquals("none", request.getJSONObject("reasoning").getString("effort"))
    }

    private fun JSONObject.content() = getJSONArray("input")
        .getJSONObject(0)
        .getJSONArray("content")

    private fun JSONObject.schema() = getJSONObject("text")
        .getJSONObject("format")
}
