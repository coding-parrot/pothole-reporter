package dev.aiengg.potholereporter.drive

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationManagerCompat
import java.util.concurrent.Executors

class NotificationActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        val expectedSession = intent.getStringExtra(NotificationHelper.EXTRA_SESSION_ID)
        val expectedNotificationId = intent.getIntExtra(
            NotificationHelper.EXTRA_NOTIFICATION_ID,
            NotificationHelper.notificationIdForSession(expectedSession.orEmpty())
        )
        val service = DriveForegroundService.activeService
        if (service == null || !service.controlsNotificationSession(expectedSession)) {
            // A stale notification must not instantiate or control a camera service. Binder
            // cancellation is kept off BroadcastReceiver/Main and targets only its generation.
            cancelStaleNotification(context.applicationContext, expectedNotificationId)
            return
        }
        when (action) {
            NotificationHelper.ACTION_PAUSE -> service.pauseDrive()
            NotificationHelper.ACTION_RESUME -> service.resumeDrive()
            NotificationHelper.ACTION_STOP -> service.stopDriveSession()
            NotificationHelper.ACTION_DISMISS -> service.stopDriveSession(
                "Stopped because the Drive Mode notification was dismissed"
            )
        }
    }

    private fun cancelStaleNotification(context: Context, notificationId: Int) {
        val pending = goAsync()
        CLEANUP_EXECUTOR.execute {
            try {
                NotificationManagerCompat.from(context).cancel(notificationId)
            } finally {
                pending.finish()
            }
        }
    }

    companion object {
        private val CLEANUP_EXECUTOR = Executors.newSingleThreadExecutor { runnable ->
            Thread(runnable, "drive-notification-cleanup").apply { isDaemon = true }
        }
    }
}
