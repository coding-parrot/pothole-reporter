package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeBitmapOwnershipTest {
    private class FakeBitmap(var recycled: Boolean = false)

    private fun recycle(bitmap: FakeBitmap) {
        bitmap.recycled = true
    }

    @Test
    fun noResizeIdentityNeverRecyclesTheBorrowedSource() {
        val source = FakeBitmap()
        val scaled = source // createScaledBitmap identity path

        NativeBitmapOwnership.recycleIfOwned(scaled, source, recycle = ::recycle)

        assertFalse(source.recycled)
    }

    @Test
    fun roadCropSharedWithScaledViewLivesUntilFinalCleanup() {
        val source = FakeBitmap()
        val cropped = FakeBitmap()
        val scaled = cropped // no downscale needed

        NativeBitmapOwnership.recycleIfOwned(cropped, source, scaled, recycle = ::recycle)
        assertFalse(cropped.recycled)

        NativeBitmapOwnership.recycleIfOwned(scaled, source, recycle = ::recycle)
        assertTrue(cropped.recycled)
        assertFalse(source.recycled)
    }

    @Test
    fun ownedIntermediateIsRecycledExactlyOnce() {
        val source = FakeBitmap()
        val scaled = FakeBitmap()
        var calls = 0

        NativeBitmapOwnership.recycleIfOwned(scaled, source) {
            calls++
            recycle(it)
        }

        assertTrue(scaled.recycled)
        assertFalse(source.recycled)
        assertEquals(1, calls)
    }
}
