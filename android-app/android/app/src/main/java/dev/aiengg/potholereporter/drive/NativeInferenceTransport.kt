package dev.aiengg.potholereporter.drive

import okhttp3.Call
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.ResponseBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

internal class NativeInferenceException(
    message: String,
    val suspendInference: Boolean = false,
    val retryAfterMs: Long? = null,
    cause: Throwable? = null
) : IOException(message, cause)

/** Owns OpenAI HTTP/SSE lifecycle; camera, storage, and report logic stay elsewhere. */
internal class NativeInferenceTransport(
    private val apiKey: String,
    private val model: String,
    private val detail: String,
    private val debug: Boolean,
    private val endpoint: String = OAI_URL,
    private val okHttpClient: OkHttpClient = defaultClient()
) {
    private var consecutiveRetryableFailures = 0
    private val activeCalls = mutableSetOf<Call>()
    private val activeCallsLock = Any()
    @Volatile private var closed = false

    fun detect(
        imageUrls: MutableList<String>,
        prompt: String,
        allowEarlyReject: Boolean
    ): AssessmentResult {
        val body = try {
            buildDetectionRequest(model, detail, imageUrls, prompt)
                .toString()
                .toRequestBody(JSON_MEDIA_TYPE)
        } finally {
            // RequestBody owns encoded bytes now; release duplicate Base64 strings before I/O.
            imageUrls.clear()
        }

        return withTrackedCall(authorizedRequest(body)) { call ->
            val response = try {
                call.execute()
            } catch (error: IOException) {
                throw retryableFailure("OpenAI detection connection failed", error)
            }
            response.use {
                if (!response.isSuccessful) {
                    throw httpFailure(
                        response.code,
                        response.header("Retry-After"),
                        RequestKind.DETECTION
                    )
                }
                val responseBody = response.body
                    ?: throw retryableFailure("Empty response body from OpenAI")
                var earlyRejection: DetectionRejectionReason? = null
                val stream = try {
                    readSse(call, responseBody, "OpenAI detection response") { text ->
                        if (!allowEarlyReject || debug) return@readSse false
                        findEarlyRejection(text)?.let {
                            earlyRejection = it
                            true
                        } ?: false
                    }
                } catch (error: NativeInferenceException) {
                    throw error
                } catch (error: IOException) {
                    throw retryableFailure("OpenAI detection stream was interrupted", error)
                }

                val assessment = try {
                    completeDetectionAssessment(
                        text = stream.text,
                        streamCompleted = stream.completed,
                        earlyRejection = earlyRejection
                    )
                } catch (error: Exception) {
                    throw normalizeRetryableFailure(
                        error,
                        "OpenAI detection response was incomplete"
                    )
                }
                consecutiveRetryableFailures = 0
                assessment
            }
        }
    }

    fun verifyRepair(imageUrls: MutableList<String>, prompt: String): RepairModelAssessment {
        val body = try {
            buildRepairRequest(model, detail, imageUrls, prompt)
                .toString()
                .toRequestBody(JSON_MEDIA_TYPE)
        } finally {
            imageUrls.clear()
        }

        return withTrackedCall(authorizedRequest(body)) { call ->
            val response = try {
                call.execute()
            } catch (error: IOException) {
                throw retryableFailure("OpenAI repair-check connection failed", error)
            }
            response.use {
                if (!response.isSuccessful) {
                    throw httpFailure(
                        response.code,
                        response.header("Retry-After"),
                        RequestKind.REPAIR
                    )
                }
                val responseBody = response.body
                    ?: throw retryableFailure("Empty repair-check response from OpenAI")
                val stream = try {
                    readSse(call, responseBody, "OpenAI repair-check response")
                } catch (error: NativeInferenceException) {
                    throw error
                } catch (error: IOException) {
                    throw retryableFailure("OpenAI repair-check stream was interrupted", error)
                }

                val assessment = try {
                    if (!stream.completed) {
                        throw NativeInferenceException("OpenAI repair-check stream was interrupted")
                    }
                    NativeRepairContract.parseAssessment(stream.text)
                } catch (error: Exception) {
                    throw normalizeRetryableFailure(
                        error,
                        "OpenAI repair-check response was incomplete"
                    )
                }
                consecutiveRetryableFailures = 0
                assessment
            }
        }
    }

    private fun readSse(
        call: Call,
        responseBody: ResponseBody,
        responseName: String,
        stopWhen: ((String) -> Boolean)? = null
    ): StreamText {
        val output = NativeSseTextAccumulator()
        var completed = false
        var intentionallyStopped = false
        try {
            responseBody.charStream().buffered().use { reader ->
                while (true) {
                    val line = reader.readLine()?.trim() ?: break
                    if (!line.startsWith("data:")) continue
                    val payload = line.substring(5).trim()
                    if (payload.isEmpty()) continue
                    if (payload == "[DONE]") {
                        completed = true
                        continue
                    }

                    val event = try {
                        JSONObject(payload)
                    } catch (_: Exception) {
                        continue
                    }
                    if (event.optString("type") == "response.completed") completed = true
                    if (event.optString("type") != "response.output_text.delta") continue
                    if (!output.append(event.optString("delta", ""))) {
                        call.cancel()
                        throw NativeInferenceException(
                            "$responseName exceeded the 64 KiB safety limit",
                            suspendInference = true
                        )
                    }
                    if (stopWhen?.invoke(output.snapshot()) == true) {
                        intentionallyStopped = true
                        call.cancel()
                        break
                    }
                }
            }
        } catch (error: NativeInferenceException) {
            // Safety-limit and validation failures are intentional, even after a terminal marker.
            throw error
        } catch (error: IOException) {
            // Completion is durable once the terminal marker or an intentional hard-negative
            // arrives. A trailing disconnect (including one caused by cancel()) cannot erase it.
            if (!completed && !intentionallyStopped) throw error
        }
        return StreamText(output.snapshot(), completed)
    }

    private fun authorizedRequest(body: okhttp3.RequestBody): Request = Request.Builder()
        .url(endpoint)
        .addHeader("Authorization", "Bearer $apiKey")
        .addHeader("Content-Type", "application/json")
        .post(body)
        .build()

    private inline fun <T> withTrackedCall(request: Request, block: (Call) -> T): T {
        val call = okHttpClient.newCall(request)
        synchronized(activeCallsLock) {
            if (closed) throw IOException("Detection engine is closed")
            activeCalls.add(call)
        }
        return try {
            block(call)
        } finally {
            synchronized(activeCallsLock) { activeCalls.remove(call) }
        }
    }

    private fun httpFailure(
        code: Int,
        retryAfter: String?,
        kind: RequestKind
    ): NativeInferenceException {
        val repair = kind == RequestKind.REPAIR
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
        val retryDelay = if (isTransientInferenceFailure(code)) {
            inferenceRetryDelay(code, retryAfter, consecutiveRetryableFailures++)
        } else {
            null
        }
        return NativeInferenceException(
            message,
            suspendInference = !isTransientInferenceFailure(code),
            retryAfterMs = retryDelay
        )
    }

    private fun retryableFailure(
        message: String,
        cause: Throwable? = null
    ): NativeInferenceException = NativeInferenceException(
        message,
        retryAfterMs = inferenceRetryDelay(0, null, consecutiveRetryableFailures++),
        cause = cause
    )

    private fun normalizeRetryableFailure(
        error: Throwable,
        fallbackMessage: String
    ): NativeInferenceException {
        if (error is NativeInferenceException &&
            (error.suspendInference || error.retryAfterMs != null)
        ) return error
        return retryableFailure(error.message?.takeIf(String::isNotBlank) ?: fallbackMessage, error)
    }

    fun close() {
        val calls = synchronized(activeCallsLock) {
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

    private enum class RequestKind { DETECTION, REPAIR }

    companion object {
        private const val OAI_URL = "https://api.openai.com/v1/responses"
        private val JSON_MEDIA_TYPE = "application/json".toMediaType()

        private fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .build()
    }
}

private data class StreamText(val text: String, val completed: Boolean)

/** Bounded text buffer for untrusted streamed model output. */
internal class NativeSseTextAccumulator(
    private val maxUtf8Bytes: Int = MAX_UTF8_BYTES
) {
    private val text = StringBuilder(minOf(maxUtf8Bytes.coerceAtLeast(1), 4 * 1024))
    private var utf8Bytes = 0

    init {
        require(maxUtf8Bytes > 0) { "SSE text limit must be positive" }
    }

    fun append(delta: String): Boolean {
        val deltaBytes = delta.toByteArray(Charsets.UTF_8).size
        if (deltaBytes > maxUtf8Bytes - utf8Bytes) return false
        text.append(delta)
        utf8Bytes += deltaBytes
        return true
    }

    fun snapshot(): String = text.toString()

    companion object {
        const val MAX_UTF8_BYTES = 64 * 1024
    }
}

internal fun isTransientInferenceFailure(code: Int): Boolean =
    code == 0 || code in setOf(408, 409, 425, 429) || code in 500..599

internal fun inferenceRetryDelay(
    code: Int,
    retryAfterHeader: String?,
    consecutiveFailure: Int
): Long? {
    if (!isTransientInferenceFailure(code)) return null
    val maxDelayMs = 60_000L
    val headerDelay = retryAfterHeader?.trim()?.toLongOrNull()
        ?.takeIf { it >= 0L }
        ?.let { seconds ->
            if (seconds > maxDelayMs / 1_000L) maxDelayMs else seconds * 1_000L
        }
    val baseDelay = when (code) {
        429 -> 5_000L
        0 -> 10_000L
        else -> 2_000L
    }
    val exponentialDelay = (baseDelay * (1L shl consecutiveFailure.coerceIn(0, 5)))
        .coerceAtMost(maxDelayMs)
    return maxOf(headerDelay ?: 0L, exponentialDelay)
}
