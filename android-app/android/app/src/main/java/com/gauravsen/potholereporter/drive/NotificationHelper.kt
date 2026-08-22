package com.gauravsen.potholereporter.drive

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import com.gauravsen.potholereporter.MainActivity

object NotificationHelper {
    const val CHANNEL_ID = "pothole_drive_channel"
    const val NOTIFICATION_ID = 4091

    const val ACTION_PAUSE = "com.gauravsen.potholereporter.ACTION_PAUSE"
    const val ACTION_RESUME = "com.gauravsen.potholereporter.ACTION_RESUME"
    const val ACTION_STOP = "com.gauravsen.potholereporter.ACTION_STOP"

    fun createNotificationChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val name = "Drive Mode Active"
            val descriptionText = "Continuous road damage scanning in Drive Mode"
            val importance = NotificationManager.IMPORTANCE_LOW
            val channel = NotificationChannel(CHANNEL_ID, name, importance).apply {
                description = descriptionText
                setShowBadge(false)
            }
            val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            notificationManager.createNotificationChannel(channel)
        }
    }

    fun buildNotification(
        context: Context,
        isPaused: Boolean,
        checkedCount: Int,
        foundCount: Int,
        alreadyCount: Int,
        statusText: String? = null
    ): Notification {
        val openAppIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val openAppPendingIntent = PendingIntent.getActivity(
            context, 0, openAppIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val pauseResumeIntent = Intent(context, NotificationActionReceiver::class.java).apply {
            action = if (isPaused) ACTION_RESUME else ACTION_PAUSE
        }
        val pauseResumePendingIntent = PendingIntent.getBroadcast(
            context, 1, pauseResumeIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val stopIntent = Intent(context, NotificationActionReceiver::class.java).apply {
            action = ACTION_STOP
        }
        val stopPendingIntent = PendingIntent.getBroadcast(
            context, 2, stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val title = if (isPaused) "Drive Mode Paused" else "Drive Mode Scanning"
        val subtitle = statusText ?: "$checkedCount frames checked · $foundCount potholes found" +
                (if (alreadyCount > 0) " ($alreadyCount seen again)" else "")

        val builder = NotificationCompat.Builder(context, CHANNEL_ID)
            .setContentTitle(title)
            .setContentText(subtitle)
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentIntent(openAppPendingIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)

        if (isPaused) {
            builder.addAction(android.R.drawable.ic_media_play, "Resume", pauseResumePendingIntent)
        } else {
            builder.addAction(android.R.drawable.ic_media_pause, "Pause", pauseResumePendingIntent)
        }
        builder.addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop", stopPendingIntent)

        return builder.build()
    }
}
