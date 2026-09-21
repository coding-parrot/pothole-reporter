package com.gauravsen.potholereporter.db.dao

import androidx.room.*
import com.gauravsen.potholereporter.db.entities.ReportEntity

/** Blob-free row used by frequent drive-mode spatial/deduplication scans. */
data class ReportMatchCandidate(
    val id: Long,
    val decision: String,
    val dedupe_eligible: Boolean,
    val capture_source: String,
    val source_event_key: String?,
    @ColumnInfo(name = "source_event_keys") val sourceEventKeys: String?,
    val drive_id: String?,
    @ColumnInfo(name = "sighting_drive_ids") val sightingDriveIds: String?,
    val damage_type: String?,
    val size: String?,
    val gps_accuracy: Float?,
    val speed_mps: Float?,
    val heading: Float?,
    val lat: Double?,
    val lng: Double?,
    val captured_at: Double?,
    val source_offset_s: Double?,
    val created_at: Double,
    val last_seen_at: Double?,
    val seen_count: Int,
)

/**
 * What Home lists for each report. No image BLOBs: Home renders this on every return,
 * and the one evidence photo a tester opens comes from getThumbnail/getFullPhoto.
 */
data class ReportListing(
    val id: Long,
    val assessment: String?,
    val image_quality: String?,
    val damage_type: String?,
    val size: String?,
    val description: String?,
    val decision: String,
    val status: String,
    val lat: Double?,
    val lng: Double?,
    val gps_accuracy: Float?,
    val speed_mps: Float?,
    val heading: Float?,
    val address: String?,
    val drive_id: String?,
    val capture_source: String,
    val source_event_key: String?,
    val captured_at: Double?,
    val created_at: Double,
    val last_seen_at: Double?,
    val seen_count: Int,
    val server_pothole_id: Long?,
    val server_duplicate: Boolean,
    val central_sync_eligible: Boolean,
    val central_sync_error: String?,
    val detection_provider: String?,
    val detection_model: String?,
    val evidence_count: Int,
    val email_subject: String?,
    val email_body: String?,
    val email_to: String?,
    val officer_title: String?,
    val body_lgd: String?,
    val body_name: String?,
    val road_ownership: String?,
    val road_ownership_detail: String?,
    val tender_number: String?,
    val contractor: String?,
    val tender_note: String?,
    val tender_resolution_reason: String?,
    val tender_resolution_checked_at: Double?,
    val unrouted_reason: String?,
    val has_photo: Boolean,
)

/** The listing view of a row already loaded in full, so every bridge reply has one shape. */
fun ReportEntity.toListing() = ReportListing(
    id = id,
    assessment = assessment,
    image_quality = image_quality,
    damage_type = damage_type,
    size = size,
    description = description,
    decision = decision,
    status = status,
    lat = lat,
    lng = lng,
    gps_accuracy = gps_accuracy,
    speed_mps = speed_mps,
    heading = heading,
    address = address,
    drive_id = drive_id,
    capture_source = capture_source,
    source_event_key = source_event_key,
    captured_at = captured_at,
    created_at = created_at,
    last_seen_at = last_seen_at,
    seen_count = seen_count,
    server_pothole_id = server_pothole_id,
    server_duplicate = server_duplicate,
    central_sync_eligible = central_sync_eligible,
    central_sync_error = central_sync_error,
    detection_provider = detection_provider,
    detection_model = detection_model,
    evidence_count = evidence_count,
    email_subject = email_subject,
    email_body = email_body,
    email_to = email_to,
    officer_title = officer_title,
    body_lgd = body_lgd,
    body_name = body_name,
    road_ownership = road_ownership,
    road_ownership_detail = road_ownership_detail,
    tender_number = tender_number,
    contractor = contractor,
    tender_note = tender_note,
    tender_resolution_reason = tender_resolution_reason,
    tender_resolution_checked_at = tender_resolution_checked_at,
    unrouted_reason = unrouted_reason,
    has_photo = photo != null && photo.isNotEmpty(),
)

@Dao
interface ReportDao {
    @Insert
    suspend fun insert(report: ReportEntity): Long

    @Update
    suspend fun update(report: ReportEntity): Int

    @Query("SELECT * FROM reports ORDER BY id DESC")
    suspend fun getAll(): List<ReportEntity>

    @Query("SELECT id FROM reports ORDER BY id DESC")
    suspend fun getAllIds(): List<Long>

