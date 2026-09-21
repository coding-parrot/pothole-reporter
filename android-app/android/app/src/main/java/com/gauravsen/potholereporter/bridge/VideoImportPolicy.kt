package com.gauravsen.potholereporter.bridge

import java.util.Locale
import java.util.GregorianCalendar
import java.util.SimpleTimeZone
import java.util.TimeZone
import kotlin.math.roundToInt
import kotlin.math.sqrt

// Off while the page has no element that shows a shared clip. The manifest no longer
// offers the app as a share target, but the activity is exported for the launcher, so
// another app can still address an explicit SEND to it; that must not copy up to 512 MB
// into the cache where the tester can neither see nor discard it.
internal const val SHARE_INGRESS_ENABLED = false

/**
 * Policy for videos delivered to Android through another app's Share/Open action.
 *
 * The normal in-app import uses the WebView's system file picker and does not copy the
 * complete file. These limits apply only to external intents, which need an app-private
 * copy because their temporary content-URI permission can disappear with the Activity.
 */
internal const val VIDEO_IMPORT_MAX_ITEM_BYTES = 512L * 1024L * 1024L
internal const val VIDEO_IMPORT_MAX_PENDING_BYTES = 768L * 1024L * 1024L
internal const val VIDEO_IMPORT_FREE_SPACE_RESERVE_BYTES = 256L * 1024L * 1024L
internal const val VIDEO_IMPORT_MAX_BATCH_ITEMS = 4
internal const val VIDEO_IMPORT_MAX_SHARED_PENDING_ITEMS = 8
internal const val VIDEO_IMPORT_MAX_PICKER_ITEMS = 64
internal const val VIDEO_IMPORT_RETENTION_MS = 24L * 60L * 60L * 1000L
internal const val VIDEO_IMPORT_PERSISTED_RETENTION_MS = 7L * 24L * 60L * 60L * 1000L
internal const val VIDEO_IMPORT_PROVIDER_SETUP_TIMEOUT_MS = 15_000L
internal const val VIDEO_IMPORT_COPY_TIMEOUT_MS = 2L * 60L * 1000L

internal const val DEFAULT_FRAME_HEIGHT = 720
internal const val MIN_FRAME_HEIGHT = 240
internal const val MAX_FRAME_HEIGHT = 1080
internal const val MAX_FRAME_DIMENSION = 1920
internal const val MAX_FRAME_PIXELS = 1920L * 1080L
internal const val DEFAULT_JPEG_QUALITY = 82
internal const val MIN_JPEG_QUALITY = 55
internal const val MAX_JPEG_QUALITY = 92
internal const val MAX_JPEG_BYTES = 4 * 1024 * 1024

private val VIDEO_EXTENSIONS = setOf(
    "mp4", "m4v", "mov", "webm", "mkv", "avi", "3gp", "3g2", "ts", "mts", "m2ts",
)

private val GENERIC_VIDEO_MIME_TYPES = setOf(
    "", "application/octet-stream", "application/mp4", "application/mpeg4",
)

internal enum class VideoImportLimitFailure {
    ITEM_TOO_LARGE,
    PENDING_STORAGE_LIMIT,
    LOW_DEVICE_STORAGE,
}

internal data class EmbeddedVideoLocation(val lat: Double, val lng: Double)

internal data class BoundedFrameSize(val width: Int, val height: Int)

internal fun clampedFrameHeight(requested: Int?, sourceHeight: Int?): Int {
    val bounded = (requested ?: DEFAULT_FRAME_HEIGHT).coerceIn(MIN_FRAME_HEIGHT, MAX_FRAME_HEIGHT)
    return sourceHeight?.takeIf { it > 0 }?.let { minOf(bounded, it) } ?: bounded
}

internal fun clampedJpegQuality(requested: Int?): Int =
    (requested ?: DEFAULT_JPEG_QUALITY).coerceIn(MIN_JPEG_QUALITY, MAX_JPEG_QUALITY)

