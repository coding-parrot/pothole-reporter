package dev.aiengg.potholereporter.plugin

import dev.aiengg.potholereporter.db.ReportMediaRef
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeReportEvidenceRecoveryPolicyTest {
    @Test
    fun `only exact live source identity can reopen a keyframe`() {
        val valid = report("1700000000000", "live:1700000000000:27")
        assertEquals(
            NativeLiveKeyframeIdentity("1700000000000", 27),
            NativeReportEvidenceRecoveryPolicy.exactLiveKeyframeIdentity(valid)
        )

        listOf(
            report("1700000000000", "live:other:27"),
            report("1700000000000", "live:1700000000000:027"),
            report("1700000000000", "live:1700000000000:27:extra"),
            report("1700000000000", "repair:1700000000000:27"),
            report(null, "live:1700000000000:27")
        ).forEach {
            assertNull(NativeReportEvidenceRecoveryPolicy.exactLiveKeyframeIdentity(it))
        }
    }

    @Test
    fun `complete reference shape requires full evidence and thumbnail`() {
        assertTrue(NativeReportEvidenceRecoveryPolicy.hasCompleteReferenceShape(report(
            "drive", "live:drive:1", fullPath = "/private/full.jpg", thumbnailChars = 80
        )))
        assertFalse(NativeReportEvidenceRecoveryPolicy.hasCompleteReferenceShape(report(
            "drive", "live:drive:1", fullPath = null, thumbnailChars = 80
        )))
        assertFalse(NativeReportEvidenceRecoveryPolicy.hasCompleteReferenceShape(report(
            "drive", "live:drive:1", fullPath = "/private/full.jpg", thumbnailChars = 0
        )))
    }

    private fun report(
        driveId: String?,
        sourceEventKey: String?,
        fullPath: String? = "/private/full.jpg",
        thumbnailChars: Long? = 80
    ) = ReportMediaRef(
        id = 1,
        photoPath = fullPath,
        photoFullPath = fullPath,
        photoDataUrlChars = thumbnailChars,
        driveId = driveId,
        sourceEventKey = sourceEventKey,
        syncedToWeb = false
    )
}
