package dev.aiengg.potholereporter.drive

import java.io.File

/** Owns one recorder file's reservation, commit, and repeatable failed-write cleanup. */
internal class NativeVideoSegmentStorage(
    private val file: File,
    private val reservation: NativeMediaStorageQuota.Reservation,
    private val quota: NativeMediaStorageQuota,
    private val deleteFile: (File) -> Boolean = File::delete
) {
    private enum class State { ACTIVE, COMMITTED, DISCARDED }

    private var state = State.ACTIVE
    private val discardedFile = NativeDiscardedMediaCleanup()

    @Synchronized
    fun commit(bytes: Long): Boolean {
        if (state != State.ACTIVE || !quota.commit(reservation, bytes)) return false
        state = State.COMMITTED
        return true
    }

    /**
     * Releases the reservation once and reconciles every later cleanup attempt by delta. A late
     * CameraX Finalize may recreate or extend the file after the first cleanup attempt.
     */
    @Synchronized
    fun discard(): Boolean {
        when (state) {
            State.COMMITTED -> return true
            State.ACTIVE -> {
                state = State.DISCARDED
                quota.release(reservation)
            }
            State.DISCARDED -> Unit
        }

        val result = discardedFile.reconcile(file, deleteFile)
        quota.noteDeletion(result.removedBytes)
        quota.noteUnexpectedExistingFile(result.addedBytes)
        return result.deleted
    }

    @Synchronized
    fun isDiscarded(): Boolean = state == State.DISCARDED
}
