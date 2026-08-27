package dev.aiengg.potholereporter.plugin

/**
 * Enforces the durability boundary for a native outbox acknowledgement.
 *
 * Evidence remains intact if Room fails or affects fewer rows than expected. Once Room
 * commits, file removal is only orphan cleanup: failure is recorded for reconciliation
 * but must not turn a durable acknowledgement into a retry.
 */
internal object NativeAcknowledgementCommit {
    suspend fun commitThenCleanup(
        expectedCount: Int,
        commit: suspend () -> Int,
        cleanup: () -> Boolean,
        onCleanupIncomplete: () -> Unit
    ): Int {
        require(expectedCount >= 0) { "acknowledgement count is invalid" }
        val affected = commit()
        check(affected == expectedCount) {
            "Room acknowledged $affected of $expectedCount rows"
        }
        val cleaned = runCatching(cleanup).getOrDefault(false)
        if (!cleaned) runCatching(onCleanupIncomplete)
        return affected
    }
}
