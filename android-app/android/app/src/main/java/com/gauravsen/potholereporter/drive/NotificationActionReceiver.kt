package com.gauravsen.potholereporter.drive

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class NotificationActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        val serviceIntent = Intent(context, DriveForegroundService::class.java).apply {
            this.action = action
        }
        context.startService(serviceIntent)
    }
}
