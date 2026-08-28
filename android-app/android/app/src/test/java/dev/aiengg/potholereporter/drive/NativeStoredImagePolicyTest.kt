package dev.aiengg.potholereporter.drive

import dev.aiengg.potholereporter.plugin.NativeBridgeImageBudget
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeStoredImagePolicyTest {
    @Test
    fun storedImageAndNativeBridgeShareOneRawImageCeiling() {
        assertEquals(
            NativeStoredImagePolicy.MAX_BRIDGE_IMAGE_BYTES,
            NativeBridgeImageBudget.MAX_SINGLE_RAW_BYTES
        )
    }

    @Test
    fun threeKeyframesFitBelowTheBridgeBase64Budget() {
        assertTrue(
            NativeStoredImagePolicy.MAX_KEYFRAME_IMAGE_BYTES <
                NativeStoredImagePolicy.MAX_BRIDGE_IMAGE_BYTES
        )
        assertTrue(NativeStoredImagePolicy.MAX_KEYFRAME_PAIR_BYTES <= 1_800L * 1024L)
        val worstCaseBase64Chars =
            ((NativeStoredImagePolicy.MAX_KEYFRAME_BURST_BYTES + 2L) / 3L) * 4L + 69L
        assertTrue(worstCaseBase64Chars < 4L * 1024L * 1024L)
        assertTrue(
            NativeStoredImagePolicy.MAX_ROOM_THUMB_IMAGE_BYTES <
                NativeStoredImagePolicy.MAX_KEYFRAME_IMAGE_BYTES
        )
    }
}
