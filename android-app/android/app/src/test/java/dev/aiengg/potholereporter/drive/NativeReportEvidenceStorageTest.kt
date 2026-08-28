package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files

class NativeReportEvidenceStorageTest {
    @Test
    fun separateQuotaCannotReservePastFiveHundredAndTwelveMiB() {
        val quota = NativeMediaStorageQuota(
            maxTotalBytes = NativeReportEvidenceStorage.MAX_TOTAL_BYTES,
            minFreeBytes = 0L,
            freeSpaceReservations = NativeFreeSpaceReservationLedger()
        )
        quota.reconcile(NativeReportEvidenceStorage.MAX_TOTAL_BYTES - 10L)

        assertNull(quota.tryReserve(11L, Long.MAX_VALUE))
        val finalBytes = quota.tryReserve(10L, Long.MAX_VALUE)
        assertNotNull(finalBytes)
        assertTrue(quota.commit(finalBytes!!, 10L))
        assertEquals(NativeReportEvidenceStorage.MAX_TOTAL_BYTES, quota.accountedBytes())
    }

    @Test
    fun inferenceCapacityLeaseCanBeFinishedExactlyOnce() {
        val lease = NativeReportEvidenceStorage.InferenceCapacityLease(
            NativeMediaStorageQuota.Reservation(id = 7L, bytes = 2L * 1024L * 1024L)
        )

        assertTrue(lease.finishOnce())
        assertTrue(lease.isFinished())
        assertTrue(!lease.finishOnce())
    }

    @Test
    fun maximumEvidenceReservationsCannotOverbookTheQuota() {
        val quota = NativeMediaStorageQuota(
            maxTotalBytes = 3L * 1024L * 1024L,
            minFreeBytes = 0L,
            freeSpaceReservations = NativeFreeSpaceReservationLedger()
        )
        quota.reconcile(0L)

        val first = quota.tryReserve(2L * 1024L * 1024L, Long.MAX_VALUE)
        assertNotNull(first)
        assertNull(quota.tryReserve(2L * 1024L * 1024L, Long.MAX_VALUE))
        quota.release(first!!)
        assertNotNull(quota.tryReserve(2L * 1024L * 1024L, Long.MAX_VALUE))
    }

    @Test
    fun pruningIsOldestFirstAndNeverReturnsReferencedOrProtectedFiles() {
        val root = Files.createTempDirectory("report-evidence-pruning").toFile()
        try {
            fun evidence(relative: String, modified: Long) = root.resolve(relative).also { file ->
                file.parentFile?.mkdirs()
                file.writeBytes(byteArrayOf(1))
                check(file.setLastModified(modified))
            }
            val protectedRoot = root.resolve("active")
            val protected = evidence("active/in-flight.jpg", 10L)
            val referenced = evidence("referenced.jpg", 20L)
            val oldest = evidence("oldest.jpg", 30L)
            val tieA = evidence("a-tie.jpg", 40L)
            val tieB = evidence("b-tie.jpg", 40L)

            val selected = NativeReportEvidencePruningPolicy.oldestUnowned(
                files = listOf(tieB, protected, referenced, tieA, oldest),
                referencedCanonicalPaths = setOf(referenced.canonicalPath),
                protectedRoots = listOf(protectedRoot)
            )

            assertEquals(
                listOf(oldest.canonicalPath, tieA.canonicalPath, tieB.canonicalPath),
                selected.map { it.canonicalPath }
            )
        } finally {
            root.deleteRecursively()
        }
    }
}
