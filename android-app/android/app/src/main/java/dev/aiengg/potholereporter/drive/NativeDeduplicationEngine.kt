package dev.aiengg.potholereporter.drive

import dev.aiengg.potholereporter.db.EventSightingEntity
import dev.aiengg.potholereporter.db.ReportEntity
import dev.aiengg.potholereporter.db.PotholeDatabase
import androidx.room.withTransaction
import org.json.JSONArray
import kotlin.math.*

data class DedupeResult(
    val isDuplicate: Boolean,
    val existingReportId: Long?,
    val matchKind: String? = null
)

class NativeDeduplicationEngine(
    private val database: PotholeDatabase
) {
    private val reportDao = database.reportDao()
    private val sightingDao = database.eventSightingDao()
    companion object {
        const val DEDUPE_ADJACENT_RADIUS_M = 12.0
        const val DEDUPE_HISTORY_RADIUS_M = 8.0
        const val DEDUPE_MISSING_HEADING_RADIUS_M = 5.0
        const val DEDUPE_SAME_DRIVE_S = 4.0
        const val DEDUPE_POOR_GPS_S = 2.0
        const val DEDUPE_HISTORY_S = 30L * 24 * 3600

        fun distMeters(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
            val r = 6371000.0
            val dLat = Math.toRadians(lat2 - lat1)
            val dLon = Math.toRadians(lon2 - lon1)
            val a = sin(dLat / 2).pow(2.0) +
                    cos(Math.toRadians(lat1)) * cos(Math.toRadians(lat2)) *
                    sin(dLon / 2).pow(2.0)
            val c = 2.0 * atan2(sqrt(a), sqrt(1.0 - a))
            return r * c
        }

        fun headingDifference(a: Float, b: Float): Float {
            val diff = abs(((a - b + 180f) % 360f + 360f) % 360f - 180f)
            return diff
        }
    }

    suspend fun checkAndCommitReport(
        candidate: ReportEntity,
        sightings: List<EventSightingEntity>
    ): DedupeResult = database.withTransaction {
        if (!candidate.dedupeEligible || candidate.debugCapture) {
            val newId = reportDao.insertReport(candidate)
            val mappedSightings = sightings.map { it.copy(reportId = newId) }
            sightingDao.insertSightings(mappedSightings)
            return@withTransaction DedupeResult(isDuplicate = false, existingReportId = newId)
        }

        val candLat = candidate.lat
        val candLng = candidate.lng
        val candidates = mutableListOf<ReportEntity>()

        if (candLat != null && candLng != null) {
            val latitudeBand = DEDUPE_HISTORY_RADIUS_M / 110900.0
            candidates.addAll(reportDao.getCandidateReportsInLatitudeBand(candLat - latitudeBand, candLat + latitudeBand))
        }

        val driveId = candidate.driveId
        if (driveId != null) {
            val driveReports = reportDao.getReportsForDrive(driveId)
            for (dr in driveReports) {
                if (candidates.none { it.id == dr.id }) {
                    candidates.add(dr)
                }
            }
        }

        for (prior in candidates) {
            val match = matchRoadEvent(candidate, prior)
            if (match != null) {
                val priorSightings = sightingDao.getSightingsForReport(prior.id).toMutableList()
                val currentDrive = candidate.driveId
                val observedAt = candidate.capturedAt ?: (System.currentTimeMillis() / 1000)
                val cutoff = observedAt - DEDUPE_HISTORY_S

                val filteredSightings = priorSightings.filter { s ->
                    val seenAt = s.capturedAt
                    (s.driveId != null && s.driveId == currentDrive) || seenAt == null || seenAt >= cutoff
                }.toMutableList()

                val sameDriveCount = if (currentDrive == null) 0 else filteredSightings.count { it.driveId == currentDrive }
                val exactReplay = match == "same_source"

                if ((match == "same_drive" || match == "prior_drive") && !exactReplay && (currentDrive == null || sameDriveCount < 64)) {
                    filteredSightings.add(
                        EventSightingEntity(
                            reportId = prior.id,
                            driveId = candidate.driveId,
                            lat = candidate.lat,
                            lng = candidate.lng,
                            sourceOffsetS = candidate.sourceOffsetS,
                            capturedAt = candidate.capturedAt,
                            gpsAccuracy = candidate.gpsAccuracy,
                            speedMps = candidate.speedMps,
                            heading = candidate.heading,
                            sourceEventKey = candidate.sourceEventKey
                        )
                    )
                }

                val sightingDrives = filteredSightings.mapNotNull { it.driveId }.distinct()
                val keys = parseJsonArray(prior.sourceEventKeysJson)
                if (candidate.sourceEventKey != null && !keys.contains(candidate.sourceEventKey)) {
                    keys.add(candidate.sourceEventKey)
                }

                val updated = prior.copy(
                    sourceEventKeysJson = JSONArray(keys.takeLast(64)).toString(),
                    sightingDriveIdsJson = JSONArray(sightingDrives).toString(),
                    seenCount = if (exactReplay) prior.seenCount else prior.seenCount + 1,
                    lastSeenAt = max(prior.lastSeenAt ?: 0L, candidate.capturedAt ?: 0L),
                    syncedToWeb = false // Notify UI of update
                )

                reportDao.updateReport(updated)
                sightingDao.deleteSightingsForReport(prior.id)
                sightingDao.insertSightings(filteredSightings)

                return@withTransaction DedupeResult(
                    isDuplicate = true,
                    existingReportId = prior.id,
                    matchKind = match
                )
            }
        }

        // No match found -> Insert as new canonical report
        val newId = reportDao.insertReport(candidate)
        val mappedSightings = sightings.map { it.copy(reportId = newId) }
        sightingDao.insertSightings(mappedSightings)

        DedupeResult(isDuplicate = false, existingReportId = newId)
    }

    private suspend fun matchRoadEvent(candidate: ReportEntity, prior: ReportEntity): String? {
        if (!candidate.dedupeEligible || prior.debugCapture || !prior.dedupeEligible) return null
        if (candidate.status == "draft" && prior.status == "unrouted") return null

        val priorKeys = parseJsonArray(prior.sourceEventKeysJson)
        if (candidate.sourceEventKey != null &&
            (prior.sourceEventKey == candidate.sourceEventKey || priorKeys.contains(candidate.sourceEventKey))
        ) {
            return "same_source"
        }

        if (candidate.captureSource == "manual") return null
        if (!areDamageTypesCompatible(candidate.damageType, prior.damageType)) return null
        if (sizeConflict(candidate.size, prior.size)) return null

        val candLat = candidate.lat
        val candLng = candidate.lng
        val priorLat = prior.lat
        val priorLng = prior.lng

        val positioned = candLat != null && candLng != null && priorLat != null && priorLng != null
        val distance = if (positioned) distMeters(candLat!!, candLng!!, priorLat!!, priorLng!!) else Double.POSITIVE_INFINITY

        val candidateDrive = candidate.driveId
        if (candidateDrive != null) {
            val priorSightings = sightingDao.getSightingsForReport(prior.id)
            val sameDriveSightings = priorSightings.filter { it.driveId == candidateDrive }
            if (sameDriveSightings.isNotEmpty()) {
                val matchesAll = sameDriveSightings.all { s ->
                    val candidateOffset = candidate.sourceOffsetS
                    val candidateTime = candidate.capturedAt?.toDouble()
                    val seenOffset = s.sourceOffsetS
                    val seenTime = s.capturedAt?.toDouble()
                    val seconds = min(
                        finiteDelta(candidateOffset, seenOffset),
                        finiteDelta(candidateTime, seenTime)
                    )
                    if (!seconds.isFinite()) return@all false
                    val sLat = s.lat
                    val sLng = s.lng
                    val positionedSighting = sLat != null && sLng != null && candLat != null && candLng != null
                    val poorGps = candidate.gpsAccuracy == null || s.gpsAccuracy == null ||
                            candidate.gpsAccuracy > 30f || s.gpsAccuracy > 30f
                    if (!positionedSighting || poorGps) return@all seconds <= DEDUPE_POOR_GPS_S

                    val sightingDistance = distMeters(candLat!!, candLng!!, sLat!!, sLng!!)
                    val stationary = candidate.speedMps != null && s.speedMps != null &&
                            candidate.speedMps <= 1f && s.speedMps <= 1f
                    if (stationary) seconds <= 30.0 && sightingDistance <= 5.0
                    else seconds <= DEDUPE_SAME_DRIVE_S && sightingDistance <= DEDUPE_ADJACENT_RADIUS_M
                }
                if (matchesAll) return "same_drive"
                return null
            }
        }

        // Cross-drive match
        val candAcc = candidate.gpsAccuracy
        val priorAcc = prior.gpsAccuracy
        if (!positioned || candAcc == null || priorAcc == null || candAcc > 15f || priorAcc > 15f) return null

        val candTime = candidate.lastSeenAt ?: candidate.capturedAt ?: candidate.createdAt
        val priorTime = prior.lastSeenAt ?: prior.capturedAt ?: prior.createdAt
        val age = abs(candTime - priorTime)
        if (age > DEDUPE_HISTORY_S) return null

        val left = candidate.damageType ?: "none"
        val right = prior.damageType ?: "none"
        if (left == "other_road_damage" || right == "other_road_damage") return null

        var radius = if (left == right) DEDUPE_HISTORY_RADIUS_M else 5.0
        val candSpeed = candidate.speedMps
        val priorSpeed = prior.speedMps
        val moving = candSpeed != null && priorSpeed != null && candSpeed >= 2f && priorSpeed >= 2f

        val candHeading = candidate.heading
        val priorHeading = prior.heading
        val headingsKnown = candHeading != null && priorHeading != null

        if (moving && headingsKnown) {
            if (headingDifference(candHeading!!, priorHeading!!) > 45f) return null
        } else {
            radius = min(radius, DEDUPE_MISSING_HEADING_RADIUS_M)
        }

        return if (distance <= radius) "prior_drive" else null
    }

    private fun areDamageTypesCompatible(left: String?, right: String?): Boolean {
        if (left == null || right == null) return false
        if (left == right) return true
        val localDamageFamily = setOf("pothole_cavity", "failed_patch")
        return localDamageFamily.contains(left) && localDamageFamily.contains(right)
    }

    private fun sizeConflict(left: String?, right: String?): Boolean =
        (left == "small" && right == "large") || (left == "large" && right == "small")

    private fun finiteDelta(left: Double?, right: Double?): Double =
        if (left != null && right != null && left.isFinite() && right.isFinite()) abs(left - right)
        else Double.POSITIVE_INFINITY

    private fun parseJsonArray(json: String?): MutableList<String> {
        val list = mutableListOf<String>()
        if (json.isNullOrBlank()) return list
        try {
            val array = JSONArray(json)
            for (i in 0 until array.length()) {
                list.add(array.getString(i))
            }
        } catch (_: Exception) {}
        return list
    }
}
