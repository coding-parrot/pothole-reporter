package dev.aiengg.potholereporter.plugin

import java.io.File

/** Path and deletion rules for media owned by the app's private storage roots. */
internal object NativePrivateMediaFiles {
    /** API-24-safe canonical containment check (java.nio.file.Path starts at API 26). */
    fun contains(root: File, candidate: File, includeRoot: Boolean = true): Boolean {
        val canonicalRoot = root.canonicalFile
        val canonicalCandidate = candidate.canonicalFile
        if (includeRoot && canonicalCandidate == canonicalRoot) return true
        val rootPrefix = canonicalRoot.path.trimEnd(File.separatorChar) + File.separator
        return canonicalCandidate.path.startsWith(rootPrefix)
    }

    fun descendant(root: File, storedPath: String): File {
        require(storedPath.isNotBlank()) { "Stored media path is empty" }
        val canonicalRoot = root.canonicalFile
        val target = File(storedPath).canonicalFile
        require(contains(canonicalRoot, target, includeRoot = false)) {
            "Stored media path is outside app-private storage"
        }
        return target
    }

    /**
     * Tries every target so one bad file does not strand the rest. Missing files already
     * satisfy deletion. False means the caller must retain its durable row and retry.
     */
    fun deleteAll(
        files: Iterable<File>,
        delete: (File) -> Boolean = { file -> !file.exists() || file.delete() }
    ): Boolean {
        var complete = true
        files.distinctBy { runCatching { it.canonicalPath }.getOrDefault(it.absolutePath) }
            .forEach { file ->
                val removed = runCatching { delete(file) && !file.exists() }.getOrDefault(false)
                if (!removed) complete = false
            }
        return complete
    }
}
