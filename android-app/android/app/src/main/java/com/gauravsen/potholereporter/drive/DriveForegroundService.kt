package com.gauravsen.potholereporter.drive

import android.annotation.SuppressLint
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import com.gauravsen.potholereporter.db.PotholeDatabase
import com.gauravsen.potholereporter.db.SessionEntity
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import org.json.JSONArray
import java.util.concurrent.ConcurrentHashMap

data class BurstJob(
    val burstFrames: List<BurstFrame>, val primaryIndex: Int, val fix: GpsFix,
    val captureSeq: Int, val capturedAtMs: Long, val sourceOffsetMs: Long
)

data class DriveStatusSnapshot(
    val isRunning: Boolean, val isPaused: Boolean, val sessionId: String?,
    val checked: Int, val found: Int, val already: Int, val queued: Int,
    val dropped: Int, val status: String
)

class DriveForegroundService : LifecycleService() {
    private var wakeLock: PowerManager.WakeLock? = null
    private var cameraManager: NativeDriveCameraManager? = null
    private var locationProvider: NativeDriveLocationProvider? = null
    private var inferenceEngine: NativeInferenceEngine? = null
    private var dedupeEngine: NativeDeduplicationEngine? = null
    private lateinit var database: PotholeDatabase

    private var sessionId = ""
    private var startedAtMs = 0L
    @Volatile private var isPaused = false
    @Volatile private var isStopping = false
    @Volatile private var sessionRunning = false
    private var captureSeq = 0
    @Volatile private var checkedCount = 0
    @Volatile private var foundCount = 0
    @Volatile private var alreadyCount = 0
    @Volatile private var queuedCount = 0
    @Volatile private var droppedCount = 0
    @Volatile private var statusText = "Idle"
    private val duplicateIds = ConcurrentHashMap.newKeySet<Long>()
    private var lastCapFix: GpsFix? = null
    private var lastCapTimeMs = 0L
    private var jobChannel: Channel<BurstJob>? = null
    private var scanJob: Job? = null
    private var workerJob: Job? = null
    private val mainHandler = Handler(Looper.getMainLooper())

    companion object {
        const val ACTION_START = "com.gauravsen.potholereporter.ACTION_START"
        const val ACTION_PAUSE = "com.gauravsen.potholereporter.ACTION_PAUSE"
        const val ACTION_RESUME = "com.gauravsen.potholereporter.ACTION_RESUME"
        const val ACTION_STOP = "com.gauravsen.potholereporter.ACTION_STOP"
        const val EXTRA_API_KEY = "extra_api_key"
        const val EXTRA_MODEL = "extra_model"
        const val EXTRA_DETAIL = "extra_detail"
        const val EXTRA_LANGUAGE = "extra_language"
        const val EXTRA_DEBUG = "extra_debug"
        private const val MAX_WAKELOCK_MS = 4 * 60 * 60 * 1000L

        @Volatile var activeService: DriveForegroundService? = null
        var onStatusListener: ((DriveStatusSnapshot) -> Unit)? = null
        var onReportListener: ((Long, String, String?) -> Unit)? = null
        var onDriveEndedListener: ((String, Int, Int, Int) -> Unit)? = null
        fun status(): DriveStatusSnapshot = activeService?.snapshot()
            ?: DriveStatusSnapshot(false, false, null, 0, 0, 0, 0, 0, "Idle")
    }

