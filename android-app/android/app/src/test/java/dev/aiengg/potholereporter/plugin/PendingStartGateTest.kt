package dev.aiengg.potholereporter.plugin

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class PendingStartGateTest {
    @Test
    fun stopDuringPendingStartPreventsReadyAndKeepsAllWaiters() {
        val gate = PendingStartGate<String>()

        assertTrue(gate.requestCancellation("stop"))
        assertTrue(gate.requestCancellation("wipe"))
        assertFalse(gate.claimReady())
        assertTrue(gate.isCancellationRequested())
        assertEquals(listOf("stop", "wipe"), gate.settleCancellation())
        assertTrue(gate.settleCancellation().isEmpty())
    }

    @Test
    fun readyAndImmediateStopCannotBothWin() {
        repeat(200) {
            val gate = PendingStartGate<String>()
            val start = CountDownLatch(1)
            val readyWon = AtomicBoolean()
            val stopWon = AtomicBoolean()
            val executor = Executors.newFixedThreadPool(2)
            val ready = executor.submit {
                start.await()
                readyWon.set(gate.claimReady())
            }
            val stop = executor.submit {
                start.await()
                stopWon.set(gate.requestCancellation("stop"))
            }
            start.countDown()
            ready.get(2, TimeUnit.SECONDS)
            stop.get(2, TimeUnit.SECONDS)
            executor.shutdownNow()

            assertNotEquals(readyWon.get(), stopWon.get())
            if (stopWon.get()) assertEquals(listOf("stop"), gate.settleCancellation())
        }
    }

    @Test
    fun stopBeforeStartDispatchQueuesOnlyAfterStartWasAccepted() {
        val gate = PendingStartGate<String>()
        assertTrue(gate.requestCancellation("stop"))
        assertFalse(gate.queueStopIntentOnce())
        assertTrue(gate.markStartRequestAccepted())
        assertTrue(gate.queueStopIntentOnce())
        assertFalse(gate.queueStopIntentOnce())
    }

    @Test
    fun cancellationPolicyCanDistinguishStopFromWipe() {
        val gate = PendingStartGate<Boolean>()
        gate.requestCancellation(false)
        assertFalse(gate.anyWaiter { it })
        gate.requestCancellation(true)
        assertTrue(gate.anyWaiter { it })
    }
}
