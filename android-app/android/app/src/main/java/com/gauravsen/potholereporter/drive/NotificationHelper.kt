package com.gauravsen.potholereporter.drive

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
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

    fun canShowDriveNotification(context: Context): Boolean {
        val runtimeGranted = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        val appEnabled = NotificationManagerCompat.from(context).areNotificationsEnabled()
        val channelEnabled = Build.VERSION.SDK_INT < Build.VERSION_CODES.O ||
            ((context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                .getNotificationChannel(CHANNEL_ID)?.importance
                ?: NotificationManager.IMPORTANCE_LOW) != NotificationManager.IMPORTANCE_NONE
        return runtimeGranted && appEnabled && channelEnabled
    }

    fun buildNotification(
        context: Context,
        isPaused: Boolean,
        checkedCount: Int,
        foundCount: Int,
        alreadyCount: Int,
        statusText: String? = null,
        isStopping: Boolean = false,
        isPausing: Boolean = false,
        recordingEnabled: Boolean = false,
        isRecording: Boolean = false,
        videoSupported: Boolean = true,
        recordingIssue: String? = null,
        cameraActive: Boolean = false
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

        val cameraStarting = !cameraActive && (statusText?.contains("starting", ignoreCase = true) == true ||
            statusText?.contains("waiting", ignoreCase = true) == true)
        val title = when {
            isStopping -> "Drive Mode Stopping Safely"
            isPausing -> "Drive Mode Pausing · Camera Stopping"
            isPaused -> "Drive Mode Paused · Camera Off"
            cameraStarting -> "Drive Mode · Camera Starting"
            !cameraActive -> "Drive Mode · Camera Interrupted"
            isRecording -> "Drive Mode · Recording Video"
            recordingEnabled && !recordingIssue.isNullOrBlank() -> "Drive Mode · Video Not Recording"
            else -> "Drive Mode · Camera Active"
        }
        val captureDisclosure = when {
            isPausing -> "Camera and video are stopping safely"
            isPaused -> "Camera off"
            cameraStarting -> "Camera starting · persistent status remains visible"
            !cameraActive -> "Camera unavailable · detection and video are paused"
            isRecording -> "Camera active · video saved locally"
            recordingEnabled && !recordingIssue.isNullOrBlank() -> "Camera active · video not recording"
            recordingEnabled && !videoSupported -> "Camera active · video unavailable"
            recordingEnabled -> "Camera active · video starting"
            else -> "Camera active · video not saved"
        }
        val subtitle = statusText ?: "$checkedCount frames checked · $foundCount potholes found" +
                (if (alreadyCount > 0) " ($alreadyCount seen again)" else "")

        val builder = NotificationCompat.Builder(context, CHANNEL_ID)
            .setContentTitle(title)
            .setContentText(subtitle)
            .setSubText(captureDisclosure)
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentIntent(openAppPendingIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)

        if (!isStopping && !isPausing) {
            if (isPaused) {
                builder.addAction(android.R.drawable.ic_media_play, "Resume", pauseResumePendingIntent)
            } else {
                builder.addAction(android.R.drawable.ic_media_pause, "Pause", pauseResumePendingIntent)
            }
        }
        builder.addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop", stopPendingIntent)

        return builder.build()
    }
}
