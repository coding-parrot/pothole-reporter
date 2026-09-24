package com.gauravsen.potholereporter.drivemode

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DetectionDispatcherPolicyTest {
    @Test
    fun missingPersonalKeyFallsBackToSharedWhileExplicitChoicesRemainStable() {
        assertEquals("shared_server", effectiveVisionProvider(null, ""))
        assertEquals("personal", effectiveVisionProvider(null, "sk-test"))
        assertEquals("shared_server", effectiveVisionProvider("personal", ""))
        assertEquals("personal", effectiveVisionProvider("personal", "sk-test"))
        assertEquals("shared_server", effectiveVisionProvider("shared_server", "sk-test"))
        assertEquals("shared_server", effectiveVisionProvider("shared", "sk-test"))
        assertEquals("shared_server", effectiveVisionProvider("shared_server", ""))
        assertEquals("personal", effectiveVisionProvider("personal_openai", "sk-test"))
        assertEquals("personal", effectiveVisionProvider("own_key", "sk-test"))
        assertEquals("shared_server", effectiveVisionProvider("personal_openai", ""))
    }

    @Test
    fun miniKeepsMinimalReasoningWhileGpt56UsesNone() {
        assertEquals(
            LlmContractGenerated.DEFAULT_REASONING_EFFORT,
            reasoningEffortForModel(LlmContractGenerated.DEFAULT_MODEL),
        )
        val experimentalModel = LlmContractGenerated.ORIGINAL_DETAIL_MODELS.single()
        assertEquals(
            LlmContractGenerated.REASONING_EFFORT_BY_MODEL.getValue(experimentalModel),
            reasoningEffortForModel(experimentalModel),
        )
    }

    @Test
    fun visionLanguageAllowsAllConfiguredLanguagesAndDefaultsUnknownToEnglish() {
        LlmContractGenerated.ALLOWED_LANGUAGES.forEach { language ->
            assertEquals(language, normalizeVisionLanguage(language))
        }
        assertEquals(
            LlmContractGenerated.DEFAULT_LANGUAGE,
            normalizeVisionLanguage(LlmContractGenerated.DEFAULT_LANGUAGE),
        )
        assertEquals(LlmContractGenerated.DEFAULT_LANGUAGE, normalizeVisionLanguage(null))
        assertEquals(
            LlmContractGenerated.DEFAULT_LANGUAGE,
            normalizeVisionLanguage("unsupported"),
        )
    }

    @Test
    fun sharedDetectionForwardsTheSelectedImageDetail() {
        val originalDetail = LlmContractGenerated.ALLOWED_IMAGE_DETAILS
            .single { it != LlmContractGenerated.DEFAULT_IMAGE_DETAIL }
        val detection = sharedVisionRequestConfig(
            language = LlmContractGenerated.ALLOWED_LANGUAGES
                .single { it == "kn" },
            model = LlmContractGenerated.ORIGINAL_DETAIL_MODELS.single(),
            detail = originalDetail,
            promptVersion = LlmContractGenerated.DETECT_PROMPT_VERSION,
        )
        assertEquals(originalDetail, detection["image_detail"])
        assertEquals(LlmContractGenerated.DETECT_PROMPT_VERSION, detection["prompt_version"])
    }

    @Test
    fun sharedDetectionBindsStableObservationAndCoordinates() {
        val fields = sharedVisionObservationFields(
            clientObservationId = "drive:session-7:42",
            lat = 12.9716,
            lng = 77.5946,
        )
        assertEquals("drive:session-7:42", fields["client_observation_id"])
        assertEquals(12.9716, fields["lat"])
        assertEquals(77.5946, fields["lng"])
        assertEquals("drive_live", fields["capture_source"])
        assertEquals("device_gps", fields["location_source"])
        assertEquals(
            mapOf("capture_source" to "drive_live", "location_source" to "device_gps"),
            driveCaptureProvenanceFields(),
        )

        val first = sharedVisionIdempotencyKey("drive:session-7:42")
        assertEquals(first, sharedVisionIdempotencyKey("drive:session-7:42"))
        assertTrue(first.startsWith("vision-"))
        assertNotEquals(first, sharedVisionIdempotencyKey("drive:session-7:43"))

        val receipt = "A1".repeat(32)
        assertEquals(receipt.lowercase(), normalizeDetectionReceipt("  $receipt  "))
        assertEquals(null, normalizeDetectionReceipt("not-a-server-receipt"))
        assertEquals("MG Road, Bengaluru", normalizeCentralAddressHint("  MG Road, Bengaluru "))
        assertEquals(null, normalizeCentralAddressHint("   "))
    }

    @Test
    fun nativePromptsAreBuiltFromTheGeneratedContract() {
        val translatedLanguage = LlmContractGenerated.ALLOWED_LANGUAGES
            .single { it == "kn" }
        val detection = detectionPromptForDrive(translatedLanguage)
        assertTrue(detection.startsWith(LlmContractGenerated.DETECT_PROMPT))
        assertTrue(detection.contains(LlmContractGenerated.DETECT_CAPTURE_DRIVE))
        assertTrue(detection.endsWith(LlmContractGenerated.DETECT_LANGUAGE_KN))
    }

    @Test
    fun nativeBoundaryNormalizesModelAndImageDetailFromGeneratedPolicy() {
        val originalModel = LlmContractGenerated.ORIGINAL_DETAIL_MODELS.single()
        val originalDetail = LlmContractGenerated.ALLOWED_IMAGE_DETAILS
            .single { it != LlmContractGenerated.DEFAULT_IMAGE_DETAIL }
        assertEquals(LlmContractGenerated.DEFAULT_MODEL, normalizeVisionModel("unsupported"))
        assertEquals(
            LlmContractGenerated.DEFAULT_IMAGE_DETAIL,
            normalizeVisionDetail(originalDetail, LlmContractGenerated.DEFAULT_MODEL),
        )
        assertEquals(originalDetail, normalizeVisionDetail(originalDetail, originalModel))
    }

    @Test
    fun binaryDetectionDecisionRejectsContradictoryDamageDetails() {
        assertEquals(
            "accept",
            detectionDecisionFor("acceptable", "damaged", "pothole_cavity", "medium"),
        )
        assertEquals(
            "reject",
            detectionDecisionFor("acceptable", "undamaged", null, null),
        )
        assertEquals(
            "review",
            detectionDecisionFor("rejected", "undamaged", null, null),
        )
        assertEquals(
            "review",
            detectionDecisionFor("acceptable", "damaged", null, null),
        )
        assertEquals(
            "review",
            detectionDecisionFor("acceptable", "damaged", "cat", "medium"),
        )
        assertEquals(
            "review",
            detectionDecisionFor("acceptable", "damaged", "pothole_cavity", "huge"),
        )
        assertEquals(
            "review",
            detectionDecisionFor("acceptable", "undamaged", "surface_breakup", null),
        )
        assertEquals(
            "review",
            detectionDecisionFor("acceptable", "undamaged", null, "small"),
        )
    }
}
