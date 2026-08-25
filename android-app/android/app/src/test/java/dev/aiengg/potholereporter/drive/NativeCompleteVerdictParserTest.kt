package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NativeCompleteVerdictParserTest {
    private val clearAbsence = mapOf<String, Any?>(
        "is_pothole" to false,
        "looks_like_speed_breaker" to false,
        "image_quality" to "usable",
        "on_drivable_surface" to true,
        "has_localized_cavity" to false,
        "has_broken_edge_or_rim" to false,
        "has_depth_or_surface_loss" to false,
        "temporal_consistency" to "not_applicable",
        "size" to null,
        "description" to "No defect visible."
    )

    private val clearPothole = clearAbsence + mapOf(
        "is_pothole" to true,
        "looks_like_speed_breaker" to false,
        "on_drivable_surface" to true,
        "has_localized_cavity" to true,
        "has_broken_edge_or_rim" to true,
        "has_depth_or_surface_loss" to true,
        "temporal_consistency" to "consistent",
        "size" to "medium",
        "description" to "A localized cavity with a broken rim."
    )

    @Test
    fun acceptsCompleteClearAbsence() {
        val result = NativeCompleteVerdictParser.fromFields(clearAbsence)
        assertEquals(false, result?.isPothole)
        assertEquals("absent", result?.assessment)
        assertEquals("usable", result?.imageQuality)
        assertEquals("none", result?.damageType)
        assertEquals("reject", result?.decision)
    }

    @Test
    fun acceptsPotholeOnlyWhenSpeedBreakerVetoIsFalse() {
        val result = NativeCompleteVerdictParser.fromFields(clearPothole)
        assertEquals(true, result?.isPothole)
        assertEquals(false, result?.looksLikeSpeedBreaker)
        assertEquals("accept", result?.decision)
        assertEquals("pothole_cavity", result?.damageType)
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
    fun everyFallbackSizeCanBeACompleteYes() {
        for (size in listOf("small", "medium", "large")) {
            val result = NativeCompleteVerdictParser.fromFields(clearPothole + ("size" to size))
            assertEquals("accept", result?.decision)
            assertEquals(size, result?.size)
        }
    }

    @Test
    fun allAmbiguousOrIncompletePhysicalEvidenceIsNo() {
        val cases = listOf(
            clearPothole + ("is_pothole" to false),
            clearPothole + ("image_quality" to "unusable"),
            clearPothole + ("on_drivable_surface" to false),
            clearPothole + ("has_localized_cavity" to false),
            clearPothole + ("has_broken_edge_or_rim" to false),
            clearPothole + ("has_depth_or_surface_loss" to false),
            clearPothole + ("temporal_consistency" to "inconsistent"),
            clearPothole + ("size" to null)
        )
        for (fields in cases) {
            val result = NativeCompleteVerdictParser.fromFields(fields)
            assertEquals("reject", result?.decision)
            assertEquals(false, result?.isPothole)
            assertEquals("none", result?.damageType)
            assertEquals(null, result?.size)
        }
    }

    @Test
    fun rejectsEmptyAndIncompleteObjects() {
        assertNull(NativeCompleteVerdictParser.parse("{}"))
        assertNull(NativeCompleteVerdictParser.fromFields(emptyMap()))
        assertNull(NativeCompleteVerdictParser.fromFields(clearAbsence - "description"))
        assertNull(NativeCompleteVerdictParser.fromFields(clearPothole - "is_pothole"))
        assertNull(NativeCompleteVerdictParser.fromFields(clearPothole - "has_localized_cavity"))
    }

    @Test
    fun rejectsMistypedOrUnknownFields() {
        assertNull(NativeCompleteVerdictParser.fromFields(
            clearAbsence + ("is_pothole" to "false")
        ))
        assertNull(NativeCompleteVerdictParser.fromFields(
            clearAbsence + ("image_quality" to "degraded")
        ))
        assertNull(NativeCompleteVerdictParser.fromFields(
            clearPothole + ("looks_like_speed_breaker" to "false")
        ))
        assertNull(NativeCompleteVerdictParser.fromFields(
            clearPothole + ("temporal_consistency" to "single_view")
        ))
    }
}
