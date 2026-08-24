package dev.aiengg.potholereporter.drive

import androidx.room.withTransaction
import dev.aiengg.potholereporter.db.PotholeDatabase
import dev.aiengg.potholereporter.db.RepairObservationEntity
import dev.aiengg.potholereporter.db.RepairTargetEntity
import kotlin.math.abs

internal object NativeRepairTime {
    fun isStrictlyAfter(capturedAt: Long, lastDamageObservedAt: Long): Boolean =
        capturedAt > 0L && lastDamageObservedAt > 0L && capturedAt > lastDamageObservedAt
}

object NativeRepairDecision {
    fun fromModel(
        currentCondition: String,
        assessment: String,
        imageQuality: String,
        sameLocationVisible: Boolean,
        completedRepairVisible: Boolean
    ): String? = when {
        currentCondition == "repaired" && assessment == "clear" &&
            imageQuality == "usable" && sameLocationVisible && completedRepairVisible -> "fixed"
        currentCondition == "repaired" && assessment == "probable" &&
            imageQuality == "usable" && sameLocationVisible && completedRepairVisible -> "repair_review"
        else -> null
    }

    fun acceptsObservation(result: RepairVerificationResult): Boolean = when (result.currentCondition) {
        "fixed" -> result.assessment == "clear" && result.imageQuality == "usable" &&
            result.sameLocationVisible && result.completedRepairVisible
        "repair_review" -> result.assessment == "probable" && result.imageQuality == "usable" &&
            result.sameLocationVisible && result.completedRepairVisible
        else -> false
    }
}

/**
 * Conservative, deterministic pre-filter for a costly before/after vision request.
 * A nearby clean frame is not enough: both fixes must be precise, both headings must
 * identify the same carriageway, and one target must be unambiguously closer.
 */
object NativeRepairCandidateMatcher {
    const val MAX_GPS_ACCURACY_M = 12f
    const val MAX_DISTANCE_M = 5.0
    const val MAX_HEADING_DIFFERENCE_DEG = 35f
    const val MIN_MOVING_SPEED_MPS = 2f

    private val repairableDamageTypes = setOf(
        "pothole_cavity",
        "failed_patch",
        "surface_breakup",
        "rut_or_depression",
        "other_road_damage"
    )

    fun selectCandidate(
        targets: List<RepairTargetEntity>,
        lat: Double,
        lng: Double,
        gpsAccuracy: Float?,
        speedMps: Float?,
        heading: Float?,
        capturedAt: Long,
        driveId: String
    ): RepairTargetEntity? {
        if (!lat.isFinite() || !lng.isFinite() || abs(lat) > 90 || abs(lng) > 180) return null
        if (capturedAt <= 0L) return null
        if (gpsAccuracy == null || !gpsAccuracy.isFinite() || gpsAccuracy < 0f ||
            gpsAccuracy > MAX_GPS_ACCURACY_M) return null
        if (speedMps == null || !speedMps.isFinite() || speedMps < MIN_MOVING_SPEED_MPS) return null
        if (heading == null || !heading.isFinite()) return null

        val eligible = targets.mapNotNull { target ->
            if (target.conditionStatus != "open" && target.conditionStatus != "repair_review") {
                return@mapNotNull null
            }
            // Imported/gallery images are not tied to their alleged capture location.
            if (target.captureSource == "manual_import") return@mapNotNull null
            if (!repairableDamageTypes.contains(target.damageType)) return@mapNotNull null
            if (!target.lat.isFinite() || !target.lng.isFinite() ||
                abs(target.lat) > 90 || abs(target.lng) > 180) return@mapNotNull null
            val targetAccuracy = target.gpsAccuracy
            if (targetAccuracy == null || !targetAccuracy.isFinite() || targetAccuracy < 0f ||
                targetAccuracy > MAX_GPS_ACCURACY_M) return@mapNotNull null
            val targetHeading = target.heading
            if (targetHeading == null || !targetHeading.isFinite() ||
                NativeDeduplicationEngine.headingDifference(heading, targetHeading) >
                MAX_HEADING_DIFFERENCE_DEG) return@mapNotNull null
            if (target.lastObservedDriveId == driveId) return@mapNotNull null
            if (!NativeRepairTime.isStrictlyAfter(capturedAt, target.lastDamageObservedAt)) {
                return@mapNotNull null
            }

            val distance = NativeDeduplicationEngine.distMeters(lat, lng, target.lat, target.lng)
            if (distance > MAX_DISTANCE_M) null else target to distance
        }.sortedBy { it.second }

        // Never choose between two physical reports, even when one GPS point is closer.
        // Phone GPS error can easily exceed that difference; only one candidate is safe.
        return eligible.singleOrNull()?.first
    }
}

