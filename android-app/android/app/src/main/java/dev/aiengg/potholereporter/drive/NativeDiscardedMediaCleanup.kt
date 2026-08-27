package dev.aiengg.potholereporter.drive

import java.io.File

/**
 * Tracks only the bytes that this discarded writer has added to the shared quota.
 *
 * A recorder can keep writing after stop/close times out. Re-running cleanup therefore
 * has to account the delta from the last failed deletion, rather than charging the whole
 * file again or assuming that a previously released reservation still covers it.
 */
internal class NativeDiscardedMediaCleanup {
    data class Result(
        val deleted: Boolean,
        val addedBytes: Long,
        val removedBytes: Long,
        val remainingBytes: Long
    )

    private var accountedBytes = 0L

    @Synchronized
    fun reconcile(
        file: File,
        delete: (File) -> Boolean = { candidate -> candidate.delete() }
    ): Result {
        if (file.isFile) runCatching { delete(file) }

        // Trust the filesystem after the attempt, not File.delete()'s return value. A
        // concurrent recorder may recreate or extend the path before Finalize arrives.
        val remainingBytes = if (file.isFile) file.length().coerceAtLeast(0L) else 0L
        val addedBytes = (remainingBytes - accountedBytes).coerceAtLeast(0L)
        val removedBytes = (accountedBytes - remainingBytes).coerceAtLeast(0L)
        accountedBytes = remainingBytes
        if (remainingBytes > 0L) {
            // The recorder has no durable Room owner for this path. Keep the bridge
            // reconciliation epoch open after the last CameraX callback goes away.
            NativeMediaReconciliationEpoch.invalidate()
        }

        return Result(
            deleted = !file.isFile,
            addedBytes = addedBytes,
            removedBytes = removedBytes,
            remainingBytes = remainingBytes
        )
    }
}
