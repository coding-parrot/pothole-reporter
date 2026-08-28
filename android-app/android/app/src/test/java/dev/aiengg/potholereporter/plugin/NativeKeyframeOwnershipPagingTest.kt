package dev.aiengg.potholereporter.plugin

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeKeyframeOwnershipPagingTest {
    @Test
    fun companionChecksItsExactPathAndItsDeterministicPrimary() {
        val companion = File("/private/footage/drive/keyframes/frame_000007_p0_c2_context.jpg")

        assertEquals(
            listOf(
                companion.absolutePath,
                File(companion.parentFile, "frame_000007_p0_c2.jpg").absolutePath
            ),
            NativeKeyframeOwnershipPaging.ownerCandidatePaths(companion)
        )
    }

    @Test
    fun ordinaryPrimaryAndLegacyNamesRemainEligibleForExactRoomOwnership() {
        listOf("frame_000007_p0_c2.jpg", "frame_000001.jpg", "legacy-name.jpg").forEach { name ->
            val file = File("/private/footage/drive/keyframes/$name")
            assertEquals(
                listOf(file.absolutePath),
                NativeKeyframeOwnershipPaging.ownerCandidatePaths(file)
            )
        }
        assertTrue(
            NativeKeyframeOwnershipPaging.ownerCandidatePaths(
                File("/private/footage/drive/keyframes/.frame.tmp")
            ).isEmpty()
        )
    }

    @Test
    fun oneFilesystemPageCannotGrowPastTheBoundedOwnerQuery() {
        val files = (0 until NativeKeyframeOwnershipPaging.FILE_PAGE_SIZE).map { index ->
            File("/private/footage/drive/keyframes/frame_${index}_context.jpg")
        }

        val candidates = NativeKeyframeOwnershipPaging.ownerCandidatePaths(files)

        assertEquals(NativeKeyframeOwnershipPaging.OWNER_QUERY_LIMIT, candidates.size)
    }

    @Test(expected = IllegalArgumentException::class)
    fun oversizedFilesystemPageFailsClosed() {
        val files = (0..NativeKeyframeOwnershipPaging.FILE_PAGE_SIZE).map { index ->
            File("/private/footage/drive/keyframes/frame_$index.jpg")
        }
        NativeKeyframeOwnershipPaging.ownerCandidatePaths(files)
    }

    @Test
    fun missingHistoricalSessionIsRecoveredAsInterrupted() {
        val recovered = NativeKeyframeOwnershipPaging.recoveredSessionState(
            sessionId = "1770000000000",
            firstCapturedAtMs = 1770000002400,
            activeSessionId = null,
            nowSeconds = 1770000100
        )

        assertEquals(1770000000L, recovered.startedAtSeconds)
        assertEquals(1770000100L, recovered.endedAtSeconds)
        assertEquals("interrupted", recovered.status)
    }

    @Test
    fun missingCurrentSessionRemainsActiveAndUsesCaptureFallbackForLegacyId() {
        val recovered = NativeKeyframeOwnershipPaging.recoveredSessionState(
            sessionId = "legacy_drive",
            firstCapturedAtMs = 1770000002400,
            activeSessionId = "legacy_drive",
            nowSeconds = 1770000100
        )

        assertEquals(1770000002L, recovered.startedAtSeconds)
        assertNull(recovered.endedAtSeconds)
        assertEquals("active", recovered.status)
    }
}
