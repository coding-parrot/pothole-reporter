package dev.aiengg.potholereporter.drive

/**
 * On-disk image limits chosen so a complete two-frame keyframe can cross the
 * native-to-WebView bridge without allocating an unbounded Base64 payload.
 */
internal object NativeStoredImagePolicy {
    const val MAX_BRIDGE_IMAGE_BYTES = 2L * 1024L * 1024L
    const val MAX_KEYFRAME_IMAGE_BYTES = 900L * 1024L
    const val MAX_KEYFRAME_PAIR_BYTES = 2L * MAX_KEYFRAME_IMAGE_BYTES
    const val KEYFRAME_MAX_DIMENSION = 1_280
    const val EVIDENCE_MAX_DIMENSION = 1_600
}
