package com.gauravsen.potholereporter

import android.content.Intent
import android.os.Bundle
import com.gauravsen.potholereporter.bridge.VideoImportPlugin
import com.gauravsen.potholereporter.bridge.isVideoIngressAction
import com.getcapacitor.BridgeActivity

/**
 * Main activity for the Pothole Reporter Capacitor app.
 *
 * Registers the native DriveModePlugin so the WebView can start and
 * control the background Drive Mode service via the Capacitor bridge.
 */
class MainActivity : BridgeActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        registerPlugin(com.gauravsen.potholereporter.bridge.DriveModePlugin::class.java)
        registerPlugin(VideoImportPlugin::class.java)
        // The pinned Capacitor BridgeActivity invokes this Activity's virtual onNewIntent
        // once after constructing all registered plugins, including for the cold-start
        // launch Intent. MainActivity.onNewIntent below therefore owns the single delivery.
        super.onCreate(savedInstanceState)
        // Recover any accepted observation committed just before a process death.
        com.gauravsen.potholereporter.drivemode.UploadWorker.enqueue(
            applicationContext,
            ensureAfterCurrent = true,
        )
        // Messages the owner sends to every phone, checked hourly and on each open.
        com.gauravsen.potholereporter.messages.MessageWorker.schedule(applicationContext)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        clearHandledVideoIntent(intent)
    }

    /** Do not import the launch share twice if Android later recreates this single-task Activity. */
    private fun clearHandledVideoIntent(candidate: Intent? = intent) {
        if (!isVideoIngressAction(candidate?.action)) return
        setIntent(Intent(this, MainActivity::class.java))
    }
}
