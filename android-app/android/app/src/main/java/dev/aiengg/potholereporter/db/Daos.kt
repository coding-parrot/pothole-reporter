package dev.aiengg.potholereporter.db

import androidx.room.*

/** Small projection for media cleanup; avoids loading Base64 report bodies into memory. */
data class ReportMediaRef(
    val id: Long,
    val photoPath: String?,
    val photoFullPath: String?,
    val photoDataUrlChars: Long?,
    val driveId: String?,
    val sourceEventKey: String?,
    val syncedToWeb: Boolean
)

/**
 * Bounded native-to-WebView projection. The potentially large Base64 thumbnail is not
 * hydrated while scanning; only its character count is read until this row is selected.
 */
data class ReportSyncCandidate(
    val id: Long,
    val createdAt: Long,
    val lat: Double?,
    val lng: Double?,
    val address: String?,
    val photoPath: String?,
    val photoFullPath: String?,
    val photoDataUrlChars: Long?,
    val isReportable: Int,
    val isPothole: Int,
    val looksLikeSpeedBreaker: Boolean,
    val damageType: String?,
    val surfaceType: String,
    val defectType: String,
    val measurementProvenance: String,
    val measurementConfidence: String,
    val assessment: String?,
    val imageQuality: String?,
    val onDrivableSurface: Boolean,
    val hasLocalizedCavity: Boolean,
    val hasUnambiguousLowerInterior: Boolean,
    val hasBrokenEdgeOrRim: Boolean,
    val hasDepthOrSurfaceLoss: Boolean,
    val temporalConsistency: String?,
    val size: String?,
    val decision: String?,
    val description: String?,
    val emailSubject: String?,
    val emailBody: String?,
    val status: String,
    val detectionModel: String?,
    val imageDetail: String?,
    val promptVersion: String?,
    val schemaVersion: Int,
    val evidenceCount: Int,
    val driveId: String?,
    val captureSource: String,
    val sourceEventKey: String?,
    val capturedAt: Long?,
    val sourceOffsetS: Double?,
    val gpsAccuracy: Float?,
    val speedMps: Float?,
    val heading: Float?,
    val seenCount: Int,
    val lastSeenAt: Long?,
    val primaryFrameIndex: Int,
    val debugCapture: Boolean
)

/**
 * Minimal ownership projection used while reconciling private Drive keyframe files.
 *
 * A selected burst can include multiple JPEGs and long drives can retain thousands of
 * rows. Reconciliation only needs this identity triple; hydrating capture metadata (or
 * every full [DriveKeyframeEntity]) would make an otherwise idle bridge call scale with
 * the entire retained history.
 */
data class DriveKeyframeOwnershipRef(
    val id: Long,
    val sessionId: String,
    val filePath: String
)

/** Lightweight cursor page for the automatic saved-frame replay scheduler. */
data class PendingKeyframeSession(
    val sessionId: String,
    val pendingCount: Int
)

/** A keyframe owner whose parent session write did not survive process death. */
data class MissingKeyframeSessionRef(
    val sessionId: String,
    val firstCapturedAtMs: Long
)

