package dev.aiengg.potholereporter.plugin

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeRepairTargetStagingLedgerTest {
    @Test
    fun acceptsTwoTwoOneBatchesAndCompletesOnlyAfterTheManifestIsReceived() {
        val ledger = NativeRepairTargetStagingLedger(listOf(11L, 12L, 13L, 14L, 15L))

        assertFalse(ledger.complete)
        assertThrows(IllegalStateException::class.java) { ledger.requireComplete() }

        ledger.acceptBatch(0, listOf(11L, 12L), listOf(10L, 20L))
        assertEquals(2, ledger.receivedCount)
        assertEquals(30L, ledger.receivedBytes)
        assertFalse(ledger.complete)

        ledger.acceptBatch(2, listOf(13L, 14L), listOf(30L, 40L))
        ledger.acceptBatch(4, listOf(15L), listOf(50L))

        assertEquals(5, ledger.receivedCount)
        assertEquals(150L, ledger.receivedBytes)
        assertTrue(ledger.complete)
        ledger.requireComplete()
    }

    @Test
    fun rejectsMoreThanTwoItemsWithoutChangingCounters() {
        val ledger = NativeRepairTargetStagingLedger(listOf(1L, 2L, 3L))

        assertThrows(IllegalArgumentException::class.java) {
            ledger.acceptBatch(0, listOf(1L, 2L, 3L), listOf(10L, 10L, 10L))
        }

        assertEquals(0, ledger.receivedCount)
        assertEquals(0L, ledger.receivedBytes)
    }

    @Test
    fun rejectsOutOfOrderAndManifestMismatchWithoutChangingCounters() {
        val ledger = NativeRepairTargetStagingLedger(listOf(21L, 22L, 23L))

        assertThrows(IllegalArgumentException::class.java) {
            ledger.acceptBatch(1, listOf(22L), listOf(10L))
        }
        assertEquals(0, ledger.receivedCount)
        assertEquals(0L, ledger.receivedBytes)

        assertThrows(IllegalArgumentException::class.java) {
            ledger.acceptBatch(0, listOf(22L), listOf(10L))
        }
        assertEquals(0, ledger.receivedCount)
        assertEquals(0L, ledger.receivedBytes)

        ledger.acceptBatch(0, listOf(21L), listOf(10L))
        assertEquals(1, ledger.receivedCount)
        assertEquals(10L, ledger.receivedBytes)
    }

    @Test
    fun rejectsAnImageOverThePerImageLimitWithoutChangingCounters() {
        val ledger = NativeRepairTargetStagingLedger(listOf(1L))

        assertThrows(IllegalArgumentException::class.java) {
            ledger.acceptBatch(
                offset = 0,
                ids = listOf(1L),
                imageBytes = listOf(NativeRepairTargetStagingLedger.MAX_IMAGE_BYTES + 1L)
            )
        }

        assertEquals(0, ledger.receivedCount)
        assertEquals(0L, ledger.receivedBytes)
    }

    @Test
    fun rejectsAnAggregateOverflowAtomicallyUsingConfiguredLimits() {
        val ledger = NativeRepairTargetStagingLedger(
            expectedIds = listOf(1L, 2L, 3L),
            maxImageBytes = 10L,
            maxTotalBytes = 10L
        )
        ledger.acceptBatch(0, listOf(1L, 2L), listOf(4L, 4L))

        assertThrows(IllegalArgumentException::class.java) {
            ledger.acceptBatch(2, listOf(3L), listOf(3L))
        }

        assertEquals(2, ledger.receivedCount)
        assertEquals(8L, ledger.receivedBytes)
        assertFalse(ledger.complete)
    }

    @Test
    fun enforcesTheMaximumManifestSizeAndRejectsDuplicateIds() {
        val maximumIds = (1L..NativeRepairTargetStagingLedger.MAX_TARGETS.toLong()).toList()
        assertEquals(
            NativeRepairTargetStagingLedger.MAX_TARGETS,
            NativeRepairTargetStagingLedger(maximumIds).expectedCount
        )

        assertThrows(IllegalArgumentException::class.java) {
            NativeRepairTargetStagingLedger(maximumIds + (maximumIds.last() + 1L))
        }
        assertThrows(IllegalArgumentException::class.java) {
            NativeRepairTargetStagingLedger(listOf(1L, 2L, 1L))
        }
    }
}
