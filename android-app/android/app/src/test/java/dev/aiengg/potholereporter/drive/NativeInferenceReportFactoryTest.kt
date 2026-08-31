package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class NativeInferenceReportFactoryTest {
    private val accepted = AssessmentResult(
        isPothole = true,
        looksLikeSpeedBreaker = false,
        reportable = true,
        assessment = "clear",
        imageQuality = "usable",
        damageType = "pothole_cavity",
        surfaceType = "bituminous_asphalt",
        defectType = "pothole",
        measurementProvenance = "visual_estimate_no_scale",
        measurementConfidence = "low",
        onDrivableSurface = true,
        hasLocalizedCavity = true,
        hasUnambiguousLowerInterior = true,
        hasBrokenEdgeOrRim = true,
        hasDepthOrSurfaceLoss = true,
        temporalConsistency = "consistent",
        size = "medium",
        description = "Pothole",
        decision = "accept"
    )

    @Test
    fun `accepted report requires a nonblank thumbnail`() {
        try {
            createInferenceOutcome(input(thumbnail = ""))
            fail("Blank thumbnail must not create a native report")
        } catch (_: IllegalArgumentException) {
            // Expected: the durable keyframe remains pending in the worker.
        }
    }

    @Test
    fun `complete evidence is copied into both native bridge paths`() {
        val outcome = createInferenceOutcome(input(thumbnail = "data:image/jpeg;base64,VALID"))
        val report = requireNotNull(outcome.reportEntity)
        assertTrue(outcome.accepted)
        assertEquals("/private/reports/drive/evidence.jpg", report.photoPath)
        assertEquals(report.photoPath, report.photoFullPath)
        assertEquals("data:image/jpeg;base64,VALID", report.photoDataUrl)
    }

    private fun input(thumbnail: String) = NativeDetectionReportInput(
        assessment = accepted,
        latitude = 12.0,
        longitude = 77.0,
        photoPath = "/private/reports/drive/evidence.jpg",
        thumbnailDataUrl = thumbnail,
        model = "test-model",
        detail = "original",
        evidenceCount = 4,
        driveId = "1700000000000",
        captureSeq = 7,
        capturedAtMs = 1_700_000_007_000,
        sourceOffsetMs = 7_000,
        gpsAccuracy = 4f,
        speedMps = 5f,
        heading = 90f,
        primaryIndex = 1,
        debug = false,
        createdAtSeconds = 1_700_000_007
    )
}
