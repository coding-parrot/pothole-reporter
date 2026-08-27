package dev.aiengg.potholereporter.drive

import android.app.AppOpsManager

/** Interprets the effective Camera AppOp without conflating foreground/default with denial. */
internal object NativeCameraAccessPolicy {
    fun isBlocked(mode: Int): Boolean =
        mode == AppOpsManager.MODE_IGNORED || mode == AppOpsManager.MODE_ERRORED
}
