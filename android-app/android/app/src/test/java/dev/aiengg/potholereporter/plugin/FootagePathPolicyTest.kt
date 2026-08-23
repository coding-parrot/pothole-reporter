package dev.aiengg.potholereporter.plugin

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.nio.file.Files

class FootagePathPolicyTest {
    @get:Rule
    val temporaryFolder = TemporaryFolder()

    @Test
    fun traversalAndNonComponentSessionIdsAreRejected() {
        val filesDir = temporaryFolder.newFolder("files")
        listOf("../reports", "../../", "/tmp/escape", ".", "drive/session", "drive.session")
            .forEach { sessionId ->
                assertThrows(IllegalArgumentException::class.java) {
                    FootagePathPolicy.sessionDirectory(filesDir, sessionId)
                }
            }
    }

    @Test
    fun sessionDirectoryMustRemainAStrictCanonicalChild() {
        val filesDir = temporaryFolder.newFolder("files")
        val footageRoot = File(filesDir, "footage").apply { mkdirs() }.canonicalFile
        val outside = temporaryFolder.newFolder("outside")
        Files.createSymbolicLink(File(footageRoot, "1724321000000").toPath(), outside.toPath())

        assertThrows(IllegalArgumentException::class.java) {
            FootagePathPolicy.sessionDirectory(filesDir, "1724321000000")
        }
    }

    @Test
    fun allStoredSegmentPathsAreValidatedBeforeDeletion() {
        val filesDir = temporaryFolder.newFolder("files")
        val sessionRoot = FootagePathPolicy.sessionDirectory(filesDir, "1724321000000")
            .apply { mkdirs() }
        val validClip = File(sessionRoot, "segment_1.mp4").apply { writeText("video") }
        val outsideFile = File(filesDir, "private.txt").apply { writeText("keep") }

        assertEquals(validClip.canonicalFile, FootagePathPolicy.segmentFile(sessionRoot, validClip.path))
        assertThrows(IllegalArgumentException::class.java) {
            listOf(validClip.path, outsideFile.path).map {
                FootagePathPolicy.segmentFile(sessionRoot, it)
            }
        }
        assertTrue(validClip.exists())
        assertTrue(outsideFile.exists())
    }
}
