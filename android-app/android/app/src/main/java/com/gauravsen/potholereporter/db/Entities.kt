package com.gauravsen.potholereporter.db

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

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
    val damageType: String? = "pothole_cavity",
    val assessment: String? = "clear",
    val imageQuality: String? = "usable",
    val onDrivableSurface: Boolean = true,
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
    val promptVersion: String? = "road-damage-v3",
    val schemaVersion: Int = 3,
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
