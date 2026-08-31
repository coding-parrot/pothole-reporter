package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertFalse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlinx.coroutines.runBlocking

class NativeDetectionRetryPolicyTest {
    private fun assessment(
        surface: String = "temporary_drivable_surface",
        decision: String = "reject",
        speedBreaker: Boolean = false,
        quality: String = "usable",
        onRoad: Boolean = true,
        temporal: String = "consistent",
        cavity: Boolean = true,
        brokenRim: Boolean = true,
        surfaceLoss: Boolean = true
    ) = AssessmentResult(
        isPothole = decision == "accept",
        looksLikeSpeedBreaker = speedBreaker,
        reportable = decision == "accept",
        assessment = if (decision == "accept") "clear" else "absent",
        imageQuality = quality,
        damageType = if (decision == "accept") "pothole_cavity" else "none",
        surfaceType = surface,
        defectType = if (decision == "accept") "pothole" else "not_pothole",
        measurementProvenance = "not_applicable",
        measurementConfidence = "not_applicable",
        onDrivableSurface = onRoad,
        hasLocalizedCavity = cavity,
        hasUnambiguousLowerInterior = decision == "accept",
        hasBrokenEdgeOrRim = brokenRim,
        hasDepthOrSurfaceLoss = surfaceLoss,
        temporalConsistency = temporal,
        size = if (decision == "accept") "medium" else null,
        description = "test",
        decision = decision
    )

    @Test
    fun onlyEligibleTemporaryResultsEnterTheBoundedVote() {
        val rejected = assessment()
        val accepted = assessment(decision = "accept")
        assertTrue(NativeDetectionRetryPolicy.isVoteEligible(rejected))
        assertTrue(NativeDetectionRetryPolicy.isVoteEligible(accepted))
        assertTrue(NativeDetectionRetryPolicy.shouldRetry(listOf(rejected)))
        assertTrue(NativeDetectionRetryPolicy.shouldRetry(listOf(accepted)))
        assertFalse(NativeDetectionRetryPolicy.shouldRetry(listOf(rejected, rejected)))
        assertFalse(NativeDetectionRetryPolicy.shouldRetry(listOf(accepted, accepted)))
        assertTrue(NativeDetectionRetryPolicy.shouldRetry(listOf(accepted, rejected)))
    }

    @Test
    fun unsafeOrOrdinaryAcceptsNeverRequestConfirmation() {
        assertFalse(NativeDetectionRetryPolicy.isVoteEligible(
            assessment(decision = "accept", speedBreaker = true)))
        assertFalse(NativeDetectionRetryPolicy.isVoteEligible(
            assessment(surface = "bituminous_asphalt", decision = "accept")))
        assertFalse(NativeDetectionRetryPolicy.isVoteEligible(
            assessment(decision = "accept", quality = "unusable")))
        assertFalse(NativeDetectionRetryPolicy.isVoteEligible(
            assessment(decision = "accept", onRoad = false)))
        assertFalse(NativeDetectionRetryPolicy.isVoteEligible(
            assessment(decision = "accept", temporal = "inconsistent")))
    }

    @Test
    fun boundedRunnerRequiresTwoOfAtMostThreeTemporaryAccepts() = runBlocking {
        val accepted = assessment(decision = "accept")
        var calls = 0

        val result = runBoundedDetectionAttempts {
            calls++
            accepted
        }

        assertSame(accepted, result)
        assertEquals(2, calls)

        calls = 0
        val rejected = runBoundedDetectionAttempts {
            calls++
            assessment()
        }
        assertEquals("reject", rejected.decision)
        assertEquals(2, calls)

        val mixed = ArrayDeque(listOf(assessment(), accepted, accepted))
        val recovered = runBoundedDetectionAttempts { mixed.removeFirst() }
        assertEquals("accept", recovered.decision)
        assertTrue(mixed.isEmpty())
    }

    @Test
    fun splitVoteUsesOneTieBreakerAndTransportFailureFailsClosed() = runBlocking {
        val accepted = assessment(decision = "accept")
        val rejected = assessment()
        var calls = 0

        val sequence = ArrayDeque(listOf(accepted, rejected, rejected))
        val contradictory = runBoundedDetectionAttempts {
            calls++
            sequence.removeFirst()
        }
        assertSame(rejected, contradictory)
        assertEquals(3, calls)

        calls = 0
        val failedConfirmation = runBoundedDetectionAttempts {
            calls++
            if (calls == 1) accepted else throw NativeInferenceException("confirmation failure")
        }
        assertEquals("reject", failedConfirmation.decision)
        assertFalse(failedConfirmation.reportable)
        assertEquals(2, calls)
    }

    @Test
    fun inconsistentSurfaceConfirmationFailsClosed() = runBlocking {
        val first = assessment(decision = "accept")
        val inconsistent = assessment(surface = "bituminous_asphalt", decision = "accept")
        val sequence = ArrayDeque(listOf(first, inconsistent))

        val result = runBoundedDetectionAttempts { sequence.removeFirst() }

        assertEquals("reject", result.decision)
        assertFalse(result.reportable)
    }

    @Test(expected = NativeInferenceException::class)
    fun firstTransportFailureStillPropagates() {
        runBlocking {
            runBoundedDetectionAttempts { throw NativeInferenceException("first failure") }
        }
    }
}
