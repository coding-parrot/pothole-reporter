package dev.aiengg.potholereporter.plugin

import dev.aiengg.potholereporter.drive.NativeMediaFilesystemMutation
import java.io.File
import java.nio.file.Files
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.withLock
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeMediaReconciliationProgressTest {
    @Test
    fun `failed verified deletion remains incomplete and a later pass can retry`() {
        val root = Files.createTempDirectory("native-reconcile-retry").toFile()
        try {
            val footage = File(root, "orphan.jpg").apply {
                writeBytes(ByteArray(17) { 7 })
            }
            val failedPass = NativeMediaReconciliationProgress()

            assertFalse(failedPass.deleteFileVerified(footage, footage = true) { false })
            assertFalse(failedPass.cleanupComplete)
            assertEquals(0L, failedPass.deletedFootageBytes)
            assertTrue(footage.isFile)

            val retryPass = NativeMediaReconciliationProgress()
            assertTrue(retryPass.deleteFileVerified(footage, footage = true))
            assertTrue(retryPass.cleanupComplete)
            assertEquals(17L, retryPass.deletedFootageBytes)
            assertFalse(footage.exists())
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun `global media mutex serializes inventory and reconciliation`() = runBlocking {
        val reconciliationEntered = CompletableDeferred<Unit>()
        NativeMediaFilesystemMutation.mutex.withLock {
            val reconciliation = launch(start = CoroutineStart.UNDISPATCHED) {
                NativeMediaFilesystemMutation.mutex.withLock {
                    reconciliationEntered.complete(Unit)
                }
            }
            assertFalse(reconciliationEntered.isCompleted)
            reconciliation.cancelAndJoin()
        }
        assertFalse(reconciliationEntered.isCompleted)
    }
}
