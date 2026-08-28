package dev.aiengg.potholereporter.drive

import java.util.ArrayDeque

/** Small thread-safe history used to pair a camera frame with the nearest real GPS fix. */
internal class NativeGpsFixHistory(private val capacity: Int = 16) {
    private val fixes = ArrayDeque<GpsFix>(capacity)

    init {
        require(capacity > 0)
    }

    @Synchronized
    fun add(fix: GpsFix) {
        fixes.addLast(fix)
        while (fixes.size > capacity) fixes.removeFirst()
    }

    @Synchronized
    fun clear() = fixes.clear()

    @Synchronized
    fun nearest(elapsedRealtimeMs: Long): GpsFix? {
        if (elapsedRealtimeMs <= 0L) return null
        var best: GpsFix? = null
        var bestDelta = Long.MAX_VALUE
        fixes.forEach { fix ->
            val delta = absoluteDifference(fix.elapsedRealtimeMs, elapsedRealtimeMs)
            if (delta < bestDelta || (delta == bestDelta &&
                    fix.elapsedRealtimeMs > (best?.elapsedRealtimeMs ?: Long.MIN_VALUE))
            ) {
                best = fix
                bestDelta = delta
            }
        }
        return best
    }

    @Synchronized
    fun nearestCaptureReady(elapsedRealtimeMs: Long, maxAccuracyM: Float): GpsFix? {
        if (elapsedRealtimeMs <= 0L || !maxAccuracyM.isFinite() || maxAccuracyM < 0f) return null
        var best: GpsFix? = null
        var bestDelta = Long.MAX_VALUE
        fixes.forEach { fix ->
            val accuracy = fix.accuracy
            if (!fix.lat.isFinite() || fix.lat !in -90.0..90.0 ||
                !fix.lng.isFinite() || fix.lng !in -180.0..180.0 ||
                fix.timestampMs <= 0L || fix.elapsedRealtimeMs <= 0L ||
                accuracy == null || !accuracy.isFinite() ||
                accuracy !in 0f..maxAccuracyM
            ) return@forEach
            val delta = absoluteDifference(fix.elapsedRealtimeMs, elapsedRealtimeMs)
            if (delta < bestDelta ||
                (delta == bestDelta &&
                    fix.elapsedRealtimeMs > (best?.elapsedRealtimeMs ?: Long.MIN_VALUE))
            ) {
                best = fix
                bestDelta = delta
            }
        }
        return best
    }

    private fun absoluteDifference(left: Long, right: Long): Long =
        if (left >= right) left - right else right - left
}
