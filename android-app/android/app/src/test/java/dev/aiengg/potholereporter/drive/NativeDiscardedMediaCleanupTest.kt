package dev.aiengg.potholereporter.drive

import java.io.File
import java.nio.file.Files
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeDiscardedMediaCleanupTest {
    @Test
    fun lateFinalizeChargesOnlyGrowthThenCreditsAccountedBytesWhenDeletionSucceeds() {
        val directory = Files.createTempDirectory("discarded-video-test").toFile()
        try {
            val file = File(directory, "segment.mp4")
            file.writeBytes(ByteArray(10))
            val cleanup = NativeDiscardedMediaCleanup()
            val quota = NativeMediaStorageQuota(
                maxTotalBytes = 1_000L,
                minFreeBytes = 0L,
                freeSpaceReservations = NativeFreeSpaceReservationLedger()
            )
            quota.reconcile(0L)
            val reservation = requireNotNull(quota.tryReserve(100L, 1_000L))
            quota.release(reservation)

            val initialFailure = cleanup.reconcile(file) { false }
            apply(initialFailure, quota)
            assertFalse(initialFailure.deleted)
            assertEquals(10L, initialFailure.addedBytes)
            assertEquals(10L, quota.accountedBytes())

            // This models CameraX continuing to write after stop/close timed out, before
            // its eventual Finalize callback causes the manager to reconcile again.
            file.appendBytes(ByteArray(15))
            val lateFailure = cleanup.reconcile(file) { false }
            apply(lateFailure, quota)
            assertFalse(lateFailure.deleted)
            assertEquals(15L, lateFailure.addedBytes)
            assertEquals(25L, lateFailure.remainingBytes)
            assertEquals(25L, quota.accountedBytes())

            val lateSuccess = cleanup.reconcile(file)
            apply(lateSuccess, quota)
            assertTrue(lateSuccess.deleted)
            assertEquals(0L, lateSuccess.addedBytes)
            assertEquals(25L, lateSuccess.removedBytes)
            assertEquals(0L, quota.accountedBytes())
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun fileRecreatedAfterSuccessfulCleanupIsNotHiddenByTheEarlierResult() {
        val directory = Files.createTempDirectory("discarded-video-recreate-test").toFile()
        try {
            val file = File(directory, "segment.mp4")
            file.writeBytes(ByteArray(8))
            val cleanup = NativeDiscardedMediaCleanup()

            val initial = cleanup.reconcile(file)
            assertTrue(initial.deleted)
            assertEquals(0L, initial.remainingBytes)

            file.writeBytes(ByteArray(12))
            val late = cleanup.reconcile(file) { false }
            assertFalse(late.deleted)
            assertEquals(12L, late.addedBytes)
            assertEquals(0L, late.removedBytes)

            val repeated = cleanup.reconcile(file) { false }
            assertEquals(0L, repeated.addedBytes)
            assertEquals(0L, repeated.removedBytes)
        } finally {
            directory.deleteRecursively()
        }
    }

    private fun apply(
        result: NativeDiscardedMediaCleanup.Result,
        quota: NativeMediaStorageQuota
    ) {
        quota.noteDeletion(result.removedBytes)
        quota.noteUnexpectedExistingFile(result.addedBytes)
    }
}