    override fun onCreate() {
        super.onCreate()
        activeService = this
        database = PotholeDatabase.getDatabase(this)
        dedupeEngine = NativeDeduplicationEngine(database)
        NotificationHelper.createNotificationChannel(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        when (intent?.action ?: ACTION_START) {
            ACTION_START -> if (!sessionRunning && !isStopping) startDriveSession(
                intent?.getStringExtra(EXTRA_API_KEY).orEmpty(),
                intent?.getStringExtra(EXTRA_MODEL) ?: "gpt-5-mini",
                intent?.getStringExtra(EXTRA_DETAIL) ?: "high",
                intent?.getStringExtra(EXTRA_LANGUAGE) ?: "en",
                intent?.getBooleanExtra(EXTRA_DEBUG, false) ?: false
            )
            ACTION_PAUSE -> pauseDrive()
            ACTION_RESUME -> resumeDrive()
            ACTION_STOP -> stopDriveSession()
        }
        return START_NOT_STICKY
    }

    private fun startDriveSession(apiKey: String, model: String, detail: String, language: String, debug: Boolean) {
        startedAtMs = System.currentTimeMillis()
        sessionId = startedAtMs.toString()
        isPaused = false; isStopping = false; sessionRunning = true
        captureSeq = 0; checkedCount = 0; foundCount = 0; alreadyCount = 0
        queuedCount = 0; droppedCount = 0; duplicateIds.clear()
        lastCapFix = null; lastCapTimeMs = 0L; statusText = "Starting camera and GPS"
        startForegroundNow()
        acquireWakeLock()

        inferenceEngine = NativeInferenceEngine(applicationContext, apiKey, model, detail, language, debug)
        jobChannel = Channel(
            capacity = 6,
            onBufferOverflow = BufferOverflow.DROP_OLDEST,
            onUndeliveredElement = { item ->
                recycle(item); droppedCount++; queuedCount = (queuedCount - 1).coerceAtLeast(0)
            }
        )
        locationProvider = NativeDriveLocationProvider(this) { }.also { it.startUpdates(startedAtMs) }
        cameraManager = NativeDriveCameraManager(this, this) { available, reason ->
            if (!isPaused && !isStopping) publish(if (available) "Scanning live" else reason ?: "Waiting for camera")
        }.also { manager -> manager.startCamera { if (!it) publish("Waiting for camera") } }

        startScanLoop()
        startInferenceWorker()
        lifecycleScope.launch(Dispatchers.IO) {
            database.sessionDao().insertSession(SessionEntity(id = sessionId, startedAt = startedAtMs / 1000))
        }
        publish("Scanning live")
    }

    private fun startForegroundNow() {
        val notification = NotificationHelper.buildNotification(this, false, 0, 0, 0, statusText)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NotificationHelper.NOTIFICATION_ID, notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA or ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
            )
        } else startForeground(NotificationHelper.NOTIFICATION_ID, notification)
    }

    @SuppressLint("WakelockTimeout")
    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "PotholeReporter:DriveMode").apply {
            setReferenceCounted(false); acquire(MAX_WAKELOCK_MS)
        }
    }

    private fun releaseWakeLock() {
        runCatching { if (wakeLock?.isHeld == true) wakeLock?.release() }
        wakeLock = null
    }

    private fun startScanLoop() {
        scanJob?.cancel()
        scanJob = lifecycleScope.launch(Dispatchers.Default) {
            while (isActive && sessionRunning && !isStopping) {
                delay(500)
                if (isPaused) continue
                val loc = locationProvider ?: continue
                val fix = loc.latestFix ?: continue
                val cam = cameraManager ?: continue
                if (!cam.isCameraReady || !loc.shouldTriggerCapture(lastCapFix, lastCapTimeMs, fix)) continue
                val capturedAt = System.currentTimeMillis()
                val burst = cam.captureBurst() ?: continue
                val item = BurstJob(burst.first, burst.second, fix, ++captureSeq, capturedAt, capturedAt - startedAtMs)
                lastCapFix = fix; lastCapTimeMs = capturedAt
                if (jobChannel?.trySend(item)?.isSuccess == true) queuedCount++ else recycle(item)
                publish("Scanning live")
            }
        }
    }

    private fun startInferenceWorker() {
        workerJob?.cancel()
        val channel = jobChannel ?: return
        workerJob = lifecycleScope.launch(Dispatchers.IO) {
            for (item in channel) {
                queuedCount = (queuedCount - 1).coerceAtLeast(0)
                if (isStopping) { recycle(item); continue }
                try {
                    val outcome = inferenceEngine?.analyzeBurst(
                        item.burstFrames, item.primaryIndex, item.fix.lat, item.fix.lng,
                        sessionId, item.captureSeq, item.capturedAtMs, item.sourceOffsetMs,
                        item.fix.accuracy, item.fix.speedMps, item.fix.heading
                    )
                    checkedCount++
                    val report = outcome?.reportEntity
                    if (outcome?.accepted == true && report != null) {
                        val result = dedupeEngine?.checkAndCommitReport(report, outcome.sightings)
                        if (result?.isDuplicate == true) {
                            result.existingReportId?.let(duplicateIds::add); alreadyCount = duplicateIds.size
                        } else if (result != null) {
                            foundCount++
                            val reportId = result.existingReportId ?: 0
                            mainHandler.post {
                                onReportListener?.invoke(reportId, report.damageType ?: "pothole_cavity", report.address)
                            }
                        }
                    }
                    publish("Scanning live")
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (error: NativeInferenceException) {
                    publish(error.message ?: "Detection temporarily failed")
                    if (error.fatal) withContext(Dispatchers.Main) {
                        stopDriveSession(error.message ?: "Detection stopped")
                    }
                } catch (error: Exception) {
                    publish("Detection retrying: ${error.message ?: "temporary error"}")
                } finally { recycle(item) }
            }
        }
    }

    fun pauseDrive() {
        if (!sessionRunning || isPaused || isStopping) return
        isPaused = true; scanJob?.cancel(); cameraManager?.pauseCamera()
        locationProvider?.stopUpdates(); releaseWakeLock()
        lifecycleScope.launch(Dispatchers.IO) { persistSession("paused", null) }
        publish("Paused")
    }

    fun resumeDrive() {
        if (!sessionRunning || !isPaused || isStopping) return
        isPaused = false; acquireWakeLock(); locationProvider?.resumeUpdates()
        cameraManager?.resumeCamera(); startScanLoop()
        lifecycleScope.launch(Dispatchers.IO) { persistSession("active", null) }
        publish("Scanning live")
    }

    fun stopDriveSession(reason: String = "Stopped") {
        if (!sessionRunning || isStopping) return
        isStopping = true; sessionRunning = false; statusText = reason
        scanJob?.cancel(); workerJob?.cancel(); jobChannel?.close(); jobChannel = null
        cameraManager?.stopCamera(); cameraManager = null
        locationProvider?.stopUpdates(); releaseWakeLock(); inferenceEngine?.close()
        val endedSession = sessionId
        val checked = checkedCount; val found = foundCount; val already = alreadyCount
        lifecycleScope.launch(Dispatchers.IO) {
            persistSession("stopped", System.currentTimeMillis() / 1000)
            withContext(Dispatchers.Main) {
                onDriveEndedListener?.invoke(endedSession, checked, found, already)
                stopForeground(STOP_FOREGROUND_REMOVE); stopSelf()
            }
        }
    }

    private suspend fun persistSession(status: String, endedAt: Long?) {
        val sourceTrack = locationProvider?.gpsTrack
        val track = JSONArray(if (sourceTrack == null) emptyList<JSONArray>() else synchronized(sourceTrack) {
            sourceTrack.toList()
        }).toString()
        database.sessionDao().insertSession(SessionEntity(
            id = sessionId, startedAt = startedAtMs / 1000, endedAt = endedAt,
            checkedCount = checkedCount, foundCount = foundCount, alreadyCount = alreadyCount,
            alreadyIdsJson = JSONArray(duplicateIds.toList()).toString(), gpsTrackJson = track, status = status
        ))
    }

    private fun snapshot() = DriveStatusSnapshot(
        sessionRunning, isPaused, sessionId.takeIf(String::isNotBlank), checkedCount,
        foundCount, alreadyCount, queuedCount, droppedCount, statusText
    )

    private fun publish(text: String) {
        statusText = text
        if (sessionRunning || isStopping) {
            val notification = NotificationHelper.buildNotification(this, isPaused, checkedCount, foundCount, alreadyCount, statusText)
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                .notify(NotificationHelper.NOTIFICATION_ID, notification)
        }
        val current = snapshot()
        mainHandler.post { onStatusListener?.invoke(current) }
    }

    private fun recycle(item: BurstJob) {
        item.burstFrames.forEach { if (!it.bitmap.isRecycled) it.bitmap.recycle() }
    }

    override fun onDestroy() {
        activeService = null; sessionRunning = false
        scanJob?.cancel(); workerJob?.cancel(); jobChannel?.cancel()
        cameraManager?.stopCamera(); locationProvider?.stopUpdates(); inferenceEngine?.close(); releaseWakeLock()
        super.onDestroy()
    }
}
