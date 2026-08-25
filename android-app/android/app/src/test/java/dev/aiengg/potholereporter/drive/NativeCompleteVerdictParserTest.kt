package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NativeCompleteVerdictParserTest {
    private val clearAbsence = mapOf<String, Any?>(
        "looks_like_speed_breaker" to false,
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

    private val clearPothole = clearAbsence + mapOf(
        "looks_like_speed_breaker" to false,
        "reportable" to true,
        "assessment" to "clear",
        "damage_type" to "pothole_cavity",
        "on_drivable_surface" to true,
        "has_broken_edge_or_rim" to true,
        "has_depth_or_surface_loss" to true,
        "temporal_consistency" to "consistent",
        "size" to "medium",
        "description" to "A localized cavity with a broken rim."
    )

    @Test
    fun acceptsCompleteClearAbsence() {
        val result = NativeCompleteVerdictParser.fromFields(clearAbsence)
        assertEquals("absent", result?.assessment)
        assertEquals("usable", result?.imageQuality)
        assertEquals("none", result?.damageType)
    }

    @Test
    fun acceptsPotholeOnlyWhenSpeedBreakerVetoIsFalse() {
        val result = NativeCompleteVerdictParser.fromFields(clearPothole)
        assertEquals(false, result?.looksLikeSpeedBreaker)
        assertEquals("accept", result?.decision)
    }

    @Test
    fun speedBreakerHardVetoesContradictoryPotholeFields() {
        val result = NativeCompleteVerdictParser.fromFields(
            clearPothole + ("looks_like_speed_breaker" to true)
        )
        assertEquals(true, result?.looksLikeSpeedBreaker)
        assertEquals("reject", result?.decision)
    }

    @Test
    fun rejectsEmptyAndIncompleteObjects() {
        assertNull(NativeCompleteVerdictParser.parse("{}"))
        assertNull(NativeCompleteVerdictParser.fromFields(emptyMap()))
        assertNull(NativeCompleteVerdictParser.fromFields(clearAbsence - "description"))
        assertNull(NativeCompleteVerdictParser.fromFields(clearPothole - "looks_like_speed_breaker"))
    }

    @Test
    fun rejectsMistypedOrUnknownFields() {
        assertNull(NativeCompleteVerdictParser.fromFields(
            clearAbsence + ("reportable" to "false")
        ))
        assertNull(NativeCompleteVerdictParser.fromFields(
            clearAbsence + ("assessment" to "missing")
        ))
        assertNull(NativeCompleteVerdictParser.fromFields(
            clearPothole + ("looks_like_speed_breaker" to "false")
        ))
    }
}
