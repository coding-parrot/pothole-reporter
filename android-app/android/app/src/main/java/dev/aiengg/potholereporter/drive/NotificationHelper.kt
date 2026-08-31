package dev.aiengg.potholereporter.drive

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import dev.aiengg.potholereporter.MainActivity

object NotificationHelper {
    const val CHANNEL_ID = "pothole_drive_channel"
    const val NOTIFICATION_ID = 4091

    const val ACTION_PAUSE = "dev.aiengg.potholereporter.ACTION_PAUSE"
    const val ACTION_RESUME = "dev.aiengg.potholereporter.ACTION_RESUME"
    const val ACTION_STOP = "dev.aiengg.potholereporter.ACTION_STOP"
    const val ACTION_DISMISS = "dev.aiengg.potholereporter.ACTION_DISMISS"
    const val EXTRA_SESSION_ID = "dev.aiengg.potholereporter.extra.SESSION_ID"
    const val EXTRA_NOTIFICATION_ID = "dev.aiengg.potholereporter.extra.NOTIFICATION_ID"

    /**
     * A service generation owns its own notification ID. A late Binder return from an older
     * generation can therefore be cancelled without overwriting or removing the current Drive.
     */
    fun notificationIdForSession(sessionId: String): Int {
        if (sessionId.isBlank()) return NOTIFICATION_ID
        return 0x10000000 or (sessionId.hashCode() and 0x0fffffff)
    }

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
        return runCatching {
            val runtimeGranted = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
                ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED
            val appEnabled = NotificationManagerCompat.from(context).areNotificationsEnabled()
            val channelEnabled = Build.VERSION.SDK_INT < Build.VERSION_CODES.O ||
                ((context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                    .getNotificationChannel(CHANNEL_ID)?.importance
                    ?: NotificationManager.IMPORTANCE_LOW) != NotificationManager.IMPORTANCE_NONE
            runtimeGranted && appEnabled && channelEnabled
        }.getOrDefault(false)
    }

    fun buildNotification(
        context: Context,
        sessionId: String,
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
        cameraActive: Boolean = false,
        captureStopped: Boolean = false,
        captureSource: NativeFrameSourceKind = NativeFrameSourceKind.PHONE_CAMERA
    ): Notification {
        val openAppIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val openAppPendingIntent = PendingIntent.getActivity(
            context, 0, openAppIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val pauseResumeAction = if (isPaused) ACTION_RESUME else ACTION_PAUSE
        val notificationId = notificationIdForSession(sessionId)
        val pauseResumeIntent = controlIntent(
            context, pauseResumeAction, sessionId, notificationId)
        val pauseResumePendingIntent = PendingIntent.getBroadcast(
            context, 1, pauseResumeIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val stopIntent = controlIntent(context, ACTION_STOP, sessionId, notificationId)
        val stopPendingIntent = PendingIntent.getBroadcast(
            context, 2, stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val dismissIntent = controlIntent(context, ACTION_DISMISS, sessionId, notificationId)
        val dismissPendingIntent = PendingIntent.getBroadcast(
            context, 3, dismissIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val dashcam = captureSource == NativeFrameSourceKind.DASHCAM
        val sourceStarting = !cameraActive && (statusText?.contains("starting", ignoreCase = true) == true ||
            statusText?.contains("waiting", ignoreCase = true) == true)
        val title = when {
            isStopping && captureStopped -> if (dashcam)
                "Drive Mode · Dashcam Off · Saving" else "Drive Mode · Camera Off · Saving"
            isStopping -> "Drive Mode Stopping Safely"
            isPausing -> if (dashcam)
                "Drive Mode Pausing · Dashcam Disconnecting" else "Drive Mode Pausing · Camera Stopping"
            isPaused -> if (dashcam)
                "Drive Mode Paused · Dashcam Off" else "Drive Mode Paused · Camera Off"
            sourceStarting -> if (dashcam)
                "Drive Mode · Dashcam Connecting" else "Drive Mode · Camera Starting"
            !cameraActive -> if (dashcam)
                "Drive Mode · Dashcam Interrupted" else "Drive Mode · Camera Interrupted"
            dashcam -> "Drive Mode · Dashcam Active"
            isRecording -> "Drive Mode · Recording Video"
            recordingEnabled && !recordingIssue.isNullOrBlank() -> "Drive Mode · Video Not Recording"
            else -> "Drive Mode · Camera Active"
        }
        val captureDisclosure = when {
            isStopping && captureStopped -> if (dashcam)
                "Dashcam off · finalizing saved data" else "Camera off · finalizing saved data"
            isPausing -> if (dashcam)
                "Dashcam stream is stopping safely" else "Camera and video are stopping safely"
            isPaused -> if (dashcam) "Dashcam off" else "Camera off"
            sourceStarting -> if (dashcam)
                "Dashcam connecting · persistent status remains visible"
            else "Camera starting · persistent status remains visible"
            !cameraActive -> if (dashcam)
                "Dashcam unavailable · detection is paused" else "Camera unavailable · detection and video are paused"
            dashcam -> "Dashcam active · selected complete frames saved · audio off"
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
            // Android 13+ lets users dismiss foreground-service notifications. Treat that
            // as an explicit Stop so camera capture can never continue without disclosure.
            .setDeleteIntent(dismissPendingIntent)
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
        // The first Stop is idempotent in the service, but exposing another Stop control
        // during teardown invites stale PendingIntent delivery and misleading UI.
        if (!isStopping) {
            builder.addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop", stopPendingIntent)
        }

        return builder.build()
    }

    /**
     * The session is part of PendingIntent identity as well as its extras. A button from
     * an old notification can therefore never control a newer Drive session.
     */
    private fun controlIntent(
        context: Context,
        action: String,
        sessionId: String,
        notificationId: Int
    ) =
        Intent(context, NotificationActionReceiver::class.java).apply {
            this.action = action
            data = Uri.Builder()
                .scheme("pothole-reporter")
                .authority("drive-control")
                .appendPath(sessionId)
                .appendPath(action.substringAfterLast('.'))
                .build()
            putExtra(EXTRA_SESSION_ID, sessionId)
            putExtra(EXTRA_NOTIFICATION_ID, notificationId)
        }
}
