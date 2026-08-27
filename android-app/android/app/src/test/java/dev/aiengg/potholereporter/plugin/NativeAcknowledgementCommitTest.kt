package dev.aiengg.potholereporter.plugin

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeAcknowledgementCommitTest {
    @Test
    fun `Room commit always precedes file cleanup`() = runBlocking {
        val trace = mutableListOf<String>()

        val acknowledged = NativeAcknowledgementCommit.commitThenCleanup(
            expectedCount = 2,
            commit = { trace += "commit"; 2 },
            cleanup = { trace += "cleanup"; true },
            onCleanupIncomplete = { trace += "reconcile" }
        )

        assertEquals(2, acknowledged)
        assertEquals(listOf("commit", "cleanup"), trace)
    }

    @Test
    fun `commit failure never deletes evidence`() {
        var cleanupCalled = false

        assertThrows(IllegalStateException::class.java) {
            runBlocking {
                NativeAcknowledgementCommit.commitThenCleanup(
                    expectedCount = 1,
                    commit = { throw IllegalStateException("database failed") },
                    cleanup = { cleanupCalled = true; true },
                    onCleanupIncomplete = {}
                )
            }
        }

        assertFalse(cleanupCalled)
    }

    @Test
    fun `affected-row mismatch never deletes evidence`() {
        var cleanupCalled = false

        assertThrows(IllegalStateException::class.java) {
            runBlocking {
                NativeAcknowledgementCommit.commitThenCleanup(
                    expectedCount = 2,
                    commit = { 1 },
                    cleanup = { cleanupCalled = true; true },
                    onCleanupIncomplete = {}
                )
            }
        }

        assertFalse(cleanupCalled)
    }

    @Test
    fun `post-commit cleanup failure schedules reconciliation but keeps ack`() = runBlocking {
        var reconcile = false

        val acknowledged = NativeAcknowledgementCommit.commitThenCleanup(
            expectedCount = 1,
            commit = { 1 },
            cleanup = { throw IllegalStateException("file busy") },
            onCleanupIncomplete = { reconcile = true }
        )

        assertEquals(1, acknowledged)
        assertTrue(reconcile)
    }
}
