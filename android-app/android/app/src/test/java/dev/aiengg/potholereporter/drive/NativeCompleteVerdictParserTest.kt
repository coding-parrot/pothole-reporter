package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NativeCompleteVerdictParserTest {
    private val clearAbsence = mapOf<String, Any?>(
        "reportable" to false,
        "assessment" to "absent",
        "image_quality" to "usable",
        "damage_type" to "none",
        "on_drivable_surface" to false,
        "has_broken_edge_or_rim" to false,
        "has_depth_or_surface_loss" to false,
        "temporal_consistency" to "not_applicable",
        "size" to null,
        "description" to "No defect visible."
    )

    @Test
    fun acceptsCompleteClearAbsence() {
        val result = NativeCompleteVerdictParser.fromFields(clearAbsence)
        assertEquals("absent", result?.assessment)
        assertEquals("usable", result?.imageQuality)
        assertEquals("none", result?.damageType)
    }

    @Test
    fun rejectsEmptyAndIncompleteObjects() {
        assertNull(NativeCompleteVerdictParser.parse("{}"))
        assertNull(NativeCompleteVerdictParser.fromFields(emptyMap()))
        assertNull(NativeCompleteVerdictParser.fromFields(clearAbsence - "description"))
    }

    @Test
    fun rejectsMistypedOrUnknownFields() {
        assertNull(NativeCompleteVerdictParser.fromFields(
            clearAbsence + ("reportable" to "false")
        ))
        assertNull(NativeCompleteVerdictParser.fromFields(
            clearAbsence + ("assessment" to "missing")
        ))
    }
}
