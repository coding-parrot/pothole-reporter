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
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.*
import java.util.concurrent.TimeUnit
import java.util.regex.Pattern

data class AssessmentResult(
    val isPothole: Boolean,
    val looksLikeSpeedBreaker: Boolean,
    val reportable: Boolean,
    val assessment: String,
    val imageQuality: String,
    val damageType: String,
    val surfaceType: String,
    val defectType: String,
    val measurementProvenance: String,
    val measurementConfidence: String,
    val onDrivableSurface: Boolean,
    val hasLocalizedCavity: Boolean,
    val hasBrokenEdgeOrRim: Boolean,
    val hasDepthOrSurfaceLoss: Boolean,
    val temporalConsistency: String,
    val size: String?,
    val description: String,
    val decision: String // "accept" or "reject"
)

/** Parses only a complete structured detector response. Missing or mistyped fields are
 * never filled with absence-friendly defaults, because that result can gate a repair check. */
internal object NativeCompleteVerdictParser {
    private val imageQualities = setOf("usable", "unusable")
    private val temporalValues = setOf("consistent", "single_view", "inconsistent", "not_applicable")
    private val sizes = setOf("small", "medium", "large")
    private val surfaceTypes = setOf(
        "bituminous_asphalt", "cement_concrete", "mastic_asphalt", "paver_blocks",
        "temporary_drivable_surface", "unpaved_or_nonroad", "unknown"
    )
    private val reportableSurfaceTypes = surfaceTypes - setOf("unknown", "unpaved_or_nonroad")

