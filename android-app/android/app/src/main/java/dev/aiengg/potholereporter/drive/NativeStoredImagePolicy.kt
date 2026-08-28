package dev.aiengg.potholereporter.drive

/**
 * On-disk image limits chosen so a complete three-frame keyframe can cross the
 * native-to-WebView bridge without allocating an unbounded Base64 payload.
 */
internal object NativeStoredImagePolicy {
    const val MAX_BRIDGE_IMAGE_BYTES = 2L * 1024L * 1024L
    const val MAX_KEYFRAME_IMAGE_BYTES = 900L * 1024L
    const val MAX_KEYFRAME_PAIR_BYTES = 2L * MAX_KEYFRAME_IMAGE_BYTES
    const val MAX_KEYFRAME_BURST_BYTES = 3L * MAX_KEYFRAME_IMAGE_BYTES
    const val MAX_ROOM_THUMB_IMAGE_BYTES = 256L * 1024L
    const val MAX_MODEL_IMAGE_BYTES = 1_500L * 1024L
    const val KEYFRAME_MAX_DIMENSION = 1_280
    const val ROOM_THUMB_MAX_DIMENSION = 640
    const val EVIDENCE_MAX_DIMENSION = 1_600
}
