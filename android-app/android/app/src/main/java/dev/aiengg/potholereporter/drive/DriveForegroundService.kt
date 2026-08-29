package dev.aiengg.potholereporter.drive

import android.Manifest
import android.annotation.SuppressLint
import android.app.AppOpsManager
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.Process
import android.os.SystemClock
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import androidx.camera.core.Preview
import androidx.core.content.ContextCompat
import dev.aiengg.potholereporter.db.DriveKeyframeEntity
import dev.aiengg.potholereporter.db.FootageSegmentEntity
import dev.aiengg.potholereporter.db.PotholeDatabase
import dev.aiengg.potholereporter.db.SessionEntity
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import org.json.JSONArray
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.ceil

data class BurstJob(
    val burstFrames: List<BurstFrame>, val primaryIndex: Int, val fix: GpsFix,
    val captureSeq: Int, val capturedAtMs: Long, val sourceOffsetMs: Long,
    val accessEpoch: Long, val capturedAtElapsedMs: Long = capturedAtMs,
    val keyframeId: Long? = null
)

data class DriveStatusSnapshot(
    val isRunning: Boolean, val isPaused: Boolean, val isPausing: Boolean,
    val isStopping: Boolean, val sessionId: String?,
    val checked: Int, val found: Int, val already: Int, val queued: Int,
    val dropped: Int, val status: String, val recordingEnabled: Boolean,
    val isRecording: Boolean, val videoSupported: Boolean,
    val segmentCount: Int, val recordedBytes: Long, val cameraActive: Boolean,
    val recordingIssue: String?, val keyframeCount: Int,
    val remainingMs: Long, val maxDurationMinutes: Int,
    val captureBlocked: Boolean = false, val captureIssue: String? = null,
    val captureStopped: Boolean = false,
    val isStarting: Boolean = false,
    val startRequestId: String? = null
)

