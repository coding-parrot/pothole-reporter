package com.gauravsen.potholereporter.db

import androidx.room.*

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

    @Query("SELECT * FROM reports WHERE syncedToWeb = 0 ORDER BY id ASC")
    suspend fun getUnsyncedReports(): List<ReportEntity>

    @Query("UPDATE reports SET syncedToWeb = 1 WHERE id IN (:ids)")
    suspend fun markReportsSynced(ids: List<Long>)

    @Query("SELECT * FROM reports WHERE lat BETWEEN :minLat AND :maxLat")
    suspend fun getCandidateReportsInLatitudeBand(minLat: Double, maxLat: Double): List<ReportEntity>

    @Query("SELECT * FROM reports WHERE driveId = :driveId")
    suspend fun getReportsForDrive(driveId: String): List<ReportEntity>

    @Query("DELETE FROM reports")
    suspend fun clearAll()
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
}

@Dao
interface SessionDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertSession(session: SessionEntity)

    @Update
    suspend fun updateSession(session: SessionEntity)

    @Query("SELECT * FROM sessions WHERE id = :id")
    suspend fun getSession(id: String): SessionEntity?

    @Query("SELECT * FROM sessions ORDER BY startedAt DESC")
    suspend fun getAllSessions(): List<SessionEntity>

    @Query("DELETE FROM sessions")
    suspend fun clearAll()
}
