package com.gauravsen.potholereporter.drive

import android.content.Context
import android.graphics.Bitmap
import com.gauravsen.potholereporter.db.EventSightingEntity
import com.gauravsen.potholereporter.db.ReportEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.*
import java.util.concurrent.TimeUnit
import java.util.regex.Pattern

data class AssessmentResult(
    val reportable: Boolean,
    val assessment: String,
    val imageQuality: String,
    val damageType: String,
    val onDrivableSurface: Boolean,
    val hasBrokenEdgeOrRim: Boolean,
    val hasDepthOrSurfaceLoss: Boolean,
    val temporalConsistency: String,
    val size: String?,
    val description: String,
    val decision: String // "accept", "review", "reject"
)

data class InferenceOutcome(
    val analyzed: Boolean,
    val accepted: Boolean,
    val decision: String,
    val assessment: AssessmentResult?,
    val reportEntity: ReportEntity? = null,
    val sightings: List<EventSightingEntity> = emptyList()
)

class NativeInferenceException(
    message: String,
    val fatal: Boolean = false
) : IOException(message)

class NativeInferenceEngine(
    private val context: Context,
    private val apiKey: String,
    private val model: String = "gpt-5-mini",
    private val detail: String = "high",
    private val language: String = "en",
    private val debug: Boolean = false
) {
    private val okHttpClient = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    private val nominatimMutex = Mutex()
    private var lastNominatimTimeMs = 0L

    companion object {
        private const val OAI_URL = "https://api.openai.com/v1/responses"
        private const val NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
        private const val PROMPT_VERSION = "road-damage-v3"
        private const val SCHEMA_VERSION = 3

        private val REPORTABLE_RE = Pattern.compile("\"reportable\"\\s*:\\s*(true|false)")
        private val ASSESSMENT_RE = Pattern.compile("\"assessment\"\\s*:\\s*\"(clear|probable|uncertain|absent)\"")
        private val QUALITY_RE = Pattern.compile("\"image_quality\"\\s*:\\s*\"(usable|degraded|unusable)\"")
        private val DAMAGE_RE = Pattern.compile("\"damage_type\"\\s*:\\s*\"(pothole_cavity|failed_patch|surface_breakup|rut_or_depression|other_road_damage|none)\"")
        private val ROAD_RE = Pattern.compile("\"on_drivable_surface\"\\s*:\\s*(true|false)")
        private val EDGE_RE = Pattern.compile("\"has_broken_edge_or_rim\"\\s*:\\s*(true|false)")
        private val DEPTH_RE = Pattern.compile("\"has_depth_or_surface_loss\"\\s*:\\s*(true|false)")
        private val TEMPORAL_RE = Pattern.compile("\"temporal_consistency\"\\s*:\\s*\"(consistent|single_view|inconsistent|not_applicable)\"")

        private const val DETECT_PROMPT =
            "You are a structural road-maintenance surveyor inspecting road surfaces in India. " +
            "Evaluate whether the travelled roadway in the provided dashcam images has reportable road damage (pothole cavity, failed patch, surface breakup, or rut). " +
            "Ignore manhole covers, shadows, paint, expansion joints, water puddles with sound asphalt underneath, and minor surface discoloration. " +
            "Return structured assessment according to the schema."
    }

    suspend fun analyzeBurst(
        burstFrames: List<BurstFrame>,
        primaryIndex: Int,
        lat: Double?,
        lng: Double?,
        driveId: String,
        captureSeq: Int,
        capturedAtMs: Long,
        sourceOffsetMs: Long,
        gpsAccuracy: Float?,
        speedMps: Float?,
        heading: Float?
    ): InferenceOutcome = withContext(Dispatchers.IO) {
        if (burstFrames.isEmpty()) {
            return@withContext InferenceOutcome(analyzed = false, accepted = false, decision = "reject", assessment = null)
        }

        val primaryFrame = burstFrames.getOrElse(primaryIndex) { burstFrames[0] }
        val contextDataUrl = FrameQualityEvaluator.prepareContextDataUrl(primaryFrame.bitmap, 768, 82)
        val roadDataUrls = burstFrames.map { FrameQualityEvaluator.prepareRoadBandDataUrl(it.bitmap, 1024, 85, true) }

        val imageInputs = mutableListOf(contextDataUrl)
        imageInputs.addAll(roadDataUrls)

        val langSuffix = when (language) {
            "kn" -> "\n- Write the description field in formal Kannada (ಕನ್ನಡ ಭಾಷೆಯಲ್ಲಿ ಬರೆಯಿರಿ)."
            "mr" -> "\n- Write the description field in clear formal Marathi (मराठी भाषेत लिहा)."
            "bn" -> "\n- Write the description field in clear formal Bengali (পরিষ্কার, প্রমিত বাংলায় লিখুন)."
            else -> ""
        }
        val layoutNote = "\n- Capture layout: image 1 is full-frame context from the sharpest burst frame. Images 2-${imageInputs.size} are lower-road crops in chronological order; the sharpest crop is chronological frame ${primaryIndex + 1}."
        val fullPrompt = DETECT_PROMPT + layoutNote + langSuffix

        val assessment = executeOaiStreaming(imageInputs, fullPrompt)
        val decision = assessment.decision
        val accepted = decision == "accept"

        if (!accepted) {
            return@withContext InferenceOutcome(
                analyzed = true,
                accepted = false,
                decision = decision,
                assessment = assessment
            )
        }

        // Routing and reverse geocoding remain in the mature WebView engine. Repeating
        // them here would double-call Nominatim and could route against two snapshots.
        val shortAddress = "Road coordinates (${lat?.let { "%.5f".format(it) }}, ${lng?.let { "%.5f".format(it) }})"

        // Save evidence image to disk
        val photoFile = saveEvidenceImage(primaryFrame.bitmap, driveId, captureSeq)
        val photoThumbUrl = roadDataUrls.getOrElse(primaryIndex) { roadDataUrls[0] }

        val sourceEventKey = "live:$driveId:$captureSeq"

        val reportEntity = ReportEntity(
            createdAt = System.currentTimeMillis() / 1000,
            lat = lat,
            lng = lng,
            address = shortAddress,
            photoPath = photoFile.absolutePath,
            photoDataUrl = photoThumbUrl,
            photoFullPath = photoFile.absolutePath,
            isReportable = if (assessment.reportable) 1 else 0,
            isPothole = if (assessment.damageType == "pothole_cavity") 1 else 0,
            damageType = assessment.damageType,
            assessment = assessment.assessment,
            imageQuality = assessment.imageQuality,
            onDrivableSurface = assessment.onDrivableSurface,
            hasBrokenEdgeOrRim = assessment.hasBrokenEdgeOrRim,
            hasDepthOrSurfaceLoss = assessment.hasDepthOrSurfaceLoss,
            temporalConsistency = assessment.temporalConsistency,
            size = assessment.size,
            decision = decision,
            description = assessment.description,
            emailSubject = null,
            emailBody = null,
            status = "draft",
            detectionModel = model,
            imageDetail = detail,
            promptVersion = PROMPT_VERSION,
            schemaVersion = SCHEMA_VERSION,
            evidenceCount = imageInputs.size,
            driveId = driveId,
            captureSource = "drive_live",
            sourceEventKey = sourceEventKey,
            sourceEventKeysJson = JSONArray(listOf(sourceEventKey)).toString(),
            capturedAt = capturedAtMs / 1000,
            sourceOffsetS = sourceOffsetMs / 1000.0,
            gpsAccuracy = gpsAccuracy,
            speedMps = speedMps,
            heading = heading,
            primaryFrameIndex = primaryIndex,
            debugCapture = debug,
            dedupeEligible = true,
            sightingDriveIdsJson = JSONArray(listOf(driveId)).toString(),
            seenCount = 1,
            lastSeenAt = capturedAtMs / 1000,
            syncedToWeb = false
        )

        val sighting = EventSightingEntity(
            reportId = 0,
            driveId = driveId,
            lat = lat,
            lng = lng,
            sourceOffsetS = sourceOffsetMs / 1000.0,
            capturedAt = capturedAtMs / 1000,
            gpsAccuracy = gpsAccuracy,
            speedMps = speedMps,
            heading = heading,
            sourceEventKey = sourceEventKey
        )

        InferenceOutcome(
            analyzed = true,
            accepted = true,
            decision = decision,
            assessment = assessment,
            reportEntity = reportEntity,
            sightings = listOf(sighting)
        )
    }

    private fun executeOaiStreaming(
        imageUrls: List<String>,
        prompt: String
    ): AssessmentResult {
        val requestJson = buildRequestBody(imageUrls, prompt)
        val body = requestJson.toString().toRequestBody("application/json".toMediaType())

        val request = Request.Builder()
            .url(OAI_URL)
            .addHeader("Authorization", "Bearer $apiKey")
            .addHeader("Content-Type", "application/json")
            .post(body)
            .build()

        val call = okHttpClient.newCall(request)
        val response = call.execute()

        if (!response.isSuccessful) {
            val code = response.code
            response.close()
            val message = when (code) {
                401 -> "OpenAI rejected the API key"
                403 -> "This API key cannot use the selected model"
                429 -> "OpenAI rate limit or credit exhausted"
                in 500..599 -> "OpenAI is temporarily unavailable"
                else -> "OpenAI rejected the detection request ($code)"
            }
            throw NativeInferenceException(message, fatal = code == 401 || code == 403 || code == 429)
        }

        val responseBody = response.body ?: run {
            response.close()
            throw NativeInferenceException("Empty response body from OpenAI")
        }
        val reader = responseBody.charStream().buffered()
        val textBuilder = StringBuilder()
        var earlyRejected = false

        try {
            var line: String?
            while (reader.readLine().also { line = it } != null) {
                val l = line?.trim() ?: continue
                if (!l.startsWith("data:")) continue
                val payload = l.substring(5).trim()
                if (payload.isEmpty() || payload == "[DONE]") continue

                try {
                    val ev = JSONObject(payload)
                    val type = ev.optString("type")
                    if (type == "response.output_text.delta") {
                        val delta = ev.optString("delta", "")
                        textBuilder.append(delta)

                        if (!debug && peekReject(textBuilder.toString())) {
                            earlyRejected = true
                            call.cancel()
                            break
                        }
                    }
                } catch (_: Exception) {}
            }
        } catch (_: IOException) {
            // Stream cancelled or aborted
        } finally {
            response.close()
        }

        return if (earlyRejected) {
            rejectedVerdict(textBuilder.toString())
        } else {
            parseCompleteVerdict(textBuilder.toString())
        }
    }

    private fun buildRequestBody(imageUrls: List<String>, prompt: String): JSONObject {
        val contentArray = JSONArray()
        for (url in imageUrls) {
            val item = JSONObject()
            item.put("type", "input_image")
            item.put("image_url", url)
            item.put("detail", detail)
            contentArray.put(item)
        }
        val textItem = JSONObject()
        textItem.put("type", "input_text")
        textItem.put("text", "$prompt\n\nThe ${imageUrls.size} supplied image(s) are ordered exactly as labelled by the capture pipeline.")
        contentArray.put(textItem)

        val userMessage = JSONObject()
        userMessage.put("role", "user")
        userMessage.put("content", contentArray)

        val inputArray = JSONArray()
        inputArray.put(userMessage)

        val schemaObj = JSONObject(
            """
            {
              "type": "object",
              "additionalProperties": false,
              "required": ["reportable", "assessment", "image_quality", "damage_type", "on_drivable_surface", "has_broken_edge_or_rim", "has_depth_or_surface_loss", "temporal_consistency", "size", "description"],
              "properties": {
                "reportable": { "type": "boolean" },
                "assessment": { "type": "string", "enum": ["clear", "probable", "uncertain", "absent"] },
                "image_quality": { "type": "string", "enum": ["usable", "degraded", "unusable"] },
                "damage_type": { "type": "string", "enum": ["pothole_cavity", "failed_patch", "surface_breakup", "rut_or_depression", "other_road_damage", "none"] },
                "on_drivable_surface": { "type": "boolean" },
                "has_broken_edge_or_rim": { "type": "boolean" },
                "has_depth_or_surface_loss": { "type": "boolean" },
                "temporal_consistency": { "type": "string", "enum": ["consistent", "single_view", "inconsistent", "not_applicable"] },
                "size": { "type": ["string", "null"], "enum": ["small", "medium", "large", null] },
                "description": { "type": "string" }
              }
            }
            """.trimIndent()
        )

        val textFormat = JSONObject()
        textFormat.put("type", "json_schema")
        textFormat.put("name", "road_damage_assessment")
        textFormat.put("strict", true)
        textFormat.put("schema", schemaObj)
        val textConfig = JSONObject()
        textConfig.put("format", textFormat)
        textConfig.put("verbosity", "low")

        val req = JSONObject()
        req.put("model", model)
        req.put("input", inputArray)
        req.put("text", textConfig)
        req.put("stream", true)
        req.put("store", false)

        val reasoningObj = JSONObject()
        reasoningObj.put("effort", if (model == "gpt-5.6") "none" else "minimal")
        req.put("reasoning", reasoningObj)

        return req
    }

    private fun peekReject(text: String): Boolean {
        val repMatcher = REPORTABLE_RE.matcher(text)
        if (!repMatcher.find()) return false
        if (repMatcher.group(1) == "false") return true

        val assessMatcher = ASSESSMENT_RE.matcher(text)
        val qualMatcher = QUALITY_RE.matcher(text)
        val dmgMatcher = DAMAGE_RE.matcher(text)
        val roadMatcher = ROAD_RE.matcher(text)
        val edgeMatcher = EDGE_RE.matcher(text)
        val depthMatcher = DEPTH_RE.matcher(text)
        val tempMatcher = TEMPORAL_RE.matcher(text)

        if (!assessMatcher.find() || !qualMatcher.find() || !dmgMatcher.find() ||
            !roadMatcher.find() || !edgeMatcher.find() || !depthMatcher.find() || !tempMatcher.find()
        ) {
            return false
        }

        val reportable = repMatcher.group(1) == "true"
        val assessment = assessMatcher.group(1) ?: "absent"
        val imageQuality = qualMatcher.group(1) ?: "unusable"
        val damageType = dmgMatcher.group(1) ?: "none"
        val onRoad = roadMatcher.group(1) == "true"
        val hasEdge = edgeMatcher.group(1) == "true"
        val hasDepth = depthMatcher.group(1) == "true"
        val temporal = tempMatcher.group(1) ?: "inconsistent"

        val dec = evaluateDecision(reportable, assessment, imageQuality, damageType, onRoad, hasEdge, hasDepth, temporal)
        return dec != "accept"
    }

    private fun evaluateDecision(
        reportable: Boolean,
        assessment: String,
        imageQuality: String,
        damageType: String,
        onDrivableSurface: Boolean,
        hasBrokenEdgeOrRim: Boolean,
        hasDepthOrSurfaceLoss: Boolean,
        temporalConsistency: String
    ): String {
        if (!reportable || damageType == "none" || !onDrivableSurface) return "reject"
        if (assessment == "absent") return "reject"
        if (imageQuality == "unusable" || assessment == "uncertain" || temporalConsistency == "inconsistent") return "review"
        if (assessment != "clear" && assessment != "probable") return "review"
        if (!hasBrokenEdgeOrRim && !hasDepthOrSurfaceLoss) return "review"
        return "accept"
    }

    private fun rejectedVerdict(text: String): AssessmentResult {
        val repMatcher = REPORTABLE_RE.matcher(text)
        val assessMatcher = ASSESSMENT_RE.matcher(text)
        val qualMatcher = QUALITY_RE.matcher(text)
        val dmgMatcher = DAMAGE_RE.matcher(text)
        val roadMatcher = ROAD_RE.matcher(text)
        val edgeMatcher = EDGE_RE.matcher(text)
        val depthMatcher = DEPTH_RE.matcher(text)
        val tempMatcher = TEMPORAL_RE.matcher(text)

        val reportable = repMatcher.find() && repMatcher.group(1) == "true"
        val assessment = if (assessMatcher.find()) assessMatcher.group(1)!! else "absent"
        val imageQuality = if (qualMatcher.find()) qualMatcher.group(1)!! else "usable"
        val damageType = if (dmgMatcher.find()) dmgMatcher.group(1)!! else "none"
        val onRoad = roadMatcher.find() && roadMatcher.group(1) == "true"
        val hasEdge = edgeMatcher.find() && edgeMatcher.group(1) == "true"
        val hasDepth = depthMatcher.find() && depthMatcher.group(1) == "true"
        val temporal = if (tempMatcher.find()) tempMatcher.group(1)!! else "not_applicable"

        val dec = evaluateDecision(reportable, assessment, imageQuality, damageType, onRoad, hasEdge, hasDepth, temporal)
        return AssessmentResult(
            reportable = reportable,
            assessment = assessment,
            imageQuality = imageQuality,
            damageType = damageType,
            onDrivableSurface = onRoad,
            hasBrokenEdgeOrRim = hasEdge,
            hasDepthOrSurfaceLoss = hasDepth,
            temporalConsistency = temporal,
            size = null,
            description = "Road damage not meeting reporting criteria.",
            decision = dec
        )
    }

    private fun parseCompleteVerdict(text: String): AssessmentResult {
        try {
            val json = JSONObject(text)
            val reportable = json.optBoolean("reportable", false)
            val assessment = json.optString("assessment", "absent")
            val imageQuality = json.optString("image_quality", "usable")
            val damageType = json.optString("damage_type", "none")
            val onRoad = json.optBoolean("on_drivable_surface", false)
            val hasEdge = json.optBoolean("has_broken_edge_or_rim", false)
            val hasDepth = json.optBoolean("has_depth_or_surface_loss", false)
            val temporal = json.optString("temporal_consistency", "not_applicable")
            val size = if (json.has("size") && !json.isNull("size")) json.getString("size") else null
            val description = json.optString("description", "")

            val dec = evaluateDecision(reportable, assessment, imageQuality, damageType, onRoad, hasEdge, hasDepth, temporal)
            return AssessmentResult(
                reportable = reportable,
                assessment = assessment,
                imageQuality = imageQuality,
                damageType = damageType,
                onDrivableSurface = onRoad,
                hasBrokenEdgeOrRim = hasEdge,
                hasDepthOrSurfaceLoss = hasDepth,
                temporalConsistency = temporal,
                size = size,
                description = description,
                decision = dec
            )
        } catch (_: Exception) {
            return rejectedVerdict(text)
        }
    }

    private suspend fun reverseGeocode(lat: Double, lng: Double): String? = withContext(Dispatchers.IO) {
        nominatimMutex.withLock {
            val now = System.currentTimeMillis()
            val remaining = 1100L - (now - lastNominatimTimeMs)
            if (remaining > 0) {
                delay(remaining)
            }
            lastNominatimTimeMs = System.currentTimeMillis()

            try {
                val url = "$NOMINATIM_URL?lat=$lat&lon=$lng&format=jsonv2&zoom=17&addressdetails=1&accept-language=en"
                val request = Request.Builder()
                    .url(url)
                    .addHeader("User-Agent", "PotholeReporter/1.20 (Android Native FGS)")
                    .build()

                okHttpClient.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) return@withContext null
                    val bodyStr = response.body?.string() ?: return@withContext null
                    val json = JSONObject(bodyStr)
                    val addressObj = json.optJSONObject("address") ?: return@withContext if (json.has("display_name")) json.getString("display_name") else null

                    val parts = mutableListOf<String>()
                    val road = addressObj.optString("road", "").ifEmpty {
                        addressObj.optString("residential", "").ifEmpty {
                            addressObj.optString("footway", "")
                        }
                    }
                    if (road.isNotEmpty()) parts.add(road)

                    val sub = addressObj.optString("suburb", "").ifEmpty {
                        addressObj.optString("neighbourhood", "")
                    }
                    if (sub.isNotEmpty()) parts.add(sub)

                    val city = addressObj.optString("city", "").ifEmpty {
                        addressObj.optString("town", "").ifEmpty {
                            addressObj.optString("municipality", "")
                        }
                    }
                    if (city.isNotEmpty()) parts.add(city)

                    val pc = addressObj.optString("postcode", "")
                    if (pc.isNotEmpty()) parts.add(pc)

                    if (parts.isNotEmpty()) parts.joinToString(", ") else if (json.has("display_name")) json.getString("display_name") else null
                }
            } catch (_: Exception) {
                null
            }
        }
    }

    private fun draftEmail(
        assessment: AssessmentResult,
        lat: Double?,
        lng: Double?,
        address: String
    ): Pair<String, String> {
        val typeName = when (assessment.damageType) {
            "pothole_cavity" -> "Pothole"
            "failed_patch" -> "Failed Asphalt Patch"
            "surface_breakup" -> "Road Surface Breakup"
            "rut_or_depression" -> "Road Rut / Depression"
            else -> "Road Damage"
        }

        val subject = "Urgent: $typeName at $address"
        val sdf = SimpleDateFormat("dd MMM yyyy, HH:mm", Locale.ENGLISH)
        val dateStr = sdf.format(Date())
        val coordStr = if (lat != null && lng != null) "%.6f, %.6f".format(lat, lng) else "Unknown"
        val mapLink = if (lat != null && lng != null) "https://www.google.com/maps/search/?api=1&query=$lat,$lng" else "N/A"

        val body = """
            Dear Road Maintenance Authority,

            I would like to report a hazardous road defect observed at $address.

            Details:
            - Issue Type: $typeName
            - Location: $address
            - GPS Coordinates: $coordStr
            - Google Maps Link: $mapLink
            - Detected on: $dateStr
            - Observation: ${assessment.description}

            Please inspect and schedule necessary repairs to prevent accidents.

            Sincerely,
            A Concerned Citizen
            (Generated via Pothole Reporter)
        """.trimIndent()

        return Pair(subject, body)
    }

    private fun saveEvidenceImage(bitmap: Bitmap, driveId: String, seq: Int): File {
        val dir = File(context.filesDir, "reports/$driveId")
        if (!dir.exists()) dir.mkdirs()
        val file = File(dir, "evidence_${seq}_${System.currentTimeMillis()}.jpg")
        FileOutputStream(file).use { out ->
            bitmap.compress(Bitmap.CompressFormat.JPEG, 92, out)
        }
        return file
    }

    fun close() {
        okHttpClient.dispatcher.cancelAll()
        okHttpClient.connectionPool.evictAll()
    }
}
