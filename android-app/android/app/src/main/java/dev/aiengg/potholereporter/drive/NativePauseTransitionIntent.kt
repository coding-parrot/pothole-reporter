package dev.aiengg.potholereporter.drive

/** Last user intent received while the camera is still completing a Pause transition. */
internal class NativePauseTransitionIntent {
    private var pendingPaused: Boolean? = null

    fun request(paused: Boolean) {
        pendingPaused = paused
    }

    fun take(): Boolean? = pendingPaused.also { pendingPaused = null }

    fun clear() {
        pendingPaused = null
    }
}
