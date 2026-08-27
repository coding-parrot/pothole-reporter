package dev.aiengg.potholereporter.drive

import java.io.File
import java.nio.file.Files
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeRetryableFileCleanupTest {
    @Test
    fun `failed verified delete advances epoch and remains retryable`() {
        val directory = Files.createTempDirectory("retryable-native-file").toFile()
        try {
            val file = File(directory, "evidence.jpg").apply { writeBytes(ByteArray(9)) }
            val before = NativeMediaReconciliationEpoch.snapshot()

            assertFalse(NativeRetryableFileCleanup.deleteVerified(file) { false })
            assertTrue(file.isFile)
            assertNotEquals(before, NativeMediaReconciliationEpoch.snapshot())

            assertTrue(NativeRetryableFileCleanup.deleteVerified(file))
            assertFalse(file.exists())
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun `keyframe rollback separates committed deletion from surviving temp bytes`() {
        val directory = Files.createTempDirectory("keyframe-failure-quota").toFile()
        try {
            val committed = File(directory, "frame.jpg").apply { writeBytes(ByteArray(11)) }
            val temporary = File(directory, ".frame.tmp").apply { writeBytes(ByteArray(7)) }
            val before = NativeMediaReconciliationEpoch.snapshot()

            val result = NativeKeyframeFailureCleanup.cleanup(
                accountedFiles = listOf(committed),
                unaccountedFiles = listOf(temporary),
                delete = { file -> if (file == temporary) false else file.delete() }
            )

            assertFalse(result.cleanupComplete)
            assertEquals(11L, result.removedAccountedBytes)
            assertEquals(7L, result.remainingUnaccountedBytes)
            assertFalse(committed.exists())
            assertTrue(temporary.isFile)
            assertNotEquals(before, NativeMediaReconciliationEpoch.snapshot())
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun `uncommitted rollback charges every surviving destination and temp file`() {
        val directory = Files.createTempDirectory("keyframe-uncommitted-quota").toFile()
        try {
            val destination = File(directory, "frame.jpg").apply { writeBytes(ByteArray(13)) }
            val temporary = File(directory, ".frame.tmp").apply { writeBytes(ByteArray(5)) }

            val result = NativeKeyframeFailureCleanup.cleanup(
                accountedFiles = emptyList(),
                unaccountedFiles = listOf(destination, temporary),
                delete = { false }
            )

            assertFalse(result.cleanupComplete)
            assertEquals(0L, result.removedAccountedBytes)
            assertEquals(18L, result.remainingUnaccountedBytes)
        } finally {
            directory.deleteRecursively()
        }
    }
}
