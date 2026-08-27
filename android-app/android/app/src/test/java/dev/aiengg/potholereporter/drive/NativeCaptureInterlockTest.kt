package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeCaptureInterlockTest {
    private val readyLocation = NativeLocationAccess(
        permissionGranted = true,
        servicesEnabled = true,
        providerAvailable = true,
        freshFixAvailable = true
    )

    @Test
    fun everyPrerequisiteFailureBlocksCaptureWithAnExplicitUserActionOrRecovery() {
        data class Case(
            val cameraPermission: Boolean = true,
            val location: NativeLocationAccess = readyLocation,
            val blocker: NativeCaptureBlocker,
            val messageFragment: String
        )

        val cases = listOf(
            Case(
                cameraPermission = false,
                blocker = NativeCaptureBlocker.CAMERA_PERMISSION,
                messageFragment = "re-enable Camera"
            ),
            Case(
                location = readyLocation.copy(permissionGranted = false),
                blocker = NativeCaptureBlocker.LOCATION_PERMISSION,
                messageFragment = "re-enable Location"
            ),
            Case(
                location = readyLocation.copy(servicesEnabled = false),
                blocker = NativeCaptureBlocker.LOCATION_SERVICES,
                messageFragment = "turn Location on"
            ),
            Case(
                location = readyLocation.copy(providerAvailable = false),
                blocker = NativeCaptureBlocker.GPS_UNAVAILABLE,
                messageFragment = "GPS is unavailable"
            ),
            Case(
                location = readyLocation.copy(freshFixAvailable = false),
                blocker = NativeCaptureBlocker.WAITING_FOR_FRESH_FIX,
                messageFragment = "fresh GPS fix"
            )
        )

        cases.forEach { case ->
            val decision = NativeCaptureInterlock.evaluate(
                cameraPermissionGranted = case.cameraPermission,
                cameraReady = true,
                cameraIssue = null,
                location = case.location
            )
            assertEquals(case.blocker, decision.blocker)
            assertFalse(decision.canCapture)
            assertTrue(decision.shouldReleaseCamera)
            assertTrue(decision.message.contains(case.messageFragment))
            assertTrue(decision.message.contains("paused"))
        }
    }

    @Test
    fun cameraPrivacyInterruptionIsVisibleAndKeepsCameraBoundForAutomaticRecovery() {
        val blocked = NativeCaptureInterlock.evaluate(
            cameraPermissionGranted = true,
            cameraReady = true,
            cameraIssue = null,
            location = readyLocation.copy(freshFixAvailable = false),
            cameraAccessBlocked = true
        )

        assertEquals(NativeCaptureBlocker.CAMERA_PRIVACY, blocked.blocker)
        assertFalse(blocked.canCapture)
        assertTrue(blocked.shouldReleaseCamera)
        assertTrue(blocked.message.contains("Camera access"))
        assertTrue(blocked.message.contains("paused"))

        val restored = NativeCaptureInterlock.evaluate(
            cameraPermissionGranted = true,
            cameraReady = true,
            cameraIssue = null,
            location = readyLocation,
            cameraAccessBlocked = false
        )
        assertEquals(NativeCaptureBlocker.NONE, restored.blocker)
        assertTrue(restored.canCapture)
        assertFalse(restored.shouldReleaseCamera)
        assertEquals("Scanning live", restored.message)
    }

    @Test
    fun restorationStaysBlockedUntilGpsIsAvailableFreshAndCameraHasReopened() {
        val sequence = listOf(
            readyLocation.copy(servicesEnabled = false) to NativeCaptureBlocker.LOCATION_SERVICES,
            readyLocation.copy(providerAvailable = false, freshFixAvailable = false) to
                NativeCaptureBlocker.GPS_UNAVAILABLE,
            readyLocation.copy(freshFixAvailable = false) to NativeCaptureBlocker.WAITING_FOR_FRESH_FIX,
            readyLocation to NativeCaptureBlocker.CAMERA_UNAVAILABLE
        )

        sequence.forEachIndexed { index, (location, blocker) ->
            val decision = NativeCaptureInterlock.evaluate(
                cameraPermissionGranted = true,
                cameraReady = index != sequence.lastIndex,
                cameraIssue = null,
                location = location
            )
            assertEquals(blocker, decision.blocker)
            assertFalse(decision.canCapture)
        }

        assertTrue(
            NativeCaptureInterlock.evaluate(true, true, null, readyLocation).canCapture
        )
    }

    @Test
    fun permissionFailuresTakePriorityOverCameraState() {
        val decision = NativeCaptureInterlock.evaluate(
            cameraPermissionGranted = false,
            cameraReady = false,
            cameraIssue = "camera failure",
            location = readyLocation.copy(permissionGranted = false)
        )

        assertEquals(NativeCaptureBlocker.CAMERA_PERMISSION, decision.blocker)
        assertTrue(decision.message.contains("Camera permission"))
    }
}
