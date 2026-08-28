package dev.aiengg.potholereporter.drive

/**
 * Monotonic fail-safe for a Drive left paused in the foreground service.
 *
 * Pausing releases CameraX, GPS updates, and the wake lock, but Android still keeps a
 * visible foreground-service notification. A forgotten Pause must therefore terminate
 * independently of the active-camera budget instead of remaining alive indefinitely.
 */
internal class DrivePauseTimeoutPolicy(
    private val timeoutMs: Long = DEFAULT_TIMEOUT_MS
) {
    private var pausedAtElapsedMs: Long? = null
    private var lastObservedElapsedMs = 0L

    init {
        require(timeoutMs > 0L) { "Pause timeout must be positive" }
    }

    @Synchronized
    fun beginPause(nowElapsedMs: Long) {
        observe(nowElapsedMs)
        if (pausedAtElapsedMs == null) pausedAtElapsedMs = nowElapsedMs
    }

    @Synchronized
    fun clearPause(nowElapsedMs: Long) {
        observe(nowElapsedMs)
        pausedAtElapsedMs = null
    }

    @Synchronized
    fun remainingMs(nowElapsedMs: Long): Long? {
        observe(nowElapsedMs)
        val pausedAt = pausedAtElapsedMs ?: return null
        return (timeoutMs - (nowElapsedMs - pausedAt)).coerceAtLeast(0L)
    }

    @Synchronized
    fun expired(nowElapsedMs: Long): Boolean = remainingMs(nowElapsedMs) == 0L

    private fun observe(nowElapsedMs: Long) {
        require(nowElapsedMs >= 0L) { "Monotonic time cannot be negative" }
        require(nowElapsedMs >= lastObservedElapsedMs) { "Monotonic time cannot move backwards" }
        lastObservedElapsedMs = nowElapsedMs
    }

    companion object {
        const val DEFAULT_TIMEOUT_MINUTES = 15
        const val DEFAULT_TIMEOUT_MS = DEFAULT_TIMEOUT_MINUTES * 60_000L
    }
}
