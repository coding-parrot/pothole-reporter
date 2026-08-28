package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.assertThrows
import org.junit.Test

class NativeDetectionStreamCompletionPolicyTest {
    private val completeAbsence = NativeCompleteVerdictParser.fromFields(
        mapOf(
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
            "description" to "No pothole detected."
        )
    )!!

    @Test
    fun completeStructuredVerdictCanBeCheckpointed() {
        val result = NativeDetectionStreamCompletionPolicy.requireVerdict(
            completeVerdict = completeAbsence,
            intentionalEarlyReject = null,
            transportCompleted = true
        )

        assertSame(completeAbsence, result)
    }

    @Test
    fun intentionalEarlyRejectSurvivesItsCancellationException() {
        val result = NativeDetectionStreamCompletionPolicy.requireVerdict(
            completeVerdict = null,
            intentionalEarlyReject = completeAbsence,
            transportCompleted = false
        )

        assertSame(completeAbsence, result)
    }

    @Test
    fun missingTerminalEventCannotCheckpointEvenCompleteLookingText() {
        assertThrows(NativeInferenceException::class.java) {
            NativeDetectionStreamCompletionPolicy.requireVerdict(
                completeVerdict = completeAbsence,
                intentionalEarlyReject = null,
                transportCompleted = false
            )
        }
    }

    @Test
    fun malformedOrTruncatedNaturalCompletionCannotCheckpoint() {
        val error = assertThrows(NativeInferenceException::class.java) {
            NativeDetectionStreamCompletionPolicy.requireVerdict(
                completeVerdict = null,
                intentionalEarlyReject = null,
                transportCompleted = true
            )
        }

        assertTrue(error.suspendInference)
    }

    @Test
    fun repairVerdictCannotChangeStatusWithoutTerminalEvent() {
        assertThrows(NativeInferenceException::class.java) {
            NativeStreamCompletionPolicy.requireCompleted(
                transportCompleted = false,
                incompleteMessage = "OpenAI repair-check stream was interrupted"
            )
        }

        // Receiving the terminal marker permits the caller to parse and apply the
        // independently validated repair schema.
        NativeStreamCompletionPolicy.requireCompleted(
            transportCompleted = true,
            incompleteMessage = "OpenAI repair-check stream was interrupted"
        )
    }
}
