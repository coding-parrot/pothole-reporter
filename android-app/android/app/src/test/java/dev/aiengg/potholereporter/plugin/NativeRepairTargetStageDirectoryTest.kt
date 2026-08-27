package dev.aiengg.potholereporter.plugin

import java.io.File
import java.nio.file.Files
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeRepairTargetStageDirectoryTest {
    @Test
    fun destructionCleanupCannotDeleteAFinalizedGeneration() {
        val root = Files.createTempDirectory("repair-stage-commit-race").toFile()
        try {
            val staging = File(root, ".staging-token").apply { mkdirs() }
            File(staging, "target-1.jpg").writeBytes(byteArrayOf(1, 2, 3))
            val generation = File(root, "generation-token")
            val stage = NativeRepairTargetStageDirectory(staging)

            // Destruction can take this immutable snapshot before or after finalizeAs().
            val destructionTarget = stage.destructionCleanupDirectory()
            assertTrue(stage.finalizeAs(generation))
            assertEquals(generation, stage.currentDirectory())

            destructionTarget.deleteRecursively()

            assertFalse(staging.exists())
            assertTrue(generation.isDirectory)
            assertTrue(File(generation, "target-1.jpg").isFile)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun destructionCleanupStillDeletesAbandonedStaging() {
        val root = Files.createTempDirectory("repair-stage-abandoned").toFile()
        try {
            val staging = File(root, ".staging-token").apply { mkdirs() }
            File(staging, "target-1.jpg").writeBytes(byteArrayOf(1))
            val stage = NativeRepairTargetStageDirectory(staging)

            assertEquals(staging, stage.currentDirectory())
            assertTrue(stage.destructionCleanupDirectory().deleteRecursively())
            assertFalse(staging.exists())
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun failedRoomCommitCanRollBackTheFinalizedGeneration() {
        val root = Files.createTempDirectory("repair-stage-rollback").toFile()
        try {
            val staging = File(root, ".staging-token").apply { mkdirs() }
            val generation = File(root, "generation-token")
            val stage = NativeRepairTargetStageDirectory(staging)

            assertTrue(stage.finalizeAs(generation))
            assertEquals(generation, stage.currentDirectory())
            assertTrue(stage.currentDirectory().deleteRecursively())
            assertFalse(generation.exists())
        } finally {
            root.deleteRecursively()
        }
    }
}
