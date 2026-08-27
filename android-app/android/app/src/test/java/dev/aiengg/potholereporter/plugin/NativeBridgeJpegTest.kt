package dev.aiengg.potholereporter.plugin

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.File
import java.nio.file.Files

class NativeBridgeJpegTest {
    @Test
    fun exactBoundedReaderRejectsMissingOversizeTruncatedAndNonJpegEvidence() {
        val root = Files.createTempDirectory("bridge-jpeg-test").toFile()
        try {
            val valid = File(root, "valid.jpg").apply { writeBytes(minimalJpeg()) }
            val truncated = File(root, "truncated.jpg").apply {
                writeBytes(minimalJpeg().copyOf(minimalJpeg().size - 2))
            }
            val corrupt = File(root, "corrupt.jpg").apply { writeBytes(ByteArray(32) { 7 }) }

            assertArrayEquals(
                minimalJpeg(),
                NativeBridgeJpeg.readExact(valid, valid.length(), 64L)
            )
            assertNull(NativeBridgeJpeg.readExact(File(root, "missing.jpg"), 28L, 64L))
            assertNull(NativeBridgeJpeg.readExact(valid, valid.length(), valid.length() - 1L))
            assertNull(NativeBridgeJpeg.readExact(truncated, truncated.length(), 64L))
            assertNull(NativeBridgeJpeg.readExact(corrupt, corrupt.length(), 64L))
            assertFalse(NativeBridgeJpeg.isPlausibleJpeg(byteArrayOf(0xff.toByte(), 0xd8.toByte(), 0xff.toByte(), 0xd9.toByte())))
        } finally {
            root.deleteRecursively()
        }
    }

    companion object {
        internal fun minimalJpeg(): ByteArray = byteArrayOf(
            0xff.toByte(), 0xd8.toByte(),
            0xff.toByte(), 0xc0.toByte(), 0x00, 0x0b, 0x08,
            0x00, 0x01, 0x00, 0x01, 0x01, 0x01, 0x11, 0x00,
            0xff.toByte(), 0xda.toByte(), 0x00, 0x08, 0x01, 0x01, 0x00, 0x00, 0x3f, 0x00,
            0x01,
            0xff.toByte(), 0xd9.toByte()
        )
    }
}
