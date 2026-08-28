package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NativeRollingBurstWindowTest {
    private fun samples(
        timesMs: List<Long> = listOf(1_000L, 1_250L, 1_500L),
        generation: Long = 7L
    ) = timesMs.map { time ->
        NativeRollingBurstWindow.Sample(time, time * 1_000_000L, generation)
    }

    @Test
    fun selectsCompleteChronologicalThreeSampleBurst() {
        assertEquals(
            listOf(0, 1, 2),
            NativeRollingBurstWindow.selectSourceIndexes(samples(), 1_550L, 7L)
        )
    }

    @Test
    fun rejectsIncompleteStaleOrTooShortWindows() {
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(samples().take(2), 1_550L, 7L))
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(samples(), 2_800L, 7L))
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(
            samples(listOf(1_000L, 1_100L, 1_200L)), 1_250L, 7L
        ))
    }

    @Test
    fun rejectsDuplicateReorderedOrMixedGenerationSamples() {
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(
            samples(listOf(1_000L, 1_250L, 1_250L)), 1_550L, 7L
        ))
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(
            samples(listOf(1_000L, 1_500L, 1_250L)), 1_550L, 7L
        ))
        val mixed = samples().toMutableList().also {
            it[1] = it[1].copy(generation = 8L)
        }
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(mixed, 1_550L, 7L))
    }
}
