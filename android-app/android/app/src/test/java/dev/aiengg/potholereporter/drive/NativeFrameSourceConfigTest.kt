package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeFrameSourceConfigTest {
    @Test
    fun `missing source retains the existing phone camera default and drops stale URL`() {
        val config = NativeFrameSourceConfig.create(
            kindValue = null,
            rtspValue = "rtsp://stale-user:stale-password@192.168.1.1/live"
        ).getOrThrow()

        assertEquals(NativeFrameSourceKind.PHONE_CAMERA, config.kind)
        assertNull(config.rtspUrl)
        assertTrue(config.kind.requiresCameraPermission)
    }

    @Test
    fun `dashcam needs a trimmed absolute rtsp endpoint`() {
        val config = NativeFrameSourceConfig.create(
            "dashcam",
            "  rtsp://dash-user:dash-password@192.168.1.1:554/live?channel=1  "
        ).getOrThrow()

        assertEquals(NativeFrameSourceKind.DASHCAM, config.kind)
        assertEquals(
            "rtsp://dash-user:dash-password@192.168.1.1:554/live?channel=1",
            config.rtspUrl
        )
        assertFalse(config.kind.requiresCameraPermission)
    }

    @Test
    fun `invalid dashcam endpoints never echo credentials in their public error`() {
        val cases = listOf(
            "https://dash-user:dash-password@192.168.1.1/live",
            "rtsp://dash-user:dash-password@192.168.1.1/live stream",
            "rtsp://dash-user:dash-password@192.168.1.1/live#fragment",
            "rtsp://dash-user:dash-password@192.168.1.1:0/live",
            "rtsp://dash-user:dash-password@192.168.1.1:65536/live",
            "rtsp://dash-user:dash-password@192.168.1.1\\live",
            "not a URL"
        )

        cases.forEach { value ->
            val result = NativeFrameSourceConfig.create("dashcam", value)
            assertTrue(result.isFailure)
            val message = result.exceptionOrNull()?.message.orEmpty()
            assertFalse("credential leaked in: $message", message.contains("dash-password"))
            assertTrue(message.isNotBlank())
        }
    }

    @Test
    fun `dashcam ignores phone camera permission and privacy gates but still requires GPS`() {
        val completeLocation = NativeLocationAccess(true, true, true, true)
        val sourceReady = NativeCaptureInterlock.evaluate(
            cameraPermissionGranted = false,
            cameraReady = true,
            cameraIssue = null,
            location = completeLocation,
            cameraAccessBlocked = true,
            sourceKind = NativeFrameSourceKind.DASHCAM
        )
        assertTrue(sourceReady.canCapture)
        assertEquals(NativeCaptureBlocker.NONE, sourceReady.blocker)

        val gpsDenied = NativeCaptureInterlock.evaluate(
            cameraPermissionGranted = false,
            cameraReady = true,
            cameraIssue = null,
            location = completeLocation.copy(permissionGranted = false),
            cameraAccessBlocked = true,
            sourceKind = NativeFrameSourceKind.DASHCAM
        )
        assertFalse(gpsDenied.canCapture)
        assertEquals(NativeCaptureBlocker.LOCATION_PERMISSION, gpsDenied.blocker)
        assertTrue(gpsDenied.message.contains("dashcam stream"))
        assertFalse(gpsDenied.shouldReleaseCamera)
    }

    @Test
    fun `uncalibrated dashcam location fails closed without changing phone evidence`() {
        assertEquals(
            8f,
            NativeEvidenceLocationPolicy.gpsAccuracyForEvidence(
                NativeFrameSourceKind.PHONE_CAMERA,
                8f
            )
        )
        assertTrue(NativeEvidenceLocationPolicy.canVerifyRepair(NativeFrameSourceKind.PHONE_CAMERA))
        assertNull(
            NativeEvidenceLocationPolicy.gpsAccuracyForEvidence(
                NativeFrameSourceKind.DASHCAM,
                8f
            )
        )
        assertFalse(NativeEvidenceLocationPolicy.canVerifyRepair(NativeFrameSourceKind.DASHCAM))
    }

    @Test
    fun `dashcam dimensions uniformly retain the complete source aspect ratio`() {
        val landscape = NativeDashcamFrameDimensions.fitInside(3840, 2160)
        val portrait = NativeDashcamFrameDimensions.fitInside(1080, 1920)

        assertEquals(1280, landscape.width)
        assertEquals(720, landscape.height)
        assertEquals(404, portrait.width)
        assertEquals(720, portrait.height)

        val preview = NativeDashcamFrameDimensions.fitInside(
            3840,
            2160,
            NativeDashcamFrameDimensions.PREVIEW_MAX_WIDTH,
            NativeDashcamFrameDimensions.PREVIEW_MAX_HEIGHT
        )
        assertEquals(480, preview.width)
        assertEquals(270, preview.height)
    }

    @Test
    fun `valid IPv6 dashcam endpoint may use the highest legal RTSP port`() {
        val config = NativeFrameSourceConfig.create(
            "dashcam",
            "rtsp://[2001:db8::7]:65535/live"
        ).getOrThrow()
        assertEquals(NativeFrameSourceKind.DASHCAM, config.kind)
    }

    @Test
    fun `reconnect delay is bounded`() {
        assertEquals(1_000L, NativeRtspFrameSource.reconnectDelayMs(1))
        assertEquals(2_000L, NativeRtspFrameSource.reconnectDelayMs(2))
        assertEquals(15_000L, NativeRtspFrameSource.reconnectDelayMs(30))
    }

    @Test
    fun `watchdog cannot consume reconnect attempts while player is in backoff`() {
        assertFalse(
            NativeRtspFrameSource.watchdogStalled(
                ready = false,
                playerActive = false,
                connectingStartedElapsedMs = 1_000L,
                lastDecodedFrameElapsedMs = 0L,
                nowElapsedMs = 60_000L
            )
        )
        assertTrue(
            NativeRtspFrameSource.watchdogStalled(
                ready = false,
                playerActive = true,
                connectingStartedElapsedMs = 1_000L,
                lastDecodedFrameElapsedMs = 0L,
                nowElapsedMs = 20_001L
            )
        )
    }

    @Test
    fun `visible dashcam preview continues at a bounded cadence outside evidence sampling`() {
        assertTrue(
            NativeDashcamPreviewSamplingPolicy.shouldDecodeForPreview(
                listenerAttached = true,
                nowElapsedMs = 1_400L,
                lastPreviewElapsedMs = 1_000L,
                intervalMs = 400L
            )
        )
        assertFalse(
            NativeDashcamPreviewSamplingPolicy.shouldDecodeForPreview(
                listenerAttached = true,
                nowElapsedMs = 1_399L,
                lastPreviewElapsedMs = 1_000L,
                intervalMs = 400L
            )
        )
        assertFalse(
            NativeDashcamPreviewSamplingPolicy.shouldDecodeForPreview(
                listenerAttached = false,
                nowElapsedMs = 100_000L,
                lastPreviewElapsedMs = 0L,
                intervalMs = 400L
            )
        )
    }

    @Test
    fun `rtsp buffering is bounded for GPS alignment`() {
        assertTrue(NativeRtspLatencyPolicy.MIN_BUFFER_MS > 0)
        assertTrue(NativeRtspLatencyPolicy.MAX_BUFFER_MS <= 1_500)
        assertTrue(NativeRtspLatencyPolicy.PLAYBACK_BUFFER_MS <= NativeRtspLatencyPolicy.MIN_BUFFER_MS)
        assertTrue(NativeRtspLatencyPolicy.REBUFFER_MS <= NativeRtspLatencyPolicy.MIN_BUFFER_MS)
    }
}
