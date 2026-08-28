package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NativeCompleteVerdictParserTest {
    private val clearAbsence = mapOf<String, Any?>(
        "is_pothole" to false,
        "looks_like_speed_breaker" to false,
        "image_quality" to "usable",
        "surface_type" to "bituminous_asphalt",
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
        assertEquals("bituminous_asphalt", result?.surfaceType)
        assertEquals("not_pothole", result?.defectType)
        assertEquals("not_applicable", result?.measurementProvenance)
        assertEquals("not_applicable", result?.measurementConfidence)
        assertEquals("reject", result?.decision)
    }

    @Test
    fun acceptsPotholeOnlyWhenSpeedBreakerVetoIsFalse() {
        val result = NativeCompleteVerdictParser.fromFields(clearPothole)
        assertEquals(true, result?.isPothole)
        assertEquals(false, result?.looksLikeSpeedBreaker)
        assertEquals("accept", result?.decision)
        assertEquals("pothole_cavity", result?.damageType)
        assertEquals("pothole", result?.defectType)
        assertEquals("visual_estimate_no_scale", result?.measurementProvenance)
        assertEquals("low", result?.measurementConfidence)
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
    fun acceptsExplicitlySupportedPavedSurfaceTypes() {
        for (surface in listOf(
            "bituminous_asphalt", "cement_concrete", "mastic_asphalt", "paver_blocks"
        )) {
            val result = NativeCompleteVerdictParser.fromFields(
                clearPothole + ("surface_type" to surface)
            )
            assertEquals(surface, result?.surfaceType)
            assertEquals("accept", result?.decision)
        }

        val unknown = NativeCompleteVerdictParser.fromFields(
            clearPothole + ("surface_type" to "unknown")
        )
        assertEquals("reject", unknown?.decision)
        assertEquals("not_pothole", unknown?.defectType)
        assertEquals("not_applicable", unknown?.measurementProvenance)
        val unpaved = NativeCompleteVerdictParser.fromFields(
            clearPothole + ("surface_type" to "unpaved_or_nonroad")
        )
        assertEquals("reject", unpaved?.decision)
        assertEquals("not_pothole", unpaved?.defectType)
    }

    @Test
    fun acceptsTemporaryDrivableSurfaceOnlyWithCompletePersistentCavityEvidence() {
        val complete = NativeCompleteVerdictParser.fromFields(
            clearPothole + ("surface_type" to "temporary_drivable_surface")
        )
        assertEquals("temporary_drivable_surface", complete?.surfaceType)
        assertEquals(true, complete?.isPothole)
        assertEquals("accept", complete?.decision)

        val missingRequiredEvidence = listOf(
            clearPothole + mapOf(
                "surface_type" to "temporary_drivable_surface",
                "is_pothole" to false
            ),
            clearPothole + mapOf(
                "surface_type" to "temporary_drivable_surface",
                "looks_like_speed_breaker" to true
            ),
            clearPothole + mapOf(
                "surface_type" to "temporary_drivable_surface",
                "image_quality" to "unusable"
            ),
            clearPothole + mapOf(
                "surface_type" to "temporary_drivable_surface",
                "has_localized_cavity" to false
            ),
            clearPothole + mapOf(
                "surface_type" to "temporary_drivable_surface",
                "has_broken_edge_or_rim" to false
            ),
            clearPothole + mapOf(
                "surface_type" to "temporary_drivable_surface",
                "has_depth_or_surface_loss" to false
            ),
            clearPothole + mapOf(
                "surface_type" to "temporary_drivable_surface",
                "temporal_consistency" to "inconsistent"
            ),
            clearPothole + mapOf(
                "surface_type" to "temporary_drivable_surface",
                "on_drivable_surface" to false
            ),
            clearPothole + mapOf(
                "surface_type" to "temporary_drivable_surface",
                "size" to null
            )
        )
        for (fields in missingRequiredEvidence) {
            val result = NativeCompleteVerdictParser.fromFields(fields)
            assertEquals(false, result?.isPothole)
            assertEquals("reject", result?.decision)
        }
    }

    @Test
    fun allAmbiguousOrIncompletePhysicalEvidenceIsNo() {
        val cases = listOf(
            clearPothole + ("is_pothole" to false),
            clearPothole + ("image_quality" to "unusable"),
            clearPothole + ("surface_type" to "unknown"),
            clearPothole + ("surface_type" to "unpaved_or_nonroad"),
            clearPothole + ("on_drivable_surface" to false),
            clearPothole + ("has_localized_cavity" to false),
            clearPothole + ("has_broken_edge_or_rim" to false),
            clearPothole + ("has_depth_or_surface_loss" to false),
            clearPothole + ("temporal_consistency" to "single_view"),
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
        assertNull(NativeCompleteVerdictParser.fromFields(clearPothole - "surface_type"))
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
            clearPothole + ("temporal_consistency" to "probable")
        ))
        assertNull(NativeCompleteVerdictParser.fromFields(
            clearPothole + ("surface_type" to "gravel")
        ))
    }
}
