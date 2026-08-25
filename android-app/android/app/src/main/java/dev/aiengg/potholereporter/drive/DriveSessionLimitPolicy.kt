package dev.aiengg.potholereporter.drive

/**
 * Tracks the active-camera budget for one Drive session.
 *
 * Callers supply timestamps from a monotonic clock such as
 * [android.os.SystemClock.elapsedRealtime]. Time spent paused does not consume the budget.
 */
internal class DriveSessionLimitPolicy(
    startedAtElapsedMs: Long,
    limitMinutes: Int = DEFAULT_LIMIT_MINUTES
) {
    private val limitMs: Long
    private var consumedActiveMs = 0L
    private var activeSinceMs: Long? = startedAtElapsedMs
    private var lastObservedMs = startedAtElapsedMs

    init {
        require(startedAtElapsedMs >= 0L) { "The monotonic start time cannot be negative" }
        require(limitMinutes in MIN_LIMIT_MINUTES..MAX_LIMIT_MINUTES) {
            "Drive session limit must be between $MIN_LIMIT_MINUTES and $MAX_LIMIT_MINUTES minutes"
        }
        limitMs = limitMinutes * 60_000L
    }

    @Synchronized
    fun pause(nowElapsedMs: Long) {
        observe(nowElapsedMs)
        val activeSince = activeSinceMs ?: return
        consumedActiveMs = (consumedActiveMs + nowElapsedMs - activeSince).coerceAtMost(limitMs)
        activeSinceMs = null
    }

    @Synchronized
    fun resume(nowElapsedMs: Long) {
        observe(nowElapsedMs)
        if (activeSinceMs == null && consumedActiveMs < limitMs) activeSinceMs = nowElapsedMs
    }

    @Synchronized
    fun remainingMs(nowElapsedMs: Long): Long {
        observe(nowElapsedMs)
        return (limitMs - activeElapsedMs(nowElapsedMs)).coerceAtLeast(0L)
    }

    @Synchronized
    fun expired(nowElapsedMs: Long): Boolean = remainingMs(nowElapsedMs) == 0L

    private fun activeElapsedMs(nowElapsedMs: Long): Long {
        val activeSince = activeSinceMs
        return (consumedActiveMs + if (activeSince == null) 0L else nowElapsedMs - activeSince)
            .coerceAtMost(limitMs)
    }

    private fun observe(nowElapsedMs: Long) {
        require(nowElapsedMs >= lastObservedMs) { "Monotonic time cannot move backwards" }
        lastObservedMs = nowElapsedMs
    }

    companion object {
        const val MIN_LIMIT_MINUTES = 15
        const val MAX_LIMIT_MINUTES = 90
        const val DEFAULT_LIMIT_MINUTES = 30
    }
}
