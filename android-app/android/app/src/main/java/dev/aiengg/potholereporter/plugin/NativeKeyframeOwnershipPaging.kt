package dev.aiengg.potholereporter.plugin

import java.io.File

/** Fixed memory bounds and path candidates for private keyframe reconciliation. */
internal object NativeKeyframeOwnershipPaging {
    const val ROW_PAGE_SIZE = 128
    const val FILE_PAGE_SIZE = 128
    const val MAX_OWNER_CANDIDATES_PER_FILE = 2
    const val OWNER_QUERY_LIMIT = FILE_PAGE_SIZE * MAX_OWNER_CANDIDATES_PER_FILE

    /**
     * A JPEG can be a row's primary file or the deterministic `_context` companion of
     * that primary. Return both possible stored paths without scanning other rows or
     * directories. Temporary/non-JPEG files can never be owned by a committed row.
     */
    fun ownerCandidatePaths(file: File): List<String> {
        if (!file.extension.equals("jpg", ignoreCase = true)) return emptyList()
        val exact = file.absolutePath
        if (!file.name.endsWith("_context.jpg", ignoreCase = true)) {
            return listOf(exact)
        }
        val primaryName = file.name.dropLast("_context.jpg".length) + ".jpg"
        return listOf(exact, File(file.parentFile, primaryName).absolutePath).distinct()
    }

    fun ownerCandidatePaths(files: List<File>): List<String> {
        require(files.size <= FILE_PAGE_SIZE) { "Keyframe file page exceeds memory bound" }
        return files.asSequence()
            .flatMap { ownerCandidatePaths(it).asSequence() }
            .distinct()
            .toList()
            .also {
                check(it.size <= OWNER_QUERY_LIMIT) { "Keyframe owner query exceeds SQL bound" }
            }
    }

    data class RecoveredSessionState(
        val startedAtSeconds: Long,
        val endedAtSeconds: Long?,
        val status: String
    )

    /**
     * Rebuilds the smallest truthful parent row for a valid keyframe whose asynchronous
     * session insert was lost to process death. A currently running producer remains
     * active; every other recovered session is an interrupted historical drive.
     */
    fun recoveredSessionState(
        sessionId: String,
        firstCapturedAtMs: Long,
        activeSessionId: String?,
        nowSeconds: Long
    ): RecoveredSessionState {
        val capturedSeconds = firstCapturedAtMs.coerceAtLeast(0L) / 1_000L
        val idSeconds = sessionId.toLongOrNull()
            ?.takeIf { it >= 0L }
            ?.div(1_000L)
        val startedAt = idSeconds ?: capturedSeconds
        return if (sessionId == activeSessionId) {
            RecoveredSessionState(startedAt, null, "active")
        } else {
            RecoveredSessionState(startedAt, nowSeconds.coerceAtLeast(startedAt), "interrupted")
        }
    }
}
