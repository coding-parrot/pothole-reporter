package dev.aiengg.potholereporter.drive

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class NativeInferenceTransportTest {
    private lateinit var server: MockWebServer

    @Before
    fun startServer() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun stopServer() {
        server.shutdown()
    }

    @Test
    fun detectionReadsMultipleDeltasAndDoneMarker() {
        val split = ACCEPTED_JSON.length / 2
        enqueueSse(
            delta(ACCEPTED_JSON.substring(0, split)),
            delta(ACCEPTED_JSON.substring(split)),
            "[DONE]"
        )

        val assessment = transport().use { it.detect(images(), "prompt", false) }

        assertEquals("accept", assessment.decision)
        assertEquals("large", assessment.size)
        assertTrue(server.takeRequest().body.readUtf8().contains("pothole_binary_assessment"))
    }

    @Test
    fun completedVerdictSurvivesATrailingDisconnect() {
        val body = sse(
            delta(ACCEPTED_JSON),
            event("response.completed"),
            JSONObject().put("type", "ignored").put("padding", "x".repeat(8_000)).toString()
        )
        server.enqueue(
            response(body).setSocketPolicy(SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY)
        )

        val assessment = transport().use { it.detect(images(), "prompt", false) }

        assertEquals("accept", assessment.decision)
    }

    @Test
    fun intentionalEarlyNoSurvivesCallCancellation() {
        enqueueSse(
            delta("{\"is_pothole\":false"),
            JSONObject().put("type", "ignored").put("padding", "x".repeat(8_000)).toString()
        )

        val assessment = transport().use { it.detect(images(), "prompt", true) }

        assertEquals("reject", assessment.decision)
        assertFalse(assessment.isPothole)
    }

    @Test
    fun unfinishedStreamIsRetryableAndNeverBecomesAVerdict() {
        enqueueSse(delta(ACCEPTED_JSON))

        val error = assertThrows(NativeInferenceException::class.java) {
            transport().use { it.detect(images(), "prompt", false) }
        }

        assertFalse(error.suspendInference)
        assertNotNull(error.retryAfterMs)
    }

    @Test
    fun oversizedOutputSuspendsInference() {
        enqueueSse(delta("x".repeat(NativeSseTextAccumulator.MAX_UTF8_BYTES + 1)))

        val error = assertThrows(NativeInferenceException::class.java) {
            transport().use { it.detect(images(), "prompt", false) }
        }

        assertTrue(error.suspendInference)
    }

    @Test
    fun terminalMarkerCannotHideAnOversizedLaterDelta() {
        enqueueSse(
            delta(ACCEPTED_JSON),
            event("response.completed"),
            delta("x".repeat(NativeSseTextAccumulator.MAX_UTF8_BYTES))
        )

        val error = assertThrows(NativeInferenceException::class.java) {
            transport().use { it.detect(images(), "prompt", false) }
        }

        assertTrue(error.suspendInference)
    }

    @Test
    fun repairUsesTheSameMultiDeltaCompletedParser() {
        val split = REPAIR_JSON.length / 2
        enqueueSse(
            delta(REPAIR_JSON.substring(0, split)),
            delta(REPAIR_JSON.substring(split)),
            event("response.completed")
        )

        val assessment = transport().use { it.verifyRepair(images(), "prompt") }

        assertEquals("repaired", assessment.currentCondition)
        assertTrue(assessment.sameLocationVisible)
        assertTrue(assessment.completedRepairVisible)
    }

    private fun transport() = NativeInferenceTransport(
        apiKey = "test-key",
        model = "gpt-5.6",
        detail = "high",
        debug = false,
        endpoint = server.url("/v1/responses").toString()
    )

    private fun images() = mutableListOf("data:image/jpeg;base64,dGVzdA==")

    private fun enqueueSse(vararg events: String) {
        server.enqueue(response(sse(*events)))
    }

    private fun response(body: String) = MockResponse()
        .setResponseCode(200)
        .setHeader("Content-Type", "text/event-stream")
        .setBody(body)

    private fun sse(vararg events: String): String =
        events.joinToString(separator = "\n\n", postfix = "\n\n") { "data: $it" }

    private fun delta(text: String): String = JSONObject()
        .put("type", "response.output_text.delta")
        .put("delta", text)
        .toString()

    private fun event(type: String): String = JSONObject().put("type", type).toString()

    private inline fun <T> NativeInferenceTransport.use(
        block: (NativeInferenceTransport) -> T
    ): T =
        try {
            block(this)
        } finally {
            close()
        }

    companion object {
        private const val ACCEPTED_JSON =
            "{\"is_pothole\":true,\"looks_like_speed_breaker\":false," +
                "\"image_quality\":\"usable\",\"surface_type\":\"bituminous_asphalt\"," +
                "\"on_drivable_surface\":true,\"has_localized_cavity\":true," +
                "\"has_broken_edge_or_rim\":true,\"has_depth_or_surface_loss\":true," +
                "\"temporal_consistency\":\"consistent\",\"size\":\"large\"," +
                "\"description\":\"A visible cavity.\"}"

        private const val REPAIR_JSON =
            "{\"current_condition\":\"repaired\",\"assessment\":\"clear\"," +
                "\"image_quality\":\"usable\",\"same_location_visible\":true," +
                "\"completed_repair_visible\":true,\"description\":\"Filled surface.\"}"
    }
}