    fun parse(text: String): AssessmentResult? {
        return try {
            val json = JSONObject(text)
            val sizeValue = json.get("size")
            fromFields(
                mapOf(
                    "is_pothole" to json.get("is_pothole"),
                    "looks_like_speed_breaker" to json.get("looks_like_speed_breaker"),
                    "image_quality" to json.get("image_quality"),
                    "surface_type" to json.get("surface_type"),
                    "on_drivable_surface" to json.get("on_drivable_surface"),
                    "has_localized_cavity" to json.get("has_localized_cavity"),
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
            "is_pothole", "looks_like_speed_breaker", "image_quality", "surface_type",
            "on_drivable_surface",
            "has_localized_cavity", "has_broken_edge_or_rim", "has_depth_or_surface_loss",
            "temporal_consistency", "size", "description"
        )
        if (!fields.keys.containsAll(required)) return null
        val modelIsPothole = fields["is_pothole"] as? Boolean ?: return null
        val looksLikeSpeedBreaker = fields["looks_like_speed_breaker"] as? Boolean ?: return null
        val onRoad = fields["on_drivable_surface"] as? Boolean ?: return null
        val localizedCavity = fields["has_localized_cavity"] as? Boolean ?: return null
        val brokenEdge = fields["has_broken_edge_or_rim"] as? Boolean ?: return null
        val depth = fields["has_depth_or_surface_loss"] as? Boolean ?: return null
        val imageQuality = fields["image_quality"] as? String ?: return null
        val surfaceType = fields["surface_type"] as? String ?: return null
        val temporal = fields["temporal_consistency"] as? String ?: return null
        val description = fields["description"] as? String ?: return null
        if (imageQuality !in imageQualities || surfaceType !in surfaceTypes ||
            temporal !in temporalValues) return null
        val sizeValue = fields["size"]
        if (sizeValue != null && (sizeValue !is String || sizeValue !in sizes)) return null
        val size = sizeValue as? String
        val decision = decisionFor(
            modelIsPothole, looksLikeSpeedBreaker, imageQuality, surfaceType, onRoad, localizedCavity,
            brokenEdge, depth, temporal, size
        )
        val accepted = decision == "accept"

        return AssessmentResult(
            isPothole = accepted,
            looksLikeSpeedBreaker = looksLikeSpeedBreaker,
            reportable = accepted,
            assessment = if (accepted) "clear" else "absent",
            imageQuality = imageQuality,
            damageType = if (accepted) "pothole_cavity" else "none",
            surfaceType = surfaceType,
            defectType = if (accepted) "pothole" else "not_pothole",
            measurementProvenance = if (accepted) "visual_estimate_no_scale" else "not_applicable",
            measurementConfidence = if (accepted) "low" else "not_applicable",
            onDrivableSurface = onRoad,
            hasLocalizedCavity = localizedCavity,
            hasBrokenEdgeOrRim = brokenEdge,
            hasDepthOrSurfaceLoss = depth,
            temporalConsistency = temporal,
            size = if (accepted) size else null,
            description = description,
            decision = decision
        )
    }

    internal fun decisionFor(
        isPothole: Boolean,
        looksLikeSpeedBreaker: Boolean,
        imageQuality: String,
        surfaceType: String,
        onDrivableSurface: Boolean,
        hasLocalizedCavity: Boolean,
        hasBrokenEdgeOrRim: Boolean,
        hasDepthOrSurfaceLoss: Boolean,
        temporalConsistency: String,
        size: String?
    ): String {
        if (!isPothole || looksLikeSpeedBreaker) return "reject"
        if (surfaceType !in reportableSurfaceTypes) return "reject"
        if (imageQuality != "usable" || !onDrivableSurface || !hasLocalizedCavity) return "reject"
        if (!hasBrokenEdgeOrRim || !hasDepthOrSurfaceLoss) return "reject"
        // NativeInferenceEngine is Drive-only and always receives a chronological burst.
        if (temporalConsistency != "consistent") return "reject"
        if (size !in sizes) return "reject"
        return "accept"
    }
}

/**
 * Decides whether a streamed detector request produced a durable verdict. An intentional
 * early reject is complete for Drive's binary purpose even though cancelling the response
 * body can surface as an IOException. Every other interrupted or malformed response must
 * remain retryable instead of being converted into a synthetic NO.
 */
internal object NativeDetectionStreamCompletionPolicy {
    fun requireVerdict(
        completeVerdict: AssessmentResult?,
        intentionalEarlyReject: AssessmentResult?,
        transportCompleted: Boolean
    ): AssessmentResult {
        if (intentionalEarlyReject != null) return intentionalEarlyReject
        NativeStreamCompletionPolicy.requireCompleted(
            transportCompleted,
            "OpenAI detection stream was interrupted"
        )
        return completeVerdict
            ?: throw NativeInferenceException(
                "OpenAI returned an invalid completed detection assessment",
                suspendInference = true
            )
    }
}

/** Shared terminal-event gate for detector and repair SSE responses. */
internal object NativeStreamCompletionPolicy {
    fun requireCompleted(transportCompleted: Boolean, incompleteMessage: String) {
        if (!transportCompleted) throw NativeInferenceException(incompleteMessage)
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
    val fatal: Boolean = false,
    val suspendInference: Boolean = false,
    val retryAfterMs: Long? = null,
    cause: Throwable? = null
) : IOException(message, cause)

/** A small bounded UTF-8 accumulator for untrusted streamed output text. */
internal class NativeSseTextAccumulator(
    private val maxUtf8Bytes: Int = MAX_UTF8_BYTES
) {
    private val text = StringBuilder(minOf(maxUtf8Bytes.coerceAtLeast(1), 4 * 1024))
    private var utf8Bytes = 0

    init {
        require(maxUtf8Bytes > 0) { "SSE text limit must be positive" }
    }

    /** Returns false without appending any part of [delta] when the cap would be crossed. */
    fun append(delta: String): Boolean {
        val deltaBytes = utf8LengthAtMost(delta, maxUtf8Bytes - utf8Bytes)
            ?: return false
        text.append(delta)
        utf8Bytes += deltaBytes
        return true
    }

    fun snapshot(): String = text.toString()

    private fun utf8LengthAtMost(value: String, remaining: Int): Int? {
        var total = 0
        var index = 0
        while (index < value.length) {
            val char = value[index]
            val bytes = when {
                char.code < 0x80 -> 1
                char.code < 0x800 -> 2
                Character.isHighSurrogate(char) && index + 1 < value.length &&
                    Character.isLowSurrogate(value[index + 1]) -> {
                    index++
                    4
                }
                else -> 3
            }
            if (total > remaining - bytes) return null
            total += bytes
            index++
        }
        return total
    }

    companion object {
        const val MAX_UTF8_BYTES = 64 * 1024
    }
}

/** HTTP retry taxonomy: only explicit transient statuses and transport failures retry. */
internal object NativeInferenceHttpFailurePolicy {
    private const val MAX_BACKOFF_MS = 60_000L
    private val TRANSIENT_HTTP_CODES = setOf(408, 409, 425, 429)

    /** Code 0 is the engine's sentinel for a network/stream transport failure. */
    fun isTransient(code: Int): Boolean =
        code == 0 || code in TRANSIENT_HTTP_CODES || code in 500..599

    fun shouldSuspendInference(code: Int): Boolean = !isTransient(code)

    fun retryDelayMs(code: Int, retryAfterHeader: String?, consecutiveFailure: Int): Long? {
        if (!isTransient(code)) return null
        val headerDelay = retryAfterHeader?.trim()?.toLongOrNull()
            ?.takeIf { it >= 0L }
            ?.let { seconds ->
                if (seconds > MAX_BACKOFF_MS / 1_000L) MAX_BACKOFF_MS else seconds * 1_000L
            }
        val base = when (code) {
            429 -> 5_000L
            0 -> 10_000L
            else -> 2_000L
        }
        val shift = consecutiveFailure.coerceIn(0, 5)
        val exponential = (base * (1L shl shift)).coerceAtMost(MAX_BACKOFF_MS)
        return maxOf(headerDelay ?: 0L, exponential)
    }
}

/**
 * Transfers a newly committed evidence file to the caller before any later allocation can
 * fail. If the receiver cannot record ownership, the producer removes the otherwise orphaned
 * file and rethrows the original failure.
 */
internal object NativeInferenceEvidenceOwnership {
    fun handOff(file: File, receiver: (String) -> Unit) {
        try {
            receiver(file.absolutePath)
        } catch (error: Throwable) {
            NativeReportEvidenceStorage.deleteVerified(file)
            throw error
        }
    }
}

class NativeInferenceEngine(
    private val context: Context,
    private val apiKey: String,
    private val model: String = "gpt-5.6",
    private val detail: String = "high",
    private val language: String = "en",
    private val debug: Boolean = false
) {
    private var consecutiveRetryableHttpFailures = 0
    private val okHttpClient = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()
    private val activeCallLock = Any()
    private val activeCalls = mutableSetOf<Call>()
    @Volatile private var engineClosed = false

    /**
     * Registers a call atomically against [close]. If Stop wins the lock, no later
     * request can begin; if this call wins, Stop receives and cancels the exact Call.
     * Ownership lasts through response-body consumption, not merely execute().
     */
    private inline fun <T> withTrackedCall(request: Request, block: (Call) -> T): T {
        val call = okHttpClient.newCall(request)
        synchronized(activeCallLock) {
            if (engineClosed) throw IOException("Detection engine is closed")
            activeCalls.add(call)
        }
        return try {
            block(call)
        } finally {
            synchronized(activeCallLock) { activeCalls.remove(call) }
        }
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
        ) {
            return error
        }
        return retryableFailure(error.message?.takeIf(String::isNotBlank) ?: message, error)
    }

    private val nominatimMutex = Mutex()
    private var lastNominatimTimeMs = 0L

    companion object {
        private const val OAI_URL = "https://api.openai.com/v1/responses"
        private const val NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
        private const val PROMPT_VERSION = "pothole-binary-v10"
        private const val SCHEMA_VERSION = 7
        private const val MAX_DETECTION_OUTPUT_TOKENS = 1_536
        private const val MAX_REPAIR_OUTPUT_TOKENS = 768
        const val REPAIR_PROMPT_VERSION = "road-repair-v1"
        const val REPAIR_SCHEMA_VERSION = 1

        private val IS_POTHOLE_RE = Pattern.compile("\"is_pothole\"\\s*:\\s*(true|false)")
        private val SPEED_BREAKER_RE = Pattern.compile("\"looks_like_speed_breaker\"\\s*:\\s*(true|false)")
        private val QUALITY_RE = Pattern.compile("\"image_quality\"\\s*:\\s*\"(usable|unusable)\"")
        private val SURFACE_RE = Pattern.compile("\"surface_type\"\\s*:\\s*\"(bituminous_asphalt|cement_concrete|mastic_asphalt|paver_blocks|temporary_drivable_surface|unpaved_or_nonroad|unknown)\"")
        private val ROAD_RE = Pattern.compile("\"on_drivable_surface\"\\s*:\\s*(true|false)")
        private val CAVITY_RE = Pattern.compile("\"has_localized_cavity\"\\s*:\\s*(true|false)")
        private val EDGE_RE = Pattern.compile("\"has_broken_edge_or_rim\"\\s*:\\s*(true|false)")
        private val DEPTH_RE = Pattern.compile("\"has_depth_or_surface_loss\"\\s*:\\s*(true|false)")
        private val TEMPORAL_RE = Pattern.compile("\"temporal_consistency\"\\s*:\\s*\"(consistent|single_view|inconsistent|not_applicable)\"")
        private val SIZE_RE = Pattern.compile("\"size\"\\s*:\\s*(?:\"(small|medium|large)\"|null)")

        // Keep this byte-for-byte equivalent (after trimIndent) to DETECT_PROMPT in
        // static/standalone.js. tests/eval_contract_test.py fails if either runtime drifts.
        private val DETECT_PROMPT =
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
        onEvidenceSaved: (String) -> Unit,
        requireCompleteVerdict: Boolean = false
    ): InferenceOutcome = withContext(Dispatchers.IO) {
        if (burstFrames.size !in NativeDriveCameraManager.MIN_DETECTION_SOURCE_FRAMES..
            NativeRollingBurstWindow.OUTPUT_COUNT
        ) {
            // Defensive gate for callers and restored keyframes: Drive Mode may never
            // turn one source bitmap plus its crop into a multi-frame YES or exceed
            // the four-image production/evaluator request contract.
            return@withContext InferenceOutcome(analyzed = false, accepted = false, decision = "reject", assessment = null)
        }

        // Reserve the maximum possible evidence JPEG before any paid request. The
        // reservation is an accounting lease only (no bitmap/JPEG is retained), and is
        // committed by an accepted result or released by every reject/error path.
        val evidenceLease = NativeReportEvidenceStorage.reserveInferenceCapacity(context)
        try {

        val primaryFrame = burstFrames.getOrElse(primaryIndex) { burstFrames[0] }
        // Prepare one temporary bitmap/data URL at a time. Each transform releases its
        // crop/enhancement bitmap before the next frame starts, and request encoding below
        // clears these Base64 strings before waiting on the network response.
        val imageInputs = ArrayList<String>(burstFrames.size + 1)
        imageInputs += FrameQualityEvaluator.prepareContextDataUrl(primaryFrame.bitmap, 768, 82)
        burstFrames.forEach { frame ->
            imageInputs += FrameQualityEvaluator.prepareRoadBandDataUrl(
                frame.bitmap,
                FrameQualityEvaluator.MAX_PREPARED_ROAD_DIMENSION,
                85,
                true
            )
        }
        val evidenceCount = imageInputs.size

        val langSuffix = when (language) {
            "kn" -> "\n- Write the description field in formal Kannada (ಕನ್ನಡ ಭಾಷೆಯಲ್ಲಿ ಬರೆಯಿರಿ)."
            "mr" -> "\n- Write the description field in clear formal Marathi (मराठी भाषेत लिहा)."
            "bn" -> "\n- Write the description field in clear formal Bengali (পরিষ্কার, প্রমিত বাংলায় লিখুন)."
            else -> ""
        }
        val layoutNote = "\n- Capture layout: image 1 is full-frame context from the sharpest burst frame. Images 2-$evidenceCount are orientation-aware road-region crops in chronological order; the sharpest crop is chronological frame ${primaryIndex + 1}."
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
        val photoFile = saveEvidenceImage(
            primaryFrame.bitmap, driveId, captureSeq, evidenceLease
        )
        // The service's worker-level finally block becomes the owner immediately. This hand-off
        // must precede thumbnail/JSON/entity allocation and the suspend-function return boundary;
        // otherwise an OOM or prompt cancellation can strand a JPEG the caller never learns about.
        NativeInferenceEvidenceOwnership.handOff(photoFile, onEvidenceSaved)
        val photoThumbJpeg = FrameQualityEvaluator.bitmapToBoundedJpegBytes(
            primaryFrame.bitmap,
            NativeStoredImagePolicy.MAX_ROOM_THUMB_IMAGE_BYTES,
            NativeStoredImagePolicy.ROOM_THUMB_MAX_DIMENSION,
            78
        )
        val photoThumbUrl = photoThumbJpeg?.let {
            "data:image/jpeg;base64," +
                AndroidBase64.encodeToString(it, AndroidBase64.NO_WRAP)
        }

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
            isPothole = if (assessment.isPothole) 1 else 0,
            looksLikeSpeedBreaker = assessment.looksLikeSpeedBreaker,
            damageType = assessment.damageType,
            surfaceType = assessment.surfaceType,
            defectType = assessment.defectType,
            measurementProvenance = assessment.measurementProvenance,
            measurementConfidence = assessment.measurementConfidence,
            assessment = assessment.assessment,
            imageQuality = assessment.imageQuality,
            onDrivableSurface = assessment.onDrivableSurface,
            hasLocalizedCavity = assessment.hasLocalizedCavity,
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
            evidenceCount = evidenceCount,
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
        } finally {
            NativeReportEvidenceStorage.releaseInferenceCapacity(evidenceLease)
        }
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
        captureSeq: Int,
        onEvidenceSaved: (String) -> Unit
    ): RepairVerificationResult? = withContext(Dispatchers.IO) {
        if (burstFrames.isEmpty()) return@withContext null
        val evidenceLease = NativeReportEvidenceStorage.reserveInferenceCapacity(context)
        try {
        val safePrimary = primaryIndex.coerceIn(0, burstFrames.lastIndex)
        val primary = burstFrames[safePrimary]

        val images = ArrayList<String>(burstFrames.size + 2)
        images += fileDataUrl(target.photoPath, target.photoMime)
            ?: return@withContext null
        images += FrameQualityEvaluator.prepareContextDataUrl(primary.bitmap, 768, 82)
        burstFrames.forEach { frame ->
            images += FrameQualityEvaluator.prepareRoadBandDataUrl(
                frame.bitmap,
                FrameQualityEvaluator.MAX_PREPARED_ROAD_DIMENSION,
                85,
                true
            )
        }
        val repairImageCount = images.size
        val layout = "\n- Input layout: image 1 is historical evidence; image 2 is " +
            "current full-frame context; images 3-$repairImageCount are the complete current " +
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
            primary.bitmap, driveId, target.reportId, captureSeq, evidenceLease
        )
        NativeInferenceEvidenceOwnership.handOff(currentPhoto, onEvidenceSaved)
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
        } finally {
            NativeReportEvidenceStorage.releaseInferenceCapacity(evidenceLease)
        }
    }

    private fun executeOaiStreaming(
        imageUrls: MutableList<String>,
        prompt: String,
        allowEarlyReject: Boolean = true
    ): AssessmentResult {
        val body = try {
            buildRequestBody(imageUrls, prompt).toString()
                .toRequestBody("application/json".toMediaType())
        } finally {
            // RequestBody owns its encoded bytes. Drop the second set of large Base64
            // strings before blocking on connect/read timeouts.
            imageUrls.clear()
        }

        val request = Request.Builder()
            .url(OAI_URL)
            .addHeader("Authorization", "Bearer $apiKey")
            .addHeader("Content-Type", "application/json")
            .post(body)
            .build()

        return withTrackedCall(request) { call ->
            val response = try {
                call.execute()
            } catch (error: IOException) {
                throw retryableFailure("OpenAI detection connection failed", error)
            }

            if (!response.isSuccessful) {
                val code = response.code
                val retryAfter = response.header("Retry-After")
                response.close()
                val message = when (code) {
                    401 -> "OpenAI rejected the API key"
                    403 -> "This API key cannot use the selected model"
                    400 -> "OpenAI rejected the model or structured-output request (400)"
                    404 -> "The selected OpenAI model or endpoint was not found (404)"
                    429 -> "OpenAI rate limit or credit exhausted"
                    in 500..599 -> "OpenAI is temporarily unavailable"
                    else -> "OpenAI rejected the detection request ($code)"
                }
                val suspendInference = NativeInferenceHttpFailurePolicy.shouldSuspendInference(code)
                val retryDelay = if (NativeInferenceHttpFailurePolicy.isTransient(code)) {
                    NativeInferenceHttpFailurePolicy.retryDelayMs(
                        code,
                        retryAfter,
                        consecutiveRetryableHttpFailures++
                    )
                } else null
                throw NativeInferenceException(
                    message,
                    suspendInference = suspendInference,
                    retryAfterMs = retryDelay
                )
            }
            val responseBody = response.body ?: run {
                response.close()
                throw retryableFailure("Empty response body from OpenAI")
            }
            val reader = responseBody.charStream().buffered()
            val textAccumulator = NativeSseTextAccumulator()
            var earlyRejected = false
            var transportCompleted = false

            try {
                var line: String?
                while (reader.readLine().also { line = it } != null) {
                    val l = line?.trim() ?: continue
                    if (!l.startsWith("data:")) continue
                    val payload = l.substring(5).trim()
                    if (payload.isEmpty()) continue
                    if (payload == "[DONE]") {
                        transportCompleted = true
                        continue
                    }

                    val ev = try {
                        JSONObject(payload)
                    } catch (_: Exception) {
                        continue
                    }
                    val type = ev.optString("type")
                    if (type == "response.completed") transportCompleted = true
                    if (type == "response.output_text.delta") {
                        val delta = ev.optString("delta", "")
                        if (!textAccumulator.append(delta)) {
                            call.cancel()
                            // With an explicit output-token ceiling, repeatedly receiving more
                            // than this is a deterministic protocol/configuration failure rather
                            // than a network interruption. Pause API work instead of filling the
                            // durable replay queue with the same oversized response.
                            throw NativeInferenceException(
                                "OpenAI detection response exceeded the 64 KiB safety limit",
                                suspendInference = true
                            )
                        }

                        if (allowEarlyReject && !debug && peekReject(textAccumulator.snapshot())) {
                            earlyRejected = true
                            call.cancel()
                            break
                        }
                    }
                }
            } catch (error: NativeInferenceException) {
                throw error
            } catch (_: IOException) {
                // The explicit terminal event below distinguishes a complete stream from a
                // clean or exceptional truncation. Intentional early rejection is separate.
            } finally {
                response.close()
            }

            val text = textAccumulator.snapshot()
            val verdict = try {
                NativeDetectionStreamCompletionPolicy.requireVerdict(
                    completeVerdict = if (!earlyRejected && transportCompleted) {
                        NativeCompleteVerdictParser.parse(text)
                    } else null,
                    intentionalEarlyReject = if (earlyRejected) rejectedVerdict(text) else null,
                    transportCompleted = transportCompleted
                )
            } catch (error: Exception) {
                throw normalizeRetryableFailure(error, "OpenAI detection response was incomplete")
            }
            consecutiveRetryableHttpFailures = 0
            verdict
        }
    }

    private fun executeRepairStreaming(
        imageUrls: MutableList<String>,
        prompt: String
    ): RepairModelAssessment {
        val body = try {
            buildRepairRequestBody(imageUrls, prompt).toString()
                .toRequestBody("application/json".toMediaType())
        } finally {
            imageUrls.clear()
        }
        val request = Request.Builder()
            .url(OAI_URL)
            .addHeader("Authorization", "Bearer $apiKey")
            .addHeader("Content-Type", "application/json")
            .post(body)
            .build()
        return withTrackedCall(request) { call ->
        val response = try {
            call.execute()
        } catch (error: IOException) {
            throw retryableFailure("OpenAI repair-check connection failed", error)
        }
        if (!response.isSuccessful) {
            val code = response.code
            val retryAfter = response.header("Retry-After")
            response.close()
            val message = when (code) {
                401 -> "OpenAI rejected the API key"
                403 -> "This API key cannot use the selected model"
                400 -> "OpenAI rejected the repair model or structured-output request (400)"
                404 -> "The selected OpenAI repair model or endpoint was not found (404)"
                429 -> "OpenAI rate limit or credit exhausted"
                in 500..599 -> "OpenAI is temporarily unavailable"
                else -> "OpenAI rejected the repair check ($code)"
            }
            val suspendInference = NativeInferenceHttpFailurePolicy.shouldSuspendInference(code)
            val retryDelay = if (NativeInferenceHttpFailurePolicy.isTransient(code)) {
                NativeInferenceHttpFailurePolicy.retryDelayMs(
                    code,
                    retryAfter,
                    consecutiveRetryableHttpFailures++
                )
            } else null
            throw NativeInferenceException(
                message,
                suspendInference = suspendInference,
                retryAfterMs = retryDelay
            )
        }
        val responseBody = response.body ?: run {
            response.close()
            throw retryableFailure("Empty repair-check response from OpenAI")
        }
        val textAccumulator = NativeSseTextAccumulator()
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
                        !textAccumulator.append(event.optString("delta", ""))
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
            parseRepairAssessment(textAccumulator.snapshot())
        } catch (error: Exception) {
            throw normalizeRetryableFailure(error, "OpenAI repair-check response was incomplete")
        }
        consecutiveRetryableHttpFailures = 0
        assessment
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
        )

        val textFormat = JSONObject()
        textFormat.put("type", "json_schema")
        textFormat.put("name", "pothole_binary_assessment")
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
        req.put("max_output_tokens", MAX_DETECTION_OUTPUT_TOKENS)

        val reasoningObj = JSONObject()
        // Exact owner-video regression uses low reasoning for Drive: it preserves every
        // supplied hard negative while reliably resolving shallow cavity geometry.
        reasoningObj.put("effort", if (model == "gpt-5.6") "low" else "minimal")
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
            put("max_output_tokens", MAX_REPAIR_OUTPUT_TOKENS)
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
            throw NativeInferenceException(
                "OpenAI returned an invalid completed repair assessment",
                suspendInference = true,
                cause = error
            )
        }
    }

    private fun peekReject(text: String): Boolean {
        val verdictMatcher = IS_POTHOLE_RE.matcher(text)
        if (!verdictMatcher.find()) return false
        val isPothole = verdictMatcher.group(1) == "true"
        if (!isPothole) return true

        val breakerMatcher = SPEED_BREAKER_RE.matcher(text)
        val breakerFound = breakerMatcher.find()
        if (breakerFound && breakerMatcher.group(1) == "true") return true
        val qualMatcher = QUALITY_RE.matcher(text)
        val surfaceMatcher = SURFACE_RE.matcher(text)
        val roadMatcher = ROAD_RE.matcher(text)
        val cavityMatcher = CAVITY_RE.matcher(text)
        val edgeMatcher = EDGE_RE.matcher(text)
        val depthMatcher = DEPTH_RE.matcher(text)
        val tempMatcher = TEMPORAL_RE.matcher(text)
        val sizeMatcher = SIZE_RE.matcher(text)

        if (!breakerFound || !qualMatcher.find() || !surfaceMatcher.find() || !roadMatcher.find() ||
            !cavityMatcher.find() || !edgeMatcher.find() || !depthMatcher.find() ||
            !tempMatcher.find() || !sizeMatcher.find()
        ) {
            return false
        }

        val dec = evaluateDecision(
            true,
            breakerMatcher.group(1) == "true",
            qualMatcher.group(1) ?: "unusable",
            surfaceMatcher.group(1) ?: "unknown",
            roadMatcher.group(1) == "true",
            cavityMatcher.group(1) == "true",
            edgeMatcher.group(1) == "true",
            depthMatcher.group(1) == "true",
            tempMatcher.group(1) ?: "inconsistent",
            sizeMatcher.group(1)
        )
        return dec != "accept"
    }

    private fun evaluateDecision(
        isPothole: Boolean,
        looksLikeSpeedBreaker: Boolean,
        imageQuality: String,
        surfaceType: String,
        onDrivableSurface: Boolean,
        hasLocalizedCavity: Boolean,
        hasBrokenEdgeOrRim: Boolean,
        hasDepthOrSurfaceLoss: Boolean,
        temporalConsistency: String,
        size: String?
    ): String = NativeCompleteVerdictParser.decisionFor(
        isPothole,
        looksLikeSpeedBreaker,
        imageQuality,
        surfaceType,
        onDrivableSurface,
        hasLocalizedCavity,
        hasBrokenEdgeOrRim,
        hasDepthOrSurfaceLoss,
        temporalConsistency,
        size
    )

    private fun rejectedVerdict(text: String): AssessmentResult {
        val verdictMatcher = IS_POTHOLE_RE.matcher(text)
        val breakerMatcher = SPEED_BREAKER_RE.matcher(text)
        val qualMatcher = QUALITY_RE.matcher(text)
        val surfaceMatcher = SURFACE_RE.matcher(text)
        val roadMatcher = ROAD_RE.matcher(text)
        val cavityMatcher = CAVITY_RE.matcher(text)
        val edgeMatcher = EDGE_RE.matcher(text)
        val depthMatcher = DEPTH_RE.matcher(text)
        val tempMatcher = TEMPORAL_RE.matcher(text)

        // The binary field is first in the strict schema. A missing value means a
        // malformed/truncated stream; either way this reconstructed result is NO.
        val modelSaidPothole = verdictMatcher.find() && verdictMatcher.group(1) == "true"
        val looksLikeSpeedBreaker = !breakerMatcher.find() || breakerMatcher.group(1) == "true"
        val imageQuality = if (qualMatcher.find()) qualMatcher.group(1)!! else "unusable"
        val surfaceType = if (surfaceMatcher.find()) surfaceMatcher.group(1)!! else "unknown"
        val onRoad = roadMatcher.find() && roadMatcher.group(1) == "true"
        val hasCavity = cavityMatcher.find() && cavityMatcher.group(1) == "true"
        val hasEdge = edgeMatcher.find() && edgeMatcher.group(1) == "true"
        val hasDepth = depthMatcher.find() && depthMatcher.group(1) == "true"
        val temporal = if (tempMatcher.find()) tempMatcher.group(1)!! else "not_applicable"

        return AssessmentResult(
            isPothole = false,
            looksLikeSpeedBreaker = looksLikeSpeedBreaker,
            reportable = false,
            assessment = "absent",
            imageQuality = imageQuality,
            damageType = "none",
            surfaceType = surfaceType,
            defectType = "not_pothole",
            measurementProvenance = "not_applicable",
            measurementConfidence = "not_applicable",
            onDrivableSurface = onRoad,
            hasLocalizedCavity = hasCavity,
            hasBrokenEdgeOrRim = hasEdge,
            hasDepthOrSurfaceLoss = hasDepth,
            temporalConsistency = temporal,
            size = null,
            description = if (modelSaidPothole) "Pothole evidence failed a required physical gate."
                else "No pothole detected.",
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

                withTrackedCall(request) { call -> call.execute().use { response ->
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
                } }
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

    private suspend fun saveEvidenceImage(
        bitmap: Bitmap,
        driveId: String,
        seq: Int,
        lease: NativeReportEvidenceStorage.InferenceCapacityLease
    ): File {
        val safeDriveId = driveId.replace(Regex("[^A-Za-z0-9_-]"), "_").take(128)
        val dir = File(context.filesDir, "reports/$safeDriveId")
        return saveJpegAtomically(
            bitmap,
            dir,
            "evidence_${seq}_${System.currentTimeMillis()}.jpg",
            92,
            lease
        )
    }

    private suspend fun saveRepairEvidenceImage(
        bitmap: Bitmap,
        driveId: String,
        targetReportId: Long,
        seq: Int,
        lease: NativeReportEvidenceStorage.InferenceCapacityLease
    ): File {
        val safeDriveId = driveId.replace(Regex("[^A-Za-z0-9_-]"), "_").take(128)
        val dir = File(context.filesDir, "reports/$safeDriveId")
        return saveJpegAtomically(
            bitmap,
            dir,
            "repair_${targetReportId}_${seq}_${System.currentTimeMillis()}.jpg",
            88,
            lease
        )
    }

    private suspend fun saveJpegAtomically(
        bitmap: Bitmap,
        directory: File,
        fileName: String,
        quality: Int,
        lease: NativeReportEvidenceStorage.InferenceCapacityLease
    ): File {
        val jpeg = FrameQualityEvaluator.bitmapToBoundedJpegBytes(
            bitmap = bitmap,
            maxBytes = NativeStoredImagePolicy.MAX_BRIDGE_IMAGE_BYTES,
            maxDimension = NativeStoredImagePolicy.EVIDENCE_MAX_DIMENSION,
            initialQuality = quality
        ) ?: throw NativeInferenceException("Could not encode evidence within the safe image limit")
        return try {
            NativeReportEvidenceStorage.saveJpegAtomically(
                context,
                directory,
                fileName,
                jpeg,
                lease
            )
        } catch (error: NativeInferenceException) {
            throw error
        } catch (error: Exception) {
            throw NativeInferenceException(
                "Could not save evidence image: ${error.message ?: "storage error"}",
                suspendInference = true,
                cause = error
            )
        }
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
        val calls = synchronized(activeCallLock) {
            engineClosed = true
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
}
