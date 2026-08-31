package dev.aiengg.potholereporter.drive

import android.content.Context
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.io.File

/** Shared footage/keyframe quota for non-CameraX producers. */
internal class NativeSourceMediaLedger(private val context: Context) {
    private val quota = NativeMediaStorageQuota()
    private val inventoryMutex = Mutex()

    private suspend fun ensureInventory() {
        if (quota.isReconciled()) return
        NativeMediaFilesystemMutation.mutex.withLock {
            inventoryMutex.withLock {
                if (quota.isReconciled()) return@withLock
                val actualBytes = File(context.filesDir, "footage").walkTopDown()
                    .filter(File::isFile)
                    .sumOf(File::length)
                quota.reconcile(actualBytes)
            }
        }
    }

    suspend fun reserve(bytes: Long): NativeMediaStorageQuota.Reservation? {
        ensureInventory()
        return quota.tryReserve(bytes, context.filesDir.usableSpace)
    }

    fun commit(
        reservation: NativeMediaStorageQuota.Reservation,
        actualBytes: Long
    ): Boolean = quota.commit(reservation, actualBytes)

    fun release(reservation: NativeMediaStorageQuota.Reservation) = quota.release(reservation)
    fun noteDeletion(bytes: Long) = quota.noteDeletion(bytes)
    fun noteUnexpectedFile(bytes: Long) = quota.noteUnexpectedExistingFile(bytes)

    fun deletionRecorderIfReconciled(): ((Long) -> Unit)? =
        if (quota.isReconciled()) quota::noteDeletion else null
}
