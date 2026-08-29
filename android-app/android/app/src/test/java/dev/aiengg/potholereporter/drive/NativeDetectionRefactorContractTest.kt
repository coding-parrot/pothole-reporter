package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeDetectionRefactorContractTest {
    private val acceptedFields = mapOf<String, Any?>(
        "is_pothole" to true,
        "looks_like_speed_breaker" to false,
        "image_quality" to "usable",
        "surface_type" to "bituminous_asphalt",
        "on_drivable_surface" to true,
        "has_localized_cavity" to true,
        "has_broken_edge_or_rim" to true,
        "has_depth_or_surface_loss" to true,
        "temporal_consistency" to "consistent",
        "size" to "medium",
        "description" to "Localized cavity with a broken rim."
    )

    @Test
    fun rawModelAnswerSurvivesASeparatePolicyVeto() {
        val raw = NativeDetectionVerdictJsonParser.fromFields(
            acceptedFields + ("has_broken_edge_or_rim" to false)
        )!!
        val policy = NativePotholeDecisionPolicy.evaluate(raw)
        val legacy = NativeDetectionAssessmentMapper.toAssessment(raw, policy)

        assertTrue(raw.isPothole)
        assertFalse(policy.accepted)
        assertEquals(listOf(DetectionRejectionReason.NO_BROKEN_EDGE_OR_RIM), policy.rejectionReasons)
        // Existing persistence semantics remain unchanged during the refactor.
        assertFalse(legacy.isPothole)
        assertEquals("reject", legacy.decision)
    }

    @Test
    fun everyDecisionGateHasAnExplicitReason() {
        val cases = listOf(
            ("is_pothole" to false) to DetectionRejectionReason.MODEL_NO,
            ("looks_like_speed_breaker" to true) to DetectionRejectionReason.SPEED_BREAKER,
            ("surface_type" to "unknown") to DetectionRejectionReason.UNSUPPORTED_SURFACE,
            ("image_quality" to "unusable") to DetectionRejectionReason.IMAGE_UNUSABLE,
            ("on_drivable_surface" to false) to DetectionRejectionReason.OFF_DRIVABLE_SURFACE,
            ("has_localized_cavity" to false) to DetectionRejectionReason.NO_LOCALIZED_CAVITY,
            ("has_broken_edge_or_rim" to false) to DetectionRejectionReason.NO_BROKEN_EDGE_OR_RIM,
            ("has_depth_or_surface_loss" to false) to DetectionRejectionReason.NO_DEPTH_OR_SURFACE_LOSS,
            ("temporal_consistency" to "inconsistent") to
                DetectionRejectionReason.TEMPORAL_NOT_CONSISTENT,
            ("size" to null) to DetectionRejectionReason.SIZE_MISSING_OR_INVALID
        )
        cases.forEach { (change, expectedReason) ->
            val raw = NativeDetectionVerdictJsonParser.fromFields(acceptedFields + change)!!
            val result = NativePotholeDecisionPolicy.evaluate(raw)
            assertFalse("$change must reject", result.accepted)
            assertTrue("$change must explain itself", expectedReason in result.rejectionReasons)
        }
    }

    @Test
    fun allCurrentAcceptedSurfacesAndSizesStillAccept() {
        val surfaces = listOf(
            "bituminous_asphalt", "cement_concrete", "mastic_asphalt", "paver_blocks",
            "temporary_drivable_surface"
        )
        for (surface in surfaces) for (size in listOf("small", "medium", "large")) {
            val raw = NativeDetectionVerdictJsonParser.fromFields(
                acceptedFields + mapOf("surface_type" to surface, "size" to size)
            )!!
            assertTrue("$surface/$size", NativePotholeDecisionPolicy.evaluate(raw).accepted)
        }
    }

    @Test
    fun earlyModelNoRecordsOnlyFieldsTheModelActuallyEmitted() {
        val early = NativePartialVerdictScanner.earlyReject("{\"is_pothole\":false")!!

        assertEquals(NativeDetectionCompletionMode.EARLY_MODEL_NO, early.completionMode)
        assertEquals(setOf("is_pothole"), early.observedFields)
        assertEquals(listOf(DetectionRejectionReason.MODEL_NO), early.rejectionReasons)
        // Compatibility defaults are kept out of observedFields and cannot masquerade as output.
        assertEquals("unusable", early.assessment.imageQuality)
        assertTrue(early.assessment.looksLikeSpeedBreaker)
    }

    @Test
    fun acceptedOutputNeverTriggersEarlyCancellationAtAnyCharacterBoundary() {
        val accepted = """
            {"is_pothole":true,"looks_like_speed_breaker":false,"image_quality":"usable","surface_type":"temporary_drivable_surface","on_drivable_surface":true,"has_localized_cavity":true,"has_broken_edge_or_rim":true,"has_depth_or_surface_loss":true,"temporal_consistency":"consistent","size":"large","description":"A cavity."}
        """.trimIndent()

        for (end in 1..accepted.length) {
            assertNull("must not abort at character $end", NativePartialVerdictScanner.earlyReject(
                accepted.substring(0, end)
            ))
        }
    }

    @Test
    fun speedBreakerAndCompleteGateVetoHaveDifferentCompletionModes() {
        val breaker = NativePartialVerdictScanner.earlyReject(
            "{\"is_pothole\":true,\"looks_like_speed_breaker\":true"
        )!!
        assertEquals(NativeDetectionCompletionMode.EARLY_SPEED_BREAKER, breaker.completionMode)
        assertEquals(listOf(DetectionRejectionReason.SPEED_BREAKER), breaker.rejectionReasons)

        val offRoad = """
            {"is_pothole":true,"looks_like_speed_breaker":false,"image_quality":"usable","surface_type":"bituminous_asphalt","on_drivable_surface":false,"has_localized_cavity":true,"has_broken_edge_or_rim":true,"has_depth_or_surface_loss":true,"temporal_consistency":"consistent","size":"medium"
        """.trimIndent()
        val gate = NativePartialVerdictScanner.earlyReject(offRoad)!!
        assertEquals(NativeDetectionCompletionMode.EARLY_GATE_VETO, gate.completionMode)
        assertTrue(DetectionRejectionReason.OFF_DRIVABLE_SURFACE in gate.rejectionReasons)
    }

    @Test
    fun detectionPromptAndRequestSpecPreserveLayoutAndTransportContract() {
        val prompt = NativeDetectionPromptFactory.build("mr", imageCount = 4, primaryIndex = 1)
        assertTrue(prompt.startsWith(NativeDetectionContract.DETECT_PROMPT))
        assertTrue(prompt.contains("Images 2-4 are orientation-aware road-region crops"))
        assertTrue(prompt.contains("sharpest crop is chronological frame 2"))
        assertTrue(prompt.contains("formal Marathi"))

        val urls = mutableListOf("context", "early", "primary", "late")
        val spec = NativeDetectionRequestFactory("gpt-5.6", "high").spec(urls, prompt)
        urls.reverse()
        assertEquals(listOf("context", "early", "primary", "late"), spec.imageUrls)
        assertEquals("pothole_binary_assessment", spec.schemaName)
        assertEquals(1_536, spec.maxOutputTokens)
        assertTrue(spec.stream)
        assertFalse(spec.store)
        assertEquals("low", spec.reasoningEffort)
        assertEquals(
            "minimal",
            NativeDetectionRequestFactory("another-model", "low")
                .spec(listOf("one"), "prompt").reasoningEffort
        )
    }

    @Test
    fun repairRequestRemainsASeparateStrictContract() {
        val prompt = NativeRepairPromptFactory.build("en", imageCount = 5, primaryIndex = 1)
        val spec = NativeRepairRequestFactory("gpt-5.6", "high")
            .spec(listOf("historical", "context", "one", "two", "three"), prompt)

        assertEquals("road_repair_assessment", spec.schemaName)
        assertEquals(768, spec.maxOutputTokens)
        assertEquals("none", spec.reasoningEffort)
        assertTrue(prompt.contains("image 1 is historical evidence"))
        assertTrue(prompt.contains("sharpest crop is image 4"))
    }
}
