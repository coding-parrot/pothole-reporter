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
 * Accounts outstanding writes against one filesystem's free-space floor.
 *
 * Drive media and report evidence intentionally keep separate hard caps, but both are
 * stored below [android.content.Context.filesDir]. Without this shared ledger, an 80 MiB
 * video reservation and a 2 MiB evidence reservation can each observe the same usable
 * space and jointly consume bytes promised to the 500 MiB safety floor.
 *
 * The production quota instances use [NativeProcessFreeSpaceReservations]. Tests can
 * inject a fresh ledger so an intentionally live reservation cannot leak into another test.
 */
internal class NativeFreeSpaceReservationLedger {
    class Reservation internal constructor(
        internal val id: Long,
        internal val bytes: Long,
        internal val minFreeBytes: Long
    )

    private var reservedBytes = 0L
    private var nextReservationId = 1L
    private val reservations = LinkedHashMap<Long, Reservation>()

    @Synchronized
    fun tryReserve(
        bytes: Long,
        usableSpaceBytes: Long,
        minFreeBytes: Long
    ): Reservation? {
        if (bytes <= 0L || usableSpaceBytes < 0L || minFreeBytes < 0L) return null

        // Honour the strictest floor held by any live writer. Production writers use
        // the same floor, while this also makes mixed test/configuration values safe.
        val requiredFloor = maxOf(
            minFreeBytes,
            reservations.values.maxOfOrNull(Reservation::minFreeBytes) ?: 0L
        )
        val requestedAndReserved = safeAdd(reservedBytes, bytes)
        if (usableSpaceBytes < requiredFloor ||
            requestedAndReserved > usableSpaceBytes - requiredFloor
        ) return null

        val reservation = Reservation(nextReservationId++, bytes, minFreeBytes)
        reservations[reservation.id] = reservation
        reservedBytes = requestedAndReserved
        return reservation
    }

    @Synchronized
    fun release(reservation: Reservation): Boolean {
        val live = reservations[reservation.id] ?: return false
        if (live !== reservation) return false
        reservations.remove(reservation.id)
        reservedBytes = (reservedBytes - live.bytes).coerceAtLeast(0L)
        return true
    }

    private companion object {
        fun safeAdd(left: Long, right: Long): Long =
            if (right > 0L && Long.MAX_VALUE - left < right) Long.MAX_VALUE else left + right
    }
}

/** One free-space reservation domain for every private-files writer in this process. */
private val NativeProcessFreeSpaceReservations = NativeFreeSpaceReservationLedger()

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
    private val minFreeBytes: Long = MIN_FREE_BYTES,
    private val freeSpaceReservations: NativeFreeSpaceReservationLedger =
        NativeProcessFreeSpaceReservations
) {
    class Reservation internal constructor(
        internal val id: Long,
        val bytes: Long,
        internal val freeSpaceReservation: NativeFreeSpaceReservationLedger.Reservation? = null
    )

    private data class ReservationEntry(
        val bytes: Long,
        val freeSpaceReservation: NativeFreeSpaceReservationLedger.Reservation
    )

    private var committedBytes: Long? = null
    private var reservedBytes = 0L
    private var nextReservationId = 1L
    private val reservations = LinkedHashMap<Long, ReservationEntry>()

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

        val freeSpaceReservation = freeSpaceReservations.tryReserve(
            bytes = bytes,
            usableSpaceBytes = usableSpaceBytes,
            minFreeBytes = minFreeBytes
        ) ?: return null

        val id = nextReservationId++
        reservations[id] = ReservationEntry(bytes, freeSpaceReservation)
        reservedBytes = safeAdd(reservedBytes, bytes)
        return Reservation(id, bytes, freeSpaceReservation)
    }

    @Synchronized
    fun commit(reservation: Reservation, actualBytes: Long): Boolean {
        val entry = reservations[reservation.id] ?: return false
        if (entry.freeSpaceReservation !== reservation.freeSpaceReservation ||
            entry.bytes != reservation.bytes
        ) return false
        // File-size limits are not guaranteed to be exact on every recorder/backend.
        // Never turn a reservation into a larger committed allocation: the writer must
        // discard the file and release this still-live reservation instead.
        if (actualBytes < 0L || actualBytes > entry.bytes) return false
        reservations.remove(reservation.id)
        reservedBytes = (reservedBytes - entry.bytes).coerceAtLeast(0L)
        committedBytes = safeAdd(committedBytes ?: 0L, actualBytes)
        freeSpaceReservations.release(entry.freeSpaceReservation)
        return true
    }

    @Synchronized
    fun release(reservation: Reservation) {
        val entry = reservations[reservation.id] ?: return
        if (entry.freeSpaceReservation !== reservation.freeSpaceReservation ||
            entry.bytes != reservation.bytes
        ) return
        reservations.remove(reservation.id)
        reservedBytes = (reservedBytes - entry.bytes).coerceAtLeast(0L)
        freeSpaceReservations.release(entry.freeSpaceReservation)
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
        const val MAX_KEYFRAME_BYTES = NativeStoredImagePolicy.MAX_KEYFRAME_BURST_BYTES

        private fun safeAdd(left: Long, right: Long): Long =
            if (right > 0L && Long.MAX_VALUE - left < right) Long.MAX_VALUE else left + right
    }
}
