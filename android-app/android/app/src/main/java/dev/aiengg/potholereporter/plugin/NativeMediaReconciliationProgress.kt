package dev.aiengg.potholereporter.plugin

import java.io.File

/**
 * Tracks cleanup truth while native Room rows and their private files are reconciled.
 * A failed verified deletion leaves [cleanupComplete] false so the next bridge read
 * retries reconciliation instead of treating an orphan as permanently handled.
 */
internal class NativeMediaReconciliationProgress {
    var cleanupComplete: Boolean = true
        private set

    var deletedFootageBytes: Long = 0L
        private set

    fun markCleanupIncomplete() {
        cleanupComplete = false
    }

    fun recordFootageDeletion(bytes: Long) {
        if (bytes <= 0L) return
        deletedFootageBytes = if (Long.MAX_VALUE - deletedFootageBytes < bytes) {
            Long.MAX_VALUE
        } else {
            deletedFootageBytes + bytes
        }
    }

    /** Deletes one file and verifies absence before crediting the live footage ledger. */
    fun deleteFileVerified(
        file: File,
        footage: Boolean = false,
        delete: (File) -> Boolean = File::delete
    ): Boolean {
        val existed = file.exists()
        val bytes = if (existed && footage && file.isFile) {
            file.length().coerceAtLeast(0L)
        } else {
            0L
        }
        val removed = runCatching {
            if (file.exists()) delete(file)
            !file.exists()
        }.getOrDefault(false)
        if (!removed) {
            markCleanupIncomplete()
            return false
        }
        if (existed && footage) recordFootageDeletion(bytes)
        return true
    }
}
