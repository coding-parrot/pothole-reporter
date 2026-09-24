package com.gauravsen.potholereporter.drivemode

/**
 * Immutable configuration constants for Drive Mode, mirroring the values
 * in the web client (index.html / standalone.js) so that native and web
 * paths behave identically.
 */
object DriveConstants {
    // Spatial gating — minimum distance between captures
    const val TARGET_SPACING_M = 6.0

    // Speed-adaptive capture interval bounds
    const val MIN_CAPTURE_MS = 500L
    const val MAX_CAPTURE_MS = 1500L
    const val FALLBACK_CAPTURE_MS = 750L
    const val PARKED_CAPTURE_MS = 1000L

    // GPS quality thresholds
    const val GPS_COARSE_M = 30f
    const val MOVING_SPEED_MS = 1.0f       // below this → considered stationary
    const val GPS_MAX_AGE_MS = 10_000L      // a fix older than this is stale

    // Concurrency limits
    const val MAX_IN_FLIGHT = 6
    const val MAX_CAPTURE_QUEUE = 24

    // Deduplication — must match standalone.js exactly
    const val DEDUPE_ADJACENT_RADIUS_M = 12.0
    const val DEDUPE_HISTORY_RADIUS_M = 8.0
    const val DEDUPE_MISSING_HEADING_RADIUS_M = 5.0
    const val DEDUPE_SAME_DRIVE_S = 4
    const val DEDUPE_POOR_GPS_S = 2
    const val DEDUPE_HISTORY_S = 30L * 24 * 60 * 60  // 30 days

    // Camera target resolution
    const val TARGET_WIDTH = 1920
    const val TARGET_HEIGHT = 1080
    const val TARGET_FRAME_RATE = 30

    // Frame polling interval (how often driveTick runs)
    const val FRAME_INTERVAL_MS = 200L

    // Camera settle time before first capture
    const val CAMERA_SETTLE_MS = 800L

    // Camera stall tolerance (number of ticks before declaring lost)
    const val CAMERA_STALL_THRESHOLD = 8

    // Notification
    const val NOTIFICATION_CHANNEL_ID = "drive_mode"
    const val NOTIFICATION_ID = 1001

    // Wake lock tag
    const val WAKE_LOCK_TAG = "PotholeReporter::DriveMode"
    // Maximum wake lock hold time (safety net — 2 hours)
    const val WAKE_LOCK_TIMEOUT_MS = 2L * 60 * 60 * 1000
}
