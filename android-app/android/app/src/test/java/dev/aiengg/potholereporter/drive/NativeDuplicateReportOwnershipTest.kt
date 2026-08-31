package dev.aiengg.potholereporter.drive

import dev.aiengg.potholereporter.db.ReportEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class NativeDuplicateReportOwnershipTest {
    @Test
    fun `revisit after bridge acknowledgement adopts fresh evidence and metadata`() {
        val firstCapture = ReportEntity(
            id = 41,
            photoPath = "/private/reports/drive-1/first.jpg",
            photoFullPath = "/private/reports/drive-1/first.jpg",
            photoDataUrl = "data:image/jpeg;base64,FIRST",
            driveId = "drive-1",
            sourceEventKey = "live:drive-1:3",
            capturedAt = 100,
            sourceOffsetS = 3.5,
            gpsAccuracy = 5f,
            speedMps = 7f,
            heading = 90f,
            primaryFrameIndex = 1,
            seenCount = 1,
            lastSeenAt = 100,
            syncedToWeb = false
        )
        // This is the durable row after acknowledgeReports has committed and scrubbed
        // the first native image following a successful WebView bridge.
        val acknowledged = firstCapture.copy(
            photoPath = null,
            photoFullPath = null,
            photoDataUrl = null,
            syncedToWeb = true
        )
        val revisit = ReportEntity(
            photoPath = "/private/reports/drive-2/revisit.jpg",
            photoFullPath = "/private/reports/drive-2/revisit.jpg",
            photoDataUrl = "data:image/jpeg;base64,REVISIT",
            driveId = "drive-2",
            captureSource = "drive_live",
            capturedAt = 200,
            sourceOffsetS = 7.25,
            gpsAccuracy = 8f,
            speedMps = 9f,
            heading = 180f,
            primaryFrameIndex = 2,
            sourceEventKey = "live:drive-2:7"
        )

        val merged = NativeDuplicateReportOwnership.merge(
            prior = acknowledged,
            candidate = revisit,
            sourceEventKeysJson = "[\"live:drive-1:3\",\"live:drive-2:7\"]",
            sightingDriveIdsJson = "[\"drive-1\",\"drive-2\"]",
            exactReplay = false
        )

        assertTrue(merged.candidateEvidenceAdopted)
        assertFalse(merged.priorEvidenceDisplaced)
        assertEquals(revisit.photoPath, merged.report.photoPath)
        assertEquals(revisit.photoFullPath, merged.report.photoFullPath)
        assertEquals(revisit.photoDataUrl, merged.report.photoDataUrl)
        assertEquals(revisit.driveId, merged.report.driveId)
        assertEquals(revisit.captureSource, merged.report.captureSource)
        assertEquals(revisit.sourceEventKey, merged.report.sourceEventKey)
        assertEquals(revisit.capturedAt, merged.report.capturedAt)
        assertEquals(revisit.sourceOffsetS, merged.report.sourceOffsetS)
        assertEquals(revisit.gpsAccuracy, merged.report.gpsAccuracy)
        assertEquals(revisit.speedMps, merged.report.speedMps)
        assertEquals(revisit.heading, merged.report.heading)
        assertEquals(revisit.primaryFrameIndex, merged.report.primaryFrameIndex)
        assertEquals(2, merged.report.seenCount)
        assertEquals(200L, merged.report.lastSeenAt)
        assertEquals("[\"live:drive-1:3\",\"live:drive-2:7\"]", merged.report.sourceEventKeysJson)
        assertEquals("[\"drive-1\",\"drive-2\"]", merged.report.sightingDriveIdsJson)
        assertFalse(merged.report.syncedToWeb)
    }

    @Test
    fun `exact replay preserves canonical evidence and seen count`() {
        val prior = ReportEntity(
            id = 41,
            photoPath = "/private/reports/canonical.jpg",
            photoFullPath = "/private/reports/canonical.jpg",
            photoDataUrl = "data:image/jpeg;base64,CANONICAL",
            seenCount = 4,
            lastSeenAt = 200,
            syncedToWeb = false
        )
        val replay = ReportEntity(
            photoPath = "/private/reports/replay.jpg",
            photoFullPath = "/private/reports/replay.jpg",
            photoDataUrl = "data:image/jpeg;base64,REPLAY",
            capturedAt = 250
        )

        val merged = NativeDuplicateReportOwnership.merge(
            prior = prior,
            candidate = replay,
            sourceEventKeysJson = "[\"same-source\"]",
            sightingDriveIdsJson = "[\"same-drive\"]",
            exactReplay = true
        )

        assertFalse(merged.candidateEvidenceAdopted)
        assertFalse(merged.priorEvidenceDisplaced)
        assertEquals(prior.photoPath, merged.report.photoPath)
        assertEquals(prior.photoFullPath, merged.report.photoFullPath)
        assertEquals(prior.photoDataUrl, merged.report.photoDataUrl)
        assertEquals(prior.seenCount, merged.report.seenCount)
        assertEquals(250L, merged.report.lastSeenAt)
    }

    @Test
    fun `incomplete candidate is rejected before a scrubbed row can be mutated`() {
        val acknowledged = ReportEntity(
            id = 41,
            photoPath = null,
            photoFullPath = null,
            photoDataUrl = null,
            syncedToWeb = true
        )
        val missingThumbnail = ReportEntity(
            photoPath = "/private/reports/revisit.jpg",
            photoFullPath = "/private/reports/revisit.jpg",
            photoDataUrl = null,
            capturedAt = 200
        )

        try {
            NativeDuplicateReportOwnership.merge(
                prior = acknowledged,
                candidate = missingThumbnail,
                sourceEventKeysJson = "[]",
                sightingDriveIdsJson = "[]",
                exactReplay = false
            )
            fail("Incomplete native evidence must be rejected")
        } catch (_: IllegalArgumentException) {
            // The Room engine invokes this same invariant before taking its mutation lock.
        }
        assertEquals(null, acknowledged.photoPath)
        assertEquals(null, acknowledged.photoFullPath)
        assertEquals(null, acknowledged.photoDataUrl)
        assertTrue(acknowledged.syncedToWeb)
    }

    @Test
    fun `legacy thumbnail path cannot replace a managed full evidence reference`() {
        val candidate = ReportEntity(
            photoPath = "/private/reports/legacy-thumbnail.jpg",
            photoFullPath = null,
            photoDataUrl = "data:image/jpeg;base64,THUMBNAIL"
        )
        try {
            requireCompleteNativeReportEvidence(candidate)
            fail("A current native candidate must own an explicit full evidence reference")
        } catch (_: IllegalArgumentException) {
            // Expected.
        }
    }

    @Test
    fun `complete candidate repairs either half of a partial canonical evidence pair`() {
        val candidate = ReportEntity(
            photoPath = "/private/reports/fresh.jpg",
            photoFullPath = "/private/reports/fresh.jpg",
            photoDataUrl = "data:image/jpeg;base64,FRESH",
            capturedAt = 300
        )
        val fullOnly = ReportEntity(
            id = 41,
            photoPath = "/private/reports/orphaned-old.jpg",
            photoFullPath = "/private/reports/orphaned-old.jpg",
            photoDataUrl = null,
            syncedToWeb = true
        )
        val thumbnailOnly = ReportEntity(
            id = 42,
            photoPath = null,
            photoFullPath = null,
            photoDataUrl = "data:image/jpeg;base64,OLD_THUMBNAIL",
            syncedToWeb = true
        )

        val repairedFullOnly = NativeDuplicateReportOwnership.merge(
            prior = fullOnly,
            candidate = candidate,
            sourceEventKeysJson = "[]",
            sightingDriveIdsJson = "[]",
            exactReplay = false
        )
        val repairedThumbnailOnly = NativeDuplicateReportOwnership.merge(
            prior = thumbnailOnly,
            candidate = candidate,
            sourceEventKeysJson = "[]",
            sightingDriveIdsJson = "[]",
            exactReplay = false
        )

        assertTrue(repairedFullOnly.candidateEvidenceAdopted)
        assertTrue(repairedFullOnly.priorEvidenceDisplaced)
        assertEquals(candidate.photoFullPath, repairedFullOnly.report.photoFullPath)
        assertEquals(candidate.photoDataUrl, repairedFullOnly.report.photoDataUrl)

        assertTrue(repairedThumbnailOnly.candidateEvidenceAdopted)
        assertFalse(repairedThumbnailOnly.priorEvidenceDisplaced)
        assertEquals(candidate.photoFullPath, repairedThumbnailOnly.report.photoFullPath)
        assertEquals(candidate.photoDataUrl, repairedThumbnailOnly.report.photoDataUrl)
    }
}
