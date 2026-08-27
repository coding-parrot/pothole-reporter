package dev.aiengg.potholereporter.drive

import android.app.AppOpsManager
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeCameraAccessPolicyTest {
    @Test
    fun onlyEffectiveDenialsBlockCameraCapture() {
        assertTrue(NativeCameraAccessPolicy.isBlocked(AppOpsManager.MODE_IGNORED))
        assertTrue(NativeCameraAccessPolicy.isBlocked(AppOpsManager.MODE_ERRORED))
        assertFalse(NativeCameraAccessPolicy.isBlocked(AppOpsManager.MODE_ALLOWED))
        assertFalse(NativeCameraAccessPolicy.isBlocked(AppOpsManager.MODE_DEFAULT))
        assertFalse(NativeCameraAccessPolicy.isBlocked(AppOpsManager.MODE_FOREGROUND))
    }
}
