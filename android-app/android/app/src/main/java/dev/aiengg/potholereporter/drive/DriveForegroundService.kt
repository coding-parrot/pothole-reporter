package dev.aiengg.potholereporter.drive

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
import androidx.camera.core.Preview
import dev.aiengg.potholereporter.db.FootageSegmentEntity
import dev.aiengg.potholereporter.db.PotholeDatabase
import dev.aiengg.potholereporter.db.SessionEntity
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import org.json.JSONArray
import java.io.File
import java.util.concurrent.ConcurrentHashMap

data class BurstJob(
    val burstFrames: List<BurstFrame>, val primaryIndex: Int, val fix: GpsFix,
    val captureSeq: Int, val capturedAtMs: Long, val sourceOffsetMs: Long
)

data class DriveStatusSnapshot(
    val isRunning: Boolean, val isPaused: Boolean, val isPausing: Boolean,
    val isStopping: Boolean, val sessionId: String?,
    val checked: Int, val found: Int, val already: Int, val queued: Int,
    val dropped: Int, val status: String, val recordingEnabled: Boolean,
    val isRecording: Boolean, val videoSupported: Boolean,
    val segmentCount: Int, val recordedBytes: Long, val cameraActive: Boolean,
    val recordingIssue: String?
)

data class DriveEndSummary(
    val sessionId: String,
    val checked: Int,
    val found: Int,
    val already: Int,
    val error: String? = null,
    val discarded: Boolean = false
)

internal class DriveStartCompletionLedger {
    private val summaries = LinkedHashMap<String, DriveEndSummary>()

    @Synchronized
    fun record(requestId: String?, summary: DriveEndSummary) {
        if (requestId.isNullOrBlank()) return
        // A late, empty ACTION_STOP can recreate an already-stopped service. Preserve
        // the first (durable, data-bearing) completion for that exact start request.
        if (summaries.containsKey(requestId)) return
        summaries[requestId] = summary
        while (summaries.size > 32) summaries.remove(summaries.keys.first())
    }

    @Synchronized
    fun summaryFor(requestId: String): DriveEndSummary? =
        summaries[requestId]
}

class DriveForegroundService : LifecycleService() {
    private var wakeLock: PowerManager.WakeLock? = null
    private var cameraManager: NativeDriveCameraManager? = null
    private var locationProvider: NativeDriveLocationProvider? = null
    private var inferenceEngine: NativeInferenceEngine? = null
    private var dedupeEngine: NativeDeduplicationEngine? = null
    private var repairEngine: NativeRepairStatusEngine? = null
    private lateinit var database: PotholeDatabase

    private var sessionId = ""
    private var startRequestId: String? = null
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
    @Volatile private var recordingEnabled = false
    @Volatile private var isRecording = false
    @Volatile private var videoSupported = true
    @Volatile private var segmentCount = 0
    @Volatile private var recordedBytes = 0L
    @Volatile private var cameraActive = false
    @Volatile private var recordingIssue: String? = null
    @Volatile private var pauseTransitioning = false
    @Volatile private var notificationStopRequested = false
    @Volatile private var lastNotificationCheckMs = 0L
    @Volatile private var discardDataOnStop = false
    @Volatile private var debugMode = false
    private val duplicateIds = ConcurrentHashMap.newKeySet<Long>()
    private var lastCapFix: GpsFix? = null
    private var lastCapTimeMs = 0L
    private var jobChannel: Channel<BurstJob>? = null
    private var scanJob: Job? = null
    private var workerJob: Job? = null
    private var cameraTransitionJob: Job? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    private val stopCallbacks = mutableListOf<(DriveEndSummary) -> Unit>()
    @Volatile private var completedStopSummary: DriveEndSummary? = null
    private val segmentPersistJobs = mutableListOf<Job>()
    private val segmentPersistErrors = mutableListOf<String>()
    private val sessionPersistJobs = mutableListOf<Job>()
    private val sessionPersistErrors = mutableListOf<String>()

