package dev.aiengg.potholereporter.drive

import okhttp3.Call
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Owns OpenAI HTTP/SSE lifecycle; it has no camera, bitmap, Room, or report responsibilities. */
internal class NativeInferenceTransport(
    private val apiKey: String,
    model: String,
    detail: String,
    private val debug: Boolean,
    private val endpoint: String = OAI_URL,
    private val okHttpClient: OkHttpClient = defaultClient()
) {
    private val detectionRequests = NativeDetectionRequestFactory(model, detail)
    private val repairRequests = NativeRepairRequestFactory(model, detail)
    private var consecutiveRetryableHttpFailures = 0
    private val activeCallLock = Any()
    private val activeCalls = mutableSetOf<Call>()
    @Volatile private var closed = false

    fun detect(
        imageUrls: MutableList<String>,
        prompt: String,
        allowEarlyReject: Boolean
    ): NativeDetectionStreamVerdict {
        val body = try {
            detectionRequests.build(imageUrls, prompt).toString()
                .toRequestBody(JSON_MEDIA_TYPE)
        } finally {
            // RequestBody owns its encoded bytes; release duplicate Base64 strings before I/O.
            imageUrls.clear()
        }
        val request = authorizedRequest(body)

        return withTrackedCall(request) { call ->
            val response = try {
                call.execute()
            } catch (error: IOException) {
                throw retryableFailure("OpenAI detection connection failed", error)
            }
            if (!response.isSuccessful) {
                val failure = httpFailure(response.code, response.header("Retry-After"), repair = false)
                response.close()
                throw failure
            }
            val responseBody = response.body ?: run {
                response.close()
                throw retryableFailure("Empty response body from OpenAI")
            }
            val accumulator = NativeSseTextAccumulator()
            var earlyReject: NativeEarlyReject? = null
            var transportCompleted = false
            try {
                var rawLine: String?
                val reader = responseBody.charStream().buffered()
                while (reader.readLine().also { rawLine = it } != null) {
                    val line = rawLine?.trim() ?: continue
                    if (!line.startsWith("data:")) continue
                    val payload = line.substring(5).trim()
                    if (payload.isEmpty()) continue
                    if (payload == "[DONE]") {
                        transportCompleted = true
                        continue
                    }
                    val event = try {
                        JSONObject(payload)
                    } catch (_: Exception) {
                        continue
                    }
                    if (event.optString("type") == "response.completed") {
                        transportCompleted = true
                    }
                    if (event.optString("type") != "response.output_text.delta") continue
                    if (!accumulator.append(event.optString("delta", ""))) {
                        call.cancel()
                        throw NativeInferenceException(
                            "OpenAI detection response exceeded the 64 KiB safety limit",
                            suspendInference = true
                        )
                    }
                    if (allowEarlyReject && !debug) {
                        val rejection = NativePartialVerdictScanner.earlyReject(accumulator.snapshot())
                        if (rejection != null) {
                            earlyReject = rejection
                            call.cancel()
                            break
                        }
                    }
                }
            } catch (error: NativeInferenceException) {
                throw error
            } catch (_: IOException) {
                // A terminal event or the explicit early-reject marker below decides durability.
            } finally {
                response.close()
            }

            val text = accumulator.snapshot()
            val rawVerdict = if (earlyReject == null && transportCompleted) {
                NativeDetectionVerdictJsonParser.parse(text)
            } else null
            val completeAssessment = rawVerdict?.let(NativeDetectionAssessmentMapper::toAssessment)
            val assessment = try {
                NativeDetectionStreamCompletionPolicy.requireVerdict(
                    completeVerdict = completeAssessment,
                    intentionalEarlyReject = earlyReject?.assessment,
                    transportCompleted = transportCompleted
                )
            } catch (error: Exception) {
                throw normalizeRetryableFailure(error, "OpenAI detection response was incomplete")
            }
            consecutiveRetryableHttpFailures = 0
            val completePolicy = rawVerdict?.let(NativePotholeDecisionPolicy::evaluate)
            NativeDetectionStreamVerdict(
                assessment = assessment,
                rawVerdict = rawVerdict,
                completionMode = earlyReject?.completionMode ?: NativeDetectionCompletionMode.COMPLETE,
                observedFields = earlyReject?.observedFields
                    ?: if (rawVerdict != null) COMPLETE_DETECTION_FIELDS else emptySet(),
                rejectionReasons = earlyReject?.rejectionReasons
                    ?: completePolicy?.rejectionReasons.orEmpty()
            )
        }
    }

    fun verifyRepair(imageUrls: MutableList<String>, prompt: String): RepairModelAssessment {
        val body = try {
            repairRequests.build(imageUrls, prompt).toString().toRequestBody(JSON_MEDIA_TYPE)
        } finally {
            imageUrls.clear()
        }
        return withTrackedCall(authorizedRequest(body)) { call ->
            val response = try {
                call.execute()
            } catch (error: IOException) {
                throw retryableFailure("OpenAI repair-check connection failed", error)
            }
            if (!response.isSuccessful) {
                val failure = httpFailure(response.code, response.header("Retry-After"), repair = true)
                response.close()
                throw failure
            }
            val responseBody = response.body ?: run {
                response.close()
                throw retryableFailure("Empty repair-check response from OpenAI")
            }
            val accumulator = NativeSseTextAccumulator()
            var transportCompleted = false
            try {
                responseBody.charStream().buffered().useLines { lines ->
                    lines.forEach { raw ->
                        val line = raw.trim()
                        if (!line.startsWith("data:")) return@forEach
                        val payload = line.substring(5).trim()
                        if (payload.isEmpty()) return@forEach
                        if (payload == "[DONE]") {
                            transportCompleted = true
                            return@forEach
                        }
                        val event = try {
                            JSONObject(payload)
                        } catch (_: Exception) {
                            return@forEach
                        }
                        if (event.optString("type") == "response.completed") {
                            transportCompleted = true
                        }
                        if (event.optString("type") == "response.output_text.delta" &&
                            !accumulator.append(event.optString("delta", ""))
                        ) {
                            throw NativeInferenceException(
                                "OpenAI repair-check response exceeded the 64 KiB safety limit",
                                suspendInference = true
                            )
                        }
                    }
                }
            } catch (error: NativeInferenceException) {
                throw error
            } catch (error: IOException) {
                throw retryableFailure("OpenAI repair-check stream was interrupted", error)
            } finally {
                response.close()
            }
            val assessment = try {
                NativeStreamCompletionPolicy.requireCompleted(
                    transportCompleted,
                    "OpenAI repair-check stream was interrupted"
                )
                NativeRepairAssessmentParser.parse(accumulator.snapshot())
            } catch (error: Exception) {
                throw normalizeRetryableFailure(error, "OpenAI repair-check response was incomplete")
            }
            consecutiveRetryableHttpFailures = 0
            assessment
        }
    }

    private fun authorizedRequest(body: okhttp3.RequestBody): Request = Request.Builder()
        .url(endpoint)
        .addHeader("Authorization", "Bearer $apiKey")
        .addHeader("Content-Type", "application/json")
        .post(body)
        .build()

    private inline fun <T> withTrackedCall(request: Request, block: (Call) -> T): T {
        val call = okHttpClient.newCall(request)
        synchronized(activeCallLock) {
            if (closed) throw IOException("Detection engine is closed")
            activeCalls.add(call)
        }
        return try {
            block(call)
        } finally {
            synchronized(activeCallLock) { activeCalls.remove(call) }
        }
    }

    private fun httpFailure(code: Int, retryAfter: String?, repair: Boolean): NativeInferenceException {
        val message = when (code) {
            401 -> "OpenAI rejected the API key"
            403 -> "This API key cannot use the selected model"
            400 -> if (repair) {
                "OpenAI rejected the repair model or structured-output request (400)"
            } else {
                "OpenAI rejected the model or structured-output request (400)"
            }
            404 -> if (repair) {
                "The selected OpenAI repair model or endpoint was not found (404)"
            } else {
                "The selected OpenAI model or endpoint was not found (404)"
            }
            429 -> "OpenAI rate limit or credit exhausted"
            in 500..599 -> "OpenAI is temporarily unavailable"
            else -> if (repair) {
                "OpenAI rejected the repair check ($code)"
            } else {
                "OpenAI rejected the detection request ($code)"
            }
        }
        val retryDelay = if (NativeInferenceHttpFailurePolicy.isTransient(code)) {
            NativeInferenceHttpFailurePolicy.retryDelayMs(
                code,
                retryAfter,
                consecutiveRetryableHttpFailures++
            )
        } else null
        return NativeInferenceException(
            message,
            suspendInference = NativeInferenceHttpFailurePolicy.shouldSuspendInference(code),
            retryAfterMs = retryDelay
        )
    }

    private fun retryableFailure(message: String, cause: Throwable? = null): NativeInferenceException {
        val retryDelay = NativeInferenceHttpFailurePolicy.retryDelayMs(
            code = 0,
            retryAfterHeader = null,
            consecutiveFailure = consecutiveRetryableHttpFailures++
        )
        return NativeInferenceException(message, retryAfterMs = retryDelay, cause = cause)
    }

    private fun normalizeRetryableFailure(error: Throwable, message: String): NativeInferenceException {
        if (error is NativeInferenceException &&
            (error.fatal || error.suspendInference || error.retryAfterMs != null)
        ) return error
        return retryableFailure(error.message?.takeIf(String::isNotBlank) ?: message, error)
    }

    fun close() {
        val calls = synchronized(activeCallLock) {
            closed = true
            activeCalls.toList()
        }
        var firstFailure: Throwable? = null
        fun attempt(block: () -> Unit) {
            runCatching(block).onFailure { error ->
                if (firstFailure == null) firstFailure = error
            }
        }
        calls.forEach { call -> attempt(call::cancel) }
        attempt(okHttpClient.dispatcher::cancelAll)
        attempt(okHttpClient.connectionPool::evictAll)
        firstFailure?.let { throw it }
    }

    companion object {
        private const val OAI_URL = "https://api.openai.com/v1/responses"
        private val JSON_MEDIA_TYPE = "application/json".toMediaType()
        private val COMPLETE_DETECTION_FIELDS = setOf(
            "is_pothole", "looks_like_speed_breaker", "image_quality", "surface_type",
            "on_drivable_surface", "has_localized_cavity", "has_broken_edge_or_rim",
            "has_depth_or_surface_loss", "temporal_consistency", "size", "description"
        )

        private fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .build()
    }
}
