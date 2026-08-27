package dev.aiengg.potholereporter.plugin

/**
 * Pure validation/accounting for one transactional repair-target cache replacement.
 *
 * The WebView supplies only ordered two-photo batches. Nothing becomes current until
 * every expected id has been received and the caller atomically switches Room.
 */
internal class NativeRepairTargetStagingLedger(
    expectedIds: List<Long>,
    private val maxTargets: Int = MAX_TARGETS,
    private val maxBatchItems: Int = MAX_BATCH_ITEMS,
    private val maxImageBytes: Long = MAX_IMAGE_BYTES,
    private val maxTotalBytes: Long = MAX_TOTAL_BYTES
) {
    private val expectedIds = expectedIds.toList()

    var receivedCount: Int = 0
        private set

    var receivedBytes: Long = 0L
        private set

    init {
        require(this.expectedIds.size <= maxTargets) { "too many repair targets" }
        require(this.expectedIds.all { it > 0L }) { "repair target id is invalid" }
        require(this.expectedIds.distinct().size == this.expectedIds.size) {
            "repair target id is duplicated"
        }
        require(maxBatchItems > 0 && maxImageBytes > 0L && maxTotalBytes > 0L)
    }

    val expectedCount: Int get() = expectedIds.size
    val complete: Boolean get() = receivedCount == expectedIds.size

    /** Validates and records a batch atomically; a rejected batch changes no counters. */
    fun acceptBatch(offset: Int, ids: List<Long>, imageBytes: List<Long>) {
        require(offset == receivedCount) { "repair target batch is out of order" }
        require(ids.isNotEmpty() && ids.size <= maxBatchItems) {
            "repair target batch must contain 1 to $maxBatchItems targets"
        }
        require(ids.size == imageBytes.size) { "repair target batch is incomplete" }
        require(offset <= expectedIds.size && ids.size <= expectedIds.size - offset) {
            "repair target batch exceeds the manifest"
        }
        require(ids == expectedIds.subList(offset, offset + ids.size)) {
            "repair target batch does not match the manifest"
        }
        require(imageBytes.all { it in 1L..maxImageBytes }) {
            "repair target photo is empty or too large"
        }
        val batchBytes = imageBytes.fold(0L) { total, bytes ->
            require(bytes <= Long.MAX_VALUE - total) { "repair target photo total overflowed" }
            total + bytes
        }
        require(receivedBytes <= maxTotalBytes && batchBytes <= maxTotalBytes - receivedBytes) {
            "repair target photos exceed the 512 MB cache limit"
        }

        receivedCount += ids.size
        receivedBytes += batchBytes
    }

    fun requireComplete() {
        check(complete) {
            "repair target staging is incomplete ($receivedCount of ${expectedIds.size})"
        }
    }

    companion object {
        const val MAX_TARGETS = 2_000
        const val MAX_BATCH_ITEMS = 2
        const val MAX_IMAGE_BYTES = 4L * 1024 * 1024
        const val MAX_TOTAL_BYTES = 512L * 1024 * 1024
    }
}
