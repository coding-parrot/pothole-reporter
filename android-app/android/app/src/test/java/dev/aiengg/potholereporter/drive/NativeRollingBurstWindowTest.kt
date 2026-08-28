package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NativeRollingBurstWindowTest {
    private fun samples(
        timesMs: List<Long> = listOf(1_000L, 1_180L, 1_360L, 1_540L, 1_720L),
        generation: Long = 7L
    ) = timesMs.map { time ->
        NativeRollingBurstWindow.Sample(time, time * 1_000_000L, generation)
    }

    @Test
    fun selectsOldestMiddleNewestAcrossFiveSampleBurst() {
        assertEquals(
            listOf(0, 2, 4),
            NativeRollingBurstWindow.selectSourceIndexes(samples(), 1_800L, 7L)
        )
    }

    @Test
    fun rejectsIncompleteStaleOrTooShortWindows() {
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(samples().take(4), 1_800L, 7L))
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(samples(), 3_300L, 7L))
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(
            samples(listOf(1_000L, 1_100L, 1_200L, 1_300L, 1_400L)), 1_450L, 7L
        ))
    }

    @Test
    fun rejectsDuplicateReorderedOrMixedGenerationSamples() {
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(
            samples(listOf(1_000L, 1_180L, 1_180L, 1_540L, 1_720L)), 1_800L, 7L
        ))
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(
            samples(listOf(1_000L, 1_360L, 1_180L, 1_540L, 1_720L)), 1_800L, 7L
        ))
        val mixed = samples().toMutableList().also {
            it[2] = it[2].copy(generation = 8L)
        }
        assertNull(NativeRollingBurstWindow.selectSourceIndexes(mixed, 1_800L, 7L))
    }
}
