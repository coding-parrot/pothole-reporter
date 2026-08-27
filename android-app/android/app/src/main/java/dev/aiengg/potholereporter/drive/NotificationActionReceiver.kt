package dev.aiengg.potholereporter.drive

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationManagerCompat

class NotificationActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        val service = DriveForegroundService.activeService
        if (service == null) {
            // A stale system notification must not instantiate a hidden camera service.
            NotificationManagerCompat.from(context).cancel(NotificationHelper.NOTIFICATION_ID)
            return
        }
        val expectedSession = intent.getStringExtra(NotificationHelper.EXTRA_SESSION_ID)
        if (!service.controlsNotificationSession(expectedSession)) return
        when (action) {
            NotificationHelper.ACTION_PAUSE -> service.pauseDrive()
            NotificationHelper.ACTION_RESUME -> service.resumeDrive()
            NotificationHelper.ACTION_STOP -> service.stopDriveSession()
        }
    }
}