@Dao
interface ReportDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertReport(report: ReportEntity): Long

    @Update
    suspend fun updateReport(report: ReportEntity)

    @Query("SELECT * FROM reports ORDER BY id DESC")
    suspend fun getAllReports(): List<ReportEntity>

    @Query("SELECT * FROM reports WHERE id = :id")
    suspend fun getReportById(id: Long): ReportEntity?

    @Query("""SELECT id, photoPath, photoFullPath, length(photoDataUrl) AS photoDataUrlChars,
        driveId, sourceEventKey, syncedToWeb
        FROM reports ORDER BY id ASC""")
    suspend fun getAllReportMediaRefs(): List<ReportMediaRef>

    @Query("""SELECT id, photoPath, photoFullPath, length(photoDataUrl) AS photoDataUrlChars,
        driveId, sourceEventKey, syncedToWeb
        FROM reports WHERE id IN (:ids)""")
    suspend fun getReportMediaRefs(ids: List<Long>): List<ReportMediaRef>

    @Query("SELECT id FROM reports ORDER BY id DESC LIMIT 1")
    suspend fun getNewestReportMediaId(): Long?

    @Query("""SELECT id, photoPath, photoFullPath, length(photoDataUrl) AS photoDataUrlChars,
        driveId, sourceEventKey, syncedToWeb FROM reports
        WHERE id > :afterId AND id <= :throughId ORDER BY id ASC LIMIT :limit""")
    suspend fun getReportMediaRefPage(
        afterId: Long,
        throughId: Long,
        limit: Int
    ): List<ReportMediaRef>

    @Query("""SELECT id, createdAt, lat, lng, address, photoPath, photoFullPath,
        length(photoDataUrl) AS photoDataUrlChars,
        isReportable, isPothole, looksLikeSpeedBreaker, damageType, surfaceType,
        defectType, measurementProvenance, measurementConfidence, assessment, imageQuality,
        onDrivableSurface, hasLocalizedCavity, hasUnambiguousLowerInterior,
        hasBrokenEdgeOrRim, hasDepthOrSurfaceLoss,
        temporalConsistency, size, decision, description, emailSubject, emailBody, status,
        detectionModel, imageDetail, promptVersion, schemaVersion, evidenceCount, driveId,
        captureSource, sourceEventKey, capturedAt, sourceOffsetS, gpsAccuracy, speedMps,
        heading, seenCount, lastSeenAt, primaryFrameIndex, debugCapture
        FROM reports
        WHERE syncedToWeb = 0 AND id > :afterId
        ORDER BY id ASC LIMIT :limit""")
    suspend fun getUnsyncedReportCandidatesAfter(
        afterId: Long,
        limit: Int
    ): List<ReportSyncCandidate>

    @Query("SELECT photoDataUrl FROM reports WHERE syncedToWeb = 0 AND id = :id")
    suspend fun getUnsyncedReportPhotoDataUrl(id: Long): String?

    @Query("SELECT COUNT(*) FROM reports WHERE syncedToWeb = 0")
    suspend fun countUnsyncedReports(): Int

    @Query("UPDATE reports SET syncedToWeb = 1 WHERE id IN (:ids)")
    suspend fun markReportsSynced(ids: List<Long>)

    @Query("""UPDATE reports SET syncedToWeb = 1, photoPath = NULL,
        photoFullPath = NULL, photoDataUrl = NULL WHERE id IN (:ids)""")
    suspend fun acknowledgeAndReleasePhotos(ids: List<Long>): Int

    @Query("SELECT * FROM reports WHERE lat BETWEEN :minLat AND :maxLat")
    suspend fun getCandidateReportsInLatitudeBand(minLat: Double, maxLat: Double): List<ReportEntity>

    @Query("SELECT * FROM reports WHERE driveId = :driveId")
    suspend fun getReportsForDrive(driveId: String): List<ReportEntity>

    @Query("DELETE FROM reports")
    suspend fun clearAll()

    @Query("DELETE FROM reports WHERE id = :id")
    suspend fun deleteReport(id: Long): Int
}

@Dao
interface EventSightingDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertSightings(sightings: List<EventSightingEntity>)

    @Query("SELECT * FROM event_sightings WHERE reportId = :reportId")
    suspend fun getSightingsForReport(reportId: Long): List<EventSightingEntity>

    @Query("DELETE FROM event_sightings")
    suspend fun clearAll()

    @Query("DELETE FROM event_sightings WHERE reportId = :reportId")
    suspend fun deleteSightingsForReport(reportId: Long)

    @Query("DELETE FROM event_sightings WHERE reportId IN (:reportIds)")
    suspend fun deleteSightingsForReports(reportIds: List<Long>)
}

@Dao
interface RepairTargetDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertTargets(targets: List<RepairTargetEntity>)

    @Query("SELECT * FROM repair_targets WHERE reportId = :reportId")
    suspend fun getTarget(reportId: Long): RepairTargetEntity?

    @Query("SELECT * FROM repair_targets WHERE lat BETWEEN :minLat AND :maxLat")
    suspend fun getTargetsInLatitudeBand(minLat: Double, maxLat: Double): List<RepairTargetEntity>

    @Query("SELECT * FROM repair_targets")
    suspend fun getAllTargets(): List<RepairTargetEntity>

    @Update
    suspend fun updateTarget(target: RepairTargetEntity)

    @Query("DELETE FROM repair_targets")
    suspend fun clearAll()

    @Query("DELETE FROM repair_targets WHERE reportId IN (:reportIds)")
    suspend fun deleteTargets(reportIds: List<Long>)
}

