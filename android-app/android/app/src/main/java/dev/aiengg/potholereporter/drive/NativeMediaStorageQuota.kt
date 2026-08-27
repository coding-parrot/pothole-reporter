package dev.aiengg.potholereporter.drive

import kotlinx.coroutines.sync.Mutex

/**
 * Serialises the first Drive-media inventory with every bridge reconciliation/deletion
 * that can change the same private files or their Room ownership.
 */
internal object NativeMediaFilesystemMutation {
    val mutex = Mutex()
}

/**
 * Process-local accounting for Drive media. Video segments and sparse JPEG keyframes
 * reserve from the same hard cap before either starts writing, so concurrent writers
 * cannot each observe the old total and overshoot it.
 *
 * The filesystem remains the source of truth. [reconcile] seeds the ledger from a disk
 * walk at the start of a service lifetime; reservations then make subsequent checks
 * constant-time and race-free.
 */
internal class NativeMediaStorageQuota(
    private val maxTotalBytes: Long = MAX_TOTAL_BYTES,
    private val minFreeBytes: Long = MIN_FREE_BYTES
) {
    class Reservation internal constructor(internal val id: Long, val bytes: Long)

    private var committedBytes: Long? = null
    private var reservedBytes = 0L
    private var nextReservationId = 1L
    private val reservations = LinkedHashMap<Long, Long>()

    init {
        require(maxTotalBytes > 0L)
        require(minFreeBytes >= 0L)
    }

    @Synchronized
    fun reconcile(actualBytes: Long) {
        require(actualBytes >= 0L)
        committedBytes = actualBytes
    }

    @Synchronized
    fun isReconciled(): Boolean = committedBytes != null

    @Synchronized
    fun tryReserve(bytes: Long, usableSpaceBytes: Long): Reservation? {
        if (bytes <= 0L || usableSpaceBytes < 0L) return null
        val committed = committedBytes ?: return null
        val withReserved = safeAdd(committed, reservedBytes)
        if (withReserved > maxTotalBytes || bytes > maxTotalBytes - withReserved) return null
        val requestedAndReserved = safeAdd(reservedBytes, bytes)
        if (usableSpaceBytes < minFreeBytes ||
            requestedAndReserved > usableSpaceBytes - minFreeBytes) return null

        val id = nextReservationId++
        reservations[id] = bytes
        reservedBytes = safeAdd(reservedBytes, bytes)
        return Reservation(id, bytes)
    }

    @Synchronized
    fun commit(reservation: Reservation, actualBytes: Long): Boolean {
        val reserved = reservations[reservation.id] ?: return false
        // File-size limits are not guaranteed to be exact on every recorder/backend.
        // Never turn a reservation into a larger committed allocation: the writer must
        // discard the file and release this still-live reservation instead.
        if (actualBytes < 0L || actualBytes > reserved) return false
        reservations.remove(reservation.id)
        reservedBytes = (reservedBytes - reserved).coerceAtLeast(0L)
        committedBytes = safeAdd(committedBytes ?: 0L, actualBytes)
        return true
    }

    @Synchronized
    fun release(reservation: Reservation) {
        val reserved = reservations.remove(reservation.id) ?: return
        reservedBytes = (reservedBytes - reserved).coerceAtLeast(0L)
    }

    @Synchronized
    fun noteDeletion(bytes: Long) {
        if (bytes <= 0L) return
        committedBytes = ((committedBytes ?: return) - bytes).coerceAtLeast(0L)
    }

    /** Accounts a writer-owned file that could not be deleted after commit rejection. */
    @Synchronized
    fun noteUnexpectedExistingFile(bytes: Long) {
        if (bytes <= 0L) return
        committedBytes = safeAdd(committedBytes ?: return, bytes)
    }

    @Synchronized
    fun accountedBytes(): Long? = committedBytes?.let { safeAdd(it, reservedBytes) }

    companion object {
        const val MAX_TOTAL_BYTES = 4L * 1024 * 1024 * 1024
        const val MIN_FREE_BYTES = 500L * 1024 * 1024
        const val VIDEO_SEGMENT_RESERVATION_BYTES = 80L * 1024 * 1024
        const val MAX_KEYFRAME_BYTES = NativeStoredImagePolicy.MAX_KEYFRAME_PAIR_BYTES

        private fun safeAdd(left: Long, right: Long): Long =
            if (right > 0L && Long.MAX_VALUE - left < right) Long.MAX_VALUE else left + right
    }
}
