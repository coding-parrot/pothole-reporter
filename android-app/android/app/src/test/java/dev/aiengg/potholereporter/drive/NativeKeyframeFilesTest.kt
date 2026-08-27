package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class NativeKeyframeFilesTest {
    @get:Rule
    val temporaryFolder = TemporaryFolder()

    @Test
    fun pairedNamesOwnExactlyOneDeterministicCompanion() {
        val directory = temporaryFolder.newFolder("keyframes")
        val plan = NativeKeyframeFiles.writePlan(directory, 7, 2, 0)

        assertEquals("frame_000007_p2_c0.jpg", plan.primaryFile.name)
        assertEquals("frame_000007_p2_c0_context.jpg", plan.companionFile.name)
        assertEquals(plan.files, NativeKeyframeFiles.ownedFiles(plan.primaryFile))
        assertTrue(NativeKeyframeFiles.isRecognizedPrimary(plan.primaryFile))
    }

    @Test
    fun pairedReadSetIsChronologicalAndPreservesPrimaryIndex() {
        val directory = temporaryFolder.newFolder("ordered")
        val plan = NativeKeyframeFiles.writePlan(directory, 8, 2, 0)
        plan.primaryFile.writeBytes(byteArrayOf(1))
        plan.companionFile.writeBytes(byteArrayOf(2))

        val readSet = NativeKeyframeFiles.readSet(plan.primaryFile)

        assertEquals(listOf(plan.companionFile, plan.primaryFile), readSet.files)
        assertEquals(1, readSet.primaryIndex)
        assertTrue(readSet.hasTemporalContext)
        assertTrue(readSet.isComplete)
    }

    @Test
    fun missingCompanionIsNotACompleteTemporalSet() {
        val directory = temporaryFolder.newFolder("incomplete")
        val plan = NativeKeyframeFiles.writePlan(directory, 9, 0, 2)
        plan.primaryFile.writeBytes(byteArrayOf(1))

        val readSet = NativeKeyframeFiles.readSet(plan.primaryFile)

        assertEquals(0, readSet.primaryIndex)
        assertTrue(readSet.hasTemporalContext)
        assertFalse(readSet.isComplete)
    }

    @Test
    fun legacyAndMalformedRowsRemainSingleView() {
        val directory = temporaryFolder.newFolder("legacy")
        val legacy = File(directory, "frame_000010.jpg").apply { writeBytes(byteArrayOf(1)) }
        val malformed = File(directory, "anything.jpg").apply { writeBytes(byteArrayOf(1)) }

        listOf(legacy, malformed).forEach { file ->
            val readSet = NativeKeyframeFiles.readSet(file)
            assertEquals(listOf(file), readSet.files)
            assertEquals(0, readSet.primaryIndex)
            assertFalse(readSet.hasTemporalContext)
        }
        assertTrue(NativeKeyframeFiles.isRecognizedPrimary(legacy))
        assertFalse(NativeKeyframeFiles.isRecognizedPrimary(malformed))
    }

    @Test
    fun companionSelectionRequiresARealTimestampDifferenceAndUsesFarthestFrame() {
        assertEquals(2, NativeKeyframeFiles.selectTemporalCompanionIndex(listOf(100L, 200L, 500L), 1))
        assertEquals(0, NativeKeyframeFiles.selectTemporalCompanionIndex(listOf(100L, 200L, 300L), 1))
        assertNull(NativeKeyframeFiles.selectTemporalCompanionIndex(listOf(100L, 100L), 0))
        assertNull(NativeKeyframeFiles.selectTemporalCompanionIndex(listOf(100L), 3))
    }
}
