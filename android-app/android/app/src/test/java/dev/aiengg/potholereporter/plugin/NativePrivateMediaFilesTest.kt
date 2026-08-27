package dev.aiengg.potholereporter.plugin

import java.io.File
import java.nio.file.Files
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class NativePrivateMediaFilesTest {
    @Test
    fun resolvesOnlyDescendantsOfTheManagedRoot() {
        val parent = Files.createTempDirectory("private-media-path").toFile()
        try {
            val root = File(parent, "reports").apply { mkdirs() }
            val nested = File(root, "drive/evidence.jpg")
            assertEquals(nested.canonicalFile, NativePrivateMediaFiles.descendant(root, nested.path))

            val outside = File(parent, "not-managed.jpg")
            assertThrows(IllegalArgumentException::class.java) {
                NativePrivateMediaFiles.descendant(root, outside.path)
            }
            assertThrows(IllegalArgumentException::class.java) {
                NativePrivateMediaFiles.descendant(root, root.path)
            }
        } finally {
            parent.deleteRecursively()
        }
    }

    @Test
    fun containmentDoesNotConfuseSiblingPathPrefixes() {
        val parent = Files.createTempDirectory("private-media-prefix").toFile()
        try {
            val root = File(parent, "reports").apply { mkdirs() }
            val child = File(root, "session/photo.jpg")
            val sibling = File(parent, "reports-old/photo.jpg")

            assertTrue(NativePrivateMediaFiles.contains(root, child))
            assertTrue(NativePrivateMediaFiles.contains(root, root))
            assertFalse(NativePrivateMediaFiles.contains(root, root, includeRoot = false))
            assertFalse(NativePrivateMediaFiles.contains(root, sibling))
        } finally {
            parent.deleteRecursively()
        }
    }

    @Test
    fun partialDeleteFailureIsReportedAfterEveryPhotoWasTried() {
        val files = listOf(File("one.jpg"), File("blocked.jpg"), File("three.jpg"))
        val attempted = mutableListOf<String>()

        val complete = NativePrivateMediaFiles.deleteAll(files) { file ->
            attempted.add(file.name)
            file.name != "blocked.jpg"
        }

        assertFalse(complete)
        assertEquals(listOf("one.jpg", "blocked.jpg", "three.jpg"), attempted)
        assertTrue(NativePrivateMediaFiles.deleteAll(emptyList()))
    }
}
