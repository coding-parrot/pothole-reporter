package dev.aiengg.potholereporter.plugin

internal sealed class NativeOutboxCandidate<out T> {
    data class Accepted<T>(val value: T) : NativeOutboxCandidate<T>()
    data object Invalid : NativeOutboxCandidate<Nothing>()
    data object CapacityReached : NativeOutboxCandidate<Nothing>()
}

internal data class NativeOutboxPage<T>(
    val values: List<T>,
    val scanned: Int,
    val skipped: Int,
    val capacityReached: Boolean
)

/**
 * Walks an outbox by primary-key pages without ever materialising its full backlog.
 * Invalid legacy rows advance the local keyset cursor, while a capacity stop leaves the
 * candidate retryable on the next bridge call.
 */
internal object NativeOutboxKeysetPager {
    const val CANDIDATE_PAGE_SIZE = 8

    suspend fun <C, T> collect(
        limit: Int,
        pageSize: Int = CANDIDATE_PAGE_SIZE,
        idOf: (C) -> Long,
        loadAfter: suspend (afterId: Long, limit: Int) -> List<C>,
        evaluate: suspend (C) -> NativeOutboxCandidate<T>
    ): NativeOutboxPage<T> {
        require(limit > 0) { "Outbox result limit must be positive" }
        require(pageSize > 0) { "Outbox candidate page must be positive" }
        val accepted = ArrayList<T>(limit)
        var afterId = 0L
        var scanned = 0
        var skipped = 0

        while (accepted.size < limit) {
            val page = loadAfter(afterId, pageSize)
            require(page.size <= pageSize) { "Outbox DAO exceeded its candidate page limit" }
            if (page.isEmpty()) break
            var advanced = false
            for (candidate in page) {
                val id = idOf(candidate)
                if (id <= afterId) continue
                afterId = id
                advanced = true
                scanned++
                when (val decision = evaluate(candidate)) {
                    is NativeOutboxCandidate.Accepted -> {
                        accepted += decision.value
                        if (accepted.size >= limit) break
                    }
                    NativeOutboxCandidate.Invalid -> skipped++
                    NativeOutboxCandidate.CapacityReached -> return NativeOutboxPage(
                        accepted, scanned, skipped, capacityReached = true
                    )
                }
            }
            if (!advanced || page.size < pageSize) break
        }
        return NativeOutboxPage(accepted, scanned, skipped, capacityReached = false)
    }
}
