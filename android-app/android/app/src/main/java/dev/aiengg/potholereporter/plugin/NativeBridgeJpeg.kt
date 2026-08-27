package dev.aiengg.potholereporter.plugin

import java.io.File
import java.io.FileInputStream

/** Bounded, allocation-predictable JPEG validation used before evidence crosses the bridge. */
internal object NativeBridgeJpeg {
    fun readExact(file: File, expectedBytes: Long, maxBytes: Long): ByteArray? {
        if (expectedBytes <= 0L || expectedBytes > maxBytes || expectedBytes > Int.MAX_VALUE) {
            return null
        }
        if (!file.isFile || file.length() != expectedBytes) return null
        val bytes = ByteArray(expectedBytes.toInt())
        return try {
            FileInputStream(file).use { input ->
                var offset = 0
                while (offset < bytes.size) {
                    val read = input.read(bytes, offset, bytes.size - offset)
                    if (read <= 0) return null
                    offset += read
                }
                if (input.read() != -1) return null
            }
            bytes.takeIf(::isPlausibleJpeg)
        } catch (_: Exception) {
            null
        }
    }

    /**
     * Rejects arbitrary/truncated bytes without decoding pixels. This validates the JPEG
     * envelope, a real start-of-frame with non-zero dimensions, a scan, and a terminal EOI.
     */
    fun isPlausibleJpeg(bytes: ByteArray): Boolean {
        if (bytes.size < 12 || u(bytes[0]) != 0xff || u(bytes[1]) != 0xd8 ||
            u(bytes[bytes.lastIndex - 1]) != 0xff || u(bytes[bytes.lastIndex]) != 0xd9
        ) return false
        var offset = 2
        var sawFrame = false
        var sawScan = false
        while (offset < bytes.size) {
            if (u(bytes[offset]) != 0xff) {
                if (!sawScan) return false
                offset++
                continue
            }
            while (offset < bytes.size && u(bytes[offset]) == 0xff) offset++
            if (offset >= bytes.size) return false
            val marker = u(bytes[offset++])
            if (marker == 0x00) {
                if (!sawScan) return false
                continue
            }
            if (marker == 0xd9) return sawFrame && sawScan && offset == bytes.size
            if (marker == 0xd8 || marker == 0x01 || marker in 0xd0..0xd7) continue
            if (offset + 1 >= bytes.size) return false
            val length = (u(bytes[offset]) shl 8) or u(bytes[offset + 1])
            if (length < 2 || offset + length > bytes.size) return false
            if (marker in FRAME_MARKERS) {
                if (length < 8) return false
                val height = (u(bytes[offset + 3]) shl 8) or u(bytes[offset + 4])
                val width = (u(bytes[offset + 5]) shl 8) or u(bytes[offset + 6])
                if (width <= 0 || height <= 0) return false
                sawFrame = true
            }
            if (marker == 0xda) sawScan = true
            offset += length
        }
        return false
    }

    private fun u(value: Byte): Int = value.toInt() and 0xff

    private val FRAME_MARKERS = setOf(
        0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
        0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf
    )
}
