package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeMediaStorageQuotaTest {
    @Test
    fun requiresFilesystemReconciliationBeforeAnyWriterCanReserve() {
        val quota = NativeMediaStorageQuota(maxTotalBytes = 100, minFreeBytes = 10)

        assertNull(quota.tryReserve(20, usableSpaceBytes = 100))
        quota.reconcile(30)
        assertNotNull(quota.tryReserve(20, usableSpaceBytes = 100))
    }

    @Test
    fun videoAndKeyframesReserveAgainstOneConcurrentCap() {
        val quota = NativeMediaStorageQuota(maxTotalBytes = 100, minFreeBytes = 10)
        quota.reconcile(25)

        val video = quota.tryReserve(60, usableSpaceBytes = 1_000)
        assertNotNull(video)
        assertNull(quota.tryReserve(16, usableSpaceBytes = 1_000))

        quota.release(video!!)
        val keyframe = quota.tryReserve(16, usableSpaceBytes = 1_000)
        assertNotNull(keyframe)
        assertTrue(quota.commit(keyframe!!, actualBytes = 12))
        assertEquals(37L, quota.accountedBytes())
    }

    @Test
    fun preservesFreeSpaceAndReclaimsCommittedBytesAfterDeletion() {
        val quota = NativeMediaStorageQuota(maxTotalBytes = 100, minFreeBytes = 40)
        quota.reconcile(80)

        assertNull(quota.tryReserve(10, usableSpaceBytes = 49))
        assertNotNull(quota.tryReserve(10, usableSpaceBytes = 50))

        quota.noteDeletion(30)
        assertEquals(60L, quota.accountedBytes()) // 50 committed + 10 reserved
    }

    @Test
    fun concurrentReservationsAlsoShareTheFreeSpaceAllowance() {
        val quota = NativeMediaStorageQuota(maxTotalBytes = 1_000, minFreeBytes = 40)
        quota.reconcile(0)

        assertNotNull(quota.tryReserve(35, usableSpaceBytes = 100))
        assertNull(quota.tryReserve(30, usableSpaceBytes = 100))
        assertNotNull(quota.tryReserve(25, usableSpaceBytes = 100))
    }

    @Test
    fun oversizedCommitIsRejectedWithoutConsumingTheReservation() {
        val quota = NativeMediaStorageQuota(maxTotalBytes = 100, minFreeBytes = 0)
        quota.reconcile(10)
        val reservation = quota.tryReserve(20, usableSpaceBytes = 1_000)!!

        assertFalse(quota.commit(reservation, actualBytes = 21))
        assertEquals(30L, quota.accountedBytes())

        quota.release(reservation)
        assertEquals(10L, quota.accountedBytes())
    }

    @Test
    fun rejectedFileThatCannotBeDeletedRemainsAccounted() {
        val quota = NativeMediaStorageQuota(maxTotalBytes = 100, minFreeBytes = 0)
        quota.reconcile(10)
        val reservation = quota.tryReserve(20, usableSpaceBytes = 1_000)!!

        assertFalse(quota.commit(reservation, actualBytes = 21))
        quota.release(reservation)
        quota.noteUnexpectedExistingFile(21)

        assertEquals(31L, quota.accountedBytes())
    }
}
