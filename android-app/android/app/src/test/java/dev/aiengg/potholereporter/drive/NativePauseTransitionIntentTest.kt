package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NativePauseTransitionIntentTest {
    @Test
    fun `latest pause or resume request wins and is consumed once`() {
        val intent = NativePauseTransitionIntent()

        intent.request(paused = false)
        intent.request(paused = true)
        intent.request(paused = false)

        assertEquals(false, intent.take())
        assertNull(intent.take())
    }

    @Test
    fun `clear removes a queued transition`() {
        val intent = NativePauseTransitionIntent()
        intent.request(paused = false)

        intent.clear()

        assertNull(intent.take())
    }
}
