package dev.aiengg.potholereporter.drive

import java.io.File

/** Verified deletion that makes a surviving private file discoverable to reconciliation. */
internal object NativeRetryableFileCleanup {
    fun deleteVerified(
        file: File,
        delete: (File) -> Boolean = File::delete
    ): Boolean {
        val removed = runCatching {
            if (file.exists()) delete(file)
            !file.exists()
        }.getOrDefault(false)
        if (!removed) NativeMediaReconciliationEpoch.invalidate()
        return removed
    }
}

/**
 * Quota delta produced while rolling back a two-file keyframe transaction.
 * [removedAccountedBytes] were already committed to the live ledger. In contrast,
 * [remainingUnaccountedBytes] survived after their reservation was not committed.
 */
internal data class NativeKeyframeCleanupResult(
    val cleanupComplete: Boolean,
    val removedAccountedBytes: Long,
    val remainingUnaccountedBytes: Long
)

internal object NativeKeyframeFailureCleanup {
    fun cleanup(
        accountedFiles: List<File>,
        unaccountedFiles: List<File>,
        delete: (File) -> Boolean = File::delete
    ): NativeKeyframeCleanupResult {
        data class OwnedFile(val file: File, val accounted: Boolean)

        val owned = LinkedHashMap<String, OwnedFile>()
        unaccountedFiles.forEach { file ->
            owned[fileKey(file)] = OwnedFile(file, accounted = false)
        }
        // If a malformed plan ever aliases a path, treating it as accounted avoids
        // subtracting nothing after deleting bytes that the ledger already owns.
        accountedFiles.forEach { file ->
            owned[fileKey(file)] = OwnedFile(file, accounted = true)
        }

        var cleanupComplete = true
        var removedAccountedBytes = 0L
        var remainingUnaccountedBytes = 0L
        owned.values.forEach { ownedFile ->
            val file = ownedFile.file
            val existedAsFile = file.isFile
            val bytesBefore = if (existedAsFile) file.length().coerceAtLeast(0L) else 0L
            val removed = NativeRetryableFileCleanup.deleteVerified(file, delete)
            if (!removed) cleanupComplete = false

            if (ownedFile.accounted && existedAsFile && removed) {
                removedAccountedBytes = safeAdd(removedAccountedBytes, bytesBefore)
            } else if (!ownedFile.accounted && file.isFile) {
                remainingUnaccountedBytes = safeAdd(
                    remainingUnaccountedBytes,
                    file.length().coerceAtLeast(0L)
                )
            }
        }
        return NativeKeyframeCleanupResult(
            cleanupComplete,
            removedAccountedBytes,
            remainingUnaccountedBytes
        )
    }

    private fun fileKey(file: File): String =
        runCatching { file.canonicalPath }.getOrDefault(file.absolutePath)

    private fun safeAdd(left: Long, right: Long): Long =
        if (right > 0L && Long.MAX_VALUE - left < right) Long.MAX_VALUE else left + right
}
