package dev.aiengg.potholereporter.plugin

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import java.io.File
import java.nio.file.Files

class NativeOutboxKeysetPagerTest {
    private data class Evidence(val id: Long, val file: File, val expectedBytes: Long)

    @Test
    fun missingOversizeAndCorruptOldestRowsDoNotStarveLaterValidEvidence() = runBlocking {
        val root = Files.createTempDirectory("outbox-keyset-test").toFile()
        try {
            val missing = File(root, "missing.jpg")
            val oversize = File(root, "oversize.jpg").apply { writeBytes(ByteArray(65)) }
            val corrupt = File(root, "corrupt.jpg").apply { writeBytes(ByteArray(32) { 9 }) }
            val valid = File(root, "valid.jpg").apply {
                writeBytes(NativeBridgeJpegTest.minimalJpeg())
            }
            val rows = listOf(
                Evidence(1L, missing, 28L),
                Evidence(2L, oversize, oversize.length()),
                Evidence(3L, corrupt, corrupt.length()),
                Evidence(4L, valid, valid.length())
            )
            val loadedAfter = mutableListOf<Long>()
            val budget = NativeBridgeImageBudget(
                maxEncodedChars = 200L,
                maxSingleRawBytes = 64L,
                maxItems = 1
            )

            val page = NativeOutboxKeysetPager.collect(
                limit = 1,
                pageSize = 2,
                idOf = Evidence::id,
                loadAfter = { afterId, limit ->
                    loadedAfter += afterId
                    rows.filter { it.id > afterId }.take(limit)
                },
                evaluate = { row ->
                    val claim = budget.rawImageClaim(row.expectedBytes)
                        ?: return@collect NativeOutboxCandidate.Invalid
                    if (!budget.canEverReserve(listOf(claim))) {
                        return@collect NativeOutboxCandidate.Invalid
                    }
                    val reservation = budget.reserve(listOf(claim))
                        ?: return@collect NativeOutboxCandidate.CapacityReached
                    try {
                        NativeBridgeJpeg.readExact(row.file, row.expectedBytes, 64L)
                            ?: return@collect NativeOutboxCandidate.Invalid
                        reservation.commit()
                        NativeOutboxCandidate.Accepted(row.id)
                    } finally {
                        reservation.close()
                    }
                }
            )

            assertEquals(listOf(4L), page.values)
            assertEquals(4, page.scanned)
            assertEquals(3, page.skipped)
            assertFalse(page.capacityReached)
            assertEquals(listOf(0L, 2L), loadedAfter)
            assertEquals(1, budget.usedItems)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun candidatePageNeverHydratesMoreThanTheConfiguredWindow() = runBlocking {
        val requested = mutableListOf<Int>()
        val rows = (1L..25L).toList()
        val page = NativeOutboxKeysetPager.collect(
            limit = 1,
            pageSize = 3,
            idOf = { it },
            loadAfter = { afterId, limit ->
                requested += limit
                rows.filter { it > afterId }.take(limit)
            },
            evaluate = { id ->
                if (id == 10L) NativeOutboxCandidate.Accepted(id)
                else NativeOutboxCandidate.Invalid
            }
        )

        assertEquals(listOf(10L), page.values)
        assertEquals(listOf(3, 3, 3, 3), requested)
    }
}
