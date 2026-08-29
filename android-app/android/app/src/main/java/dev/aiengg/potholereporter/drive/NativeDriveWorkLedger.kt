package dev.aiengg.potholereporter.drive

internal data class NativeDriveWorkSnapshot(
    val checked: Int,
    val queued: Int,
    val deferred: Int
)

/**
 * Serializes live-work accounting shared by the camera scanner, inference worker, channel
 * callbacks, status snapshots, and Stop. The public API still calls deferred work "dropped" for
 * compatibility, but every such burst is already durable and remains eligible for replay.
 */
internal class NativeDriveWorkLedger {
    private var checked = 0
    private var queued = 0
    private var deferred = 0

    @Synchronized
    fun reset() {
        checked = 0
        queued = 0
        deferred = 0
    }

    @Synchronized
    fun admitLive() {
        queued++
    }

    /** Returns false on a duplicate/out-of-order completion without allowing underflow. */
    @Synchronized
    fun consumeLive(): Boolean {
        if (queued <= 0) return false
        queued--
        return true
    }

    /** Moves one attempted live hand-off to durable replay, atomically when it was queued. */
    @Synchronized
    fun deferLive(): Boolean {
        val removedFromQueue = queued > 0
        if (removedFromQueue) queued--
        deferred++
        return removedFromQueue
    }

    /** Records exactly one completed model verdict; incomplete/null work stays pending. */
    @Synchronized
    fun completeIfAnalyzed(analyzed: Boolean): Boolean {
        if (!analyzed) return false
        checked++
        return true
    }

    @Synchronized
    fun completedCount(): Int = checked

    @Synchronized
    fun snapshot(): NativeDriveWorkSnapshot = NativeDriveWorkSnapshot(
        checked = checked,
        queued = queued,
        deferred = deferred
    )
}
