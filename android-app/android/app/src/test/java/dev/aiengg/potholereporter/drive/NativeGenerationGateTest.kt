package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeGenerationGateTest {
    @Test
    fun `pause resume ABA rejects the old asynchronous callback`() {
        val gate = NativeGenerationGate()
        val firstStart = gate.issue()
        gate.invalidate()
        val resumedStart = gate.issue()

        assertFalse(gate.isCurrent(firstStart))
        assertTrue(gate.isCurrent(resumedStart))
    }

    @Test
    fun `graph replacement makes old analyzer and camera state callbacks inert`() {
        val gate = NativeGenerationGate()
        val oldGraph = gate.issue()
        val newGraph = gate.issue()

        assertFalse(gate.isCurrent(oldGraph))
        assertTrue(gate.isCurrent(newGraph))
    }
}
