package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Test

class NativePreparedImageScaleTest {
    @Test
    fun `never upscales a source smaller than the requested maximum`() {
        assertEquals(
            1280 to 216,
            NativePreparedImageScale.downscaleOnly(1280, 216, 1920)
        )
        assertEquals(
            480 to 187,
            NativePreparedImageScale.downscaleOnly(480, 187, 1280)
        )
    }

    @Test
    fun `downscales larger sources proportionally`() {
        assertEquals(
            1280 to 720,
            NativePreparedImageScale.downscaleOnly(1920, 1080, 1280)
        )
    }
}
