package dev.aiengg.potholereporter.plugin

import java.io.File

/**
 * Owns the filesystem transition for one repair-target staging generation.
 *
 * The original staging path is immutable because plugin destruction may race a commit.
 * Destruction may remove only that path; rollback may remove the current path, including
 * a finalized generation whose Room transaction did not commit.
 */
internal class NativeRepairTargetStageDirectory(
    val stagingDirectory: File
) {
    private var finalizedDirectory: File? = null

    fun currentDirectory(): File = finalizedDirectory ?: stagingDirectory

    fun finalizeAs(generationDirectory: File): Boolean {
        check(finalizedDirectory == null) { "repair target staging is already finalized" }
        if (generationDirectory.exists() || !stagingDirectory.renameTo(generationDirectory)) {
            return false
        }
        finalizedDirectory = generationDirectory
        return true
    }

    /** A destruction observer must never be handed the mutable/current generation path. */
    fun destructionCleanupDirectory(): File = stagingDirectory
}
