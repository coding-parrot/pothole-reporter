package dev.aiengg.potholereporter.drive

import android.content.Context
import android.graphics.Bitmap
import android.util.Base64 as AndroidBase64
import dev.aiengg.potholereporter.db.RepairTargetEntity
import dev.aiengg.potholereporter.db.EventSightingEntity
import dev.aiengg.potholereporter.db.ReportEntity
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

/** Parses only a complete structured detector response. Missing or mistyped fields are
 * never filled with absence-friendly defaults, because that result can gate a repair check. */
internal object NativeCompleteVerdictParser {
    private val assessments = setOf("clear", "probable", "uncertain", "absent")
    private val imageQualities = setOf("usable", "degraded", "unusable")
    private val damageTypes = setOf(
        "pothole_cavity", "failed_patch", "surface_breakup", "rut_or_depression",
        "other_road_damage", "none"
    )
    private val temporalValues = setOf(
        "consistent", "single_view", "inconsistent", "not_applicable"
    )
    private val sizes = setOf("small", "medium", "large")

    fun parse(text: String): AssessmentResult? {
        return try {
            val json = JSONObject(text)
            val sizeValue = json.get("size")
            fromFields(
                mapOf(
                    "reportable" to json.get("reportable"),
                    "assessment" to json.get("assessment"),
                    "image_quality" to json.get("image_quality"),
                    "damage_type" to json.get("damage_type"),
                    "on_drivable_surface" to json.get("on_drivable_surface"),
                    "has_broken_edge_or_rim" to json.get("has_broken_edge_or_rim"),
                    "has_depth_or_surface_loss" to json.get("has_depth_or_surface_loss"),
                    "temporal_consistency" to json.get("temporal_consistency"),
                    "size" to if (sizeValue === JSONObject.NULL) null else sizeValue,
                    "description" to json.get("description")
                )
            )
        } catch (_: Exception) {
            null
        }
    }

    internal fun fromFields(fields: Map<String, Any?>): AssessmentResult? {
        val required = setOf(
            "reportable", "assessment", "image_quality", "damage_type",
            "on_drivable_surface", "has_broken_edge_or_rim", "has_depth_or_surface_loss",
            "temporal_consistency", "size", "description"
        )
        if (!fields.keys.containsAll(required)) return null
        val reportable = fields["reportable"] as? Boolean ?: return null
        val onRoad = fields["on_drivable_surface"] as? Boolean ?: return null
        val brokenEdge = fields["has_broken_edge_or_rim"] as? Boolean ?: return null
        val depth = fields["has_depth_or_surface_loss"] as? Boolean ?: return null
        val assessment = fields["assessment"] as? String ?: return null
        val imageQuality = fields["image_quality"] as? String ?: return null
        val damageType = fields["damage_type"] as? String ?: return null
        val temporal = fields["temporal_consistency"] as? String ?: return null
        val description = fields["description"] as? String ?: return null
        if (assessment !in assessments || imageQuality !in imageQualities ||
            damageType !in damageTypes || temporal !in temporalValues) return null
        val sizeValue = fields["size"]
        if (sizeValue != null && (sizeValue !is String || sizeValue !in sizes)) return null
        val size = sizeValue as? String

        return AssessmentResult(
            reportable = reportable,
            assessment = assessment,
            imageQuality = imageQuality,
            damageType = damageType,
            onDrivableSurface = onRoad,
            hasBrokenEdgeOrRim = brokenEdge,
            hasDepthOrSurfaceLoss = depth,
            temporalConsistency = temporal,
            size = size,
            description = description,
            decision = decisionFor(
                reportable, assessment, imageQuality, damageType, onRoad, brokenEdge, depth,
                temporal
            )
        )
    }

    internal fun decisionFor(
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
        if (imageQuality == "unusable" || assessment == "uncertain" ||
            temporalConsistency == "inconsistent") return "review"
        if (assessment != "clear" && assessment != "probable") return "review"
        if (!hasBrokenEdgeOrRim && !hasDepthOrSurfaceLoss) return "review"
        return "accept"
    }
}

data class InferenceOutcome(
    val analyzed: Boolean,
    val accepted: Boolean,
    val decision: String,
    val assessment: AssessmentResult?,
    val reportEntity: ReportEntity? = null,
    val sightings: List<EventSightingEntity> = emptyList()
)

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

