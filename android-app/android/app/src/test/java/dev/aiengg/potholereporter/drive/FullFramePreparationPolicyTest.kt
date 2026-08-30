package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Test

class FullFramePreparationPolicyTest {
    @Test
    fun framesWithinLimitKeepEverySourceDimension() {
        assertEquals(480 to 720, NativePreparedImageScale.downscaleOnly(480, 720, 1280))
        assertEquals(1280 to 720, NativePreparedImageScale.downscaleOnly(1280, 720, 1280))
        assertEquals(100 to 100, NativePreparedImageScale.downscaleOnly(100, 100, 1280))
        assertEquals(1 to 1, NativePreparedImageScale.downscaleOnly(1, 1, 1280))
    }

    @Test
    fun landscapeFrameIsDownscaledWithoutChangingItsFieldOfView() {
        assertEquals(1280 to 720, NativePreparedImageScale.downscaleOnly(2560, 1440, 1280))
    }

    @Test
    fun portraitFrameIsDownscaledWithoutChangingItsFieldOfView() {
        assertEquals(720 to 1280, NativePreparedImageScale.downscaleOnly(1440, 2560, 1280))
    }

    @Test(expected = IllegalArgumentException::class)
    fun invalidSourceDimensionsFailClosed() {
        NativePreparedImageScale.downscaleOnly(0, 720, 1280)
    }

    @Test(expected = IllegalArgumentException::class)
    fun invalidMaximumDimensionFailsClosed() {
        NativePreparedImageScale.downscaleOnly(480, 720, 0)
    }
}
