package com.gauravsen.potholereporter.messages

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.gauravsen.potholereporter.MainActivity
import com.gauravsen.potholereporter.R
import com.gauravsen.potholereporter.drivemode.CentralServiceIdentity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.TimeUnit

/**
 * Messages the owner sends to every phone (infra/aws-central/tools/send-announcement.mjs),
 * shown as notifications. About once an hour, and once each time the app opens, this asks
 * GET /v1/messages for anything newer than the last message it saw. The request carries
 * nothing about the phone. The first answer only sets the starting point, so a new install
 * never receives a backlog.
 */
class MessageWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val prefs = applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val cursor = prefs.getLong(KEY_CURSOR, -1L)
        val address = CentralServiceIdentity.DEFAULT_SERVICE_URL.trimEnd('/') + "/v1/messages" +
            if (cursor >= 0) "?since=$cursor" else ""
        val connection = (URL(address).openConnection() as HttpURLConnection).apply {
            connectTimeout = TIMEOUT_MS
            readTimeout = TIMEOUT_MS
            setRequestProperty("Accept", "application/json")
        }
        try {
            if (connection.responseCode != 200) return@withContext Result.retry()
            val answer = JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
            val messages = answer.optJSONArray("messages") ?: JSONArray()
            var newest = if (cursor >= 0) cursor else answer.optLong("now", System.currentTimeMillis())
            // A phone that was off for days shows the latest few, not a pile.
            val first = maxOf(0, messages.length() - MAX_SHOWN)
            for (index in 0 until messages.length()) {
                val message = messages.getJSONObject(index)
                val createdAt = message.optLong("created_at")
                if (index >= first) show(message.optString("title"), message.optString("body"), createdAt)
                if (createdAt > newest) newest = createdAt
            }
            prefs.edit().putLong(KEY_CURSOR, newest).apply()
            Result.success()
        } catch (error: Exception) {
            Log.w(TAG, "message check failed: ${error.message}")
            Result.retry()
        } finally {
            connection.disconnect()
        }
    }

    private fun show(title: String, body: String, createdAt: Long) {
        if (title.isBlank() || body.isBlank()) return
        val manager = NotificationManagerCompat.from(applicationContext)
        // Without the notification permission there is nothing to show; the message is
        // still counted as seen so it does not arrive days late once permission is given.
        if (!manager.areNotificationsEnabled()) return
        ensureChannel(applicationContext)
        val open = PendingIntent.getActivity(
            applicationContext, 0,
            Intent(applicationContext, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = NotificationCompat.Builder(applicationContext, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_drive_notification)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        try {
            manager.notify((createdAt % Int.MAX_VALUE).toInt(), notification)
        } catch (error: SecurityException) {
            Log.w(TAG, "notification refused: ${error.message}")
        }
    }

    companion object {
        private const val TAG = "MessageWorker"
        private const val PREFS = "pothole_messages"
        private const val KEY_CURSOR = "last_message_at"
        private const val CHANNEL_ID = "announcements"
        private const val PERIODIC = "messages-hourly"
        private const val NOW = "messages-now"
        private const val TIMEOUT_MS = 10_000
        private const val MAX_SHOWN = 3

        private fun ensureChannel(context: Context) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
            val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (manager.getNotificationChannel(CHANNEL_ID) != null) return
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Messages from Pothole Reporter", NotificationManager.IMPORTANCE_DEFAULT)
            )
        }

        /** Hourly checks for as long as the app is installed, plus one now. */
        fun schedule(context: Context) {
            val online = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
            val work = WorkManager.getInstance(context)
            work.enqueueUniquePeriodicWork(
                PERIODIC, ExistingPeriodicWorkPolicy.KEEP,
                PeriodicWorkRequestBuilder<MessageWorker>(1, TimeUnit.HOURS).setConstraints(online).build(),
            )
            work.enqueueUniqueWork(
                NOW, ExistingWorkPolicy.KEEP,
                OneTimeWorkRequestBuilder<MessageWorker>().setConstraints(online).build(),
            )
        }
    }
}
