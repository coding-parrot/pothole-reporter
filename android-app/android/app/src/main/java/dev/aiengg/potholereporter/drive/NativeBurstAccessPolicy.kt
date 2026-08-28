package dev.aiengg.potholereporter.drive

import java.util.concurrent.atomic.AtomicLong

/** Monotonic token invalidated by any camera, permission, GPS-access, pause, or stop transition. */
internal class NativeCaptureAccessEpoch {
    private val value = AtomicLong(0L)

    fun snapshot(): Long = value.get()

    fun invalidate(): Long = value.incrementAndGet()
}

/**
 * Final fail-closed gate between an asynchronous camera burst and all evidence/model work.
 * The accepted fix is intentionally the post-burst fix nearest the selected primary frame.
 */
internal object NativeBurstAccessPolicy {
    const val MAX_FIX_PRIMARY_DELTA_MS = 2_500L
    const val MAX_FIX_ACCURACY_M = 30f

    fun validatedPostBurstFix(
        epochBeforeCapture: Long,
        epochImmediatelyBeforeWork: Long,
        sessionRunning: Boolean,
        paused: Boolean,
        stopping: Boolean,
        interlockCanCapture: Boolean,
        cameraReady: Boolean,
        cameraReleased: Boolean,
        primaryCapturedAtMs: Long,
        postBurstFix: GpsFix?
    ): GpsFix? {
        if (epochBeforeCapture != epochImmediatelyBeforeWork ||
            !sessionRunning || paused || stopping || !interlockCanCapture ||
            !cameraReady || cameraReleased || primaryCapturedAtMs <= 0L) return null

        val fix = postBurstFix ?: return null
        val accuracy = fix.accuracy ?: return null
        if (!fix.lat.isFinite() || fix.lat !in -90.0..90.0 ||
            !fix.lng.isFinite() || fix.lng !in -180.0..180.0 ||
            fix.timestampMs <= 0L || fix.elapsedRealtimeMs <= 0L ||
            !accuracy.isFinite() || accuracy !in 0f..MAX_FIX_ACCURACY_M
        ) return null

        val deltaMs = if (fix.elapsedRealtimeMs >= primaryCapturedAtMs) {
            fix.elapsedRealtimeMs - primaryCapturedAtMs
        } else {
            primaryCapturedAtMs - fix.elapsedRealtimeMs
        }
        return fix.takeIf { deltaMs <= MAX_FIX_PRIMARY_DELTA_MS }
    }
}