data class DriveEndSummary(
    val sessionId: String,
    val checked: Int,
    val found: Int,
    val already: Int,
    val error: String? = null,
    val discarded: Boolean = false,
    val reason: String? = null,
    val startRequestId: String? = null
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
    @Volatile private var captureStopped = false
    @Volatile private var sessionRunning = false
    private var captureSeq = 0
    private val workLedger = NativeDriveWorkLedger()
    private val checkedCount: Int get() = workLedger.completedCount()
    @Volatile private var foundCount = 0
    @Volatile private var alreadyCount = 0
    @Volatile private var statusText = "Idle"
    @Volatile private var recordingEnabled = false
    @Volatile private var isRecording = false
    @Volatile private var videoSupported = true
    @Volatile private var segmentCount = 0
    @Volatile private var recordedBytes = 0L
    @Volatile private var cameraActive = false
    @Volatile private var cameraIssue: String? = "Waiting for camera"
    @Volatile private var cameraAccessBlocked = false
    @Volatile private var accessCameraReleased = false
    @Volatile private var captureBlocker = NativeCaptureBlocker.NONE
    @Volatile private var recordingIssue: String? = null
    @Volatile private var inferenceSuspendedReason: String? = null
    @Volatile private var keyframeCount = 0
    @Volatile private var pauseTransitioning = false
    @Volatile private var terminalStatusSealed = false
    @Volatile private var notificationStopRequested = false
    @Volatile private var lastNotificationCheckMs = 0L
    @Volatile private var notificationStartedElapsedMs = 0L
    @Volatile private var notificationMissingChecks = 0
    @Volatile private var notificationQueryFailureChecks = 0
    @Volatile private var discardDataOnStop = false
    @Volatile private var debugMode = false
    private val duplicateIds = ConcurrentHashMap.newKeySet<Long>()
    private var lastCapFix: GpsFix? = null
    private var lastCapTimeMs = 0L
    private var jobChannel: Channel<BurstJob>? = null
    private var scanJob: Job? = null
    private var workerJob: Job? = null
    private var cameraTransitionJob: Job? = null
    private var stopTeardownJob: Job? = null
    private var accessTransitionJob: Job? = null
    private var recordingTransitionJob: Job? = null
    private var criticalCameraRecoveryJob: Job? = null
    private var pendingCriticalCameraRecoveryReason: String? = null
    private var pendingCameraRecoveryIsUserState = false
    private var sessionLimitJob: Job? = null
    private var sessionLimitPolicy: DriveSessionLimitPolicy? = null
    private var pauseTimeoutJob: Job? = null
    private var pauseTimeoutPolicy = DrivePauseTimeoutPolicy()
    private var sessionLimitMinutes = DriveSessionLimitPolicy.DEFAULT_LIMIT_MINUTES
    @Volatile private var lastStatusDispatchElapsedMs = 0L
    @Volatile private var lastDispatchedStatusText = ""
    @Volatile private var lastInterlockRefreshPostElapsedMs = Long.MIN_VALUE
    @Volatile private var lastCameraAccessCheckElapsedMs = 0L
    @Volatile private var lastPrerequisiteAccessCheckElapsedMs = 0L
    @Volatile private var cameraPermissionGranted = false
    @Volatile private var lastCameraRestartAttemptElapsedMs = Long.MIN_VALUE
    private var cameraAppOps: AppOpsManager? = null
    private var cameraAppOpsListener: AppOpsManager.OnOpChangedListener? = null
    private val criticalCameraRetryBudget = NativeCameraRetryBudget(MAX_CRITICAL_CAMERA_RETRIES)
    private val captureAccessEpoch = NativeCaptureAccessEpoch()
    @Volatile private var lastLocationAccess: NativeLocationAccess? = null
    @Volatile private var deferredStatusDispatch = false
    private val mainHandler = Handler(Looper.getMainLooper())
    private val deferredStatusRunnable = Runnable {
        deferredStatusDispatch = false
        dispatchStatus()
    }
    private val pauseTransitionIntent = NativePauseTransitionIntent()
    private val pendingPauseTransitionCallbacks = mutableListOf<(DriveStatusSnapshot) -> Unit>()
    private val stopCallbacks = mutableListOf<(DriveEndSummary) -> Unit>()
    @Volatile private var completedStopSummary: DriveEndSummary? = null
    private val segmentPersistJobs = mutableListOf<Job>()
    private val segmentPersistErrors = mutableListOf<String>()
    private val sessionPersistJobs = mutableListOf<Job>()
    private var sessionPersistTail: Job? = null
    private val sessionPersistErrors = mutableListOf<String>()

    private data class StopCompletionClaim(
        val summary: DriveEndSummary,
        val callbacks: List<(DriveEndSummary) -> Unit>,
        val won: Boolean
    )

    private data class DriveNotificationState(
        val sessionId: String,
        val paused: Boolean,
        val stopping: Boolean,
        val pausing: Boolean,
        val checked: Int,
        val found: Int,
        val already: Int,
        val status: String,
        val recordingEnabled: Boolean,
        val recording: Boolean,
        val videoSupported: Boolean,
        val recordingIssue: String?,
        val cameraActive: Boolean,
        val captureStopped: Boolean
    )

    private var lastNotificationState: DriveNotificationState? = null

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
        const val EXTRA_MAX_DRIVE_MINUTES = "extra_max_drive_minutes"
        const val EXTRA_START_REQUEST_ID = "extra_start_request_id"
        const val EXTRA_STOP_REQUEST_ID = "extra_stop_request_id"
        const val EXTRA_STOP_DISCARD_DATA = "extra_stop_discard_data"
        private const val MAX_WAKELOCK_MS = 4 * 60 * 60 * 1000L
        private const val KEYFRAME_JPEG_QUALITY = 88
        private const val ROUTINE_STATUS_THROTTLE_MS = 1_000L
        private const val INTERLOCK_REFRESH_POST_MS = 250L
        private const val PREREQUISITE_ACCESS_RECHECK_MS = 1_000L
        private const val CAMERA_ACCESS_RECHECK_MS = 2_000L
        private const val CAMERA_RESTART_RETRY_MS = 3_000L
        private const val CRITICAL_CAMERA_RETRY_OBSERVE_MS = 1_000L
        private const val USER_CAMERA_STATE_RETRY_MS = 5_000L
        private const val MAX_CRITICAL_CAMERA_RETRIES = 3
        private const val ABNORMAL_TEARDOWN_JOIN_MS = 8_000L
        private const val START_ADMISSION_TTL_MS = 30_000L
        private val startCompletionLedger = DriveStartCompletionLedger()
        private val startAdmissionLock = Any()
        private var admittedStartRequestId: String? = null
        private var admittedStartElapsedMs = Long.MIN_VALUE
        private var admittedStartCancelled = false
        private var admittedStartDiscardData = false
        // LifecycleService cancels lifecycleScope after onDestroy. This process-owned
        // scope exists solely for a bounded ownership/session barrier when Android
        // destroys the service without going through explicit Stop.
        private val abnormalTeardownScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

        @Volatile var activeService: DriveForegroundService? = null
        var onStatusListener: ((DriveStatusSnapshot) -> Unit)? = null
        var onReportListener: ((Long, String, String?) -> Unit)? = null
        var onDriveEndedListener: ((DriveEndSummary) -> Unit)? = null
        fun status(): DriveStatusSnapshot = activeService?.snapshot()
            ?: synchronized(startAdmissionLock) {
                expireStaleAdmissionLocked()
                val pendingRequestId = admittedStartRequestId
                DriveStatusSnapshot(
                false, false, false, false, null, 0, 0, 0, 0, 0,
                if (pendingRequestId != null) "Starting camera and GPS" else "Idle",
                false, false, true, 0, 0, false, null, 0, 0,
                DriveSessionLimitPolicy.DEFAULT_LIMIT_MINUTES,
                isStarting = pendingRequestId != null,
                startRequestId = pendingRequestId
            )
            }
        fun activeStartRequestId(): String? = activeService?.startRequestId
        fun completedStartSummary(requestId: String): DriveEndSummary? =
            startCompletionLedger.summaryFor(requestId)

        /**
         * Process-owned start admission closes the gap between
         * startForegroundService() and onStartCommand(). A recreated WebView/plugin can
         * therefore distinguish an accepted start from true Idle instead of hiding a
         * camera session that Android is still launching.
         */
        fun admitStart(requestId: String): Boolean = synchronized(startAdmissionLock) {
            expireStaleAdmissionLocked()
            if (admittedStartRequestId != null) return@synchronized false
            admittedStartRequestId = requestId
            admittedStartElapsedMs = SystemClock.elapsedRealtime()
            admittedStartCancelled = false
            admittedStartDiscardData = false
            true
        }

        fun cancelStartAdmission(requestId: String, discardData: Boolean): Boolean =
            synchronized(startAdmissionLock) {
                expireStaleAdmissionLocked()
                if (admittedStartRequestId != requestId) return@synchronized false
                admittedStartCancelled = true
                admittedStartDiscardData = admittedStartDiscardData || discardData
                true
            }

        fun clearStartAdmission(requestId: String?) = synchronized(startAdmissionLock) {
            if (requestId != null && admittedStartRequestId == requestId) {
                admittedStartRequestId = null
                admittedStartElapsedMs = Long.MIN_VALUE
                admittedStartCancelled = false
                admittedStartDiscardData = false
            }
        }

        private fun cancelledStartDisposition(requestId: String?): Boolean? =
            synchronized(startAdmissionLock) {
                expireStaleAdmissionLocked()
                if (requestId == null || admittedStartRequestId != requestId ||
                    !admittedStartCancelled) null else admittedStartDiscardData
            }

        private fun hasAdmittedStart(): Boolean = synchronized(startAdmissionLock) {
            expireStaleAdmissionLocked()
            admittedStartRequestId != null
        }

        private fun pendingAdmittedStartRequestId(): String? = synchronized(startAdmissionLock) {
            expireStaleAdmissionLocked()
            admittedStartRequestId
        }

        private fun expireStaleAdmissionLocked() {
            if (admittedStartRequestId != null &&
                SystemClock.elapsedRealtime() - admittedStartElapsedMs > START_ADMISSION_TTL_MS) {
                admittedStartRequestId = null
                admittedStartElapsedMs = Long.MIN_VALUE
                admittedStartCancelled = false
                admittedStartDiscardData = false
            }
        }

        /** Called only while [NativeMediaFilesystemMutation.mutex] is held. */
        internal fun externalMediaDeletionRecorderIfReconciled(): ((Long) -> Unit)? =
            activeService?.cameraManager?.mediaDeletionRecorderIfReconciled()
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
        // Never interpret a process-restart null Intent as permission to reopen camera,
        // location, and networking with blank/default credentials.
        if (intent == null) {
            stopSelf(startId)
            return START_NOT_STICKY
        }
        when (intent.action ?: ACTION_START) {
            ACTION_START -> if (!sessionRunning && !isStopping) {
                startRequestId = intent?.getStringExtra(EXTRA_START_REQUEST_ID)
                    ?: "service-$startId-${System.currentTimeMillis()}"
                val cancelledDiscard = cancelledStartDisposition(startRequestId)
                if (cancelledDiscard != null) {
                    completeCancelledStart(startId, cancelledDiscard)
                } else try {
                    startDriveSession(
                        intent?.getStringExtra(EXTRA_API_KEY).orEmpty(),
                        intent?.getStringExtra(EXTRA_MODEL) ?: "gpt-5.6",
                        intent?.getStringExtra(EXTRA_DETAIL) ?: "high",
                        intent?.getStringExtra(EXTRA_LANGUAGE) ?: "en",
                        intent?.getBooleanExtra(EXTRA_DEBUG, false) ?: false,
                        intent?.getBooleanExtra(EXTRA_RECORD_VIDEO, false) ?: false,
                        (intent?.getIntExtra(
                            EXTRA_MAX_DRIVE_MINUTES,
                            DriveSessionLimitPolicy.DEFAULT_LIMIT_MINUTES
                        ) ?: DriveSessionLimitPolicy.DEFAULT_LIMIT_MINUTES).coerceIn(
                            DriveSessionLimitPolicy.MIN_LIMIT_MINUTES,
                            DriveSessionLimitPolicy.MAX_LIMIT_MINUTES
                        )
                    )
                    // startDriveSession marks the service running before this admission
                    // is released, so observers can never see an Idle gap in between.
                    clearStartAdmission(startRequestId)
                } catch (_: Exception) {
                    clearStartAdmission(startRequestId)
                    failDriveStart(startId)
                }
            }
            ACTION_PAUSE -> if (sessionRunning || isStopping) pauseDrive() else stopSelf(startId)
            ACTION_RESUME -> if (sessionRunning || isStopping) resumeDrive() else stopSelf(startId)
            ACTION_STOP -> handleStopIntent(intent, startId)
        }
        return START_NOT_STICKY
    }

    private fun completeCancelledStart(startId: Int, discardData: Boolean) {
        val requestId = startRequestId
        var summary = DriveEndSummary(
            sessionId = requestId?.let { "start-$it" }
                ?: "start-${System.currentTimeMillis()}",
            checked = 0,
            found = 0,
            already = 0,
            discarded = discardData,
            reason = "Drive start cancelled",
            startRequestId = requestId
        )
        if (!runCatching {
                NativeDriveEndSummaryStore.record(applicationContext, summary)
            }.getOrDefault(false)) {
            summary = summary.copy(error = "Could not persist the cancelled Drive start result")
            runCatching { NativeDriveEndSummaryStore.record(applicationContext, summary) }
        }
        completedStopSummary = summary
        startCompletionLedger.record(requestId, summary)
        NativeMediaReconciliationEpoch.invalidate()
        clearStartAdmission(requestId)
        terminalStatusSealed = true
        runCatching { onDriveEndedListener?.invoke(summary) }
        runCatching { stopForeground(STOP_FOREGROUND_REMOVE) }
        stopSelf(startId)
    }

    private fun failDriveStart(startId: Int) {
        val message = "Drive Mode could not start safely. Return to Pothole Reporter and tap Drive again."
        sessionRunning = false
        isPaused = false
        isStopping = false
        cameraActive = false
        cameraIssue = message
        statusText = message
        var summary = DriveEndSummary(
            sessionId = sessionId.ifBlank {
                startRequestId?.let { "start-$it" }
                    ?: "start-${System.currentTimeMillis()}"
            },
            checked = checkedCount,
            found = foundCount,
            already = alreadyCount,
            error = message,
            discarded = true,
            reason = "Start failed",
            startRequestId = startRequestId
        )
        val terminalStored = runCatching {
            NativeDriveEndSummaryStore.record(applicationContext, summary)
        }.getOrDefault(false)
        if (!terminalStored) {
            val storageError = "Could not persist the terminal Drive start result"
            summary = summary.copy(error = "$message; $storageError")
            runCatching { NativeDriveEndSummaryStore.record(applicationContext, summary) }
        }
        completedStopSummary = summary
        startCompletionLedger.record(startRequestId, summary)
        NativeMediaReconciliationEpoch.invalidate()
        clearStartAdmission(startRequestId)
        terminalStatusSealed = true
        runCatching { onDriveEndedListener?.invoke(summary) }
        runCatching { stopForeground(STOP_FOREGROUND_REMOVE) }
        stopSelf(startId)
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
            discarded = discardData,
            reason = "Stopped",
            startRequestId = activeRequest ?: requestedStart
        )
        startCompletionLedger.record(requestedStart, summary)
        clearStartAdmission(requestedStart)
        runCatching { stopForeground(STOP_FOREGROUND_REMOVE) }
        stopSelf(startId)
    }

    private fun startDriveSession(
        apiKey: String,
        model: String,
        detail: String,
        language: String,
        debug: Boolean,
        recordVideo: Boolean,
        maxDriveMinutes: Int
    ) {
        // A new producer lifetime invalidates any bridge inventory completed before it.
        // The epoch cannot be overwritten by an older reconciliation pass racing here.
        NativeMediaReconciliationEpoch.invalidate()
        captureAccessEpoch.invalidate()
        criticalCameraRecoveryJob?.cancel()
        criticalCameraRecoveryJob = null
        pendingCriticalCameraRecoveryReason = null
        pendingCameraRecoveryIsUserState = false
        criticalCameraRetryBudget.reset()
        lastLocationAccess = null
        cameraPermissionGranted = hasCameraPermission()
        lastPrerequisiteAccessCheckElapsedMs = 0L
        startedAtMs = System.currentTimeMillis()
        sessionId = startedAtMs.toString()
        isPaused = false; isStopping = false; captureStopped = false; sessionRunning = true
        captureSeq = 0; foundCount = 0; alreadyCount = 0
        workLedger.reset(); duplicateIds.clear()
        stopTeardownJob = null
        recordingEnabled = recordVideo; isRecording = false; videoSupported = true
        debugMode = debug
        segmentCount = 0; recordedBytes = 0L; pauseTransitioning = false
        terminalStatusSealed = false
        pauseTransitionIntent.clear()
        pendingPauseTransitionCallbacks.clear()
        keyframeCount = 0
        sessionLimitMinutes = maxDriveMinutes
        sessionLimitPolicy = DriveSessionLimitPolicy(SystemClock.elapsedRealtime(), maxDriveMinutes)
        pauseTimeoutJob?.cancel()
        pauseTimeoutJob = null
        pauseTimeoutPolicy = DrivePauseTimeoutPolicy()
        discardDataOnStop = false
        completedStopSummary = null
        synchronized(segmentPersistErrors) { segmentPersistErrors.clear() }
        synchronized(sessionPersistErrors) { sessionPersistErrors.clear() }
        synchronized(sessionPersistJobs) { sessionPersistTail = null }
        cameraActive = false
        cameraIssue = "Waiting for camera"
        startCameraAccessMonitoring()
        // CameraX must not bind until the location provider supplies a genuinely fresh
        // fix. This starts true because no camera use case has been opened yet.
        accessCameraReleased = true
        lastCameraRestartAttemptElapsedMs = Long.MIN_VALUE
        captureBlocker = NativeCaptureBlocker.NONE
        recordingIssue = null
        inferenceSuspendedReason = null
        notificationStopRequested = false; lastNotificationCheckMs = 0L
        notificationStartedElapsedMs = 0L; notificationMissingChecks = 0
        notificationQueryFailureChecks = 0
        lastNotificationState = null
        lastCapFix = null; lastCapTimeMs = 0L
        statusText = "Waiting for a fresh, precise GPS fix (30 m or better). Camera, detection, and video are paused."
        startForegroundNow()
        acquireWakeLock()

        inferenceEngine = NativeInferenceEngine(applicationContext, apiKey, model, detail, language, debug)
        jobChannel = Channel(
            // A remote model cannot consume raw camera bursts at capture cadence. Do not
            // retain a second ~10.5 MiB ARGB burst while one is already in inference;
            // every selected burst is durable before this rendezvous, so a failed send
            // safely defers model work to the bounded post-drive replay path.
            capacity = Channel.RENDEZVOUS,
            onUndeliveredElement = { item ->
                recycle(item); workLedger.deferLive()
            }
        )
        locationProvider = NativeDriveLocationProvider(
            context = this,
            onLocationUpdate = { mainHandler.post(::refreshCaptureInterlock) },
            onAvailabilityChange = { access ->
                mainHandler.post { noteLocationAccess(access) }
            }
        ).also { it.startUpdates(startedAtMs) }
        cameraManager = NativeDriveCameraManager(
            context = this,
            lifecycleOwner = this,
            initialVideoRecordingEnabled = recordVideo,
            sessionId = sessionId,
            onCameraStateChange = { available, reason ->
                mainHandler.post { noteCameraState(available, reason) }
            },
            onCameraRecoveryRequired = { action, reason ->
                mainHandler.post { handleCriticalCameraRecovery(action, reason) }
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
                if (!message.isNullOrBlank() && !isStopping) {
                    val interlock = captureInterlockDecision()
                    publish(if (interlock.canCapture) message else interlock.message)
                }
            },
            onSegmentFinalized = ::persistVideoSegment
        )

        // Evaluate only after both providers exist. With GPS disabled, unavailable, or
        // stale this publishes the explicit pause reason and leaves CameraX unopened.
        // A fresh location transition enters the controlled resume branch below.
        refreshCaptureInterlock()

        startScanLoop()
        startInferenceWorker()
        startSessionLimitLoop()
        persistSessionTracked("active", null)
    }

    private fun startForegroundNow() {
        val notification = NotificationHelper.buildNotification(
            this, sessionId, false, 0, 0, 0, statusText,
            isStopping = false,
            isPausing = false,
            recordingEnabled = recordingEnabled,
            isRecording = isRecording,
            videoSupported = videoSupported,
            recordingIssue = recordingIssue,
            cameraActive = cameraActive
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            startForeground(
                NotificationHelper.NOTIFICATION_ID, notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA or ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
            )
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            // Android 10 supports typed foreground services, but the camera type was
            // introduced only in Android 11. Passing the newer bit on API 29 is invalid.
            startForeground(
                NotificationHelper.NOTIFICATION_ID, notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
            )
        } else startForeground(NotificationHelper.NOTIFICATION_ID, notification)
        notificationStartedElapsedMs = SystemClock.elapsedRealtime()
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
                // The analyzer produces at most one retained source every 250 ms and GPS
                // admission is never faster than 500 ms. Polling at 100 ms stays ahead of
                // both without hammering permission/LocationManager/notification Binder.
                delay(100)
                val accessCheckNow = SystemClock.elapsedRealtime()
                if (accessCheckNow - lastPrerequisiteAccessCheckElapsedMs >=
                    PREREQUISITE_ACCESS_RECHECK_MS
                ) {
                    lastPrerequisiteAccessCheckElapsedMs = accessCheckNow
                    mainHandler.post(::refreshPrerequisiteAccessState)
                }
                if (accessCheckNow - lastCameraAccessCheckElapsedMs >= CAMERA_ACCESS_RECHECK_MS) {
                    lastCameraAccessCheckElapsedMs = accessCheckNow
                    mainHandler.post(::refreshCameraAccessState)
                }
                if (!notificationStillVisible()) continue
                if (isPaused) continue
                val loc = locationProvider ?: continue
                val interlock = captureInterlockDecision()
                if (!interlock.canCapture || accessCameraReleased) {
                    requestCaptureInterlockRefresh()
                    continue
                }
                val fix = loc.latestFix ?: continue
                val cam = cameraManager ?: continue
                // A deterministic API/configuration rejection cannot be repaired by
                // retrying the same request. Keep the transparent camera preview and
                // optional local video alive, but stop creating pending inference bursts
                // until the user starts a new Drive with corrected settings/app code.
                if (inferenceSuspendedReason != null) continue
                if (!cam.isCameraReady || !loc.shouldTriggerCapture(lastCapFix, lastCapTimeMs, fix)) continue
                val captureAdmissionElapsedMs = SystemClock.elapsedRealtime()
                val accessEpochBeforeCapture = captureAccessEpoch.snapshot()
                val burst = try {
                    cam.captureBurst()
                } catch (_: OutOfMemoryError) {
                    withContext(Dispatchers.Main.immediate) {
                        stopDriveSession(
                            "Stopped because this device ran out of image memory. Close other apps and start Drive again."
                        )
                    }
                    return@launch
                } ?: continue
                val primaryIndex = burst.second.takeIf { it in burst.first.indices }
                if (primaryIndex == null) {
                    recycleFrames(burst.first)
                    continue
                }
                val primaryFrame = burst.first[primaryIndex]
                val primaryCapturedAt = primaryFrame.capturedAtMs
                val validatedFix = validatedPostBurstFix(
                    accessEpochBeforeCapture,
                    cam,
                    primaryFrame.capturedAtElapsedMs,
                    loc.fixNearestToElapsed(primaryFrame.capturedAtElapsedMs) ?: loc.latestFix
                )
                if (validatedFix == null) {
                    // Camera/GPS/privacy state changed while CameraX was collecting the
                    // temporal burst. Nothing from it may reach JPEG, Room, or the model.
                    recycleFrames(burst.first)
                    mainHandler.post(::refreshCaptureInterlock)
                    continue
                }
                val sequence = ++captureSeq
                val sourceOffset = (primaryCapturedAt - startedAtMs).coerceAtLeast(0L)
                val baseItem = BurstJob(
                    burstFrames = burst.first,
                    primaryIndex = primaryIndex,
                    fix = validatedFix,
                    captureSeq = sequence,
                    capturedAtMs = primaryCapturedAt,
                    sourceOffsetMs = sourceOffset,
                    accessEpoch = accessEpochBeforeCapture,
                    capturedAtElapsedMs = primaryFrame.capturedAtElapsedMs
                )
                val keyframeId = try {
                    persistSelectedBurst(baseItem)
                } catch (_: OutOfMemoryError) {
                    recycle(baseItem)
                    withContext(Dispatchers.Main.immediate) {
                        stopDriveSession(
                            "Stopped because this device ran out of image memory. Close other apps and start Drive again."
                        )
                    }
                    return@launch
                } catch (_: Exception) {
                    null
                }
                if (keyframeId == null) {
                    val accessStillValid = isBurstAccessStillValid(baseItem)
                    recycle(baseItem)
                    if (!accessStillValid) {
                        // Access changed during persistence. No inference is allowed and
                        // the regular interlock owns recovery/pause messaging.
                        mainHandler.post(::refreshCaptureInterlock)
                    } else {
                        // Never send a selected burst to the model unless its source
                        // frames and capture metadata are already durable.
                        withContext(Dispatchers.Main.immediate) {
                            stopDriveSession(
                                "Stopped because saved-frame storage is unavailable. Free app storage and start Drive again."
                            )
                        }
                    }
                    continue
                }
                val item = baseItem.copy(keyframeId = keyframeId)
                if (!isBurstAccessStillValid(item)) {
                    // A transition during JPEG persistence keeps the saved frame pending
                    // for offline replay, but it must not start live inference now.
                    recycle(item)
                    mainHandler.post(::refreshCaptureInterlock)
                    continue
                }
                // Admission time controls cadence. The selected evidence frame may be
                // earlier inside the burst and must not pull the next capture forward.
                lastCapFix = validatedFix; lastCapTimeMs = captureAdmissionElapsedMs
                // A RENDEZVOUS receiver can resume before trySend returns. Increment
                // first and roll back failed hand-offs so the public counter cannot be
                // left at one after the worker has already consumed the burst.
                workLedger.admitLive()
                if (jobChannel?.trySend(item)?.isSuccess != true) {
                    workLedger.deferLive()
                    // The source JPEGs and metadata were committed before this point.
                    // Release only the raw Bitmaps; durable replay still owns the work.
                    recycle(item)
                }
                publish("Scanning live")
            }
        }
    }

    private fun validatedPostBurstFix(
        accessEpochBeforeCapture: Long,
        camera: NativeDriveCameraManager,
        primaryCapturedAtElapsedMs: Long,
        candidateFix: GpsFix?
    ): GpsFix? {
        // Notification disclosure is part of capture authorization. The Stop request
        // can be claimed on a Binder/worker thread while the main thread is busy; reject
        // the burst immediately instead of waiting for the posted teardown runnable.
        if (notificationStopRequested) return null
        val liveCameraAccessBlocked = effectiveCameraAppOpMode()
            ?.let(NativeCameraAccessPolicy::isBlocked)
            ?: cameraAccessBlocked
        val decision = captureInterlockDecision(liveCameraAccessBlocked)
        val running = sessionRunning
        val paused = isPaused
        val stopping = isStopping
        val cameraReady = camera.isCameraReady
        val cameraReleased = accessCameraReleased
        // Read this last: a transition concurrent with the state reads makes the token
        // differ and rejects the burst rather than pairing it with stale coordinates.
        val epochImmediatelyBeforeWork = captureAccessEpoch.snapshot()
        return NativeBurstAccessPolicy.validatedPostBurstFix(
            epochBeforeCapture = accessEpochBeforeCapture,
            epochImmediatelyBeforeWork = epochImmediatelyBeforeWork,
            sessionRunning = running,
            paused = paused,
            stopping = stopping,
            interlockCanCapture = decision.canCapture,
            cameraReady = cameraReady,
            cameraReleased = cameraReleased,
            primaryCapturedAtMs = primaryCapturedAtElapsedMs,
            postBurstFix = candidateFix
        )
    }

    private fun isBurstAccessStillValid(item: BurstJob): Boolean {
        val camera = cameraManager ?: return false
        return validatedPostBurstFix(
            item.accessEpoch,
            camera,
            item.capturedAtElapsedMs,
            item.fix
        ) != null
    }

    private fun noteLocationAccess(access: NativeLocationAccess) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post { noteLocationAccess(access) }
            return
        }
        if (lastLocationAccess != access) {
            lastLocationAccess = access
            captureAccessEpoch.invalidate()
        }
        refreshCaptureInterlock()
    }

    /**
     * Permission and LocationManager queries may cross Binder. Keep them off the 100 ms
     * frame scheduler while still detecting revoked/restored prerequisites within 1 s.
     */
    private fun refreshPrerequisiteAccessState() {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post(::refreshPrerequisiteAccessState)
            return
        }
        if (!sessionRunning || isStopping) return
        val cameraPermissionNow = hasCameraPermission()
        val locationAccessNow = locationProvider?.captureAccess() ?: NativeLocationAccess(
            permissionGranted = false,
            servicesEnabled = false,
            providerAvailable = false,
            freshFixAvailable = false
        )
        val cameraChanged = cameraPermissionNow != cameraPermissionGranted
        cameraPermissionGranted = cameraPermissionNow
        if (cameraChanged) captureAccessEpoch.invalidate()

        val locationChanged = lastLocationAccess != locationAccessNow
        if (locationChanged) {
            lastLocationAccess = locationAccessNow
            captureAccessEpoch.invalidate()
        }
        if (cameraChanged || locationChanged) refreshCaptureInterlock()
    }

    private fun noteCameraState(available: Boolean, reason: String?) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post { noteCameraState(available, reason) }
            return
        }
        // Repeated CameraState emissions are harmless invalidations and close the narrow
        // race where a critical code follows a PENDING_OPEN event with the same Boolean.
        captureAccessEpoch.invalidate()
        cameraActive = available
        cameraIssue = if (available) null else reason ?: "Camera access is unavailable"
        if (available) {
            pendingCriticalCameraRecoveryReason = null
            pendingCameraRecoveryIsUserState = false
            criticalCameraRetryBudget.reset()
        }
        refreshCaptureInterlock()
    }

    private fun handleCriticalCameraRecovery(
        action: NativeCameraRecoveryAction,
        reason: String
    ) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post { handleCriticalCameraRecovery(action, reason) }
            return
        }
        if (!sessionRunning || isPaused || isStopping) return
        captureAccessEpoch.invalidate()
        cameraActive = false
        cameraIssue = reason
        when (action) {
            NativeCameraRecoveryAction.WAIT_FOR_CAMERAX -> refreshCaptureInterlock()
            NativeCameraRecoveryAction.STOP_TERMINALLY -> {
                val terminal = "$reason Drive Mode cannot safely continue."
                publish(terminal)
                stopDriveSession("Stopped because $reason")
            }
            NativeCameraRecoveryAction.RELEASE_AND_RETRY_USER_STATE -> {
                pendingCriticalCameraRecoveryReason = reason
                pendingCameraRecoveryIsUserState = true
                scheduleCriticalCameraRecovery()
            }
            NativeCameraRecoveryAction.RELEASE_AND_RETRY -> {
                val currentBlocker = captureInterlockDecision()
                if (currentBlocker.shouldReleaseCamera) {
                    // Permission, privacy, and GPS transitions already have a controlled
                    // release/resume path. Do not race it with a second CameraX teardown.
                    pendingCriticalCameraRecoveryReason = null
                    pendingCameraRecoveryIsUserState = false
                    refreshCaptureInterlock()
                    return
                }
                pendingCriticalCameraRecoveryReason = reason
                pendingCameraRecoveryIsUserState = false
                scheduleCriticalCameraRecovery()
            }
        }
    }

    private fun scheduleCriticalCameraRecovery() {
        if (criticalCameraRecoveryJob?.isActive == true ||
            !sessionRunning || isPaused || isStopping) return
        criticalCameraRecoveryJob = lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
            try {
                while (isActive && sessionRunning && !isPaused && !isStopping) {
                    val reason = pendingCriticalCameraRecoveryReason ?: return@launch
                    pendingCriticalCameraRecoveryReason = null
                    val userStateRecovery = pendingCameraRecoveryIsUserState
                    pendingCameraRecoveryIsUserState = false
                    val attempt = if (userStateRecovery) null
                    else criticalCameraRetryBudget.nextAttemptOrNull()
                    if (!userStateRecovery && attempt == null) {
                        val terminal =
                            "Camera remained unavailable after $MAX_CRITICAL_CAMERA_RETRIES recovery attempts."
                        cameraIssue = terminal
                        publish(terminal)
                        stopDriveSession("Stopped because $terminal")
                        return@launch
                    }
                    val manager = cameraManager
                    if (manager == null) {
                        stopDriveSession("Stopped because the camera service was unavailable")
                        return@launch
                    }

                    captureAccessEpoch.invalidate()
                    accessCameraReleased = true
                    cameraActive = false
                    cameraIssue = if (userStateRecovery) {
                        "$reason Waiting for camera access to return."
                    } else {
                        "$reason Retrying camera ($attempt/$MAX_CRITICAL_CAMERA_RETRIES)."
                    }
                    publish(cameraIssue!!)
                    try {
                        manager.pauseCameraSafely()
                    } catch (cancelled: CancellationException) {
                        throw cancelled
                    } catch (_: Exception) {
                        pendingCriticalCameraRecoveryReason =
                            "Camera could not be released for recovery."
                        pendingCameraRecoveryIsUserState = userStateRecovery
                        delay(CAMERA_RESTART_RETRY_MS)
                        continue
                    }

                    delay(CAMERA_RESTART_RETRY_MS)
                    if (!sessionRunning || isPaused || isStopping) return@launch
                    val decision = captureInterlockDecision()
                    if (decision.blocker != NativeCaptureBlocker.CAMERA_UNAVAILABLE) {
                        // Leave CameraX released. The ordinary interlock will reopen it only
                        // after camera permission, privacy access, and a fresh GPS fix return.
                        publish(decision.message)
                        refreshCaptureInterlock()
                        return@launch
                    }

                    captureAccessEpoch.invalidate()
                    accessCameraReleased = false
                    var bindAccepted: Boolean? = null
                    manager.resumeCamera { ready ->
                        bindAccepted = ready
                        if (!ready && sessionRunning && !isPaused && !isStopping) {
                            captureAccessEpoch.invalidate()
                            accessCameraReleased = true
                            pendingCriticalCameraRecoveryReason =
                                "Camera could not bind during recovery."
                            pendingCameraRecoveryIsUserState = userStateRecovery
                        }
                    }
                    if (bindAccepted == false) {
                        delay(CAMERA_RESTART_RETRY_MS)
                        continue
                    }

                    delay(CRITICAL_CAMERA_RETRY_OBSERVE_MS)
                    if (cameraActive) {
                        pendingCriticalCameraRecoveryReason = null
                        criticalCameraRetryBudget.reset()
                        return@launch
                    }
                    if (pendingCriticalCameraRecoveryReason != null) continue

                    if (userStateRecovery) {
                        // Public Android APIs expose toggle support, not its current state.
                        // Keep a device-policy/privacy denial as a visible pause/retry;
                        // never convert the user's camera toggle into a terminal quit.
                        pendingCriticalCameraRecoveryReason = reason
                        pendingCameraRecoveryIsUserState = true
                        delay(USER_CAMERA_STATE_RETRY_MS)
                        continue
                    }

                    // A successful bind may legitimately remain PENDING_OPEN while a call
                    // owns the camera. CameraX's documented recoverable path stays bound.
                    return@launch
                }
            } finally {
                criticalCameraRecoveryJob = null
                if (pendingCriticalCameraRecoveryReason != null &&
                    sessionRunning && !isPaused && !isStopping) {
                    mainHandler.post(::scheduleCriticalCameraRecovery)
                }
            }
        }
    }

    private fun hasCameraPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED

    private fun startCameraAccessMonitoring() {
        stopCameraAccessMonitoring()
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
            cameraAccessBlocked = false
            return
        }
        val manager = getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val listener = AppOpsManager.OnOpChangedListener { operation, changedPackage ->
            if (operation == AppOpsManager.OPSTR_CAMERA &&
                (changedPackage == null || changedPackage == packageName)) {
                mainHandler.post(::refreshCameraAccessState)
            }
        }
        cameraAppOps = manager
        cameraAppOpsListener = listener
        runCatching {
            manager.startWatchingMode(AppOpsManager.OPSTR_CAMERA, packageName, listener)
        }
        refreshCameraAccessState()
    }

    private fun stopCameraAccessMonitoring() {
        val listener = cameraAppOpsListener
        if (listener != null) runCatching { cameraAppOps?.stopWatchingMode(listener) }
        cameraAppOpsListener = null
        cameraAppOps = null
    }

    @Suppress("DEPRECATION")
    private fun effectiveCameraAppOpMode(): Int? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return null
        val manager = cameraAppOps
            ?: (getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager)
        return runCatching {
            if (Build.VERSION.SDK_INT >= 36) {
                manager.checkOpNoThrow(AppOpsManager.OPSTR_CAMERA, Process.myUid(), packageName)
            } else {
                manager.unsafeCheckOpNoThrow(
                    AppOpsManager.OPSTR_CAMERA,
                    Process.myUid(),
                    packageName
                )
            }
        }.getOrNull()
    }

    private fun refreshCameraAccessState() {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post(::refreshCameraAccessState)
            return
        }
        val blocked = effectiveCameraAppOpMode()?.let(NativeCameraAccessPolicy::isBlocked) ?: false
        val changed = blocked != cameraAccessBlocked
        if (changed) captureAccessEpoch.invalidate()
        cameraAccessBlocked = blocked
        if (changed && sessionRunning && !isStopping) refreshCaptureInterlock()
    }

    private fun captureInterlockDecision(
        cameraAccessBlockedNow: Boolean = cameraAccessBlocked
    ): NativeCaptureDecision {
        val location = lastLocationAccess ?: NativeLocationAccess(
            permissionGranted = false,
            servicesEnabled = false,
            providerAvailable = false,
            freshFixAvailable = false
        )
        return NativeCaptureInterlock.evaluate(
            cameraPermissionGranted = cameraPermissionGranted,
            cameraReady = cameraActive,
            cameraIssue = cameraIssue,
            location = location,
            cameraAccessBlocked = cameraAccessBlockedNow
        )
    }

    /**
     * Gates only expensive analyzer conversion. Preview and optional local video remain
     * bound and visible while GPS is stale or deterministic API configuration is blocked.
     */
    private fun updateAnalyzerSampling(decision: NativeCaptureDecision) {
        val enabled = sessionRunning && !isStopping && !isPaused &&
            !accessCameraReleased && decision.canCapture &&
            inferenceSuspendedReason == null
        cameraManager?.setAnalyzerSamplingEnabled(enabled)
    }

    /**
     * Keeps CameraX closed whenever a frame cannot carry a current GPS fix. Both the
     * persistent notification and the foreground HUD receive the same explicit reason.
     */
    @Synchronized
    private fun requestCaptureInterlockRefresh() {
        val now = SystemClock.elapsedRealtime()
        if (lastInterlockRefreshPostElapsedMs != Long.MIN_VALUE &&
            now - lastInterlockRefreshPostElapsedMs < INTERLOCK_REFRESH_POST_MS
        ) return
        lastInterlockRefreshPostElapsedMs = now
        mainHandler.post(::refreshCaptureInterlock)
    }

    private fun refreshCaptureInterlock() {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post(::refreshCaptureInterlock)
            return
        }
        if (!sessionRunning || isStopping || isPaused) return

        var decision = captureInterlockDecision()
        val priorBlocker = captureBlocker
        if (priorBlocker in setOf(
                NativeCaptureBlocker.LOCATION_PERMISSION,
                NativeCaptureBlocker.LOCATION_SERVICES
            ) && decision.blocker !in setOf(
                NativeCaptureBlocker.LOCATION_PERMISSION,
                NativeCaptureBlocker.LOCATION_SERVICES
            )) {
            // A request registered before permission/services were restored may never
            // receive callbacks. Re-register once on the transition, then wait for a
            // genuinely fresh fix rather than reopening the camera on stale coordinates.
            locationProvider?.restartUpdates()
            decision = captureInterlockDecision()
        }
        captureBlocker = decision.blocker
        if (priorBlocker != decision.blocker) captureAccessEpoch.invalidate()
        updateAnalyzerSampling(decision)

        val prerequisitesRestored = decision.blocker == NativeCaptureBlocker.CAMERA_UNAVAILABLE
        if (accessCameraReleased && prerequisitesRestored &&
            accessTransitionJob?.isActive != true &&
            criticalCameraRecoveryJob?.isActive != true) {
            val now = SystemClock.elapsedRealtime()
            val sinceLastAttempt = if (lastCameraRestartAttemptElapsedMs == Long.MIN_VALUE) {
                Long.MAX_VALUE
            } else now - lastCameraRestartAttemptElapsedMs
            if (sinceLastAttempt in 0 until CAMERA_RESTART_RETRY_MS) return
            lastCameraRestartAttemptElapsedMs = now
            captureAccessEpoch.invalidate()
            accessCameraReleased = false
            cameraIssue = "Camera restarting after GPS/location recovery"
            publish(cameraIssue!!)
            val manager = cameraManager
            if (manager == null) {
                accessCameraReleased = true
                publish("Camera could not restart. Detection and video remain paused.")
                return
            }
            manager.resumeCamera { ready ->
                if (!ready && sessionRunning && !isPaused && !isStopping) {
                    // An immediate bind/configuration failure cannot be left stuck in the
                    // non-released state. Retry with a small backoff; CameraX's ordinary
                    // PENDING_OPEN path remains bound and recovers without this branch.
                    accessCameraReleased = true
                    cameraIssue = "Camera could not restart. Detection and video remain paused."
                    publish(cameraIssue!!)
                    mainHandler.postDelayed(::refreshCaptureInterlock, CAMERA_RESTART_RETRY_MS)
                }
            }
            return
        }

        if (decision.canCapture && !accessCameraReleased) {
            if (priorBlocker != NativeCaptureBlocker.NONE || statusText != "Scanning live") {
                publish("Scanning live")
            }
            return
        }

        publish(decision.message)
        if (!decision.shouldReleaseCamera || accessCameraReleased ||
            accessTransitionJob?.isActive == true) return

        val manager = cameraManager ?: return
        captureAccessEpoch.invalidate()
        accessCameraReleased = true
        accessTransitionJob = lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
            try {
                manager.pauseCameraSafely()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                // CameraX may already have closed itself after permission/privacy loss.
            } finally {
                cameraActive = false
                if (!isStopping && !isPaused) publish(captureInterlockDecision().message)
                mainHandler.post(::refreshCaptureInterlock)
            }
        }
    }

    /** Saves every selected burst as three bounded, temporally distinct JPEGs. */
    private suspend fun persistSelectedBurst(item: BurstJob): Long? {
        if (!isBurstAccessStillValid(item)) return null
        val primaryIndex = item.primaryIndex.takeIf { it in item.burstFrames.indices } ?: return null
        val contextIndexes = NativeKeyframeFiles.selectTemporalContextIndexes(
            // Wall time can jump while a burst is being captured (NTP/manual clock
            // correction). Camera chronology must use the monotonic capture clock.
            item.burstFrames.map(BurstFrame::capturedAtElapsedMs),
            primaryIndex
        ) ?: return null
        val frame = item.burstFrames[primaryIndex]
        val sourceIndexes = listOf(primaryIndex) + contextIndexes
        return withContext(Dispatchers.IO) {
            val encodedBySourceIndex = LinkedHashMap<Int, ByteArray>(sourceIndexes.size)
            for (sourceIndex in sourceIndexes) {
                val jpeg = try {
                    FrameQualityEvaluator.bitmapToBoundedJpegBytes(
                        item.burstFrames[sourceIndex].bitmap,
                        NativeStoredImagePolicy.MAX_KEYFRAME_IMAGE_BYTES,
                        NativeStoredImagePolicy.KEYFRAME_MAX_DIMENSION,
                        KEYFRAME_JPEG_QUALITY
                    )
                } catch (_: Exception) {
                    return@withContext null
                } ?: return@withContext null
                if (jpeg.isEmpty()) return@withContext null
                encodedBySourceIndex[sourceIndex] = jpeg
            }
            val expectedBytes = encodedBySourceIndex.values.sumOf { it.size.toLong() }
            if (encodedBySourceIndex.size != 3 ||
                expectedBytes <= 0L || expectedBytes > NativeMediaStorageQuota.MAX_KEYFRAME_BYTES
            ) return@withContext null

            val storage = cameraManager ?: return@withContext null
            val reservation = storage.reserveMediaBytes(expectedBytes)
                ?: return@withContext null
            val directory = File(filesDir, "footage/$sessionId/keyframes")
            if (!directory.exists() && !directory.mkdirs()) {
                storage.releaseMediaBytes(reservation)
                return@withContext null
            }
            val plan = NativeKeyframeFiles.writePlan(
                directory,
                item.captureSeq,
                primaryIndex,
                contextIndexes
            )
            // writePlan canonicalizes context filenames by source index. Match each
            // file to its own source bytes instead of zipping it with the caller's
            // selection order, which could silently reverse evidence after clock skew.
            val encodedFrames = (listOf(plan.primarySourceIndex) + plan.contextSourceIndexes)
                .map { sourceIndex -> encodedBySourceIndex[sourceIndex] ?: return@withContext null }
            val temporaryFiles = plan.files.map { File(directory, ".${it.name}.tmp") }
            var quotaCommitted = false
            var rowCommitted = false
            try {
                temporaryFiles.zip(encodedFrames).forEach { (temporary, jpeg) ->
                    FileOutputStream(temporary).use { it.write(jpeg) }
                }
                if (temporaryFiles.zip(encodedFrames).any { (temporary, jpeg) ->
                        temporary.length() != jpeg.size.toLong()
                    }
                ) throw IllegalStateException("Could not write the complete saved-frame burst")

                // Commit context first and the primary (the Room row's path) last.
                // A crash can therefore never leave a seemingly complete primary while
                // either temporal view is still only a temporary file.
                val contextCommitted = temporaryFiles.drop(1).zip(plan.files.drop(1))
                    .all { (temporary, destination) ->
                        commitKeyframeFile(temporary, destination)
                    }
                if (!contextCommitted ||
                    !commitKeyframeFile(temporaryFiles.first(), plan.primaryFile)
                ) throw IllegalStateException("Could not commit saved-frame burst")
                val actualBytes = plan.files.sumOf(File::length)
                if (actualBytes != expectedBytes ||
                    actualBytes > NativeMediaStorageQuota.MAX_KEYFRAME_BYTES ||
                    !storage.commitMediaBytes(reservation, actualBytes)
                ) {
                    throw IllegalStateException("Saved-frame burst exceeded its storage reservation")
                }
                quotaCommitted = true
                if (!isBurstAccessStillValid(item)) {
                    throw IllegalStateException("Capture access changed before saved-frame indexing")
                }
                val id = withContext(NonCancellable) {
                    val insertedId = database.driveKeyframeDao().insertKeyframe(
                        DriveKeyframeEntity(
                            sessionId = sessionId,
                            captureSeq = item.captureSeq,
                            filePath = plan.primaryFile.absolutePath,
                            // The burst takes several hundred milliseconds. Anchor replay
                            // context to the selected sharp frame, not to the instant before
                            // the burst began, so before/evidence/after ordering is truthful.
                            capturedAtMs = frame.capturedAtMs,
                            sourceOffsetMs = (frame.capturedAtMs - startedAtMs).coerceAtLeast(0L),
                            lat = item.fix.lat,
                            lng = item.fix.lng,
                            gpsAccuracy = item.fix.accuracy,
                            speedMps = item.fix.speedMps,
                            heading = item.fix.heading,
                            width = frame.bitmap.width,
                            height = frame.bitmap.height,
                            bytes = actualBytes
                        )
                    )
                    rowCommitted = true
                    keyframeCount++
                    insertedId
                }
                id
            } catch (cancelled: CancellationException) {
                if (!rowCommitted) cleanFailedKeyframeWrite(
                    storage, reservation, plan.files, temporaryFiles, quotaCommitted
                )
                throw cancelled
            } catch (_: Exception) {
                if (!rowCommitted) {
                    cleanFailedKeyframeWrite(
                        storage, reservation, plan.files, temporaryFiles, quotaCommitted
                    )
                    null
                } else throw IllegalStateException("Saved-frame row committed before a later failure")
            }
        }
    }

    private fun commitKeyframeFile(temporary: File, destination: File): Boolean =
        temporary.renameTo(destination) || runCatching {
            temporary.copyTo(destination, overwrite = true)
            NativeRetryableFileCleanup.deleteVerified(temporary) && destination.isFile
        }.getOrDefault(false)

    private fun cleanFailedKeyframeWrite(
        storage: NativeDriveCameraManager,
        reservation: NativeMediaStorageQuota.Reservation,
        committedFiles: List<File>,
        temporaryFiles: List<File>,
        quotaCommitted: Boolean
    ) {
        val cleanup = NativeKeyframeFailureCleanup.cleanup(
            accountedFiles = if (quotaCommitted) committedFiles else emptyList(),
            unaccountedFiles = if (quotaCommitted) temporaryFiles
                else committedFiles + temporaryFiles
        )
        if (quotaCommitted) {
            storage.noteDeletedMediaBytes(cleanup.removedAccountedBytes)
            storage.noteUnexpectedMediaBytes(cleanup.remainingUnaccountedBytes)
        } else {
            // Charge surviving files before releasing the reservation. This may briefly
            // over-count during a concurrent reserve, but can never permit a cap overrun.
            storage.noteUnexpectedMediaBytes(cleanup.remainingUnaccountedBytes)
            storage.releaseMediaBytes(reservation)
        }
    }

    private fun startInferenceWorker() {
        workerJob?.cancel()
        val channel = jobChannel ?: return
        workerJob = lifecycleScope.launch(Dispatchers.IO) {
            for (item in channel) {
                workLedger.consumeLive()
                var analysisCompleted = false
                var uncommittedReportPhoto: String? = null
                var uncommittedRepairPhoto: String? = null
                try {
                    // Queued work may outlive the camera/GPS state that produced it. Recheck
                    // at the worker boundary before even a local repair lookup or model call.
                    if (!isBurstAccessStillValid(item)) {
                        mainHandler.post(::refreshCaptureInterlock)
                        continue
                    }
                    // One burst can already be buffered when a deterministic 4xx arrives.
                    // Leave its durable keyframe pending for a later corrected replay, but
                    // never issue the same known-invalid request again in this Drive.
                    if (inferenceSuspendedReason != null) continue
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
                        onEvidenceSaved = { uncommittedReportPhoto = it },
                        requireCompleteVerdict = repairCandidate != null
                    )
                    workLedger.completeCheck()
                    val report = outcome?.reportEntity
                    if (outcome?.accepted == true && report != null) {
                        uncommittedReportPhoto = report.photoFullPath ?: report.photoPath
                        var result: DedupeResult? = null
                        withContext(NonCancellable) {
                            result = dedupeEngine?.checkAndCommitReport(report, outcome.sightings)
                            // A new canonical row or an incomplete-evidence duplicate that
                            // adopted this candidate now durably owns the photo. Transfer cleanup
                            // ownership inside the non-cancellable boundary so prompt cancellation
                            // cannot delete evidence after the transaction commits.
                            if (result?.let { !it.isDuplicate || it.candidateEvidenceAdopted } == true) {
                                uncommittedReportPhoto = null
                            }
                        }
                        val committedResult = result
                        if (committedResult?.isDuplicate == true) {
                            committedResult.existingReportId?.let(duplicateIds::add); alreadyCount = duplicateIds.size
                        } else if (committedResult != null) {
                            foundCount++
                            val reportId = committedResult.existingReportId ?: 0
                            mainHandler.post {
                                onReportListener?.invoke(reportId, report.damageType ?: "pothole_cavity", report.address)
                            }
                        }
                    } else if (repairCandidate != null && outcome?.assessment?.let { assessment ->
                            // This is a complete verdict because candidate presence disabled
                            // early stream cancellation above. Even so, it is merely the gate
                            // for a separate before/after comparison, never repair proof.
                            !assessment.isPothole &&
                                !assessment.looksLikeSpeedBreaker &&
                                !assessment.reportable &&
                                assessment.damageType == "none" &&
                                assessment.assessment == "absent" &&
                                assessment.imageQuality == "usable" &&
                                !assessment.hasLocalizedCavity &&
                                !assessment.hasBrokenEdgeOrRim &&
                                !assessment.hasDepthOrSurfaceLoss &&
                                assessment.decision == "reject"
                        } == true) {
                        val verification = inferenceEngine?.verifyRepair(
                            repairCandidate,
                            item.burstFrames,
                            item.primaryIndex,
                            sessionId,
                            item.captureSeq,
                            onEvidenceSaved = { uncommittedRepairPhoto = it }
                        )
                        if (verification != null) {
                            uncommittedRepairPhoto = verification.currentPhotoPath
                            val sourceEventKey =
                                "repair:$sessionId:${item.captureSeq}:${repairCandidate.reportId}"
                            withContext(NonCancellable) {
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
                                // Keep the ownership transition in the same cancellation
                                // boundary as the Room transaction.
                                if (queued) uncommittedRepairPhoto = null
                            }
                        }
                    }
                    // Only a complete detector verdict closes durable replay. A null or
                    // deliberately incomplete result stays pending, as does any local
                    // deduplication/report-persistence failure thrown above.
                    analysisCompleted = outcome?.analyzed == true
                    publish(if (isStopping) "Finishing queued detections" else "Scanning live")
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (_: OutOfMemoryError) {
                    publish("Detection stopped because device image memory is exhausted")
                    if (!isStopping) withContext(Dispatchers.Main) {
                        stopDriveSession(
                            "Stopped because this device ran out of image memory. Close other apps and start Drive again."
                        )
                    }
                } catch (error: NativeInferenceException) {
                    val message = error.message ?: "Detection temporarily failed"
                    if (error.suspendInference) {
                        inferenceSuspendedReason = message
                        cameraManager?.setAnalyzerSamplingEnabled(false)
                        publish(inferenceSuspendedStatus(message))
                    } else {
                        publish(if (error.fatal) message else "$message · frame saved for later")
                        if (error.fatal && !isStopping) withContext(Dispatchers.Main) {
                            stopDriveSession(message)
                        }
                        if (!error.fatal) error.retryAfterMs?.let { delay(it) }
                    }
                } catch (error: Exception) {
                    publish("Detection retrying: ${error.message ?: "temporary error"}")
                } finally {
                    uncommittedReportPhoto?.let {
                        NativeReportEvidenceStorage.deleteVerified(File(it))
                    }
                    uncommittedRepairPhoto?.let {
                        NativeReportEvidenceStorage.deleteVerified(File(it))
                    }
                    if (analysisCompleted) item.keyframeId?.let { keyframeId ->
                        runCatching { database.driveKeyframeDao().markAnalyzed(keyframeId) }
                    }
                    recycle(item)
                }
            }
        }
    }

    private fun startSessionLimitLoop() {
        sessionLimitJob?.cancel()
        sessionLimitJob = lifecycleScope.launch {
            var lastShownMinutes = Int.MAX_VALUE
            while (isActive && sessionRunning && !isStopping) {
                delay(1_000L)
                if (!notificationStillVisible()) continue
                if (isPaused) continue
                val remaining = remainingDriveMs()
                if (remaining <= 0L) {
                    stopDriveSession("Stopped after the $sessionLimitMinutes-minute battery limit")
                    return@launch
                }
                val minutes = ceil(remaining / 60_000.0).toInt()
                if (minutes != lastShownMinutes) {
                    lastShownMinutes = minutes
                    publish(statusText)
                }
            }
        }
    }

    @Synchronized
    private fun remainingDriveMs(): Long = sessionLimitPolicy
        ?.remainingMs(SystemClock.elapsedRealtime()) ?: 0L

    @Synchronized
    private fun pauseSessionLimit() {
        sessionLimitPolicy?.pause(SystemClock.elapsedRealtime())
    }

    @Synchronized
    private fun resumeSessionLimit() {
        sessionLimitPolicy?.resume(SystemClock.elapsedRealtime())
    }

    private fun startPauseTimeout() {
        val now = SystemClock.elapsedRealtime()
        pauseTimeoutPolicy.beginPause(now)
        pauseTimeoutJob?.cancel()
        val delayMs = pauseTimeoutPolicy.remainingMs(now)
            ?: DrivePauseTimeoutPolicy.DEFAULT_TIMEOUT_MS
        pauseTimeoutJob = lifecycleScope.launch {
            delay(delayMs)
            if (sessionRunning && isPaused && !isStopping &&
                pauseTimeoutPolicy.expired(SystemClock.elapsedRealtime())
            ) {
                stopDriveSession(
                    "Stopped after ${DrivePauseTimeoutPolicy.DEFAULT_TIMEOUT_MINUTES} minutes paused"
                )
            }
        }
    }

    private fun clearPauseTimeout() {
        pauseTimeoutJob?.cancel()
        pauseTimeoutJob = null
        pauseTimeoutPolicy.clearPause(SystemClock.elapsedRealtime())
    }

    /**
     * Pause finalizes the active recording before releasing CameraX. A Resume that arrives
     * during that bounded operation is an intent for the state after it, not a no-op.
     */
    private fun queuePauseTransitionIntent(
        paused: Boolean,
        onComplete: ((DriveStatusSnapshot) -> Unit)?
    ): Boolean {
        if (!pauseTransitioning) return false
        pauseTransitionIntent.request(paused)
        if (onComplete != null) pendingPauseTransitionCallbacks.add(onComplete)
        return true
    }

    fun pauseDrive(onComplete: ((DriveStatusSnapshot) -> Unit)? = null) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post { pauseDrive(onComplete) }
            return
        }
        if (!sessionRunning || isStopping) {
            onComplete?.invoke(snapshot())
            return
        }
        if (queuePauseTransitionIntent(paused = true, onComplete)) return
        if (isPaused) {
            onComplete?.invoke(snapshot())
            return
        }
        pauseTransitionIntent.clear()
        pendingPauseTransitionCallbacks.clear()
        pauseSessionLimit()
        captureAccessEpoch.invalidate()
        pendingCriticalCameraRecoveryReason = null
        pendingCameraRecoveryIsUserState = false
        val criticalRecovery = criticalCameraRecoveryJob
        criticalRecovery?.cancel()
        val accessTransition = accessTransitionJob
        val recordingTransition = recordingTransitionJob
        isPaused = true; pauseTransitioning = true; scanJob?.cancel()
        startPauseTimeout()
        accessTransition?.cancel()
        recordingTransition?.cancel()
        locationProvider?.stopUpdates(); releaseWakeLock()
        publish("Pausing safely")
        cameraTransitionJob = lifecycleScope.launch {
            try {
                criticalRecovery?.join()
                accessTransition?.join()
                recordingTransition?.join()
                cameraManager?.pauseCameraSafely()
            } catch (error: Exception) {
                if (error !is CancellationException) {
                    recordingIssue = error.message ?: "Camera could not finish pausing cleanly"
                }
            } finally {
                // Even an OEM unbind failure means capture is no longer trustworthy.
                // Resume must enter the controlled re-open path instead of stranding the
                // session in a paused-but-not-released state.
                accessCameraReleased = true
                pauseTransitioning = false
                if (sessionRunning && !isStopping && !terminalStatusSealed) {
                    publish("Paused")
                    persistSessionTracked("paused", null)
                }
                val desiredPaused = pauseTransitionIntent.take()
                val queuedCallbacks = pendingPauseTransitionCallbacks.toList()
                pendingPauseTransitionCallbacks.clear()
                runCatching { onComplete?.invoke(snapshot()) }
                if (desiredPaused == false && sessionRunning && !isStopping) {
                    resumeDrive { resumed ->
                        queuedCallbacks.forEach { callback ->
                            runCatching { callback(resumed) }
                        }
                    }
                } else {
                    val settled = snapshot()
                    queuedCallbacks.forEach { callback ->
                        runCatching { callback(settled) }
                    }
                }
            }
        }
    }

    fun resumeDrive(onComplete: ((DriveStatusSnapshot) -> Unit)? = null) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post { resumeDrive(onComplete) }
            return
        }
        if (!sessionRunning || isStopping) {
            onComplete?.invoke(snapshot())
            return
        }
        if (queuePauseTransitionIntent(paused = false, onComplete)) return
        if (!isPaused) {
            onComplete?.invoke(snapshot())
            return
        }
        if (!NotificationHelper.canShowDriveNotification(this)) {
            stopDriveSession("Stopped because Drive Mode notifications were disabled")
            onComplete?.invoke(snapshot())
            return
        }
        clearPauseTimeout()
        resumeSessionLimit()
        captureAccessEpoch.invalidate()
        pendingCriticalCameraRecoveryReason = null
        pendingCameraRecoveryIsUserState = false
        criticalCameraRetryBudget.reset()
        isPaused = false; acquireWakeLock(); locationProvider?.resumeUpdates()
        refreshCameraAccessState()
        cameraActive = false
        cameraIssue = "Waiting for a fresh, precise GPS fix"
        publish("Waiting for a fresh, precise GPS fix (30 m or better). Camera, detection, and video are paused.")
        val manager = cameraManager
        if (manager == null) {
            stopDriveSession("Camera could not resume")
            runCatching { onComplete?.invoke(snapshot()) }
            return
        } else startScanLoop()
        refreshCaptureInterlock()
        // Camera/GPS state is the control-plane result. Room durability is tracked for
        // Stop, but a slow disk must never leave the Resume bridge call hanging.
        persistSessionTracked("active", null)
        runCatching { onComplete?.invoke(snapshot()) }
    }

    fun attachPreview(surfaceProvider: Preview.SurfaceProvider): Boolean? {
        if (!sessionRunning || isStopping) return false
        return cameraManager?.attachPreview(surfaceProvider)
    }

    fun detachPreview(surfaceProvider: Preview.SurfaceProvider) {
        cameraManager?.detachPreview(surfaceProvider)
    }

    fun controlsNotificationSession(expectedSessionId: String?): Boolean =
        sessionRunning && !isStopping && !expectedSessionId.isNullOrBlank() &&
            expectedSessionId == sessionId

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
        val previousTransition = recordingTransitionJob
        previousTransition?.cancel()
        recordingTransitionJob = lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
            try {
                previousTransition?.join()
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
                discarded = discardDataOnStop,
                reason = reason,
                startRequestId = startRequestId
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
        captureAccessEpoch.invalidate()
        pendingCriticalCameraRecoveryReason = null
        pendingCameraRecoveryIsUserState = false
        val criticalRecovery = criticalCameraRecoveryJob
        criticalRecovery?.cancel()
        val accessTransition = accessTransitionJob
        val recordingTransition = recordingTransitionJob
        isStopping = true; captureStopped = false; sessionRunning = false; statusText = reason
        sessionLimitJob?.cancel()
        pauseTimeoutJob?.cancel()
        pauseTimeoutJob = null
        accessTransition?.cancel()
        recordingTransition?.cancel()
        runCatching { publish("Stopping safely") }
            .onFailure { recordStopError("Could not update the stopping status", it) }
        val scanningJob = scanJob
        runCatching { scanningJob?.cancel() }
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
        // Stop owns camera and persistence teardown even if Android destroys the
        // LifecycleService mid-flight. A process-owned job is tracked by onDestroy and
        // keeps destructive bridge calls fenced until every producer has actually ended.
        stopTeardownJob = abnormalTeardownScope.launch(
            Dispatchers.Main.immediate,
            start = CoroutineStart.UNDISPATCHED
        ) {
            var inferenceClosed = false
            var inferenceCloseFailure: Throwable? = null
            var cameraStoppedCleanly = false

            fun closeInference(): Boolean {
                if (inferenceClosed) return true
                val closed = runCatching { engine?.close() }
                inferenceClosed = closed.isSuccess
                inferenceCloseFailure = closed.exceptionOrNull()
                if (inferenceClosed) inferenceCloseFailure = null
                return inferenceClosed
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

            suspend fun stopScannerWithinLimit(limitMs: Long): Boolean {
                val scanner = scanningJob ?: return true
                return try {
                    withTimeoutOrNull(limitMs) {
                        scanner.join()
                        true
                    } == true
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Throwable) {
                    recordStopError("Camera scheduler teardown failed", error)
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
                if (!cancelled) {
                    publish("Camera off · waiting for saved data")
                    // Cancellation is only a request. Room/file ownership may be inside
                    // NonCancellable code, so never release Stop/clear callbacks until
                    // the jobs have really reached a terminal state.
                    jobs.joinAll()
                }
            }

            val cameraStopped = async {
                try {
                    criticalRecovery?.join()
                    accessTransition?.join()
                    recordingTransition?.join()
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
                if (cameraStoppedCleanly) {
                    // This is the only early completion boundary exposed to the UI. It is
                    // reached only after MediaRecorder finalization/discard and CameraX
                    // unbind have returned successfully. Room/media persistence continues
                    // below and still owns driveEnded plus every Stop/clear callback.
                    withContext(NonCancellable + Dispatchers.Main.immediate) {
                        if (!captureStopped && isStopping && sessionId == endedSession &&
                            !terminalStatusSealed
                        ) {
                            cameraActive = false
                            isRecording = false
                            captureStopped = true
                            publish("Camera off · finalizing saved data")
                        }
                    }
                }
            }
            try {
                if (!stopScannerWithinLimit(3_000L)) {
                    recordStopError("Camera scheduling did not stop within the cancellation limit")
                }
                val drained = stopWorkerWithinLimit(8_000L)
                if (!drained) {
                    recordStopError("A live detection was saved for post-drive analysis after exceeding the 8-second Stop limit")
                    // OkHttp execute() is blocking. Cancel its Call before cancelling the
                    // coroutine, then give the worker only a second bounded exit window.
                    closeInference()
                    runCatching { workerJob?.cancel() }
                        .onFailure { recordStopError("Could not cancel the detection worker", it) }
                    runCatching { drainingChannel?.cancel() }
                        .onFailure { recordStopError("Could not discard the remaining detection queue", it) }
                    if (!stopWorkerWithinLimit(3_000L)) {
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
                    runCatching { scanningJob?.cancel() }
                        .onFailure { recordStopError("Could not stop camera scheduling", it) }
                    if (scanningJob?.isCompleted != true && !stopScannerWithinLimit(3_000L)) {
                        recordStopError("Camera scheduling remained active after Stop")
                        publish("Camera off · waiting for saved data")
                        scanningJob?.join()
                    }
                    runCatching { drainingChannel?.cancel() }
                        .onFailure { recordStopError("Could not close the detection queue", it) }

                    if (workerJob?.isCompleted != true) {
                        closeInference()
                        runCatching { workerJob?.cancel() }
                            .onFailure { recordStopError("Could not cancel the detection worker", it) }
                        if (!stopWorkerWithinLimit(5_000L)) {
                            recordStopError("Detection worker remained active after Stop")
                            publish("Camera off · waiting for saved data")
                            workerJob?.join()
                        }
                    }
                    closeInference()
                    // A first close can race a Call that is completing its cancellation.
                    // Retry only after the worker has relinquished every engine resource;
                    // a transient close failure is not a failed Stop.
                    if (!inferenceClosed) closeInference()
                    if (inferenceClosed) inferenceEngine = null

                    if (!cameraStoppedCleanly || !cameraStopped.isCompleted) {
                        runCatching { cameraStopped.cancel() }
                        runCatching { cameraTransitionJob?.cancel() }
                        val emergencyClosed = runCatching { manager?.closeImmediately() }
                            .onFailure { recordStopError("Emergency camera teardown failed", it) }
                        cameraStoppedCleanly = emergencyClosed.isSuccess
                    }
                    if (cameraStoppedCleanly) {
                        cameraManager = null
                        cameraActive = false
                        isRecording = false
                    }

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
                            val finalSessionWrite = async(Dispatchers.IO) {
                                persistSession("stopped", System.currentTimeMillis() / 1000)
                            }
                            val savedWithinSoftLimit = withTimeoutOrNull(10_000L) {
                                finalSessionWrite.join()
                                true
                            } == true
                            if (!savedWithinSoftLimit) {
                                recordStopError("Saving the final drive summary exceeded the Stop limit")
                                publish("Camera off · waiting for saved data")
                            }
                            // Timeout only changes the visible status. It never proves a
                            // Room write ended and therefore cannot release Stop/clear.
                            finalSessionWrite.await()
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
                        discarded = discarded,
                        reason = reason,
                        startRequestId = endedStartRequest
                    )
                    } catch (error: Throwable) {
                        recordStopError("Final Drive teardown failed", error)
                    } finally {
                        // This emergency pass is deliberately idempotent. It also covers an
                        // unexpected failure in the normal NonCancellable cleanup above.
                        if (!inferenceClosed) closeInference()
                        if (!inferenceClosed) closeInference()
                        if (!inferenceClosed) {
                            inferenceCloseFailure?.let {
                                recordStopError("Could not close the detection engine", it)
                            } ?: recordStopError("Could not close the detection engine")
                        }
                        // A failed close must remain reachable until Android invokes
                        // onDestroy, which is the lifecycle's final cleanup attempt.
                        if (inferenceClosed) inferenceEngine = null
                        runCatching { workerJob?.cancel() }
                        runCatching { drainingChannel?.cancel() }
                        if (!cameraStoppedCleanly) {
                            val emergencyClosed = runCatching { manager?.closeImmediately() }
                                .onFailure { recordStopError("Emergency camera teardown failed", it) }
                            cameraStoppedCleanly = emergencyClosed.isSuccess
                        }
                        if (cameraStoppedCleanly) {
                            cameraManager = null
                            cameraActive = false
                            isRecording = false
                        }
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

                        // The camera, worker, files and wakelock are now closed. Remove the
                        // foreground disclosure before freezing the terminal result so an
                        // OS-level notification teardown failure cannot be reported as success.
                        if (cameraStoppedCleanly) {
                            runCatching { stopForeground(STOP_FOREGROUND_REMOVE) }
                                .onFailure { recordStopError("Could not remove the Drive Mode notification", it) }
                        }

                        val baseSummary = completedSummary ?: DriveEndSummary(
                            endedSession, checkedCount, foundCount, alreadyCount,
                            error = synchronized(stopErrors) {
                                stopErrors.takeIf { it.isNotEmpty() }?.joinToString("; ")
                            },
                            discarded = discardDataOnStop,
                            reason = reason,
                            startRequestId = endedStartRequest
                        )
                        val currentStopError = synchronized(stopErrors) {
                            stopErrors.takeIf { it.isNotEmpty() }?.joinToString("; ")
                        }
                        val terminalError = when {
                            currentStopError.isNullOrBlank() -> baseSummary.error
                            baseSummary.error.isNullOrBlank() -> currentStopError
                            currentStopError == baseSummary.error -> currentStopError
                            currentStopError.startsWith("${baseSummary.error}; ") -> currentStopError
                            else -> "${baseSummary.error}; $currentStopError"
                        }
                        var terminalCandidate = baseSummary.copy(
                            error = terminalError,
                            discarded = baseSummary.discarded || discardDataOnStop
                        )
                        val terminalStored = runCatching {
                            NativeDriveEndSummaryStore.record(applicationContext, terminalCandidate)
                        }.getOrDefault(false)
                        if (!terminalStored) {
                            val storageError = "Could not persist the terminal Drive result"
                            recordStopError(storageError)
                            terminalCandidate = terminalCandidate.copy(
                                error = listOfNotNull(terminalCandidate.error, storageError)
                                    .distinct().joinToString("; ")
                            )
                            // The store always updates its bounded in-process ledger first;
                            // retry the durable copy with the final error-bearing summary.
                            runCatching {
                                NativeDriveEndSummaryStore.record(applicationContext, terminalCandidate)
                            }
                        }
                        val completion = claimStopCompletion(terminalCandidate)
                        if (completion.won) {
                            // Open the bridge retry gate before clients receive driveEnded.
                            // This covers protected active-session files and any final
                            // best-effort cleanup that could not remove its private output.
                            NativeMediaReconciliationEpoch.invalidate()
                            // No cancelled/non-cancellable persistence continuation may emit an
                            // old status after clients have received this terminal summary.
                            terminalStatusSealed = true
                            mainHandler.removeCallbacks(deferredStatusRunnable)
                            deferredStatusDispatch = false
                            startCompletionLedger.record(endedStartRequest, completion.summary)
                            // Delivery failures are recoverable through the durable terminal
                            // result and are not camera/service teardown failures.
                            runCatching { onDriveEndedListener?.invoke(completion.summary) }
                            completion.callbacks.forEach { callback ->
                                runCatching { callback(completion.summary) }
                            }
                        }

                        locationProvider = null
                        workerJob = null
                        scanJob = null
                        cameraTransitionJob = null
                        sessionLimitJob = null
                        // stopSelf is asynchronous; completedStopSummary is already sealed, so
                        // onDestroy cannot reinterpret this normal Stop as an interruption.
                        runCatching { stopSelf() }
                    }
                }
            }
        }
    }

    private fun persistVideoSegment(segment: NativeVideoSegment) {
        val storageLedger = cameraManager
        val job = lifecycleScope.launch(Dispatchers.IO) {
            var rowCommitted = false
            try {
                // Keep the Room commit and ownership hand-off indivisible with respect
                // to service cancellation. Once this returns, cleanup must never delete
                // the segment merely because a later UI update is cancelled.
                withContext(NonCancellable) {
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
                    rowCommitted = true
                }
                withContext(NonCancellable + Dispatchers.Main) {
                    segmentCount++
                    recordedBytes += segment.bytes
                    publish(statusText)
                }
            } catch (error: Exception) {
                val message = if (!rowCommitted) {
                    val removed = NativeRetryableFileCleanup.deleteVerified(File(segment.filePath))
                    if (removed) storageLedger?.noteDeletedMediaBytes(segment.bytes)
                    "A video clip could not be indexed and was discarded"
                } else {
                    "A saved video clip could not update the live status"
                }
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

    private fun persistSessionTracked(status: String, endedAt: Long?) {
        // Pause can immediately hand off to a queued Resume. Preserve request order while
        // keeping both bridge calls independent of disk latency, otherwise a slow Paused
        // write can land after Active and leave durable history in the wrong state.
        val previous = synchronized(sessionPersistJobs) { sessionPersistTail }
        val job = lifecycleScope.launch(Dispatchers.IO, start = CoroutineStart.LAZY) {
            try {
                previous?.join()
                persistSession(status, endedAt)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                recordSessionPersistError(error)
            }
        }
        synchronized(sessionPersistJobs) {
            sessionPersistTail = job
            sessionPersistJobs.add(job)
        }
        job.invokeOnCompletion {
            synchronized(sessionPersistJobs) {
                sessionPersistJobs.remove(job)
                if (sessionPersistTail === job) sessionPersistTail = null
            }
        }
        job.start()
    }

    private fun recordSessionPersistError(error: Exception) {
        val message = error.message ?: "A drive summary write failed"
        synchronized(sessionPersistErrors) { sessionPersistErrors.add(message) }
    }

    @Synchronized
    private fun notificationStillVisible(): Boolean {
        val now = SystemClock.elapsedRealtime()
        if (now - lastNotificationCheckMs < 2_000L) return !notificationStopRequested
        lastNotificationCheckMs = now
        if (!NotificationHelper.canShowDriveNotification(this)) {
            requestNotificationStop("Stopped because Drive Mode notifications were disabled")
            return false
        }
        // DeleteIntent handles normal Android 13+ dismissal immediately. This independent
        // watchdog also covers OEMs that remove the active notification without delivering
        // the delete broadcast, including while Drive is paused and scanJob is stopped.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M &&
            now - notificationStartedElapsedMs >= 4_000L
        ) {
            val active = runCatching {
                (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                    .activeNotifications.any { it.id == NotificationHelper.NOTIFICATION_ID }
            }.getOrNull()
            if (active == null) {
                notificationQueryFailureChecks++
                if (notificationQueryFailureChecks >= 2) {
                    requestNotificationStop(
                        "Stopped because Android could not verify the Drive Mode notification"
                    )
                    return false
                }
                return true
            }
            notificationQueryFailureChecks = 0
            notificationMissingChecks = if (active) 0 else notificationMissingChecks + 1
            if (notificationMissingChecks >= 2) {
                requestNotificationStop("Stopped because the Drive Mode notification disappeared")
                return false
            }
        }
        return true
    }

    @Synchronized
    private fun claimNotificationStop(): String? {
        if (notificationStopRequested || !sessionRunning || isStopping) return null
        notificationStopRequested = true
        // Close every in-flight burst synchronously. This method can run before the
        // main-thread Stop runnable, so setting only a Boolean would leave a validation
        // window in which evidence or inference could still be admitted.
        captureAccessEpoch.invalidate()
        return sessionId
    }

    private fun requestNotificationStop(reason: String) {
        // Close the capture gate before posting to main. A janked main thread must never
        // allow another analyzer admission after disclosure has disappeared.
        val expectedSession = claimNotificationStop() ?: return
        val stop = {
            if (sessionId == expectedSession && notificationStopRequested &&
                sessionRunning && !isStopping
            ) stopDriveSession(reason)
        }
        if (Looper.myLooper() == Looper.getMainLooper()) stop() else mainHandler.post(stop)
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

    private fun snapshot(): DriveStatusSnapshot {
        val work = workLedger.snapshot()
        // Derive both public fields from one current decision. The cached blocker exists
        // only for transition handling and can lag one callback during permission/GPS changes.
        val capture = captureInterlockDecision()
        val visibleStatus = inferenceSuspendedReason
            ?.takeIf { capture.canCapture && sessionRunning && !isStopping }
            ?.let(::inferenceSuspendedStatus)
            ?: statusText
        // onCreate publishes activeService just before onStartCommand can copy the
        // admitted request token into this instance. Preserve ownership across that
        // observable gap so a recreated WebView never adopts an anonymous Start.
        val effectiveStartRequestId = startRequestId ?: pendingAdmittedStartRequestId()
        return DriveStatusSnapshot(
            sessionRunning, isPaused, pauseTransitioning, isStopping,
            sessionId.takeIf(String::isNotBlank), work.checked,
            foundCount, alreadyCount, work.queued, work.deferred, visibleStatus,
            recordingEnabled, isRecording, videoSupported, segmentCount, recordedBytes,
            cameraActive,
            recordingIssue, keyframeCount, remainingDriveMs(), sessionLimitMinutes,
            !capture.canCapture,
            capture.takeIf { !it.canCapture }?.message,
            captureStopped,
            isStarting = !sessionRunning && !isStopping && hasAdmittedStart(),
            startRequestId = effectiveStartRequestId
        )
    }

    private fun inferenceSuspendedStatus(reason: String): String =
        "$reason · Detection paused; camera and local video continue. " +
            "Stop Drive, check the API key/model, then start again."

    private fun claimStopCompletion(candidate: DriveEndSummary): StopCompletionClaim =
        synchronized(stopCallbacks) {
            completedStopSummary?.let {
                return@synchronized StopCompletionClaim(it, emptyList(), won = false)
            }
            val summary = candidate.copy(discarded = candidate.discarded || discardDataOnStop)
            completedStopSummary = summary
            val callbacks = stopCallbacks.toList()
            stopCallbacks.clear()
            StopCompletionClaim(summary, callbacks, won = true)
        }

    private fun publish(text: String) {
        if (terminalStatusSealed || activeService !== this) return
        statusText = text
        val routine = text == "Scanning live" || text == "Finishing queued detections"
        val now = SystemClock.elapsedRealtime()
        val waitMs = ROUTINE_STATUS_THROTTLE_MS - (now - lastStatusDispatchElapsedMs)
        if ((routine || text == lastDispatchedStatusText) && waitMs > 0L) {
            if (!deferredStatusDispatch) {
                deferredStatusDispatch = true
                mainHandler.postDelayed(deferredStatusRunnable, waitMs)
            }
            return
        }
        mainHandler.removeCallbacks(deferredStatusRunnable)
        deferredStatusDispatch = false
        dispatchStatus()
    }

    private fun dispatchStatus() {
        // Inference runs on IO while CameraX and service callbacks normally run on the
        // main thread. Serialize the complete compare/build/notify transaction so two
        // publishers cannot overwrite the foreground disclosure with stale state.
        if (Looper.myLooper() != Looper.getMainLooper()) {
            val sourceSessionId = sessionId
            mainHandler.post {
                if (!terminalStatusSealed && activeService === this && sessionId == sourceSessionId) {
                    dispatchStatus()
                }
            }
            return
        }
        if (terminalStatusSealed || activeService !== this) return
        lastStatusDispatchElapsedMs = SystemClock.elapsedRealtime()
        lastDispatchedStatusText = statusText
        val remaining = if (sessionRunning && !isStopping) remainingDriveMs() else 0L
        val notificationStatus = if (isPaused && sessionRunning && !isStopping) {
            "$statusText · auto-stops after ${DrivePauseTimeoutPolicy.DEFAULT_TIMEOUT_MINUTES} min paused"
        } else if (remaining > 0L) {
            val minutes = ceil(remaining / 60_000.0).toInt()
            "$statusText · $minutes min left"
        } else statusText
        if ((sessionRunning || isStopping) && !notificationStopRequested) {
            val notificationState = DriveNotificationState(
                sessionId, isPaused, isStopping, pauseTransitioning,
                checkedCount, foundCount, alreadyCount, notificationStatus,
                recordingEnabled, isRecording, videoSupported, recordingIssue,
                cameraActive, captureStopped
            )
            if (notificationState != lastNotificationState) {
                runCatching {
                    val notification = NotificationHelper.buildNotification(
                        this, sessionId, isPaused, checkedCount, foundCount, alreadyCount,
                        notificationStatus,
                        isStopping = isStopping,
                        isPausing = pauseTransitioning,
                        recordingEnabled = recordingEnabled,
                        isRecording = isRecording,
                        videoSupported = videoSupported,
                        recordingIssue = recordingIssue,
                        cameraActive = cameraActive,
                        captureStopped = captureStopped
                    )
                    (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                        .notify(NotificationHelper.NOTIFICATION_ID, notification)
                    lastNotificationState = notificationState
                }.onFailure {
                    if (!isStopping) requestNotificationStop(
                        "Stopped because the Drive Mode notification could not be shown"
                    )
                }
            }
        }
        val current = snapshot()
        val sourceSessionId = sessionId
        mainHandler.post {
            if (!terminalStatusSealed && activeService === this && sessionId == sourceSessionId) {
                onStatusListener?.invoke(current)
            }
        }
    }

    private fun recycle(item: BurstJob) {
        recycleFrames(item.burstFrames)
    }

    private fun recycleFrames(frames: List<BurstFrame>) {
        frames.forEach { if (!it.bitmap.isRecycled) it.bitmap.recycle() }
    }

    override fun onDestroy() {
        val wasActive = sessionRunning || isStopping
        val destroyedSessionId = sessionId
        val destroyedStartRequest = startRequestId
        val abnormalTeardown = wasActive && completedStopSummary == null
        val jobsToJoin = if (abnormalTeardown) {
            buildList {
                listOfNotNull(
                    scanJob,
                    workerJob,
                    criticalCameraRecoveryJob,
                    accessTransitionJob,
                    recordingTransitionJob,
                    cameraTransitionJob,
                    stopTeardownJob
                ).forEach(::add)
                synchronized(segmentPersistJobs) { addAll(segmentPersistJobs) }
                synchronized(sessionPersistJobs) { addAll(sessionPersistJobs) }
            }.distinct()
        } else emptyList()
        try {
            terminalStatusSealed = true
            notificationStopRequested = true
            captureAccessEpoch.invalidate()
            // Abort any reconciliation pass that inventoried before teardown began. For
            // abnormal destruction, this service remains the protected producer until
            // the independent bounded durability barrier below completes.
            NativeMediaReconciliationEpoch.invalidate()
            sessionRunning = false
            isStopping = abnormalTeardown
            pauseTransitioning = false
            pendingCriticalCameraRecoveryReason = null
            pendingCameraRecoveryIsUserState = false
            runCatching { criticalCameraRecoveryJob?.cancel() }
            runCatching { accessTransitionJob?.cancel() }
            runCatching { recordingTransitionJob?.cancel() }
            runCatching { cameraTransitionJob?.cancel() }
            runCatching { scanJob?.cancel() }
            runCatching { workerJob?.cancel() }
            runCatching { sessionLimitJob?.cancel() }
            runCatching { pauseTimeoutJob?.cancel() }
            pauseTimeoutJob = null
            runCatching { jobChannel?.cancel() }
            runCatching { mainHandler.removeCallbacks(deferredStatusRunnable) }
            runCatching { stopCameraAccessMonitoring() }
            // OEM CameraX implementations can throw from unbindAll during process or
            // Task-Manager teardown. One failure must never skip GPS, network, wakelock,
            // or LifecycleService cleanup.
            val cameraClosed = runCatching { cameraManager?.closeImmediately() }.isSuccess
            if (cameraClosed) {
                cameraActive = false
                isRecording = false
                captureStopped = true
            }
            runCatching { locationProvider?.stopUpdates() }
            runCatching { inferenceEngine?.close() }
            inferenceEngine = null
            runCatching { releaseWakeLock() }
            if (abnormalTeardown) {
                statusText = if (cameraClosed) {
                    "Camera off · finalizing interrupted drive data"
                } else {
                    "Android ended Drive Mode · finalizing interrupted drive data"
                }
                // The routine publisher is sealed, but a visible WebView still needs the
                // monotonic control-plane state so it cannot expose capture/wipe actions.
                runCatching { onStatusListener?.invoke(snapshot()) }
                finalizeAbnormalTeardown(
                    destroyedSessionId,
                    destroyedStartRequest,
                    jobsToJoin
                )
            } else {
                isStopping = false
                if (activeService === this) activeService = null
            }
        } finally {
            super.onDestroy()
        }
    }

    private fun finalizeAbnormalTeardown(
        destroyedSessionId: String,
        destroyedStartRequest: String?,
        jobsToJoin: List<Job>
    ) {
        abnormalTeardownScope.launch {
            val teardownErrors = mutableListOf<String>()
            val jobsFinishedWithinSoftLimit = try {
                withTimeoutOrNull(ABNORMAL_TEARDOWN_JOIN_MS) {
                    jobsToJoin.joinAll()
                    true
                } == true
            } catch (error: Throwable) {
                teardownErrors += error.message ?: "Interrupted Drive ownership barrier failed"
                false
            }
            if (!jobsFinishedWithinSoftLimit) {
                teardownErrors += "Interrupted Drive ownership barrier exceeded its limit"
                withContext(NonCancellable + Dispatchers.Main.immediate) {
                    statusText = "Camera off · waiting for safe data finalization"
                    runCatching { onStatusListener?.invoke(snapshot()) }
                }
                // A timeout is not completion. Keep activeService as the protected
                // producer and wait in the process-owned scope until every job has truly
                // ended; otherwise a later Room commit could resurrect data after wipe.
                try {
                    jobsToJoin.joinAll()
                } catch (error: Throwable) {
                    teardownErrors += error.message ?: "Interrupted Drive ownership barrier failed"
                }
            }

            // An ordinary Stop finalizer may have completed while onDestroy was waiting.
            // It already persisted the correct status and delivered callbacks exactly
            // once; the abnormal path must only release the producer fence.
            if (completedStopSummary != null) {
                withContext(NonCancellable + Dispatchers.Main.immediate) {
                    if (activeService === this@DriveForegroundService) activeService = null
                    NativeMediaReconciliationEpoch.invalidate()
                }
                return@launch
            }

            val endedAt = System.currentTimeMillis() / 1000
            val sessionPersisted = try {
                // Do not turn a database timeout into a false durable boundary. This
                // process-owned coroutine may release callbacks only after the write
                // returned or definitively failed.
                persistSession("interrupted", endedAt)
                true
            } catch (error: Throwable) {
                teardownErrors += error.message ?: "Interrupted Drive summary could not be saved"
                false
            }
            if (!sessionPersisted) {
                teardownErrors += "Interrupted Drive summary exceeded its save limit"
            }

            val reason = "Drive Mode stopped because Android ended the foreground camera service."
            withContext(NonCancellable + Dispatchers.Main.immediate) {
                var summary = DriveEndSummary(
                    sessionId = destroyedSessionId,
                    checked = checkedCount,
                    found = foundCount,
                    already = alreadyCount,
                    error = (listOf(reason) + teardownErrors).distinct().joinToString("; "),
                    discarded = discardDataOnStop,
                    reason = reason,
                    startRequestId = destroyedStartRequest
                )
                val terminalStored = runCatching {
                    NativeDriveEndSummaryStore.record(applicationContext, summary)
                }.getOrDefault(false)
                if (!terminalStored) {
                    teardownErrors += "Interrupted Drive terminal result could not be persisted"
                    summary = summary.copy(
                        error = (listOf(reason) + teardownErrors).distinct().joinToString("; ")
                    )
                    runCatching { NativeDriveEndSummaryStore.record(applicationContext, summary) }
                }
                val completion = claimStopCompletion(summary)
                // Producer jobs and the interrupted session row are now settled. Only at
                // this point may reconciliation or deletion treat the session as idle.
                if (activeService === this@DriveForegroundService) activeService = null
                NativeMediaReconciliationEpoch.invalidate()
                if (completion.won) {
                    startCompletionLedger.record(destroyedStartRequest, completion.summary)
                    runCatching { onDriveEndedListener?.invoke(completion.summary) }
                    completion.callbacks.forEach { callback ->
                        runCatching { callback(completion.summary) }
                    }
                }
            }
        }
    }
}
