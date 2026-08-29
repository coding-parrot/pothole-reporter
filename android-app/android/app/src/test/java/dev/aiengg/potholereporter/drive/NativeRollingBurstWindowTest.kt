package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NativeRollingBurstWindowTest {
    private fun samples(
        timesMs: List<Long> = listOf(1_000L, 1_167L, 1_334L),
        generation: Long = 7L
    ) = timesMs.map { time ->
        NativeRollingBurstWindow.Sample(time, time * 1_000_000L, generation)
    }

    @Test
    fun selectsCompleteChronologicalThreeSampleBurst() {
        assertEquals(
            listOf(0, 1, 2),
            NativeRollingBurstWindow.selectSourceIndexes(samples(), 1_400L, 7L)
        )
    }

    @Test
    fun rejectsIncompleteStaleOrTooShortWindows() {
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(samples().take(2), 1_400L, 7L))
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(samples(), 2_000L, 7L))
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(
            samples(listOf(1_000L, 1_050L, 1_100L)), 1_150L, 7L
        ))
    }

    @Test
    fun `complete stale window is discarded so a later moving window can recover`() {
        assertEquals(
            NativeRollingBurstWindow.Disposition.DISCARD,
            NativeRollingBurstWindow.disposition(samples(), 2_000L, 7L)
        )
        assertEquals(
            NativeRollingBurstWindow.Disposition.READY,
            NativeRollingBurstWindow.disposition(
                samples(listOf(2_100L, 2_267L, 2_434L)), 2_500L, 7L
            )
        )
    }

    @Test
    fun `adjacent source samples below the production minimum are discarded`() {
        val tooClose = listOf(
            NativeRollingBurstWindow.Sample(1_000L, 1_000_000_000L, 7L),
            NativeRollingBurstWindow.Sample(1_150L, 1_139_999_999L, 7L),
            NativeRollingBurstWindow.Sample(1_300L, 1_300_000_000L, 7L)
        )
        assertEquals(
            NativeRollingBurstWindow.Disposition.DISCARD,
            NativeRollingBurstWindow.disposition(tooClose, 1_350L, 7L)
        )
    }

    @Test
    fun rejectsDuplicateReorderedOrMixedGenerationSamples() {
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(
            samples(listOf(1_000L, 1_167L, 1_167L)), 1_400L, 7L
        ))
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(
            samples(listOf(1_000L, 1_334L, 1_167L)), 1_400L, 7L
        ))
        val mixed = samples().toMutableList().also {
            it[1] = it[1].copy(generation = 8L)
        }
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(mixed, 1_400L, 7L))
    }
}