private data class RepairModelAssessment(
    val currentCondition: String,
    val assessment: String,
    val imageQuality: String,
    val sameLocationVisible: Boolean,
    val completedRepairVisible: Boolean,
    val description: String
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
        const val REPAIR_PROMPT_VERSION = "road-repair-v1"
        const val REPAIR_SCHEMA_VERSION = 1

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

        private const val REPAIR_PROMPT =
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
        heading: Float?,
        requireCompleteVerdict: Boolean = false
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

        val assessment = executeOaiStreaming(
            imageInputs, fullPrompt, allowEarlyReject = !requireCompleteVerdict
        )
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

    /**
     * Runs only after the ordinary detector produced a complete, usable absence verdict.
     * The historical image is always supplied and a strict second schema must prove both
     * exact-location visibility and completed repair; this method never promotes a generic
     * `none` result on its own.
     */
    suspend fun verifyRepair(
        target: RepairTargetEntity,
        burstFrames: List<BurstFrame>,
        primaryIndex: Int,
        driveId: String,
        captureSeq: Int
    ): RepairVerificationResult? = withContext(Dispatchers.IO) {
        if (burstFrames.isEmpty()) return@withContext null
        val historical = fileDataUrl(target.photoPath, target.photoMime)
            ?: return@withContext null
        val safePrimary = primaryIndex.coerceIn(0, burstFrames.lastIndex)
        val primary = burstFrames[safePrimary]

        val images = mutableListOf(
            historical,
            FrameQualityEvaluator.prepareContextDataUrl(primary.bitmap, 768, 82)
        )
        images.addAll(burstFrames.map {
            FrameQualityEvaluator.prepareRoadBandDataUrl(it.bitmap, 1024, 85, true)
        })
        val layout = "\n- Input layout: image 1 is historical evidence; image 2 is " +
            "current full-frame context; images 3-${images.size} are the complete current " +
            "lower-road burst in chronological order; its sharpest crop is image ${safePrimary + 3}."
        val languageNote = when (language) {
            "kn" -> "\n- Write description in formal Kannada."
            "mr" -> "\n- Write description in clear formal Marathi."
            "bn" -> "\n- Write description in clear formal Bengali."
            else -> ""
        }
        val assessment = executeRepairStreaming(images, REPAIR_PROMPT + layout + languageNote)
        val mappedCondition = NativeRepairDecision.fromModel(
            assessment.currentCondition,
            assessment.assessment,
            assessment.imageQuality,
            assessment.sameLocationVisible,
            assessment.completedRepairVisible
        ) ?: return@withContext null

        val currentPhoto = saveRepairEvidenceImage(
            primary.bitmap, driveId, target.reportId, captureSeq
        )
        RepairVerificationResult(
            currentCondition = mappedCondition,
            assessment = assessment.assessment,
            imageQuality = assessment.imageQuality,
            sameLocationVisible = assessment.sameLocationVisible,
            completedRepairVisible = assessment.completedRepairVisible,
            description = assessment.description,
            currentPhotoPath = currentPhoto.absolutePath,
            detectionModel = model,
            imageDetail = detail,
            promptVersion = REPAIR_PROMPT_VERSION,
            schemaVersion = REPAIR_SCHEMA_VERSION
        )
    }

    private fun executeOaiStreaming(
        imageUrls: List<String>,
        prompt: String,
        allowEarlyReject: Boolean = true
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

                        if (allowEarlyReject && !debug && peekReject(textBuilder.toString())) {
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

    private fun executeRepairStreaming(
        imageUrls: List<String>,
        prompt: String
    ): RepairModelAssessment {
        val requestJson = buildRepairRequestBody(imageUrls, prompt)
        val body = requestJson.toString().toRequestBody("application/json".toMediaType())
        val request = Request.Builder()
            .url(OAI_URL)
            .addHeader("Authorization", "Bearer $apiKey")
            .addHeader("Content-Type", "application/json")
            .post(body)
            .build()
        val response = okHttpClient.newCall(request).execute()
        if (!response.isSuccessful) {
            val code = response.code
            response.close()
            val message = when (code) {
                401 -> "OpenAI rejected the API key"
                403 -> "This API key cannot use the selected model"
                429 -> "OpenAI rate limit or credit exhausted"
                in 500..599 -> "OpenAI is temporarily unavailable"
                else -> "OpenAI rejected the repair check ($code)"
            }
            throw NativeInferenceException(
                message, fatal = code == 401 || code == 403 || code == 429
            )
        }

        val responseBody = response.body ?: run {
            response.close()
            throw NativeInferenceException("Empty repair-check response from OpenAI")
        }
        val textBuilder = StringBuilder()
        try {
            responseBody.charStream().buffered().useLines { lines ->
                lines.forEach { raw ->
                    val line = raw.trim()
                    if (!line.startsWith("data:")) return@forEach
                    val payload = line.substring(5).trim()
                    if (payload.isEmpty() || payload == "[DONE]") return@forEach
                    try {
                        val event = JSONObject(payload)
                        if (event.optString("type") == "response.output_text.delta") {
                            textBuilder.append(event.optString("delta", ""))
                        }
                    } catch (_: Exception) {}
                }
            }
        } finally {
            response.close()
        }
        return parseRepairAssessment(textBuilder.toString())
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

    private fun buildRepairRequestBody(imageUrls: List<String>, prompt: String): JSONObject {
        val contentArray = JSONArray()
        for (url in imageUrls) {
            contentArray.put(JSONObject().apply {
                put("type", "input_image")
                put("image_url", url)
                put("detail", detail)
            })
        }
        contentArray.put(JSONObject().apply {
            put("type", "input_text")
            put("text", prompt)
        })
        val schema = JSONObject(
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
        )
        val format = JSONObject().apply {
            put("type", "json_schema")
            put("name", "road_repair_assessment")
            put("strict", true)
            put("schema", schema)
        }
        return JSONObject().apply {
            put("model", model)
            put("input", JSONArray().put(JSONObject().apply {
                put("role", "user")
                put("content", contentArray)
            }))
            put("text", JSONObject().apply {
                put("format", format)
                put("verbosity", "low")
            })
            put("stream", true)
            put("store", false)
            put("reasoning", JSONObject().put(
                "effort", if (model == "gpt-5.6") "none" else "minimal"
            ))
        }
    }

    private fun parseRepairAssessment(text: String): RepairModelAssessment {
        try {
            val json = JSONObject(text)
            val condition = json.getString("current_condition")
            val assessment = json.getString("assessment")
            val imageQuality = json.getString("image_quality")
            val sameLocation = json.getBoolean("same_location_visible")
            val completedRepair = json.getBoolean("completed_repair_visible")
            if (condition !in setOf("repaired", "still_damaged", "not_visible", "uncertain") ||
                assessment !in setOf("clear", "probable", "uncertain") ||
                imageQuality !in setOf("usable", "degraded", "unusable")) {
                throw IllegalArgumentException("Unexpected repair assessment value")
            }
            return RepairModelAssessment(
                currentCondition = condition,
                assessment = assessment,
                imageQuality = imageQuality,
                sameLocationVisible = sameLocation,
                completedRepairVisible = completedRepair,
                description = json.getString("description").take(1000)
            )
        } catch (error: Exception) {
            throw NativeInferenceException("OpenAI returned an invalid repair assessment")
        }
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
    ): String = NativeCompleteVerdictParser.decisionFor(
        reportable,
        assessment,
        imageQuality,
        damageType,
        onDrivableSurface,
        hasBrokenEdgeOrRim,
        hasDepthOrSurfaceLoss,
        temporalConsistency
    )

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
        return NativeCompleteVerdictParser.parse(text) ?: AssessmentResult(
            reportable = false,
            assessment = "uncertain",
            imageQuality = "unusable",
            damageType = "none",
            onDrivableSurface = false,
            hasBrokenEdgeOrRim = false,
            hasDepthOrSurfaceLoss = false,
            temporalConsistency = "inconsistent",
            size = null,
            description = "Detection response was incomplete.",
            decision = "reject"
        )
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

    private fun saveRepairEvidenceImage(
        bitmap: Bitmap,
        driveId: String,
        targetReportId: Long,
        seq: Int
    ): File {
        val safeDriveId = driveId.replace(Regex("[^A-Za-z0-9_-]"), "_").take(128)
        val dir = File(context.filesDir, "reports/$safeDriveId")
        if (!dir.exists() && !dir.mkdirs()) {
            throw NativeInferenceException("Could not create repair evidence storage")
        }
        val file = File(
            dir,
            "repair_${targetReportId}_${seq}_${System.currentTimeMillis()}.jpg"
        )
        FileOutputStream(file).use { out ->
            if (!bitmap.compress(Bitmap.CompressFormat.JPEG, 88, out)) {
                throw NativeInferenceException("Could not save repair evidence")
            }
        }
        return file
    }

    private fun fileDataUrl(path: String, mime: String): String? {
        val file = File(path)
        if (!file.isFile || file.length() <= 0 || file.length() > 8L * 1024 * 1024) return null
        val safeMime = mime.takeIf {
            it in setOf("image/jpeg", "image/png", "image/webp", "image/gif")
        } ?: return null
        return "data:$safeMime;base64," +
            AndroidBase64.encodeToString(file.readBytes(), AndroidBase64.NO_WRAP)
    }

    fun close() {
        okHttpClient.dispatcher.cancelAll()
        okHttpClient.connectionPool.evictAll()
    }
}
