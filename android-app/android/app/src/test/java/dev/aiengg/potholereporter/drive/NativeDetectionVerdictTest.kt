package dev.aiengg.potholereporter.drive

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeDetectionVerdictTest {
    private val pothole = DetectionModelVerdict(
        isPothole = true,
        looksLikeSpeedBreaker = false,
        imageQuality = "usable",
        surfaceType = "bituminous_asphalt",
        onDrivableSurface = true,
        hasLocalizedCavity = true,
        hasUnambiguousLowerInterior = true,
        hasBrokenEdgeOrRim = true,
        hasDepthOrSurfaceLoss = true,
        temporalConsistency = "consistent",
        size = "medium",
        description = "Localized cavity with a broken rim."
    )

    @Test
    fun everyGateExplainsWhyItRejected() {
        val cases = listOf(
            pothole.copy(isPothole = false) to DetectionRejectionReason.MODEL_NO,
            pothole.copy(looksLikeSpeedBreaker = true) to DetectionRejectionReason.SPEED_BREAKER,
            pothole.copy(surfaceType = "unknown") to DetectionRejectionReason.UNSUPPORTED_SURFACE,
            pothole.copy(imageQuality = "unusable") to DetectionRejectionReason.IMAGE_UNUSABLE,
            pothole.copy(onDrivableSurface = false) to
                DetectionRejectionReason.OFF_DRIVABLE_SURFACE,
            pothole.copy(hasLocalizedCavity = false) to
                DetectionRejectionReason.NO_LOCALIZED_CAVITY,
            pothole.copy(
                surfaceType = "temporary_drivable_surface",
                hasUnambiguousLowerInterior = false
            ) to
                DetectionRejectionReason.NO_UNAMBIGUOUS_LOWER_INTERIOR,
            pothole.copy(hasBrokenEdgeOrRim = false) to
                DetectionRejectionReason.NO_BROKEN_EDGE_OR_RIM,
            pothole.copy(hasDepthOrSurfaceLoss = false) to
                DetectionRejectionReason.NO_DEPTH_OR_SURFACE_LOSS,
            pothole.copy(temporalConsistency = "inconsistent") to
                DetectionRejectionReason.TEMPORAL_NOT_CONSISTENT,
            pothole.copy(size = null) to DetectionRejectionReason.SIZE_MISSING_OR_INVALID
        )

        cases.forEach { (verdict, reason) ->
            assertTrue("$reason must be explicit", reason in verdict.rejectionReasons())
            assertEquals("reject", verdict.toAssessment().decision)
        }
    }

    @Test
    fun supportedSurfacesAndSizesAccept() {
        val surfaces = listOf(
            "bituminous_asphalt", "cement_concrete", "mastic_asphalt", "paver_blocks",
            "temporary_drivable_surface"
        )
        for (surface in surfaces) for (size in listOf("small", "medium", "large")) {
            val verdict = pothole.copy(surfaceType = surface, size = size)
            assertTrue("$surface/$size", verdict.rejectionReasons().isEmpty())
            assertEquals("accept", verdict.toAssessment().decision)
        }
    }

    @Test
    fun appPolicyDoesNotOverwriteTheRawModelAnswer() {
        val raw = pothole.copy(hasBrokenEdgeOrRim = false)
        val assessment = raw.toAssessment()

        assertTrue(raw.isPothole)
        assertFalse(assessment.isPothole)
        assertEquals("reject", assessment.decision)
    }

    @Test
    fun parserReadsACompleteRealJsonResponse() {
        val parsed = parseDetectionVerdict(VALID_POTHOLE_JSON)

        assertEquals(pothole, parsed)
        assertEquals("accept", parsed?.toAssessment()?.decision)
    }

    @Test
    fun completeClearNegativePreservesTheFieldsRequiredForRepairChecking() {
        val assessment = completeDetectionAssessment(
            text = CLEAR_NEGATIVE_JSON,
            streamCompleted = true,
            earlyRejection = null
        )

        assertFalse(assessment.isPothole)
        assertFalse(assessment.looksLikeSpeedBreaker)
        assertFalse(assessment.reportable)
        assertEquals("usable", assessment.imageQuality)
        assertFalse(assessment.hasLocalizedCavity)
        assertFalse(assessment.hasUnambiguousLowerInterior)
        assertFalse(assessment.hasBrokenEdgeOrRim)
        assertFalse(assessment.hasDepthOrSurfaceLoss)
        assertNull(assessment.size)
        assertEquals("reject", assessment.decision)
    }

    @Test
    fun parserRejectsMissingMistypedAndUnknownValues() {
        val missingDescription = JSONObject(VALID_POTHOLE_JSON)
            .apply { remove("description") }
            .toString()
        val invalid = listOf(
            "{}",
            missingDescription,
            changed("is_pothole", "true"),
            changed("image_quality", "degraded"),
            changed("surface_type", "gravel"),
            changed("size", "enormous")
        )

        invalid.forEach { assertNull(it, parseDetectionVerdict(it)) }
    }

    private fun changed(field: String, value: Any): String =
        JSONObject(VALID_POTHOLE_JSON).put(field, value).toString()

    companion object {
        private val VALID_POTHOLE_JSON =
            """
            {
              "is_pothole": true,
              "looks_like_speed_breaker": false,
              "image_quality": "usable",
              "surface_type": "bituminous_asphalt",
              "on_drivable_surface": true,
              "has_localized_cavity": true,
              "has_unambiguous_lower_interior": true,
              "has_broken_edge_or_rim": true,
              "has_depth_or_surface_loss": true,
              "temporal_consistency": "consistent",
              "size": "medium",
              "description": "Localized cavity with a broken rim."
            }
            """.trimIndent()

        private val CLEAR_NEGATIVE_JSON =
            """
            {
              "is_pothole": false,
              "looks_like_speed_breaker": false,
              "image_quality": "usable",
              "surface_type": "bituminous_asphalt",
              "on_drivable_surface": true,
              "has_localized_cavity": false,
              "has_unambiguous_lower_interior": false,
              "has_broken_edge_or_rim": false,
              "has_depth_or_surface_loss": false,
              "temporal_consistency": "consistent",
              "size": null,
              "description": "The road surface is intact at the historical footprint."
            }
            """.trimIndent()
    }
}
