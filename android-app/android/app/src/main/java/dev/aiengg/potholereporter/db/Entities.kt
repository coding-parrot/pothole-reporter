package dev.aiengg.potholereporter.db

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import androidx.room.ColumnInfo

@Entity(
    tableName = "reports",
    indices = [
        Index(value = ["lat"]),
        Index(value = ["driveId"]),
        Index(value = ["syncedToWeb"])
    ]
)
data class ReportEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val createdAt: Long = System.currentTimeMillis() / 1000,
    val lat: Double? = null,
    val lng: Double? = null,
    val address: String? = null,
    val photoPath: String? = null,
    val photoDataUrl: String? = null,
    val photoFullPath: String? = null,
    val isReportable: Int = 1,
    val isPothole: Int = 1,
    @ColumnInfo(defaultValue = "1")
    val looksLikeSpeedBreaker: Boolean = false,
    val damageType: String? = "pothole_cavity",
    @ColumnInfo(defaultValue = "'unknown'")
    val surfaceType: String = "unknown",
    @ColumnInfo(defaultValue = "'not_pothole'")
    val defectType: String = "not_pothole",
    @ColumnInfo(defaultValue = "'not_applicable'")
    val measurementProvenance: String = "not_applicable",
    @ColumnInfo(defaultValue = "'not_applicable'")
    val measurementConfidence: String = "not_applicable",
    val assessment: String? = "clear",
    val imageQuality: String? = "usable",
    val onDrivableSurface: Boolean = true,
    @ColumnInfo(defaultValue = "0")
    val hasLocalizedCavity: Boolean = true,
    @ColumnInfo(defaultValue = "0")
    val hasUnambiguousLowerInterior: Boolean = false,
    val hasBrokenEdgeOrRim: Boolean = true,
    val hasDepthOrSurfaceLoss: Boolean = true,
    val temporalConsistency: String? = "consistent",
    val size: String? = "medium",
    val decision: String? = "accept",
    val description: String? = null,
    val emailSubject: String? = null,
    val emailBody: String? = null,
    val status: String = "draft",
    val detectionModel: String? = "gpt-5-mini",
    val imageDetail: String? = "high",
    val promptVersion: String? = "pothole-binary-v19",
    val schemaVersion: Int = 9,
    val evidenceCount: Int = 4,
    val unroutedReason: String? = null,
    val unroutedBody: String? = null,
    val officerName: String? = null,
    val officerEmail: String? = null,
    val authorityId: String? = null,
    val authorityName: String? = null,
    val authorityRegistryVersion: Int? = null,
    val deliveryChannel: String? = "email",
    val wardCode: String? = null,
    val routingSource: String? = null,
    val highwayRef: String? = null,
    val tenderNumber: String? = null,
    val contractor: String? = null,
    val driveId: String? = null,
    val captureSource: String = "drive_live",
    val sourceEventKey: String? = null,
    val sourceEventKeysJson: String = "[]",
    val capturedAt: Long? = null,
    val sourceOffsetS: Double? = null,
    val gpsAccuracy: Float? = null,
    val speedMps: Float? = null,
    val heading: Float? = null,
    val primaryFrameIndex: Int = 0,
    val debugCapture: Boolean = false,
    val dedupeEligible: Boolean = true,
    val sightingDriveIdsJson: String = "[]",
    val seenCount: Int = 1,
    val lastSeenAt: Long? = null,
    val syncedToWeb: Boolean = false
)

@Entity(
    tableName = "event_sightings",
    indices = [
        Index(value = ["reportId"]),
        Index(value = ["driveId"])
    ]
)
data class EventSightingEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val reportId: Long,
    val driveId: String?,
    val lat: Double?,
    val lng: Double?,
    val sourceOffsetS: Double?,
    val capturedAt: Long?,
    val gpsAccuracy: Float?,
    val speedMps: Float?,
    val heading: Float?,
    val sourceEventKey: String?
)

/**
 * A lightweight mirror of a WebView report that the foreground Drive service may use
 * while the WebView (and usually Google Maps) is backgrounded. The original image is
 * deliberately retained: proximity to a clean-looking frame is not evidence that the
 * previously reported defect was repaired.
 */
