package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files

class NativeInferenceEvidenceOwnershipTest {
    @Test
    fun successfulHandoffKeepsFileAndPublishesExactPath() {
        val directory = Files.createTempDirectory("inference-evidence-handoff").toFile()
        try {
            val file = directory.resolve("evidence.jpg").apply { writeBytes(byteArrayOf(1, 2, 3)) }
            var receivedPath: String? = null

            publishEvidence(file) { receivedPath = it }

            assertEquals(file.absolutePath, receivedPath)
            assertTrue(file.isFile)
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun failedHandoffDeletesFileBeforeRethrowing() {
        val directory = Files.createTempDirectory("inference-evidence-failed-handoff").toFile()
        try {
            val file = directory.resolve("evidence.jpg").apply { writeBytes(byteArrayOf(1, 2, 3)) }
            val failure = OutOfMemoryError("simulated receiver allocation failure")
            var thrown: Throwable? = null

            try {
                publishEvidence(file) { throw failure }
            } catch (error: Throwable) {
                thrown = error
            }

            assertTrue(thrown === failure)
            assertFalse(file.exists())
        } finally {
            directory.deleteRecursively()
        }
    }
}