    companion object {
        const val ACTION_START = "dev.aiengg.potholereporter.ACTION_START"
        const val ACTION_PAUSE = "dev.aiengg.potholereporter.ACTION_PAUSE"
        const val ACTION_RESUME = "dev.aiengg.potholereporter.ACTION_RESUME"
        const val ACTION_STOP = "dev.aiengg.potholereporter.ACTION_STOP"
        const val EXTRA_API_KEY = "extra_api_key"
        const val EXTRA_MODEL = "extra_model"
        const val EXTRA_DETAIL = "extra_detail"
        const val EXTRA_LANGUAGE = "extra_language"
        const val EXTRA_DEBUG = "extra_debug"
        const val EXTRA_RECORD_VIDEO = "extra_record_video"
        const val EXTRA_START_REQUEST_ID = "extra_start_request_id"
        const val EXTRA_STOP_REQUEST_ID = "extra_stop_request_id"
        const val EXTRA_STOP_DISCARD_DATA = "extra_stop_discard_data"
        private const val MAX_WAKELOCK_MS = 4 * 60 * 60 * 1000L
        private val startCompletionLedger = DriveStartCompletionLedger()

        @Volatile var activeService: DriveForegroundService? = null
        var onStatusListener: ((DriveStatusSnapshot) -> Unit)? = null
        var onReportListener: ((Long, String, String?) -> Unit)? = null
        var onDriveEndedListener: ((DriveEndSummary) -> Unit)? = null
        fun status(): DriveStatusSnapshot = activeService?.snapshot()
            ?: DriveStatusSnapshot(false, false, false, false, null, 0, 0, 0, 0, 0, "Idle", false, false, true, 0, 0, false, null)
        fun activeStartRequestId(): String? = activeService?.startRequestId
        fun completedStartSummary(requestId: String): DriveEndSummary? =
            startCompletionLedger.summaryFor(requestId)
    }