@Dao
interface RepairObservationDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertObservation(observation: RepairObservationEntity): Long

    @Query("SELECT * FROM repair_observations WHERE syncedToWeb = 0 ORDER BY id ASC")
    suspend fun getUnsyncedObservations(): List<RepairObservationEntity>

    /** Lightweight ownership inventory for the persistent report-evidence quota. */
    @Query("SELECT currentPhotoPath FROM repair_observations ORDER BY id ASC")
    suspend fun getAllPhotoPaths(): List<String>

    @Query("""SELECT * FROM repair_observations
        WHERE syncedToWeb = 0 AND id > :afterId
        ORDER BY id ASC LIMIT :limit""")
    suspend fun getUnsyncedObservationsAfter(
        afterId: Long,
        limit: Int
    ): List<RepairObservationEntity>

    @Query("SELECT COUNT(*) FROM repair_observations WHERE syncedToWeb = 0")
    suspend fun countUnsyncedObservations(): Int

    @Query("SELECT * FROM repair_observations WHERE id IN (:ids)")
    suspend fun getObservations(ids: List<Long>): List<RepairObservationEntity>

    @Query("UPDATE repair_observations SET syncedToWeb = 1 WHERE id IN (:ids)")
    suspend fun markObservationsSynced(ids: List<Long>)

    @Query("DELETE FROM repair_observations WHERE id IN (:ids)")
    suspend fun deleteObservations(ids: List<Long>): Int

    @Query("DELETE FROM repair_observations")
    suspend fun clearAll()
}

@Dao
interface SessionDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertSession(session: SessionEntity)

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertSessionIfMissing(session: SessionEntity): Long

    @Update
    suspend fun updateSession(session: SessionEntity)

    @Query("SELECT * FROM sessions WHERE id = :id")
    suspend fun getSession(id: String): SessionEntity?

    @Query("SELECT * FROM sessions ORDER BY startedAt DESC")
    suspend fun getAllSessions(): List<SessionEntity>

    @Query("DELETE FROM sessions")
    suspend fun clearAll()

    @Query("UPDATE sessions SET status = 'interrupted', endedAt = :endedAt WHERE status IN ('active', 'paused')")
    suspend fun markAllStaleInterrupted(endedAt: Long)

    @Query("UPDATE sessions SET status = 'interrupted', endedAt = :endedAt WHERE status IN ('active', 'paused') AND id != :activeSessionId")
    suspend fun markOtherStaleInterrupted(activeSessionId: String, endedAt: Long)
}

@Dao
interface FootageDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertSegment(segment: FootageSegmentEntity): Long

    @Query("SELECT * FROM footage_segments WHERE sessionId = :sessionId ORDER BY startedAt ASC")
    suspend fun getSegmentsForSession(sessionId: String): List<FootageSegmentEntity>

    @Query("""SELECT * FROM footage_segments
        WHERE sessionId = :sessionId AND endedAt >= :windowStartSeconds AND startedAt <= :windowEndSeconds
        ORDER BY startedAt ASC""")
    suspend fun getSegmentsNear(
        sessionId: String,
        windowStartSeconds: Long,
        windowEndSeconds: Long
    ): List<FootageSegmentEntity>

    @Query("SELECT * FROM footage_segments ORDER BY startedAt DESC")
    suspend fun getAllSegments(): List<FootageSegmentEntity>

    @Query("DELETE FROM footage_segments WHERE sessionId = :sessionId")
    suspend fun deleteForSession(sessionId: String)

    @Query("DELETE FROM footage_segments WHERE id = :id")
    suspend fun deleteSegment(id: Long)

    @Query("DELETE FROM footage_segments")
    suspend fun clearAll()
}

