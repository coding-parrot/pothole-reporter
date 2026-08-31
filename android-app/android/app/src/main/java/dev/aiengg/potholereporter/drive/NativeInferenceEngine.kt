package dev.aiengg.potholereporter.drive

import android.content.Context
import dev.aiengg.potholereporter.db.RepairTargetEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Coordinates one inference attempt. Detection rules, request encoding, transport, evidence
 * persistence, repair parsing, and report construction deliberately live in separate modules.
 */
class NativeInferenceEngine(
    context: Context,
    apiKey: String,
    private val model: String = "gpt-5.6",
    private val detail: String = "original",
    private val language: String = "en",
    private val debug: Boolean = false
) {
    private val transport = NativeInferenceTransport(apiKey, model, detail, debug)
    private val evidenceStore = NativeInferenceEvidenceStore(context)
    private val appContext = context.applicationContext

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
        allowEarlyReject: Boolean,
        onEvidenceSaved: (String) -> Unit
    ): InferenceOutcome = withContext(Dispatchers.IO) {
        if (burstFrames.size !in NativeFrameBurstContract.MIN_INFERENCE_FRAMES..
            NativeRollingBurstWindow.OUTPUT_COUNT
        ) {
            return@withContext InferenceOutcome(
                analyzed = false,
                accepted = false,
                decision = "reject",
                assessment = null
            )
        }

        // Capacity is reserved before a paid request and released on every exit path.
        val evidenceLease = NativeReportEvidenceStorage.reserveInferenceCapacity(appContext)
        try {
            val primaryFrame = burstFrames.getOrElse(primaryIndex) { burstFrames[0] }
            val evidenceCount = burstFrames.size + 1
            val prompt = NativeDetectionContract.buildPrompt(
                language = language,
                imageCount = evidenceCount,
                primaryIndex = primaryIndex
            )
            val assessment = runBoundedDetectionAttempts {
                // NativeInferenceTransport takes ownership of and clears each encoded list.
                // Re-encode from the still-owned complete bitmaps only for a bounded retry.
                transport.detect(
                    imageUrls = prepareDetectionImages(burstFrames, primaryFrame),
                    prompt = prompt,
                    // Ordinary model-NO or speed-breaker can stop immediately. Repair
                    // revisits explicitly disable this because their separate before/after
                    // verifier may run only after a complete usable absence verdict.
                    allowEarlyReject = allowEarlyReject
                )
            }
            if (assessment.decision != "accept") {
                return@withContext InferenceOutcome(
                    analyzed = true,
                    accepted = false,
                    decision = assessment.decision,
                    assessment = assessment
                )
            }

            val photoFile = evidenceStore.saveDetection(
                primaryFrame.bitmap,
                driveId,
                captureSeq,
                evidenceLease
            )
            // Transfer cleanup ownership before any thumbnail/entity allocation can fail.
            publishEvidence(photoFile, onEvidenceSaved)
            val thumbnailDataUrl = evidenceStore.thumbnailDataUrl(primaryFrame.bitmap)
                ?.takeIf(String::isNotBlank)
                ?: throw NativeInferenceException(
                    "Could not encode the report thumbnail; the saved frame remains pending"
                )
            createInferenceOutcome(
                NativeDetectionReportInput(
                    assessment = assessment,
                    latitude = lat,
                    longitude = lng,
                    photoPath = photoFile.absolutePath,
                    thumbnailDataUrl = thumbnailDataUrl,
                    model = model,
                    detail = detail,
                    evidenceCount = evidenceCount,
                    driveId = driveId,
                    captureSeq = captureSeq,
                    capturedAtMs = capturedAtMs,
                    sourceOffsetMs = sourceOffsetMs,
                    gpsAccuracy = gpsAccuracy,
                    speedMps = speedMps,
                    heading = heading,
                    primaryIndex = primaryIndex,
                    debug = debug
                )
            )
        } finally {
            NativeReportEvidenceStorage.releaseInferenceCapacity(evidenceLease)
        }
    }

    /**
     * Runs only after a complete usable absence verdict. A separate strict comparison must prove
     * the historical footprint is visible and repaired; ordinary absence never marks it fixed.
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
        val evidenceLease = NativeReportEvidenceStorage.reserveInferenceCapacity(appContext)
        try {
            val safePrimary = primaryIndex.coerceIn(0, burstFrames.lastIndex)
            val primary = burstFrames[safePrimary]
            val images = prepareRepairImages(target, burstFrames, primary)
                ?: return@withContext null
            val assessment = transport.verifyRepair(
                images,
                NativeRepairContract.buildPrompt(language, images.size, safePrimary)
            )
            val mappedCondition = NativeRepairDecision.fromModel(
                assessment.currentCondition,
                assessment.assessment,
                assessment.imageQuality,
                assessment.sameLocationVisible,
                assessment.completedRepairVisible
            ) ?: return@withContext null

            val currentPhoto = evidenceStore.saveRepair(
                primary.bitmap,
                driveId,
                target.reportId,
                captureSeq,
                evidenceLease
            )
            publishEvidence(currentPhoto, onEvidenceSaved)
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
                promptVersion = NativeRepairContract.PROMPT_VERSION,
                schemaVersion = NativeRepairContract.SCHEMA_VERSION
            )
        } finally {
            NativeReportEvidenceStorage.releaseInferenceCapacity(evidenceLease)
        }
    }

    private fun prepareDetectionImages(
        burstFrames: List<BurstFrame>,
        primaryFrame: BurstFrame
    ): MutableList<String> = ArrayList<String>(burstFrames.size + 1).apply {
        add(FrameQualityEvaluator.prepareContextDataUrl(primaryFrame.bitmap, 768, 82))
        // Every chronological view is the complete frame. Sequential preparation bounds
        // peak bitmap/Base64 memory without introducing any crop or region of interest.
        burstFrames.forEach { frame ->
            add(FrameQualityEvaluator.prepareDetectionFrameDataUrl(
                frame.bitmap,
                FrameQualityEvaluator.MAX_PREPARED_FRAME_DIMENSION,
                85,
                true
            ))
        }
    }

    private fun prepareRepairImages(
        target: RepairTargetEntity,
        burstFrames: List<BurstFrame>,
        primaryFrame: BurstFrame
    ): MutableList<String>? {
        val historical = evidenceStore.fileDataUrl(target.photoPath, target.photoMime) ?: return null
        return ArrayList<String>(burstFrames.size + 2).apply {
            add(historical)
            add(FrameQualityEvaluator.prepareContextDataUrl(primaryFrame.bitmap, 768, 82))
            burstFrames.forEach { frame ->
                add(FrameQualityEvaluator.prepareDetectionFrameDataUrl(
                    frame.bitmap,
                    FrameQualityEvaluator.MAX_PREPARED_FRAME_DIMENSION,
                    85,
                    true
                ))
            }
        }
    }

    fun close() = transport.close()
}
