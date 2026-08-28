package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NativeGpsFixHistoryTest {
    private fun fix(timestampMs: Long) = GpsFix(19.0, 72.0, 5f, 10f, 90f, timestampMs)

    @Test
    fun selectsNearestFixAndPrefersLaterFixOnTie() {
        val history = NativeGpsFixHistory()
        history.add(fix(1_000L))
        history.add(fix(1_500L))
        history.add(fix(2_000L))
        assertEquals(1_500L, history.nearest(1_400L)?.timestampMs)
        assertEquals(2_000L, history.nearest(1_750L)?.timestampMs)
    }

    @Test
    fun boundsHistoryAndClearsIt() {
        val history = NativeGpsFixHistory(capacity = 2)
        history.add(fix(1_000L))
        history.add(fix(2_000L))
        history.add(fix(3_000L))
        assertEquals(2_000L, history.nearest(1_000L)?.timestampMs)
        history.clear()
        assertNull(history.nearest(3_000L))
    }

    @Test
    fun nearestCaptureFixSkipsACloserCoarseOrMalformedSample() {
        val history = NativeGpsFixHistory()
        history.add(fix(1_000L))
        history.add(fix(1_490L).copy(accuracy = 80f))
        history.add(fix(2_000L))
        history.add(fix(1_495L).copy(lat = Double.NaN))

        assertEquals(
            2_000L,
            history.nearestCaptureReady(1_500L, 30f)?.timestampMs
        )
    }
}
