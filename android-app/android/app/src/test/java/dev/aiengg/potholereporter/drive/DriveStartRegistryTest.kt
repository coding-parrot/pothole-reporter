package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Collections

class DriveStartRegistryTest {
    @Test
    fun oneStartOwnsAdmissionUntilItIsCleared() {
        val registry = DriveStartRegistry(nowMs = { 100L })

        assertTrue(registry.admit("first"))
        assertFalse(registry.admit("second"))
        assertEquals("first", registry.pendingRequestId())
        registry.clear("wrong")
        assertEquals("first", registry.pendingRequestId())
        registry.clear("first")
        assertNull(registry.pendingRequestId())
        assertTrue(registry.admit("second"))
    }

    @Test
    fun cancellationKeepsTheStrongestDiscardIntent() {
        val registry = DriveStartRegistry(nowMs = { 100L })
        registry.admit("request")

        assertTrue(registry.cancel("request", discardData = false))
        assertEquals(false, registry.cancellationFor("request"))
        assertTrue(registry.cancel("request", discardData = true))
        assertEquals(true, registry.cancellationFor("request"))
        assertFalse(registry.cancel("other", discardData = true))
    }

    @Test
    fun staleAdmissionExpiresUsingTheInjectedClock() {
        var now = 100L
        val registry = DriveStartRegistry(nowMs = { now }, admissionTtlMs = 30L)
        registry.admit("request")

        now = 131L

        assertNull(registry.pendingRequestId())
        assertTrue(registry.admit("replacement"))
    }

    @Test
    fun firstCompletionWinsAndRecentHistoryIsBounded() {
        val registry = DriveStartRegistry(nowMs = { 0L })
        val first = summary("first")
        registry.recordCompletion("request", first)
        registry.recordCompletion("request", summary("late-empty"))
        assertEquals(first, registry.completionFor("request"))

        repeat(33) { registry.recordCompletion("request-$it", summary("session-$it")) }
        assertNull(registry.completionFor("request"))
        assertEquals(summary("session-32"), registry.completionFor("request-32"))
    }

    @Test
    fun concurrentAdmissionHasExactlyOneWinner() {
        val registry = DriveStartRegistry(nowMs = { 0L })
        val results = Collections.synchronizedList(mutableListOf<Boolean>())
        val threads = (1..16).map { index ->
            Thread { results += registry.admit("request-$index") }
        }

        threads.forEach(Thread::start)
        threads.forEach(Thread::join)

        assertEquals(1, results.count { it })
    }

    private fun summary(sessionId: String) = DriveEndSummary(
        sessionId = sessionId,
        checked = 1,
        found = 1,
        already = 0
    )
}
