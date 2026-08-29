package dev.aiengg.potholereporter.drive

import dev.aiengg.potholereporter.db.EventSightingEntity
import dev.aiengg.potholereporter.db.ReportEntity
import org.json.JSONArray

data class InferenceOutcome(
    val analyzed: Boolean,
    val accepted: Boolean,
    val decision: String,
    val assessment: AssessmentResult?,
    val reportEntity: ReportEntity? = null,
    val sightings: List<EventSightingEntity> = emptyList(),
    val detectionTrace: DetectionTrace? = null
)

/** Safe diagnostic truth for a decision; never contains keys, images, or response bodies. */
data class DetectionTrace(
    val rawModelIsPothole: Boolean?,
    val completionMode: String,
    val observedFields: Set<String>,
    val rejectionReasons: List<String>
)

internal data class NativeDetectionReportInput(
    val assessment: AssessmentResult,
    val latitude: Double?,
    val longitude: Double?,
    val photoPath: String,
    val thumbnailDataUrl: String?,
    val model: String,
    val detail: String,
    val evidenceCount: Int,
    val driveId: String,
    val captureSeq: Int,
    val capturedAtMs: Long,
    val sourceOffsetMs: Long,
    val gpsAccuracy: Float?,
    val speedMps: Float?,
    val heading: Float?,
    val primaryIndex: Int,
    val debug: Boolean,
    val detectionTrace: DetectionTrace,
    val createdAtSeconds: Long = System.currentTimeMillis() / 1000
)

internal object NativeInferenceReportFactory {
    fun create(input: NativeDetectionReportInput): InferenceOutcome {
        val assessment = input.assessment
        require(assessment.decision == "accept" && assessment.isPothole && assessment.reportable) {
            "Only accepted pothole assessments can create reports"
        }
        val sourceEventKey = "live:${input.driveId}:${input.captureSeq}"
        val shortAddress = "Road coordinates (" +
            "${input.latitude?.let { "%.5f".format(it) }}, " +
            "${input.longitude?.let { "%.5f".format(it) }})"
        val report = ReportEntity(
            createdAt = input.createdAtSeconds,
            lat = input.latitude,
            lng = input.longitude,
            address = shortAddress,
            photoPath = input.photoPath,
            photoDataUrl = input.thumbnailDataUrl,
            photoFullPath = input.photoPath,
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
            decision = assessment.decision,
            description = assessment.description,
            emailSubject = null,
            emailBody = null,
            status = "draft",
            detectionModel = input.model,
            imageDetail = input.detail,
            promptVersion = NativeDetectionContract.PROMPT_VERSION,
            schemaVersion = NativeDetectionContract.SCHEMA_VERSION,
            evidenceCount = input.evidenceCount,
            driveId = input.driveId,
            captureSource = "drive_live",
            sourceEventKey = sourceEventKey,
            sourceEventKeysJson = JSONArray(listOf(sourceEventKey)).toString(),
            capturedAt = input.capturedAtMs / 1000,
            sourceOffsetS = input.sourceOffsetMs / 1000.0,
            gpsAccuracy = input.gpsAccuracy,
            speedMps = input.speedMps,
            heading = input.heading,
            primaryFrameIndex = input.primaryIndex,
            debugCapture = input.debug,
            dedupeEligible = true,
            sightingDriveIdsJson = JSONArray(listOf(input.driveId)).toString(),
            seenCount = 1,
            lastSeenAt = input.capturedAtMs / 1000,
            syncedToWeb = false
        )
        val sighting = EventSightingEntity(
            reportId = 0,
            driveId = input.driveId,
            lat = input.latitude,
            lng = input.longitude,
            sourceOffsetS = input.sourceOffsetMs / 1000.0,
            capturedAt = input.capturedAtMs / 1000,
            gpsAccuracy = input.gpsAccuracy,
            speedMps = input.speedMps,
            heading = input.heading,
            sourceEventKey = sourceEventKey
        )
        return InferenceOutcome(
            analyzed = true,
            accepted = true,
            decision = assessment.decision,
            assessment = assessment,
            reportEntity = report,
            sightings = listOf(sighting),
            detectionTrace = input.detectionTrace
        )
    }
}
