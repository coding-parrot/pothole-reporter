package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files

class NativeVideoSegmentStorageTest {
    @Test
    fun commitHappensOnceAndCommittedMediaIsNeverDiscarded() = withStorage { file, quota, storage ->
        file.writeBytes(ByteArray(40))

        assertTrue(storage.commit(40))
        assertFalse(storage.commit(40))
        assertTrue(storage.discard())
        assertTrue(file.isFile)
        assertEquals(40L, quota.accountedBytes())
    }

    @Test
    fun oversizedCommitLeavesTheReservationAvailableForCleanup() =
        withStorage { file, quota, storage ->
            file.writeBytes(ByteArray(101))

            assertFalse(storage.commit(101))
            assertTrue(storage.discard())
            assertFalse(file.exists())
            assertEquals(0L, quota.accountedBytes())
        }

    @Test
    fun repeatedDiscardReconcilesARecorderThatKeepsWriting() {
        var deletionAllowed = false
        withStorage(deleteFile = { file -> deletionAllowed && file.delete() }) {
            file, quota, storage ->
            file.writeBytes(ByteArray(10))
            assertFalse(storage.discard())
            assertTrue(storage.isDiscarded())
            assertEquals(10L, quota.accountedBytes())

            file.appendBytes(ByteArray(15))
            assertFalse(storage.discard())
            assertEquals(25L, quota.accountedBytes())

            deletionAllowed = true
            assertTrue(storage.discard())
            assertEquals(0L, quota.accountedBytes())
        }
    }

    private fun withStorage(
        deleteFile: (java.io.File) -> Boolean = java.io.File::delete,
        block: (java.io.File, NativeMediaStorageQuota, NativeVideoSegmentStorage) -> Unit
    ) {
        val directory = Files.createTempDirectory("video-segment-storage").toFile()
        try {
            val file = directory.resolve("segment.mp4")
            val quota = NativeMediaStorageQuota(
                maxTotalBytes = 1_000L,
                minFreeBytes = 0L,
                freeSpaceReservations = NativeFreeSpaceReservationLedger()
            )
            quota.reconcile(0L)
            val reservation = requireNotNull(quota.tryReserve(100L, 1_000L))
            block(file, quota, NativeVideoSegmentStorage(file, reservation, quota, deleteFile))
        } finally {
            directory.deleteRecursively()
        }
    }
}