@Dao
interface DriveKeyframeDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertKeyframe(keyframe: DriveKeyframeEntity): Long

    @Query("SELECT * FROM drive_keyframes WHERE id = :id")
    suspend fun getKeyframe(id: Long): DriveKeyframeEntity?

    @Query("""SELECT * FROM drive_keyframes
        WHERE sessionId = :sessionId AND captureSeq = :captureSeq LIMIT 1""")
    suspend fun getBySessionAndCaptureSeq(
        sessionId: String,
        captureSeq: Int
    ): DriveKeyframeEntity?

    @Query("SELECT * FROM drive_keyframes WHERE sessionId = :sessionId ORDER BY captureSeq ASC LIMIT :limit")
    suspend fun getForSession(sessionId: String, limit: Int): List<DriveKeyframeEntity>

    @Query("""SELECT * FROM drive_keyframes
        WHERE sessionId = :sessionId AND liveAnalyzed = 0
        ORDER BY captureSeq ASC LIMIT :limit""")
    suspend fun getPendingForSession(
        sessionId: String,
        limit: Int
    ): List<DriveKeyframeEntity>

    @Query("SELECT COUNT(*) FROM drive_keyframes WHERE sessionId = :sessionId AND liveAnalyzed = 0")
    suspend fun countPendingForSession(sessionId: String): Int

    @Query("""SELECT sessionId AS sessionId,
        COUNT(*) AS keyframeCount,
        SUM(CASE WHEN liveAnalyzed = 0 THEN 1 ELSE 0 END) AS pendingCount,
        COALESCE(SUM(bytes), 0) AS keyframeBytes
        FROM drive_keyframes GROUP BY sessionId""")
    suspend fun getSummaries(): List<DriveKeyframeSummary>

    @Query("SELECT id FROM drive_keyframes ORDER BY id DESC LIMIT 1")
    suspend fun getNewestOwnershipId(): Long?

    @Query("""SELECT id, sessionId, filePath FROM drive_keyframes
        WHERE id > :afterId AND id <= :throughId
        ORDER BY id ASC LIMIT :limit""")
    suspend fun getOwnershipPage(
        afterId: Long,
        throughId: Long,
        limit: Int
    ): List<DriveKeyframeOwnershipRef>

    @Query("""SELECT id, sessionId, filePath FROM drive_keyframes
        WHERE sessionId = :sessionId AND filePath IN (:storedPaths)
        ORDER BY id ASC LIMIT :limit""")
    suspend fun getOwnershipPageForSession(
        sessionId: String,
        storedPaths: List<String>,
        limit: Int
    ): List<DriveKeyframeOwnershipRef>

    @Query("""SELECT drive_keyframes.sessionId AS sessionId, COUNT(*) AS pendingCount
        FROM drive_keyframes
        INNER JOIN sessions ON sessions.id = drive_keyframes.sessionId
        WHERE drive_keyframes.liveAnalyzed = 0
          AND sessions.status IN ('stopped', 'interrupted')
          AND drive_keyframes.sessionId > :afterSessionId
        GROUP BY drive_keyframes.sessionId
        ORDER BY drive_keyframes.sessionId ASC LIMIT :limit""")
    suspend fun getPendingSessionPage(
        afterSessionId: String,
        limit: Int
    ): List<PendingKeyframeSession>

    @Query("""SELECT drive_keyframes.sessionId AS sessionId,
        MIN(drive_keyframes.capturedAtMs) AS firstCapturedAtMs
        FROM drive_keyframes
        LEFT JOIN sessions ON sessions.id = drive_keyframes.sessionId
        WHERE sessions.id IS NULL
          AND drive_keyframes.sessionId > :afterSessionId
        GROUP BY drive_keyframes.sessionId
        ORDER BY drive_keyframes.sessionId ASC LIMIT :limit""")
    suspend fun getMissingSessionPage(
        afterSessionId: String,
        limit: Int
    ): List<MissingKeyframeSessionRef>

    @Query("UPDATE drive_keyframes SET liveAnalyzed = 1 WHERE id = :id")
    suspend fun markAnalyzed(id: Long)

    @Query("UPDATE drive_keyframes SET liveAnalyzed = 0 WHERE id = :id")
    suspend fun markPending(id: Long)

    @Query("DELETE FROM drive_keyframes WHERE id = :id")
    suspend fun deleteKeyframe(id: Long)

    @Query("DELETE FROM drive_keyframes WHERE sessionId = :sessionId")
    suspend fun deleteForSession(sessionId: String)

    @Query("DELETE FROM drive_keyframes")
    suspend fun clearAll()
}