/**
 * Produce a fixed scale-to-fit box. Supplying both dimensions to Media3 is important:
 * height-only scaling trusts the source aspect ratio and can allocate a huge bitmap for
 * corrupt or adversarial metadata.
 */
internal fun boundedFrameSize(
    requestedHeight: Int?,
    sourceWidth: Int?,
    sourceHeight: Int?,
    rotationDegrees: Int?,
): BoundedFrameSize {
    val rotated = rotationDegrees in setOf(90, 270)
    val effectiveWidth = if (rotated) sourceHeight else sourceWidth
    val effectiveHeight = if (rotated) sourceWidth else sourceHeight
    val targetHeight = clampedFrameHeight(requestedHeight, effectiveHeight)
    val aspect = if (effectiveWidth != null && effectiveWidth > 0 &&
        effectiveHeight != null && effectiveHeight > 0
    ) {
        effectiveWidth.toDouble() / effectiveHeight.toDouble()
    } else {
        1.0
    }
    var width = targetHeight.toDouble() * aspect
    var height = targetHeight.toDouble()
    val dimensionScale = minOf(
        1.0,
        MAX_FRAME_DIMENSION.toDouble() / width,
        MAX_FRAME_DIMENSION.toDouble() / height,
    )
    width *= dimensionScale
    height *= dimensionScale
    val pixels = width * height
    if (pixels > MAX_FRAME_PIXELS) {
        val pixelScale = sqrt(MAX_FRAME_PIXELS.toDouble() / pixels)
        width *= pixelScale
        height *= pixelScale
    }
    return BoundedFrameSize(
        width = width.roundToInt().coerceIn(1, MAX_FRAME_DIMENSION),
        height = height.roundToInt().coerceIn(1, MAX_FRAME_DIMENSION),
    )
}

internal fun safeVideoDisplayName(value: String?): String {
    val cleaned = value.orEmpty()
        .replace(Regex("[\\p{Cc}/\\\\]+"), " ")
        .replace(Regex("\\s+"), " ")
        .trim()
        .trimStart('.')
        .trim()
        .take(120)
    return cleaned.ifBlank { "Shared video" }
}

internal fun videoFileExtension(displayName: String?): String? {
    val name = displayName.orEmpty().substringBefore('?').substringBefore('#')
    val extension = name.substringAfterLast('.', "").lowercase(Locale.ROOT)
    return extension.takeIf { it in VIDEO_EXTENSIONS }
}

internal fun isPlausibleVideo(mimeType: String?, displayName: String?): Boolean {
    val mime = mimeType.orEmpty().substringBefore(';').trim().lowercase(Locale.ROOT)
    if (mime.startsWith("video/")) return true
    return mime in GENERIC_VIDEO_MIME_TYPES && videoFileExtension(displayName) != null
}

internal fun videoImportLimitFailure(
    declaredBytes: Long?,
    pendingBytes: Long,
    availableBytes: Long,
): VideoImportLimitFailure? {
    if (declaredBytes != null && declaredBytes > VIDEO_IMPORT_MAX_ITEM_BYTES) {
        return VideoImportLimitFailure.ITEM_TOO_LARGE
    }
    if (pendingBytes >= VIDEO_IMPORT_MAX_PENDING_BYTES ||
        declaredBytes != null && declaredBytes > VIDEO_IMPORT_MAX_PENDING_BYTES - pendingBytes
    ) {
        return VideoImportLimitFailure.PENDING_STORAGE_LIMIT
    }
    if (availableBytes <= VIDEO_IMPORT_FREE_SPACE_RESERVE_BYTES ||
        declaredBytes != null && declaredBytes > availableBytes - VIDEO_IMPORT_FREE_SPACE_RESERVE_BYTES
    ) {
        return VideoImportLimitFailure.LOW_DEVICE_STORAGE
    }
    return null
}