    override fun onCreate() {
        super.onCreate()
        activeService = this
        database = PotholeDatabase.getDatabase(this)
        dedupeEngine = NativeDeduplicationEngine(database)
        repairEngine = NativeRepairStatusEngine(database)
        NotificationHelper.createNotificationChannel(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        when (intent?.action ?: ACTION_START) {
            ACTION_START -> if (!sessionRunning && !isStopping) {
                startRequestId = intent?.getStringExtra(EXTRA_START_REQUEST_ID)
                    ?: "service-$startId-${System.currentTimeMillis()}"
                startDriveSession(
                    intent?.getStringExtra(EXTRA_API_KEY).orEmpty(),
                    intent?.getStringExtra(EXTRA_MODEL) ?: "gpt-5-mini",
                    intent?.getStringExtra(EXTRA_DETAIL) ?: "high",
                    intent?.getStringExtra(EXTRA_LANGUAGE) ?: "en",
                    intent?.getBooleanExtra(EXTRA_DEBUG, false) ?: false,
                    intent?.getBooleanExtra(EXTRA_RECORD_VIDEO, false) ?: false
                )
            }
            ACTION_PAUSE -> pauseDrive()
            ACTION_RESUME -> resumeDrive()
            ACTION_STOP -> handleStopIntent(intent, startId)
        }
        return START_NOT_STICKY
    }

    private fun handleStopIntent(intent: Intent?, startId: Int) {
        val requestedStart = intent?.getStringExtra(EXTRA_STOP_REQUEST_ID)
        val discardData = intent?.getBooleanExtra(EXTRA_STOP_DISCARD_DATA, false) ?: false
        val activeRequest = startRequestId
        if (!requestedStart.isNullOrBlank() && !activeRequest.isNullOrBlank() &&
            requestedStart != activeRequest) {
            return // stale Stop must never terminate a newer Drive session
        }
        if (sessionRunning || isStopping) {
            stopDriveSession(discardData = discardData)
            return
        }

        // startService(ACTION_STOP) may instantiate an otherwise empty service if the
        // paired ACTION_START failed or was already torn down. Acknowledge that exact
        // request and remove the component immediately; never leave an idle service
        // waiting for Android's foreground-service deadline.
        val summary = DriveEndSummary(
            sessionId = sessionId,
            checked = checkedCount,
            found = foundCount,
            already = alreadyCount,
            discarded = discardData
        )
        startCompletionLedger.record(requestedStart, summary)
        runCatching { stopForeground(STOP_FOREGROUND_REMOVE) }
        stopSelf(startId)
    }

    private fun startDriveSession(
        apiKey: String,
        model: String,
        detail: String,
        language: String,
        debug: Boolean,
        recordVideo: Boolean
    ) {
        startedAtMs = System.currentTimeMillis()
        sessionId = startedAtMs.toString()
        isPaused = false; isStopping = false; sessionRunning = true
        captureSeq = 0; checkedCount = 0; foundCount = 0; alreadyCount = 0
        queuedCount = 0; droppedCount = 0; duplicateIds.clear()
        recordingEnabled = recordVideo; isRecording = false; videoSupported = true
        debugMode = debug
        segmentCount = 0; recordedBytes = 0L; pauseTransitioning = false
        discardDataOnStop = false
        completedStopSummary = null
        synchronized(segmentPersistErrors) { segmentPersistErrors.clear() }
        synchronized(sessionPersistErrors) { sessionPersistErrors.clear() }
        cameraActive = false
        recordingIssue = null
        notificationStopRequested = false; lastNotificationCheckMs = 0L
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
        cameraManager = NativeDriveCameraManager(
            context = this,
            lifecycleOwner = this,
            initialVideoRecordingEnabled = recordVideo,
            sessionId = sessionId,
            onCameraStateChange = { available, reason ->
                cameraActive = available
                if (!isPaused && !isStopping) publish(if (available) "Scanning live" else reason ?: "Waiting for camera")
            },
            onRecordingStateChange = { enabled, recording, supported, message ->
                recordingEnabled = enabled
                isRecording = recording
                videoSupported = supported
                recordingIssue = when {
                    recording || !enabled -> null
                    !supported -> message ?: "Video recording is unavailable"
                    message?.startsWith("Video stopped") == true ||
                        message?.startsWith("Video could not") == true ||
                        message?.startsWith("Video finalization") == true -> message
                    else -> null
                }
                if (!message.isNullOrBlank() && !isStopping) publish(message)
            },
            onSegmentFinalized = ::persistVideoSegment
        ).also { manager ->
            manager.startCamera { ready ->
                if (!ready && sessionRunning && !isPaused && !isStopping) {
                    val failure = statusText.takeIf {
                        it.contains("camera", ignoreCase = true) &&
                            !it.contains("starting", ignoreCase = true)
                    } ?: "Camera could not start"
                    stopDriveSession(failure)
                }
            }
        }

        startScanLoop()
        startInferenceWorker()
        trackSessionPersist(lifecycleScope.launch(Dispatchers.IO) {
            try {
                database.sessionDao().insertSession(SessionEntity(id = sessionId, startedAt = startedAtMs / 1000))
            } catch (error: Exception) {
                recordSessionPersistError(error)
            }
        })
    }

    private fun startForegroundNow() {
        val notification = NotificationHelper.buildNotification(
            this, false, 0, 0, 0, statusText,
            isStopping = false,
            isPausing = false,
            recordingEnabled = recordingEnabled,
            isRecording = isRecording,
            videoSupported = videoSupported,
            recordingIssue = recordingIssue,
            cameraActive = cameraActive
        )
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
                if (!notificationStillVisible()) continue
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
                try {
                    // Looking up a candidate is local and fail-closed. If Room has a
                    // transient problem, ordinary damage detection must still proceed.
                    val repairCandidate = if (debugMode) null else try {
                        repairEngine?.findCandidate(
                            item.fix.lat,
                            item.fix.lng,
                            item.fix.accuracy,
                            item.fix.speedMps,
                            item.fix.heading,
                            item.capturedAtMs / 1000,
                            sessionId
                        )
                    } catch (_: Exception) {
                        null
                    }
                    val outcome = inferenceEngine?.analyzeBurst(
                        item.burstFrames, item.primaryIndex, item.fix.lat, item.fix.lng,
                        sessionId, item.captureSeq, item.capturedAtMs, item.sourceOffsetMs,
                        item.fix.accuracy, item.fix.speedMps, item.fix.heading,
                        requireCompleteVerdict = repairCandidate != null
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
                    } else if (repairCandidate != null && outcome?.assessment?.let { assessment ->
                            // This is a complete verdict because candidate presence disabled
                            // early stream cancellation above. Even so, it is merely the gate
                            // for a separate before/after comparison, never repair proof.
                            !assessment.looksLikeSpeedBreaker &&
                                !assessment.reportable &&
                                assessment.damageType == "none" &&
                                assessment.assessment == "absent" &&
                                assessment.imageQuality == "usable" &&
                                !assessment.hasBrokenEdgeOrRim &&
                                !assessment.hasDepthOrSurfaceLoss &&
                                assessment.decision == "reject"
                        } == true) {
                        val verification = inferenceEngine?.verifyRepair(
                            repairCandidate,
                            item.burstFrames,
                            item.primaryIndex,
                            sessionId,
                            item.captureSeq
                        )
                        if (verification != null) {
                            val sourceEventKey =
                                "repair:$sessionId:${item.captureSeq}:${repairCandidate.reportId}"
                            val queued = repairEngine?.queueObservation(
                                repairCandidate,
                                verification,
                                sourceEventKey,
                                item.capturedAtMs / 1000,
                                sessionId,
                                item.fix.lat,
                                item.fix.lng,
                                item.fix.accuracy,
                                item.fix.speedMps,
                                item.fix.heading
                            ) == true
                            if (!queued) runCatching { File(verification.currentPhotoPath).delete() }
                        }
                    }
                    publish(if (isStopping) "Finishing queued detections" else "Scanning live")
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (error: NativeInferenceException) {
                    publish(error.message ?: "Detection temporarily failed")
                    if (error.fatal && !isStopping) withContext(Dispatchers.Main) {
                        stopDriveSession(error.message ?: "Detection stopped")
                    }
                } catch (error: Exception) {
                    publish("Detection retrying: ${error.message ?: "temporary error"}")
                } finally { recycle(item) }
            }
        }
    }

