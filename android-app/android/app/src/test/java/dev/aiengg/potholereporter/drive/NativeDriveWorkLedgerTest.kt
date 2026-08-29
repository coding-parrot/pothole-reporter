package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class NativeDriveWorkLedgerTest {
    @Test
    fun liveAdmissionCanBeConsumedOrDeferredExactlyOnce() {
        val ledger = NativeDriveWorkLedger()
        ledger.admitLive()
        ledger.admitLive()

        assertTrue(ledger.consumeLive())
        assertTrue(ledger.deferLive())
        assertEquals(NativeDriveWorkSnapshot(checked = 0, queued = 0, deferred = 1), ledger.snapshot())
    }

    @Test
    fun duplicateCompletionCannotUnderflowQueue() {
        val ledger = NativeDriveWorkLedger()

        assertFalse(ledger.consumeLive())
        assertEquals(0, ledger.snapshot().queued)
        ledger.admitLive()
        assertTrue(ledger.consumeLive())
        assertFalse(ledger.consumeLive())
        assertEquals(0, ledger.snapshot().queued)
    }

    @Test
    fun resetStartsACompletelyNewSession() {
        val ledger = NativeDriveWorkLedger()
        ledger.admitLive()
        ledger.deferLive()
        ledger.completeIfAnalyzed(true)

        ledger.reset()

        assertEquals(NativeDriveWorkSnapshot(0, 0, 0), ledger.snapshot())
    }

    @Test
    fun onlyCompleteModelVerdictsIncrementChecked() {
        val ledger = NativeDriveWorkLedger()

        assertFalse(ledger.completeIfAnalyzed(false))
        assertEquals(0, ledger.completedCount())
        assertTrue(ledger.completeIfAnalyzed(true))
        assertEquals(1, ledger.completedCount())
    }

    @Test
    fun scannerAndChannelCallbacksCannotLoseConcurrentCounts() {
        val ledger = NativeDriveWorkLedger()
        val workers = 8
        val iterations = 1_000
        val executor = Executors.newFixedThreadPool(workers)
        val start = CountDownLatch(1)
        val done = CountDownLatch(workers)
        repeat(workers) {
            executor.execute {
                start.await()
                repeat(iterations) {
                    ledger.admitLive()
                    ledger.deferLive()
                    ledger.completeIfAnalyzed(true)
                }
                done.countDown()
            }
        }

        start.countDown()
        val completed = done.await(10, TimeUnit.SECONDS)
        executor.shutdownNow()
        assertTrue(completed)
        assertEquals(
            NativeDriveWorkSnapshot(
                checked = workers * iterations,
                queued = 0,
                deferred = workers * iterations
            ),
            ledger.snapshot()
        )
    }
}
