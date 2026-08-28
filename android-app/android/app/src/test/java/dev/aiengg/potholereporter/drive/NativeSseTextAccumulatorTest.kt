package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeSseTextAccumulatorTest {
    @Test
    fun `accepts exactly sixty four KiB and rejects overflow atomically`() {
        val accumulator = NativeSseTextAccumulator()
        val exact = "a".repeat(NativeSseTextAccumulator.MAX_UTF8_BYTES)

        assertTrue(accumulator.append(exact))
        assertFalse(accumulator.append("b"))
        assertEquals(exact, accumulator.snapshot())
    }

    @Test
    fun `counts UTF eight bytes instead of UTF sixteen characters`() {
        val accumulator = NativeSseTextAccumulator(maxUtf8Bytes = 6)

        assertTrue(accumulator.append("é")) // two UTF-8 bytes
        assertTrue(accumulator.append("🙂")) // four UTF-8 bytes
        assertFalse(accumulator.append("a"))
        assertEquals("é🙂", accumulator.snapshot())
    }
}