class NativeRepairStatusEngine(
    private val database: PotholeDatabase
) {
    private val targetDao = database.repairTargetDao()
    private val observationDao = database.repairObservationDao()

    suspend fun findCandidate(
        lat: Double,
        lng: Double,
        gpsAccuracy: Float?,
        speedMps: Float?,
        heading: Float?,
        capturedAt: Long,
        driveId: String
    ): RepairTargetEntity? {
        if (!lat.isFinite() || !lng.isFinite()) return null
        val latitudeBand = NativeRepairCandidateMatcher.MAX_DISTANCE_M / 110900.0
        val targets = targetDao.getTargetsInLatitudeBand(lat - latitudeBand, lat + latitudeBand)
        return NativeRepairCandidateMatcher.selectCandidate(
            targets, lat, lng, gpsAccuracy, speedMps, heading, capturedAt, driveId
        )
    }

    /**
     * Commits the target transition and its sync outbox row together. The unique source
     * key makes a retried burst harmless, and all semantic gates are checked again here
     * rather than trusting the caller or model output alone.
     */
    suspend fun queueObservation(
        target: RepairTargetEntity,
        verification: RepairVerificationResult,
        sourceEventKey: String,
        observedAt: Long,
        driveId: String,
        lat: Double,
        lng: Double,
        gpsAccuracy: Float?,
        speedMps: Float?,
        heading: Float?
    ): Boolean = database.withTransaction {
        if (!NativeRepairDecision.acceptsObservation(verification)) return@withTransaction false
        val condition = verification.currentCondition

        val current = targetDao.getTarget(target.reportId) ?: return@withTransaction false
        if (current.conditionStatus == "fixed" || current.lastObservedDriveId == driveId) {
            return@withTransaction false
        }
        // Re-check against the row loaded inside this transaction. A stale candidate or
        // same-second capture must never claim that an older defect has since been fixed.
        if (!NativeRepairTime.isStrictlyAfter(observedAt, current.lastDamageObservedAt)) {
            return@withTransaction false
        }

        val observation = RepairObservationEntity(
            targetReportId = current.reportId,
            sourceEventKey = sourceEventKey.take(180),
            observedAt = observedAt,
            driveId = driveId.take(128),
            lat = lat,
            lng = lng,
            gpsAccuracy = gpsAccuracy,
            speedMps = speedMps,
            heading = heading,
            currentPhotoPath = verification.currentPhotoPath,
            currentCondition = condition,
            assessment = verification.assessment,
            imageQuality = verification.imageQuality,
            sameLocationVisible = verification.sameLocationVisible,
            completedRepairVisible = verification.completedRepairVisible,
            description = verification.description,
            detectionModel = verification.detectionModel,
            imageDetail = verification.imageDetail,
            promptVersion = verification.promptVersion,
            schemaVersion = verification.schemaVersion
        )
        val observationId = observationDao.insertObservation(observation)
        if (observationId <= 0) return@withTransaction false

        targetDao.updateTarget(current.copy(
            conditionStatus = condition,
            lastObservedDriveId = driveId,
            lastObservedAt = observedAt
        ))
        true
    }
}
