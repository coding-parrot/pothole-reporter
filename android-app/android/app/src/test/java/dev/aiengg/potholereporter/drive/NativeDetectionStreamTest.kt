package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeDetectionStreamTest {
    @Test
    fun modelNoCanFinishImmediately() {
        val reason = findEarlyRejection("{\"is_pothole\":false")
        val assessment = completeDetectionAssessment(
            text = "",
            streamCompleted = false,
            earlyRejection = reason
        )

        assertEquals(DetectionRejectionReason.MODEL_NO, reason)
        assertEquals("reject", assessment.decision)
        assertFalse(assessment.looksLikeSpeedBreaker)
    }

    @Test
    fun speedBreakerCanFinishImmediately() {
        val reason = findEarlyRejection(
            "{\"is_pothole\":true,\"looks_like_speed_breaker\":true"
        )
        val assessment = completeDetectionAssessment("", false, reason)

        assertEquals(DetectionRejectionReason.SPEED_BREAKER, reason)
        assertTrue(assessment.looksLikeSpeedBreaker)
        assertEquals("reject", assessment.decision)
    }

    @Test
    fun acceptedOutputNeverStopsAtAnyCharacterBoundary() {
        for (end in 1..ACCEPTED_JSON.length) {
            assertNull("must not stop at character $end", findEarlyRejection(
                ACCEPTED_JSON.substring(0, end)
            ))
        }
        assertEquals(
            "accept",
            completeDetectionAssessment(ACCEPTED_JSON, true, null).decision
        )
    }

    @Test
    fun ordinaryGateVetoUsesTheCompleteParser() {
        val offRoad = ACCEPTED_JSON.replace(
            "\"on_drivable_surface\":true",
            "\"on_drivable_surface\":false"
        )

        assertNull(findEarlyRejection(offRoad))
        val verdict = parseDetectionVerdict(offRoad)!!
        assertTrue(DetectionRejectionReason.OFF_DRIVABLE_SURFACE in verdict.rejectionReasons())
        assertEquals("reject", completeDetectionAssessment(offRoad, true, null).decision)
    }

    @Test
    fun interruptedOrMalformedResponsesNeverBecomeDurableVerdicts() {
        assertThrows(NativeInferenceException::class.java) {
            completeDetectionAssessment(ACCEPTED_JSON, false, null)
        }
        val malformed = assertThrows(NativeInferenceException::class.java) {
            completeDetectionAssessment("{}", true, null)
        }
        assertTrue(malformed.suspendInference)
    }

    companion object {
        private const val ACCEPTED_JSON =
            "{\"is_pothole\":true,\"looks_like_speed_breaker\":false," +
                "\"image_quality\":\"usable\",\"surface_type\":\"temporary_drivable_surface\"," +
                "\"on_drivable_surface\":true,\"has_localized_cavity\":true," +
                "\"has_broken_edge_or_rim\":true,\"has_depth_or_surface_loss\":true," +
                "\"temporal_consistency\":\"consistent\",\"size\":\"large\"," +
                "\"description\":\"A cavity.\"}"
    }
}