    fun pauseDrive(onComplete: ((DriveStatusSnapshot) -> Unit)? = null) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post { pauseDrive(onComplete) }
            return
        }
        if (!sessionRunning || isPaused || isStopping || pauseTransitioning) {
            onComplete?.invoke(snapshot())
            return
        }
        isPaused = true; pauseTransitioning = true; scanJob?.cancel()
        locationProvider?.stopUpdates(); releaseWakeLock()
        publish("Pausing safely")
        cameraTransitionJob = lifecycleScope.launch {
            var summarySaved = true
            try {
                cameraManager?.pauseCameraSafely()
                withContext(Dispatchers.IO) { persistSession("paused", null) }
            } catch (error: Exception) {
                summarySaved = false
                recordSessionPersistError(error)
            } finally {
                pauseTransitioning = false
                if (!isStopping) {
                    publish(if (summarySaved) "Paused" else "Paused · summary could not be saved")
                }
                onComplete?.invoke(snapshot())
            }
        }
    }

    fun resumeDrive(onComplete: ((DriveStatusSnapshot) -> Unit)? = null) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post { resumeDrive(onComplete) }
            return
        }
        if (!sessionRunning || !isPaused || isStopping || pauseTransitioning) {
            onComplete?.invoke(snapshot())
            return
        }
        if (!NotificationHelper.canShowDriveNotification(this)) {
            stopDriveSession("Stopped because Drive Mode notifications were disabled")
            onComplete?.invoke(snapshot())
            return
        }
        isPaused = false; acquireWakeLock(); locationProvider?.resumeUpdates()
        cameraActive = false
        publish("Camera starting")
        val manager = cameraManager
        if (manager == null) {
            stopDriveSession("Camera could not resume")
            runCatching { onComplete?.invoke(snapshot()) }
            return
        } else {
            manager.resumeCamera { ready ->
                if (!ready && sessionRunning && !isPaused && !isStopping) {
                    val failure = statusText.takeIf {
                        it.contains("camera", ignoreCase = true) &&
                            !it.contains("starting", ignoreCase = true)
                    } ?: "Camera could not resume"
                    stopDriveSession(failure)
                }
            }
            startScanLoop()
        }
        val persistJob = lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { persistSession("active", null) }
            } catch (error: Exception) {
                recordSessionPersistError(error)
            } finally {
                onComplete?.invoke(snapshot())
            }
        }
        trackSessionPersist(persistJob)
    }

    fun attachPreview(surfaceProvider: Preview.SurfaceProvider) {
        cameraManager?.attachPreview(surfaceProvider)
    }

    fun detachPreview() {
        cameraManager?.detachPreview()
    }

    fun setVideoRecordingEnabled(enabled: Boolean, onComplete: ((DriveStatusSnapshot) -> Unit)? = null) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post { setVideoRecordingEnabled(enabled, onComplete) }
            return
        }
        if (!sessionRunning || isStopping) {
            runCatching { onComplete?.invoke(snapshot()) }
            return
        }
        recordingEnabled = enabled
        publish(if (enabled) "Starting local video recording" else "Scanning frames; video is not saved")
        lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
            try {
                cameraManager?.setVideoRecordingEnabled(enabled)
            } catch (cancelled: CancellationException) {
                // Stop owns camera finalization once teardown begins. The bridge still
                // receives a final status instead of being left with a pending call.
            } catch (error: Exception) {
                recordingIssue = error.message ?: "Video recording could not be changed"
                if (!isStopping) publish(recordingIssue!!)
            } finally {
                withContext(NonCancellable + Dispatchers.Main.immediate) {
                    runCatching { onComplete?.invoke(snapshot()) }
                }
            }
        }
    }

    fun stopDriveSession(
        reason: String = "Stopped",
        discardData: Boolean = false,
        onComplete: ((DriveEndSummary) -> Unit)? = null
    ) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            // Publish destructive intent immediately so a concurrently finishing Stop
            // cannot emit a syncable summary before the main-thread registration runs.
            if (discardData) discardDataOnStop = true
            mainHandler.post { stopDriveSession(reason, discardData, onComplete) }
            return
        }
        var alreadyCompleted: DriveEndSummary? = null
        synchronized(stopCallbacks) {
            if (discardData) discardDataOnStop = true
            val completed = completedStopSummary
            if (completed != null) {
                alreadyCompleted = completed.copy(discarded = completed.discarded || discardDataOnStop)
                completedStopSummary = alreadyCompleted
            } else if (onComplete != null) {
                stopCallbacks.add(onComplete)
            }
        }
        if (alreadyCompleted != null) {
            if (onComplete != null) runCatching { onComplete(alreadyCompleted!!) }
            return
        }
        if (isStopping) return
        if (!sessionRunning) {
            val summary = DriveEndSummary(
                sessionId, checkedCount, foundCount, alreadyCount,
                discarded = discardDataOnStop
            )
            val callbacks = synchronized(stopCallbacks) {
                stopCallbacks.toList().also { stopCallbacks.clear() }
            }
            callbacks.forEach { callback -> runCatching { callback(summary) } }
            return
        }
        val stopErrors = mutableListOf<String>()
        fun recordStopError(message: String) {
            val normalized = message.trim().ifBlank { "Drive teardown failed" }
            synchronized(stopErrors) {
                if (!stopErrors.contains(normalized)) stopErrors.add(normalized)
            }
        }
        fun recordStopError(prefix: String, error: Throwable) {
            recordStopError(error.message?.let { "$prefix: $it" } ?: prefix)
        }
        isStopping = true; sessionRunning = false; statusText = reason
        runCatching { publish("Stopping safely") }
            .onFailure { recordStopError("Could not update the stopping status", it) }
        runCatching { scanJob?.cancel() }
            .onFailure { recordStopError("Could not stop camera scheduling", it) }
        val drainingChannel = jobChannel
        runCatching { drainingChannel?.close() }
            .onFailure { recordStopError("Could not close the detection queue", it) }
        jobChannel = null
        runCatching { locationProvider?.stopUpdates() }
            .onFailure { recordStopError("Could not stop location updates", it) }
        runCatching { releaseWakeLock() }
            .onFailure { recordStopError("Could not release the Drive Mode wake lock", it) }
        val manager = cameraManager
        val engine = inferenceEngine
        val endedSession = sessionId
        val endedStartRequest = startRequestId
        lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
            var inferenceClosed = false
            var cameraStoppedCleanly = false

            fun closeInference() {
                if (inferenceClosed) return
                val closed = runCatching { engine?.close() }
                    .onFailure { recordStopError("Could not close the detection engine", it) }
                inferenceClosed = closed.isSuccess
            }

            suspend fun stopWorkerWithinLimit(limitMs: Long): Boolean {
                val worker = workerJob ?: return true
                return try {
                    withTimeoutOrNull(limitMs) {
                        worker.join()
                        true
                    } == true
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Throwable) {
                    recordStopError("Detection worker teardown failed", error)
                    false
                }
            }

            suspend fun finishTrackedJobs(
                jobs: List<Job>,
                label: String,
                waitMs: Long = 12_000L
            ) {
                if (jobs.isEmpty()) return
                val finished = try {
                    withTimeoutOrNull(waitMs) {
                        jobs.joinAll()
                        true
                    } == true
                } catch (error: Throwable) {
                    recordStopError("$label persistence failed", error)
                    false
                }
                if (finished) return
                recordStopError("$label persistence exceeded the Stop limit")
                jobs.forEach { job -> runCatching { job.cancel() } }
                val cancelled = try {
                    withTimeoutOrNull(3_000L) {
                        jobs.joinAll()
                        true
                    } == true
                } catch (_: Throwable) {
                    false
                }
                if (!cancelled) recordStopError("$label persistence did not stop cleanly")
            }

            val cameraStopped = async {
                try {
                    cameraTransitionJob?.join()
                    manager?.stopCameraSafely()
                    cameraStoppedCleanly = true
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (error: Throwable) {
                    recordStopError("Camera teardown failed", error)
                    val closed = runCatching { manager?.closeImmediately() }
                        .onFailure { recordStopError("Emergency camera teardown failed", it) }
                    cameraStoppedCleanly = closed.isSuccess
                }
            }
            try {
                val drained = stopWorkerWithinLimit(30_000L)
                if (!drained) {
                    recordStopError("Some queued detections exceeded the 30-second Stop limit")
                    // OkHttp execute() is blocking. Cancel its Call before cancelling the
                    // coroutine, then give the worker only a second bounded exit window.
                    closeInference()
                    runCatching { workerJob?.cancel() }
                        .onFailure { recordStopError("Could not cancel the detection worker", it) }
                    runCatching { drainingChannel?.cancel() }
                        .onFailure { recordStopError("Could not discard the remaining detection queue", it) }
                    if (!stopWorkerWithinLimit(5_000L)) {
                        recordStopError("Detection worker did not stop within the cancellation limit")
                    }
                }

                val cameraFinished = withTimeoutOrNull(18_000L) {
                    cameraStopped.await()
                    true
                } == true
                if (!cameraFinished) {
                    recordStopError("Camera teardown exceeded the Stop limit")
                    cameraStopped.cancel()
                }
                closeInference()
            } catch (cancelled: CancellationException) {
                recordStopError("Drive teardown was interrupted")
            } catch (error: Throwable) {
                recordStopError("Drive teardown failed", error)
            } finally {
                withContext(NonCancellable) {
                    var completedSummary: DriveEndSummary? = null
                    try {
                    // Every operation below is isolated so no camera, database, or client
                    // exception can leave the foreground notification or a plugin call stuck.
                    runCatching { scanJob?.cancel() }
                        .onFailure { recordStopError("Could not stop camera scheduling", it) }
                    runCatching { drainingChannel?.cancel() }
                        .onFailure { recordStopError("Could not close the detection queue", it) }

                    if (workerJob?.isCompleted != true) {
                        closeInference()
                        runCatching { workerJob?.cancel() }
                            .onFailure { recordStopError("Could not cancel the detection worker", it) }
                        if (!stopWorkerWithinLimit(5_000L)) {
                            recordStopError("Detection worker remained active after Stop")
                        }
                    }
                    closeInference()
                    if (inferenceClosed) inferenceEngine = null

                    if (!cameraStoppedCleanly || !cameraStopped.isCompleted) {
                        runCatching { cameraStopped.cancel() }
                        runCatching { cameraTransitionJob?.cancel() }
                        runCatching { manager?.closeImmediately() }
                            .onFailure { recordStopError("Emergency camera teardown failed", it) }
                    }
                    cameraManager = null
                    cameraActive = false
                    isRecording = false

                    runCatching { locationProvider?.stopUpdates() }
                        .onFailure { recordStopError("Could not stop location updates", it) }
                    runCatching { releaseWakeLock() }
                        .onFailure { recordStopError("Could not release the Drive Mode wake lock", it) }

                    val footageJobs = synchronized(segmentPersistJobs) { segmentPersistJobs.toList() }
                    finishTrackedJobs(footageJobs, "Video")
                    val sessionJobs = synchronized(sessionPersistJobs) { sessionPersistJobs.toList() }
                    finishTrackedJobs(sessionJobs, "Drive summary")

                    synchronized(segmentPersistErrors) {
                        segmentPersistErrors.forEach(::recordStopError)
                    }
                    synchronized(sessionPersistErrors) {
                        sessionPersistErrors.forEach(::recordStopError)
                    }

                    if (!discardDataOnStop) {
                        try {
                            val saved = withTimeoutOrNull(10_000L) {
                                withContext(Dispatchers.IO) {
                                    persistSession("stopped", System.currentTimeMillis() / 1000)
                                }
                                true
                            } == true
                            if (!saved) recordStopError("Saving the final drive summary exceeded the Stop limit")
                        } catch (error: Throwable) {
                            recordStopError("Could not save the final drive summary", error)
                        }
                    }

                    val discarded = discardDataOnStop
                    val summaryError = synchronized(stopErrors) {
                        stopErrors.takeIf { it.isNotEmpty() }?.joinToString("; ")
                    }
                    completedSummary = DriveEndSummary(
                        endedSession, checkedCount, foundCount, alreadyCount,
                        error = summaryError,
                        discarded = discarded
                    )
                    } catch (error: Throwable) {
                        recordStopError("Final Drive teardown failed", error)
                    } finally {
                        // This emergency pass is deliberately idempotent. It also covers an
                        // unexpected failure in the normal NonCancellable cleanup above.
                        if (inferenceEngine != null) {
                            runCatching { engine?.close() }
                                .onFailure { recordStopError("Could not close the detection engine", it) }
                        }
                        inferenceEngine = null
                        runCatching { workerJob?.cancel() }
                        runCatching { drainingChannel?.cancel() }
                        if (cameraManager != null) {
                            runCatching { manager?.closeImmediately() }
                                .onFailure { recordStopError("Emergency camera teardown failed", it) }
                        }
                        cameraManager = null
                        try {
                            finishTrackedJobs(
                                synchronized(segmentPersistJobs) { segmentPersistJobs.toList() },
                                "Video",
                                3_000L
                            )
                            finishTrackedJobs(
                                synchronized(sessionPersistJobs) { sessionPersistJobs.toList() },
                                "Drive summary",
                                3_000L
                            )
                        } catch (error: Throwable) {
                            recordStopError("Final persistence shutdown failed", error)
                        }
                        runCatching { locationProvider?.stopUpdates() }
                            .onFailure { recordStopError("Could not stop location updates", it) }
                        runCatching { releaseWakeLock() }
                            .onFailure { recordStopError("Could not release the Drive Mode wake lock", it) }

                        val baseSummary = completedSummary ?: DriveEndSummary(
                            endedSession, checkedCount, foundCount, alreadyCount,
                            error = synchronized(stopErrors) {
                                stopErrors.takeIf { it.isNotEmpty() }?.joinToString("; ")
                            },
                            discarded = discardDataOnStop
                        )
                        val callbacks: List<(DriveEndSummary) -> Unit>
                        val summary = synchronized(stopCallbacks) {
                            // Serialize the final summary with late Stop/clear requests. A
                            // callback arriving after this point receives completedStopSummary
                            // immediately instead of being left behind in stopCallbacks.
                            baseSummary.copy(discarded = baseSummary.discarded || discardDataOnStop).also {
                                completedStopSummary = it
                                callbacks = stopCallbacks.toList()
                                stopCallbacks.clear()
                            }
                        }
                        startCompletionLedger.record(endedStartRequest, summary)
                        runCatching { onDriveEndedListener?.invoke(summary) }
                            .onFailure { recordStopError("Drive-ended listener failed", it) }
                        callbacks.forEach { callback ->
                            runCatching { callback(summary) }
                                .onFailure { recordStopError("A Stop callback failed", it) }
                        }

                        locationProvider = null
                        workerJob = null
                        scanJob = null
                        cameraTransitionJob = null
                        try {
                            runCatching { stopForeground(STOP_FOREGROUND_REMOVE) }
                                .onFailure { recordStopError("Could not remove the Drive Mode notification", it) }
                        } finally {
                            runCatching { stopSelf() }
                                .onFailure { recordStopError("Could not stop the Drive Mode service", it) }
                        }
                    }
                }
            }
        }
    }

    private fun persistVideoSegment(segment: NativeVideoSegment) {
        val job = lifecycleScope.launch(Dispatchers.IO) {
            try {
                database.footageDao().insertSegment(FootageSegmentEntity(
                    sessionId = segment.sessionId,
                    filePath = segment.filePath,
                    startedAt = segment.startedAtMs / 1000,
                    endedAt = segment.endedAtMs / 1000,
                    durationMs = segment.durationMs,
                    bytes = segment.bytes,
                    errorCode = segment.errorCode,
                    complete = segment.complete
                ))
                withContext(Dispatchers.Main) {
                    segmentCount++
                    recordedBytes += segment.bytes
                    publish(statusText)
                }
            } catch (error: Exception) {
                File(segment.filePath).delete()
                val message = "A video clip could not be indexed and was discarded"
                synchronized(segmentPersistErrors) {
                    segmentPersistErrors.add(error.message?.let { "$message: $it" } ?: message)
                }
                withContext(Dispatchers.Main) {
                    recordingIssue = message
                    if (!isStopping) publish(message)
                }
            }
        }
        synchronized(segmentPersistJobs) { segmentPersistJobs.add(job) }
        job.invokeOnCompletion { synchronized(segmentPersistJobs) { segmentPersistJobs.remove(job) } }
    }

    private fun trackSessionPersist(job: Job) {
        synchronized(sessionPersistJobs) { sessionPersistJobs.add(job) }
        job.invokeOnCompletion { synchronized(sessionPersistJobs) { sessionPersistJobs.remove(job) } }
    }

    private fun recordSessionPersistError(error: Exception) {
        val message = error.message ?: "A drive summary write failed"
        synchronized(sessionPersistErrors) { sessionPersistErrors.add(message) }
    }

    private fun notificationStillVisible(): Boolean {
        val now = System.currentTimeMillis()
        if (now - lastNotificationCheckMs < 2_000L) return !notificationStopRequested
        lastNotificationCheckMs = now
        if (NotificationHelper.canShowDriveNotification(this)) return true
        if (!notificationStopRequested) {
            notificationStopRequested = true
            mainHandler.post {
                stopDriveSession("Stopped because Drive Mode notifications were disabled")
            }
        }
        return false
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
        sessionRunning, isPaused, pauseTransitioning, isStopping,
        sessionId.takeIf(String::isNotBlank), checkedCount,
        foundCount, alreadyCount, queuedCount, droppedCount, statusText,
        recordingEnabled, isRecording, videoSupported, segmentCount, recordedBytes, cameraActive,
        recordingIssue
    )

    private fun publish(text: String) {
        statusText = text
        if (sessionRunning || isStopping) {
            val notification = NotificationHelper.buildNotification(
                this, isPaused, checkedCount, foundCount, alreadyCount, statusText,
                isStopping = isStopping,
                isPausing = pauseTransitioning,
                recordingEnabled = recordingEnabled,
                isRecording = isRecording,
                videoSupported = videoSupported,
                recordingIssue = recordingIssue,
                cameraActive = cameraActive
            )
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
        cameraManager?.closeImmediately(); locationProvider?.stopUpdates(); inferenceEngine?.close(); releaseWakeLock()
        super.onDestroy()
    }
}
