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
    private val detail: String = "high",
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
        onEvidenceSaved: (String) -> Unit,
        requireCompleteVerdict: Boolean = false
    ): InferenceOutcome = withContext(Dispatchers.IO) {
        if (burstFrames.size !in NativeDriveCameraManager.MIN_DETECTION_SOURCE_FRAMES..
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
            val imageInputs = prepareDetectionImages(burstFrames, primaryFrame)
            val evidenceCount = imageInputs.size
            val prompt = NativeDetectionPromptFactory.build(
                language = language,
                imageCount = evidenceCount,
                primaryIndex = primaryIndex
            )
            val streamVerdict = transport.detect(
                imageUrls = imageInputs,
                prompt = prompt,
                allowEarlyReject = !requireCompleteVerdict
            )
            val assessment = streamVerdict.assessment
            val trace = streamVerdict.toPublicTrace()
            if (assessment.decision != "accept") {
                return@withContext InferenceOutcome(
                    analyzed = true,
                    accepted = false,
                    decision = assessment.decision,
                    assessment = assessment,
                    detectionTrace = trace
                )
            }

            val photoFile = evidenceStore.saveDetection(
                primaryFrame.bitmap,
                driveId,
                captureSeq,
                evidenceLease
            )
            // Transfer cleanup ownership before any thumbnail/entity allocation can fail.
            NativeInferenceEvidenceOwnership.handOff(photoFile, onEvidenceSaved)
            NativeInferenceReportFactory.create(
                NativeDetectionReportInput(
                    assessment = assessment,
                    latitude = lat,
                    longitude = lng,
                    photoPath = photoFile.absolutePath,
                    thumbnailDataUrl = evidenceStore.thumbnailDataUrl(primaryFrame.bitmap),
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
                    debug = debug,
                    detectionTrace = trace
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
                NativeRepairPromptFactory.build(language, images.size, safePrimary)
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

    private fun prepareDetectionImages(
        burstFrames: List<BurstFrame>,
        primaryFrame: BurstFrame
    ): MutableList<String> = ArrayList<String>(burstFrames.size + 1).apply {
        add(FrameQualityEvaluator.prepareContextDataUrl(primaryFrame.bitmap, 768, 82))
        // Sequential preparation bounds peak bitmap/Base64 memory.
        burstFrames.forEach { frame ->
            add(FrameQualityEvaluator.prepareRoadBandDataUrl(
                frame.bitmap,
                FrameQualityEvaluator.MAX_PREPARED_ROAD_DIMENSION,
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
                add(FrameQualityEvaluator.prepareRoadBandDataUrl(
                    frame.bitmap,
                    FrameQualityEvaluator.MAX_PREPARED_ROAD_DIMENSION,
                    85,
                    true
                ))
            }
        }
    }

    fun close() = transport.close()

    companion object {
        const val REPAIR_PROMPT_VERSION = NativeRepairContract.PROMPT_VERSION
        const val REPAIR_SCHEMA_VERSION = NativeRepairContract.SCHEMA_VERSION
    }
}

private fun NativeDetectionStreamVerdict.toPublicTrace(): DetectionTrace = DetectionTrace(
    rawModelIsPothole = rawVerdict?.isPothole ?: when (completionMode) {
        NativeDetectionCompletionMode.EARLY_MODEL_NO -> false
        NativeDetectionCompletionMode.EARLY_SPEED_BREAKER,
        NativeDetectionCompletionMode.EARLY_GATE_VETO -> true
        NativeDetectionCompletionMode.COMPLETE -> null
    },
    completionMode = completionMode.name,
    observedFields = observedFields,
    rejectionReasons = rejectionReasons.map(DetectionRejectionReason::name)
)
