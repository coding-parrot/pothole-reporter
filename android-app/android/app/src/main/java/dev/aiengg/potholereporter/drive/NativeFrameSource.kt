package dev.aiengg.potholereporter.drive

import android.graphics.Bitmap
import java.net.URI

/** The frame producer selected for one Drive session. */
enum class NativeFrameSourceKind(
    val wireValue: String,
    val requiresCameraPermission: Boolean
) {
    PHONE_CAMERA("phone_camera", true),
    DASHCAM("dashcam", false);

    companion object {
        fun fromWireValue(value: String?): NativeFrameSourceKind? = when (value?.trim()) {
            null, "", PHONE_CAMERA.wireValue -> PHONE_CAMERA
            DASHCAM.wireValue -> DASHCAM
            else -> null
        }
    }
}

enum class NativeFrameSourceState(val wireValue: String) {
    IDLE("idle"),
    CONNECTING("connecting"),
    STREAMING("streaming"),
    RECONNECTING("reconnecting"),
    PAUSED("paused"),
    STOPPED("stopped"),
    ERROR("error")
}

data class NativeFrameSourceConfig(
    val kind: NativeFrameSourceKind,
    val rtspUrl: String? = null
) {
    companion object {
        fun create(kindValue: String?, rtspValue: String?): Result<NativeFrameSourceConfig> =
            runCatching {
                val kind = NativeFrameSourceKind.fromWireValue(kindValue)
                    ?: throw IllegalArgumentException(
                        "captureSource must be phone_camera or dashcam"
                    )
                if (kind == NativeFrameSourceKind.PHONE_CAMERA) {
                    NativeFrameSourceConfig(kind)
                } else {
                    NativeFrameSourceConfig(kind, validatedRtspUrl(rtspValue))
                }
            }

        private fun validatedRtspUrl(value: String?): String {
            val candidate = value?.trim().orEmpty()
            require(candidate.isNotEmpty()) { "A dashcam RTSP URL is required" }
            require(candidate.none { it.isWhitespace() || it.isISOControl() || it == '\\' }) {
                "The dashcam RTSP URL is invalid"
            }
            // URI's stock parse error includes the entire input string. A dashcam URL can
            // contain credentials, so never allow that error to bubble through the bridge
            // or service status. The caller receives a stable, actionable public message.
            val uri = try {
                URI(candidate)
            } catch (_: Exception) {
                throw IllegalArgumentException("The dashcam RTSP URL is invalid")
            }
            require(uri.scheme.equals("rtsp", ignoreCase = true) && !uri.host.isNullOrBlank()) {
                "The dashcam URL must start with rtsp:// and include a host"
            }
            require(uri.port == -1 || uri.port in 1..65535) {
                "The dashcam RTSP port must be between 1 and 65535"
            }
            require(uri.fragment == null) { "The dashcam RTSP URL must not contain a fragment" }
            // Parsing plus the scheme/host checks gives Media3 one absolute network
            // endpoint while retaining
            // credentials, port, path and query options used by real dashboard cameras.
            return candidate
        }
    }
}

/**
 * Source-neutral ownership contract used by the Drive service.
 *
 * Every returned [BurstFrame] is one independently complete decoded frame. Implementations
 * may rotate or uniformly resize the whole image, but may never remove any spatial region.
 */
internal interface NativeFrameSource {
    val kind: NativeFrameSourceKind
    val isReady: Boolean
    val state: NativeFrameSourceState
    val issue: String?
    val isVideoRecordingEnabled: Boolean
    val isVideoRecording: Boolean
    val isVideoSupported: Boolean

    fun start(onReady: (Boolean) -> Unit = {})
    fun setSamplingEnabled(enabled: Boolean)
    fun hasCompleteBurst(): Boolean
    suspend fun captureBurst(): Pair<List<BurstFrame>, Int>?
    suspend fun setVideoRecordingEnabled(enabled: Boolean)
    suspend fun pauseSafely()
    fun resume(onReady: (Boolean) -> Unit = {})
    suspend fun stopSafely()
    fun closeImmediately()

    suspend fun reserveMediaBytes(bytes: Long): NativeMediaStorageQuota.Reservation?
    fun commitMediaBytes(
        reservation: NativeMediaStorageQuota.Reservation,
        actualBytes: Long
    ): Boolean
    fun releaseMediaBytes(reservation: NativeMediaStorageQuota.Reservation)
    fun noteDeletedMediaBytes(bytes: Long)
    fun noteUnexpectedMediaBytes(bytes: Long)
    fun mediaDeletionRecorderIfReconciled(): ((Long) -> Unit)?
}

/** Source-neutral bounds shared by phone-camera and dashcam frame producers. */
internal object NativeFrameBurstContract {
    const val FRAME_COUNT = 3
    const val MIN_INFERENCE_FRAMES = 2
}

/**
 * Location truth at the evidence boundary.
 *
 * Phone-camera frames and phone GPS share one device clock, so the measured GPS accuracy
 * remains meaningful. RTSP does not expose the dashcam sensor's capture instant; treating
 * decoder-delivery time as capture time can move a report onto the next road. Until a
 * dashcam/phone pair has an end-to-end latency calibration, routing and repair verification
 * must fail closed while detection and evidence saving continue.
 */
internal object NativeEvidenceLocationPolicy {
    fun gpsAccuracyForEvidence(source: NativeFrameSourceKind, phoneGpsAccuracy: Float?): Float? =
        if (source == NativeFrameSourceKind.PHONE_CAMERA) phoneGpsAccuracy else null

    fun canVerifyRepair(source: NativeFrameSourceKind): Boolean =
        source == NativeFrameSourceKind.PHONE_CAMERA
}

/** Activity-owned dashcam preview. The listener owns each delivered bitmap. */
fun interface NativeDashcamPreviewListener {
    fun onFrame(frame: Bitmap)
}

/** Keeps the visible, non-evidence dashboard preview responsive without decoding every frame. */
internal object NativeDashcamPreviewSamplingPolicy {
    fun shouldDecodeForPreview(
        listenerAttached: Boolean,
        nowElapsedMs: Long,
        lastPreviewElapsedMs: Long,
        intervalMs: Long
    ): Boolean = listenerAttached && nowElapsedMs - lastPreviewElapsedMs >= intervalMs
}
