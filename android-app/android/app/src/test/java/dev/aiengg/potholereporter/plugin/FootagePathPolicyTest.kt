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

    @Test
    fun savedFramesMustStayInsideTheSessionKeyframeDirectory() {
        val filesDir = temporaryFolder.newFolder("files-keyframes")
        val sessionRoot = FootagePathPolicy.sessionDirectory(filesDir, "1724321000001")
            .apply { mkdirs() }
        val keyframeRoot = File(sessionRoot, "keyframes").apply { mkdirs() }
        val valid = File(keyframeRoot, "frame_000001.jpg").apply { writeText("jpeg") }
        val segment = File(sessionRoot, "segment_0001.mp4").apply { writeText("video") }

        assertEquals(valid.canonicalFile, FootagePathPolicy.keyframeFile(sessionRoot, valid.path))
        assertThrows(IllegalArgumentException::class.java) {
            FootagePathPolicy.keyframeFile(sessionRoot, segment.path)
        }
        assertThrows(IllegalArgumentException::class.java) {
            FootagePathPolicy.keyframeFile(sessionRoot, File(filesDir, "outside.jpg").path)
        }
    }
}
