package com.gauravsen.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DriveStartCompletionLedgerTest {
    @Test
    fun completionSurvivesTransientServiceDisappearance() {
        val ledger = DriveStartCompletionLedger()
        val completed = DriveEndSummary(
            sessionId = "1724321000000",
            checked = 3,
            found = 1,
            already = 0,
            discarded = false
        )

        ledger.record("start-request-1", completed)

        // The plugin can read this exact acknowledgement even after activeService has
        // already gone null between two polling ticks.
        assertEquals(completed, ledger.summaryFor("start-request-1"))
        assertNull(ledger.summaryFor("different-request"))
    }

    @Test
    fun lateEmptyStopDoesNotOverwriteDurableSummary() {
        val ledger = DriveStartCompletionLedger()
        val completed = DriveEndSummary("1724321000000", 7, 2, 1, discarded = false)
        ledger.record("start-request-1", completed)

        // ACTION_STOP can recreate an empty service after the real one stopped. Its
        // empty acknowledgement must not erase the data-bearing completion.
        ledger.record("start-request-1", DriveEndSummary("", 0, 0, 0, discarded = true))

        assertEquals(completed, ledger.summaryFor("start-request-1"))
    }
}