@Entity(
    tableName = "repair_targets",
    indices = [
        Index(value = ["lat"]),
        Index(value = ["conditionStatus"])
    ]
)
data class RepairTargetEntity(
    @PrimaryKey
    val reportId: Long,
    val lat: Double,
    val lng: Double,
    val gpsAccuracy: Float?,
    val heading: Float?,
    val captureSource: String,
    val photoPath: String,
    val photoMime: String = "image/jpeg",
    val damageType: String,
    val conditionStatus: String = "open",
    val lastDamageObservedAt: Long,
    val lastObservedDriveId: String? = null,
    val lastObservedAt: Long? = null
)

/**
 * Durable outbox entry for one native before/after comparison. It is acknowledged only
 * after IndexedDB commits the corresponding condition update, so an Activity/process
 * restart cannot lose a verified repair or apply it twice.
 */
@Entity(
    tableName = "repair_observations",
    indices = [
        Index(value = ["targetReportId"]),
        Index(value = ["sourceEventKey"], unique = true),
        Index(value = ["syncedToWeb"])
    ]
)
data class RepairObservationEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val targetReportId: Long,
    val sourceEventKey: String,
    val observedAt: Long,
    val driveId: String,
    val lat: Double,
    val lng: Double,
    val gpsAccuracy: Float?,
    val speedMps: Float?,
    val heading: Float?,
    val currentPhotoPath: String,
    val currentCondition: String,
    val assessment: String,
    val imageQuality: String,
    val sameLocationVisible: Boolean,
    val completedRepairVisible: Boolean,
    val description: String,
    val detectionModel: String,
    val imageDetail: String,
    val promptVersion: String,
    val schemaVersion: Int,
    val syncedToWeb: Boolean = false
)

@Entity(tableName = "sessions")
data class SessionEntity(
    @PrimaryKey
    val id: String,
    val startedAt: Long = System.currentTimeMillis() / 1000,
    val endedAt: Long? = null,
    val checkedCount: Int = 0,
    val foundCount: Int = 0,
    val alreadyCount: Int = 0,
    val alreadyIdsJson: String = "[]",
    val gpsTrackJson: String = "[]",
    val status: String = "active" // active, paused, stopped, interrupted
)

@Entity(
    tableName = "footage_segments",
    indices = [
        Index(value = ["sessionId"]),
        Index(value = ["filePath"], unique = true)
    ]
)
data class FootageSegmentEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val sessionId: String,
    val filePath: String,
    val startedAt: Long,
    val endedAt: Long,
    val durationMs: Long,
    val bytes: Long,
    val errorCode: Int? = null,
    val complete: Boolean = true
)

/**
 * A sparse evidence-resolution frame captured alongside the low-resolution local video.
 * Raw camera bitmaps are never retained: the service writes one bounded primary JPEG and
 * one deterministic same-burst temporal companion, then recycles the burst. One row owns
 * both files and [bytes] is their aggregate size. Frames that the live worker could not
 * finish remain available for the explicit post-drive pass.
 */
@Entity(
    tableName = "drive_keyframes",
    indices = [
        Index(value = ["sessionId"]),
        Index(value = ["sessionId", "captureSeq"], unique = true),
        Index(value = ["liveAnalyzed"]),
        Index(value = ["filePath"], unique = true)
    ]
)
data class DriveKeyframeEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val sessionId: String,
    val captureSeq: Int,
    val filePath: String,
    val capturedAtMs: Long,
    val sourceOffsetMs: Long,
    val lat: Double?,
    val lng: Double?,
    val gpsAccuracy: Float?,
    val speedMps: Float?,
    val heading: Float?,
    val width: Int,
    val height: Int,
    val bytes: Long,
    val liveAnalyzed: Boolean = false
)

/** Bounded history projection; rendering drives does not materialize every frame row. */
data class DriveKeyframeSummary(
    val sessionId: String,
    val keyframeCount: Int,
    val pendingCount: Int,
    val keyframeBytes: Long
)
