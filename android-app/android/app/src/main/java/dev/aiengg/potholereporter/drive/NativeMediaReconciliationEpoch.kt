package dev.aiengg.potholereporter.drive

import java.util.concurrent.atomic.AtomicLong

/**
 * Monotonic invalidation for private-media reconciliation.
 *
 * A Boolean can lose an invalidation that races with a reconciliation pass: the pass can
 * write `true` after a producer has written `false`. A captured epoch is current only if
 * no native producer or cleanup path advanced it while the pass was running.
 */
internal object NativeMediaReconciliationEpoch {
    private val epoch = AtomicLong(0L)

    fun snapshot(): Long = epoch.get()

    fun invalidate(): Long = epoch.incrementAndGet()

    fun isCurrent(snapshot: Long): Boolean = epoch.get() == snapshot
}