    // Home's listing on every render. length() reads the BLOB size from the record
    // header, so neither image is loaded.
    @Query("""
        SELECT id,assessment,image_quality,damage_type,size,description,decision,status,
          lat,lng,gps_accuracy,speed_mps,heading,address,drive_id,capture_source,source_event_key,
          captured_at,created_at,last_seen_at,seen_count,server_pothole_id,server_duplicate,
          central_sync_eligible,central_sync_error,detection_provider,detection_model,
          evidence_count,email_subject,email_body,email_to,officer_title,body_lgd,
          body_name,road_ownership,road_ownership_detail,tender_number,contractor,
          tender_note,tender_resolution_reason,tender_resolution_checked_at,unrouted_reason,
          (photo IS NOT NULL AND length(photo) > 0) AS has_photo
        FROM reports ORDER BY id DESC
    """)
    suspend fun listForHome(): List<ReportListing>

    @Query("SELECT * FROM reports WHERE id = :id")
    suspend fun getById(id: Long): ReportEntity?

    @Query("SELECT photo FROM reports WHERE id = :id")
    suspend fun getThumbnail(id: Long): ByteArray?

    @Query("SELECT photo_full FROM reports WHERE id = :id")
    suspend fun getFullPhoto(id: Long): ByteArray?

    @Query("SELECT EXISTS(SELECT 1 FROM reports WHERE id = :id)")
    suspend fun exists(id: Long): Boolean

    @Query("SELECT * FROM reports WHERE server_pothole_id = :serverId ORDER BY id LIMIT 1")
    suspend fun getByServerPotholeId(serverId: Long): ReportEntity?

    @Query("""
        SELECT id FROM reports
        WHERE decision = 'accept'
          AND central_sync_eligible = 1
          AND (status = 'draft'
            OR (lat IS NOT NULL AND lng IS NOT NULL
              AND (tender_resolution_checked_at IS NULL
                OR (detection_provider = 'shared_server' AND road_ownership IS NULL)))
            OR (server_pothole_id IS NULL AND central_sync_error IS NULL))
        ORDER BY id
    """)
    suspend fun centralWorkIds(): List<Long>

    @Query("DELETE FROM reports WHERE id = :id")
    suspend fun deleteById(id: Long)

    @Query("SELECT * FROM reports WHERE drive_id = :driveId")
    suspend fun getByDriveId(driveId: String): List<ReportEntity>

    // Frequent drive scans deliberately omit all image BLOBs.
    @Query("""
        SELECT id,decision,dedupe_eligible,capture_source,
          source_event_key,source_event_keys,drive_id,sighting_drive_ids,
          damage_type,size,gps_accuracy,speed_mps,heading,lat,lng,captured_at,
          source_offset_s,created_at,last_seen_at,seen_count
        FROM reports
        WHERE decision = 'accept' AND lat BETWEEN :minLat AND :maxLat
    """)
    suspend fun findInLatBand(minLat: Double, maxLat: Double): List<ReportMatchCandidate>

    // For dedup: find reports from the same drive
    @Query("""
        SELECT id,decision,dedupe_eligible,capture_source,
          source_event_key,source_event_keys,drive_id,sighting_drive_ids,
          damage_type,size,gps_accuracy,speed_mps,heading,lat,lng,captured_at,
          source_offset_s,created_at,last_seen_at,seen_count
        FROM reports
        WHERE decision = 'accept' AND drive_id = :driveId
    """)
    suspend fun findAcceptedByDriveId(driveId: String): List<ReportMatchCandidate>

    // For dedup: find reports that have sighted this drive
    // Uses LIKE since sighting_drive_ids is a JSON array stored as text
    @Query("""
        SELECT id,decision,dedupe_eligible,capture_source,
          source_event_key,source_event_keys,drive_id,sighting_drive_ids,
          damage_type,size,gps_accuracy,speed_mps,heading,lat,lng,captured_at,
          source_offset_s,created_at,last_seen_at,seen_count
        FROM reports
        WHERE decision = 'accept' AND sighting_drive_ids LIKE '%' || :driveId || '%'
    """)
    suspend fun findBySightingDriveId(driveId: String): List<ReportMatchCandidate>

    @Query("SELECT COUNT(*) FROM reports")
    suspend fun count(): Int

    @Query("DELETE FROM reports")
    suspend fun deleteAll()
}