/** Maximum bytes the streaming copy may write before it must stop and delete its partial file. */
internal fun videoImportCopyBudget(pendingBytes: Long, availableBytes: Long): Long = minOf(
    VIDEO_IMPORT_MAX_ITEM_BYTES,
    (VIDEO_IMPORT_MAX_PENDING_BYTES - pendingBytes).coerceAtLeast(0L),
    (availableBytes - VIDEO_IMPORT_FREE_SPACE_RESERVE_BYTES).coerceAtLeast(0L),
)

internal fun isExpiredVideoImport(createdAtMs: Long, nowMs: Long): Boolean =
    nowMs - createdAtMs >= VIDEO_IMPORT_RETENTION_MS

internal fun isExpiredPersistedVideoImport(createdAtMs: Long, nowMs: Long): Boolean =
    nowMs - createdAtMs >= VIDEO_IMPORT_PERSISTED_RETENTION_MS

/**
 * Android video metadata normally exposes ISO-6709 as +DD.dddd+DDD.dddd/.
 * Deliberately reject compact degrees/minutes and altitude variants rather than guessing.
 */
internal fun parseDecimalIso6709(value: String?): EmbeddedVideoLocation? {
    val candidate = value.orEmpty().trim().take(128)
    val match = Regex("^([+-]\\d{1,2}(?:\\.\\d+)?)([+-]\\d{1,3}(?:\\.\\d+)?)/?$")
        .matchEntire(candidate) ?: return null
    val lat = match.groupValues[1].toDoubleOrNull() ?: return null
    val lng = match.groupValues[2].toDoubleOrNull() ?: return null
    if (!lat.isFinite() || !lng.isFinite() || lat !in -90.0..90.0 || lng !in -180.0..180.0) {
        return null
    }
    return EmbeddedVideoLocation(lat, lng)
}

/** Parse only ISO-like metadata dates that include a timezone; never guess local time. */
internal fun parseVideoRecordedAtMs(value: String?): Long? {
    val candidate = value.orEmpty().trim().take(128)
    val match = Regex(
        "^(\\d{4})-?(\\d{2})-?(\\d{2})T(\\d{2}):?(\\d{2}):?(\\d{2})" +
            "(?:\\.(\\d{1,9}))?(Z|[+-]\\d{2}:?\\d{2})$",
    ).matchEntire(candidate) ?: return null
    val parts = (1..6).map { match.groupValues[it].toIntOrNull() ?: return null }
    val year = parts[0]
    if (year !in 1970..2100) return null
    val fraction = match.groupValues[7]
    val millis = fraction.take(3).padEnd(3, '0').toIntOrNull() ?: 0
    val zoneToken = match.groupValues[8]
    val zone = if (zoneToken == "Z") {
        TimeZone.getTimeZone("GMT")
    } else {
        val compact = zoneToken.replace(":", "")
        val offsetHours = compact.substring(1, 3).toIntOrNull() ?: return null
        val offsetMinutes = compact.substring(3, 5).toIntOrNull() ?: return null
        if (offsetHours > 23 || offsetMinutes > 59) return null
        val sign = if (compact.startsWith('-')) -1 else 1
        val offsetMs = sign * (offsetHours * 60 + offsetMinutes) * 60 * 1000
        // Avoid TimeZone.getTimeZone silently falling back to GMT for a malformed ID.
        SimpleTimeZone(offsetMs, "video-metadata-offset")
    }
    return runCatching {
        GregorianCalendar(zone).apply {
            isLenient = false
            clear()
            set(year, parts[1] - 1, parts[2], parts[3], parts[4], parts[5])
            set(GregorianCalendar.MILLISECOND, millis)
        }.timeInMillis
    }.getOrNull()
}

internal fun videoPlaybackSupport(codecMime: String?): String = when (
    codecMime.orEmpty().lowercase(Locale.ROOT)
) {
    "video/avc", "video/x-vnd.on2.vp8", "video/x-vnd.on2.vp9", "video/av01" -> "direct"
    "video/hevc", "video/dolby-vision" -> "device_dependent"
    else -> "may_require_conversion"
}
