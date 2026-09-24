package com.gauravsen.potholereporter.drivemode

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import com.gauravsen.potholereporter.R
import com.gauravsen.potholereporter.db.entities.CentralObservationEntity
import androidx.camera.core.CameraInfo
import androidx.camera.core.CameraSelector
import androidx.camera.core.CameraState
import androidx.camera.core.ImageAnalysis
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.Observer
import androidx.room.withTransaction
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import java.util.concurrent.Executors

internal fun shouldTreatCameraClosedAsConflict(stopping: Boolean): Boolean = !stopping

/**
 * Foreground service that owns the Drive Mode camera, location, and detection
 * lifecycle. Runs independently of the Activity/WebView so that detection
 * continues when the user switches to Google Maps or another app.
 *
 * Lifecycle:
 *   1. Started via startForegroundService() from the Capacitor plugin while
 *      the Activity is visible (so no ACCESS_BACKGROUND_LOCATION is needed).
 *   2. Immediately promotes to foreground with an ongoing notification.
 *   3. Binds CameraX ImageAnalysis (no Preview) for frame capture.
 *   4. Starts FusedLocation updates for GPS.
 *   5. Runs a coroutine-based tick loop that mirrors the web client's driveTick().
 *   6. Stops on user action (notification Stop, plugin stop) or fatal error.
 *
 * The service does NOT record video or audio. It captures individual JPEG
 * frames for analysis and deletes rejected frames immediately.
 */
class DriveModeService : LifecycleService() {

    companion object {
        private const val TAG = "DriveModeService"
        // Six jobs may be talking to the model and at most 24 more may wait behind them.
        // The previous fire-and-forget launch had no service-side bound at all.
        private const val MAX_OUTSTANDING_DETECTIONS =
            DriveConstants.MAX_IN_FLIGHT + DriveConstants.MAX_CAPTURE_QUEUE

        const val ACTION_START = "com.gauravsen.potholereporter.DRIVE_START"
        const val ACTION_PAUSE = "com.gauravsen.potholereporter.DRIVE_PAUSE"
        const val ACTION_RESUME = "com.gauravsen.potholereporter.DRIVE_RESUME"
        const val ACTION_STOP = "com.gauravsen.potholereporter.DRIVE_STOP"

        const val EXTRA_API_KEY = "api_key"
        const val EXTRA_PROVIDER = "vision_provider"
        const val EXTRA_SERVICE_URL = "service_url"
        const val EXTRA_MODEL = "model"
        const val EXTRA_DETAIL = "detail"
        const val EXTRA_LANGUAGE = "language"

        /** Current session, accessible from the Capacitor plugin thread. */
        @Volatile
        var currentSession: DriveSession? = null
            private set

        /** Listener for status updates, set by the Capacitor plugin. */
        @Volatile
        var statusListener: ((Map<String, Any?>) -> Unit)? = null

        fun isRunning(): Boolean = currentSession != null
    }

    private var session: DriveSession? = null
    private var notificationController: NotificationController? = null
    private var locationTracker: LocationTracker? = null
    private var frameAnalyzer: FrameAnalyzer? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var cameraProvider: ProcessCameraProvider? = null
    private var cameraStateObserver: Observer<CameraState>? = null

    // Phase 2: Detection pipeline
    private var detectionDispatcher: DetectionDispatcher? = null
    private var duplicateDetector: DuplicateDetector? = null
    private val database by lazy { com.gauravsen.potholereporter.db.AppDatabase.getInstance(this) }

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val analysisExecutor = Executors.newSingleThreadExecutor()

    // Configuration passed from the Capacitor plugin
    private var apiKey: String = ""
    private var visionProvider: String = "personal"
    private var serviceUrl: String = ""
    private var model: String = LlmContractGenerated.DEFAULT_MODEL
    private var detail: String = LlmContractGenerated.DEFAULT_IMAGE_DETAIL
    private var language: String = LlmContractGenerated.DEFAULT_LANGUAGE
    private val centralIdentity by lazy { CentralServiceIdentity(this) }
    private var centralClient: CentralServiceClient? = null

    // Tick loop job
    private var tickJob: Job? = null

    // Completed-frame channel — bounded to prevent memory accumulation
    private val frameChannel = Channel<CapturedFrame>(DriveConstants.MAX_CAPTURE_QUEUE)

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)

        when (intent?.action) {
            ACTION_START -> handleStart(intent)
            ACTION_PAUSE -> handlePause()
            ACTION_RESUME -> handleResume()
            ACTION_STOP -> handleStop()
            else -> {
                // Unknown or null action — if no session, stop self
                if (session == null) stopSelf()
            }
        }

        // If the system kills this service, do NOT restart it automatically.
        // The user must explicitly start a new drive session.
        return START_NOT_STICKY
    }

    override fun onBind(intent: Intent): IBinder? {
        super.onBind(intent)
        return null
    }

    override fun onDestroy() {
        // Explicit Stop clears the session only after its graceful drain. If Android tears
        // the service down independently, it is too late to promise a drain; clear the
        // process-global handle so the next launch is not permanently told it is running.
        session?.let { interrupted ->
            interrupted.stopping = true
            interrupted.state = DriveSession.State.STOPPED
        }
        session = null
        currentSession = null
        serviceScope.cancel()
        analysisExecutor.shutdown()
        super.onDestroy()
    }

    // --- Lifecycle handlers ---

    private fun handleStart(intent: Intent) {
        if (session != null) {
            Log.w(TAG, "Drive session already active, ignoring start")
            return
        }

        apiKey = intent.getStringExtra(EXTRA_API_KEY) ?: ""
        visionProvider = effectiveVisionProvider(intent.getStringExtra(EXTRA_PROVIDER), apiKey)
        serviceUrl = intent.getStringExtra(EXTRA_SERVICE_URL) ?: ""
        model = normalizeVisionModel(intent.getStringExtra(EXTRA_MODEL))
        detail = normalizeVisionDetail(intent.getStringExtra(EXTRA_DETAIL), model)
        language = normalizeVisionLanguage(intent.getStringExtra(EXTRA_LANGUAGE))
        if (serviceUrl.isNotBlank()) {
            getSharedPreferences(CentralServiceIdentity.PREFS, Context.MODE_PRIVATE)
                .edit().putString("service_url", serviceUrl).apply()
            centralClient = CentralServiceClient(serviceUrl, centralIdentity)
        }

        val newSession = DriveSession(sessionId = System.currentTimeMillis().toString())
        session = newSession
        currentSession = newSession

        // This build declares no foreground service types, so Android 14 and later would
        // throw when the service tried to promote itself with camera and location. The
        // path that starts this service is unreachable here (the plugin reports itself
        // unavailable), but a stale intent must stop the service rather than crash the
        // app. Refuse before touching the camera.
        if (!declaresCameraForegroundType()) {
            stopSelf()
            return
        }

        // Notification must be shown before startForeground returns
        notificationController = NotificationController(this)
        val notification = notificationController!!.buildForegroundNotification()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                DriveConstants.NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA or
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
            )
        } else {
            startForeground(DriveConstants.NOTIFICATION_ID, notification)
        }

        acquireWakeLock()

        // Initialize detection pipeline
        detectionDispatcher = DetectionDispatcher(
            provider = visionProvider,
            apiKey = apiKey,
            serviceUrl = serviceUrl,
            serviceIdentity = centralIdentity,
            model = model,
            detail = detail,
            language = language,
        )
        duplicateDetector = DuplicateDetector(database.reportDao())

        startLocation(newSession)
        startCamera(newSession)

        newSession.state = DriveSession.State.RUNNING
        notifyStatus()

        Log.i(TAG, "Drive session started: ${newSession.sessionId}")
    }

    private fun handlePause() {
        val s = session ?: return
        if (s.state != DriveSession.State.RUNNING) return

        s.state = DriveSession.State.PAUSED
        tickJob?.cancel()
        tickJob = null

        notificationController?.updateNotification(
            getString(R.string.drive_notification_paused),
            isPaused = true,
            found = s.found,
            checked = s.checked
        )
        notifyStatus()
        Log.i(TAG, "Drive session paused")
    }

    private fun handleResume() {
        val s = session ?: return
        if (s.state != DriveSession.State.PAUSED) return

        s.state = DriveSession.State.RUNNING
        startTickLoop(s)

        notificationController?.updateNotification(
            getString(R.string.drive_notification_scanning),
            isPaused = false,
            found = s.found,
            checked = s.checked
        )
        notifyStatus()
        Log.i(TAG, "Drive session resumed")
    }

    private fun handleStop() {
        val s = session ?: return
        if (s.stopping) return
        s.stopping = true
        s.state = DriveSession.State.STOPPING

        Log.i(TAG, "Stopping drive session: ${s.sessionId}")

        // Setting `stopping` prevents another loop iteration. If JPEG conversion is already
        // running, the analyzer's monitor lets that frame reach the channel before teardown,
        // and the active tick participates in the same finalization barrier as detections.
        val finishingTick = tickJob
        finishingTick?.invokeOnCompletion {
            if (tickJob === finishingTick) tickJob = null
            maybeFinalizeStop(s)
        }

        // CameraX cannot deliver more images after unbind. Cancel a request for which no
        // ImageProxy arrived; an in-progress JPEG and callback finish under the same lock.
        frameAnalyzer?.cancelPendingCapture()

        // Release camera
        try {
            cameraProvider?.unbindAll()
        } catch (e: Exception) {
            Log.e(TAG, "Error unbinding camera", e)
        }
        cameraProvider = null
        s.cameraState = DriveSession.CameraState.UNAVAILABLE

        // Stop location
        locationTracker?.stop()
        locationTracker = null

        // Release wake lock
        releaseWakeLock()

        updateStoppingStatus(s)
        maybeFinalizeStop(s)
    }

    /**
     * Complete a user-requested Stop after the current frame capture and every model
     * job reserved before Stop have finished. The foreground notification remains visible
     * during this drain so Android does not kill useful work in the background.
     */
    private fun maybeFinalizeStop(s: DriveSession) {
        if (session !== s || tickJob?.isActive == true || !s.tryBeginStopFinalization()) return
        serviceScope.launch {
            try {
                saveDriveSession(s)

                // Accepted detections have already been committed to Room. Start their
                // durable central/tender processing only after the final tally is stored.
                runCatching {
                    UploadWorker.enqueue(this@DriveModeService, ensureAfterCurrent = true)
                }.onFailure { Log.e(TAG, "Could not enqueue drained reports", it) }
            } finally {
                // A completed frame can be delivered just after CameraX is unbound. Keep
                // the channel open through the tick barrier so that the last frame is not
                // rejected merely because Stop won the callback race.
                frameChannel.close()
                detectionDispatcher = null
                duplicateDetector = null
                frameAnalyzer = null

                s.state = DriveSession.State.STOPPED
                statusListener?.invoke(s.snapshot())
                if (session === s) session = null
                if (currentSession === s) currentSession = null

                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                Log.i(TAG, "Drive session drained, saved, and service terminated")
            }
        }
    }

    private fun updateStoppingStatus(s: DriveSession) {
        val remaining = s.inFlight
        val text = if (remaining > 0) {
            "Finishing $remaining captured frame${if (remaining == 1) "" else "s"}"
        } else {
            "Finishing drive"
        }
        notificationController?.updateNotification(
            text,
            isPaused = false,
            found = s.found,
            checked = s.checked,
        )
        if (session === s) statusListener?.invoke(s.snapshot())
    }

    // --- Camera ---

    private fun startCamera(session: DriveSession, startLoop: Boolean = true) {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(this)

        cameraProviderFuture.addListener({
            try {
                val provider = cameraProviderFuture.get()
                // Stop may win while CameraX is still resolving its provider. Binding after
                // the stop barrier started would resurrect the camera on a dying service.
                if (session.stopping || this.session !== session) {
                    provider.unbindAll()
                    return@addListener
                }
                cameraProvider = provider

                val analyzer = FrameAnalyzer { frame ->
                    // Channel.trySend is thread-safe and non-blocking. Sending directly
                    // closes the Stop/unbind race in which an extra coroutine had not yet
                    // delivered an already-finished frame before teardown began.
                    frameChannel.trySend(frame)
                }
                frameAnalyzer = analyzer

                val resolutionSelector = androidx.camera.core.resolutionselector.ResolutionSelector.Builder()
                    .setResolutionStrategy(
                        androidx.camera.core.resolutionselector.ResolutionStrategy(
                            android.util.Size(
                                DriveConstants.TARGET_WIDTH,
                                DriveConstants.TARGET_HEIGHT
                            ),
                            androidx.camera.core.resolutionselector.ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER
                        )
                    )
                    .build()

                val imageAnalysis = ImageAnalysis.Builder()
                    .setResolutionSelector(resolutionSelector)
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
                    .build()
                    .also { it.setAnalyzer(analysisExecutor, analyzer) }

                val cameraSelector = CameraSelector.Builder()
                    .requireLensFacing(CameraSelector.LENS_FACING_BACK)
                    .build()

                // Unbind any existing use cases
                provider.unbindAll()

                // Bind to this LifecycleService's lifecycle
                val camera = provider.bindToLifecycle(
                    this,
                    cameraSelector,
                    imageAnalysis
                )

                session.cameraState = DriveSession.CameraState.ACTIVE

                // Observe camera state for conflict detection
                observeCameraState(camera.cameraInfo, session)

                // Start the tick loop after camera settles
                serviceScope.launch {
                    delay(DriveConstants.CAMERA_SETTLE_MS)
                    if (startLoop && session.state == DriveSession.State.RUNNING) {
                        startTickLoop(session)
                    }
                }

                Log.i(TAG, "CameraX bound successfully")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to bind camera", e)
                session.cameraState = DriveSession.CameraState.UNAVAILABLE
                notifyStatus()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    /**
     * Observes CameraX state changes to detect when another app takes the
     * camera (e.g., a video call) and automatically recover when it becomes
     * available again.
     */
    private fun observeCameraState(cameraInfo: CameraInfo, session: DriveSession) {
        val observer = Observer<CameraState> { state ->
            when (state.type) {
                CameraState.Type.OPEN -> {
                    if (session.cameraState == DriveSession.CameraState.PAUSED_CONFLICT) {
                        Log.i(TAG, "Camera recovered from conflict")
                        session.cameraState = DriveSession.CameraState.ACTIVE
                        if (session.state == DriveSession.State.RUNNING) {
                            startTickLoop(session)
                        }
                        notifyStatus()
                    }
                    session.cameraState = DriveSession.CameraState.ACTIVE
                }
                CameraState.Type.CLOSED -> {
                    if (!shouldTreatCameraClosedAsConflict(session.stopping)) {
                        // CLOSED is the expected result of handleStop's unbindAll(). The
                        // active tick owns the final capture/drain barrier and must not be
                        // cancelled by this lifecycle callback.
                        session.cameraState = DriveSession.CameraState.UNAVAILABLE
                        notifyStatus()
                        return@Observer
                    }
                    Log.w(TAG, "Camera closed — another app may have taken it")
                    session.cameraState = DriveSession.CameraState.PAUSED_CONFLICT
                    tickJob?.cancel()
                    tickJob = null
                    notificationController?.updateNotification(
                        getString(R.string.drive_notification_paused_camera),
                        isPaused = false,
                        found = session.found,
                        checked = session.checked
                    )
                    notifyStatus()
                }
                CameraState.Type.CLOSING -> {
                    // Transitional — wait for CLOSED
                }
                CameraState.Type.OPENING -> {
                    // Transitional — wait for OPEN
                }
                CameraState.Type.PENDING_OPEN -> {
                    Log.i(TAG, "Camera pending open — waiting for availability")
                    session.cameraState = DriveSession.CameraState.PAUSED_CONFLICT
                    notifyStatus()
                }
            }
        }
        cameraStateObserver = observer

        // Camera state must be observed on the main thread
        ContextCompat.getMainExecutor(this).execute {
            cameraInfo.cameraState.observe(this, observer)
        }
    }

    // --- Location ---

    private fun startLocation(session: DriveSession) {
        locationTracker = LocationTracker(this, session)
        locationTracker?.start()
    }

    // --- Wake Lock ---

    /** Whether this build declares the camera foreground service permission. */
    private fun declaresCameraForegroundType(): Boolean = try {
        packageManager.getPackageInfo(packageName, PackageManager.GET_PERMISSIONS)
            .requestedPermissions
            ?.contains("android.permission.FOREGROUND_SERVICE_CAMERA") == true
    } catch (e: Exception) {
        false
    }

    private fun acquireWakeLock() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            DriveConstants.WAKE_LOCK_TAG
        ).apply {
            acquire(DriveConstants.WAKE_LOCK_TIMEOUT_MS)
        }
    }

    private fun releaseWakeLock() {
        try {
            if (wakeLock?.isHeld == true) {
                wakeLock?.release()
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error releasing wake lock", e)
        }
        wakeLock = null
    }

    // --- Tick Loop ---

    /**
     * Coroutine-based drive tick that mirrors the web client's setInterval(driveTick, 200).
     * Decides when to capture based on speed, distance, and GPS freshness.
     */
    private fun startTickLoop(session: DriveSession) {
        // Cancel any existing tick loop
        tickJob?.cancel()

        tickJob = serviceScope.launch {
            while (isActive && session.state == DriveSession.State.RUNNING && !session.stopping) {
                try {
                    driveTick(session)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    Log.e(TAG, "Error in drive tick", e)
                }
                delay(DriveConstants.FRAME_INTERVAL_MS)
            }
        }
    }

    /**
     * Single tick of the drive loop. Checks GPS freshness, spatial gating,
     * and requests one frame when appropriate.
     *
     * This mirrors the web client's driveTick() function exactly.
     */
    private suspend fun driveTick(session: DriveSession) {
        if (session.stopping || session.state != DriveSession.State.RUNNING) return
        if (session.cameraState != DriveSession.CameraState.ACTIVE) return
        if (System.currentTimeMillis() < session.visionBackoffUntilMs) {
            updateStatus("Vision service unavailable; retrying shortly", session)
            return
        }

        // Check GPS freshness
        if (!session.isGpsFresh()) {
            val status = if (session.currentLocation == null) {
                if (session.durationMs > 30_000) getString(R.string.drive_notification_waiting_gps)
                else getString(R.string.drive_notification_waiting_gps)
            } else {
                "GPS signal lost"
            }
            updateStatus(status, session)
            return
        }

        val pos = session.currentLocation ?: return
        val sinceLastCapture = System.currentTimeMillis() - session.lastCaptureAtMs

        // Speed-adaptive cadence, matching the web client's logic exactly
        val accuracy = session.currentAccuracy()
        val speed = session.currentSpeed()
        val coarse = accuracy == null || accuracy > DriveConstants.GPS_COARSE_M
        val knownStill = speed != null && speed <= DriveConstants.MOVING_SPEED_MS && !coarse

        val cadenceMs = when {
            knownStill -> DriveConstants.PARKED_CAPTURE_MS
            speed != null && speed > DriveConstants.MOVING_SPEED_MS ->
                (DriveConstants.TARGET_SPACING_M / speed * 1000).toLong()
                    .coerceIn(DriveConstants.MIN_CAPTURE_MS, DriveConstants.MAX_CAPTURE_MS)
            else -> DriveConstants.FALLBACK_CAPTURE_MS
        }

        // Distance gating
        val lastPos = session.lastCapturePos
        val moved = if (lastPos != null) {
            distMeters(
                lastPos.latitude, lastPos.longitude,
                pos.latitude, pos.longitude
            )
        } else {
            Double.MAX_VALUE
        }

        if (lastPos != null && moved < DriveConstants.TARGET_SPACING_M && sinceLastCapture < cadenceMs) {
            // Not enough distance or time — hold
            return
        }

        // Don't request another frame while one is in progress
        if (session.stillBusy) return

        // Request exactly one frame
        session.stillBusy = true
        val captureStartedAt = System.currentTimeMillis()

        val analyzer = frameAnalyzer
        if (analyzer == null) {
            session.stillBusy = false
            return
        }

        analyzer.requestCapture()

        // Wait for that frame with a timeout
        val frame = try {
            withTimeout(3_000L) {
                frameChannel.receive()
            }
        } catch (e: kotlinx.coroutines.TimeoutCancellationException) {
            analyzer.cancelPendingCapture()
            null
        } catch (e: CancellationException) {
            session.stillBusy = false
            throw e
        } catch (e: Exception) {
            null
        }

        session.stillBusy = false

        if (frame == null) {
            session.capBadTicks++
            if (session.capBadTicks >= 2) {
                updateStatus("Reconnecting camera", session)
                session.capBadTicks = 0
                startCamera(session, startLoop = false)
            }
            return
        }

        session.capBadTicks = 0
        session.lastCapturePos = pos
        session.lastCaptureAtMs = captureStartedAt
        session.captured++
        session.captureSeq++

        // Reserve before launching. DetectionDispatcher still limits actual network calls
        // to six, while this outer bound caps the waiting backlog as well.
        if (!session.tryBeginDetection(MAX_OUTSTANDING_DETECTIONS)) {
            updateStatus("Analysis queue full; keeping camera coverage bounded", session)
            return
        }

        // --- Detection pipeline: compress → detect → dedup → persist ---
        val capturePos = pos
        val captureHeading = session.currentHeading()
        val captureSpeed = session.currentSpeed()
        val captureAccuracy = session.currentAccuracy()
        val driveId = session.sessionId
        val seq = session.captureSeq

        // Fire-and-forget: detection runs concurrently, bounded by the dispatcher's semaphore.
        // The tick loop does not block on detection — it continues capturing at cadence.
        serviceScope.launch {
            try {
                processDetection(
                    frame, capturePos, captureHeading,
                    captureSpeed, captureAccuracy, driveId, seq, session
                )
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.e(TAG, "Detection pipeline error", e)
                session.recordError()
            } finally {
                session.finishDetection()
                if (session.stopping) {
                    updateStoppingStatus(session)
                    maybeFinalizeStop(session)
                }
            }
        }

        updateStatus(
            getString(R.string.drive_notification_scanning),
            session
        )
    }

    // --- Detection Pipeline ---

    /**
     * Full detection pipeline for one captured frame:
     * 1. Prepare the captured frame
     * 2. Call OpenAI detection API with SSE streaming
     * 3. Evaluate accept/reject/review decision
     * 4. Check for spatial/heading/temporal duplicates
     * 5. Persist accepted reports to Room
     */
    private suspend fun processDetection(
        frame: CapturedFrame,
        capturePos: android.location.Location,
        heading: Float?,
        speed: Float?,
        accuracy: Float?,
        driveId: String,
        seq: Int,
        session: DriveSession
    ) {
        val dispatcher = detectionDispatcher ?: return
        val deduper = duplicateDetector ?: return

        // 1. Send the one frame acquired for this scheduled sample.
        val prepared = withContext(Dispatchers.Default) {
            FrameCompressor.prepare(frame.jpeg)
        }

        try {
            val observedAtMs = frame.capturedAtMs
            val clientObservationId = "drive:$driveId:$seq"

            // 2. Detect through the selected personal/shared provider.
            if (visionProvider == "personal" && serviceUrl.isNotBlank()) {
                // Personal-key images still go directly to OpenAI. This separate,
                // metadata-only call lets aggregate usage include scans with no finding.
                serviceScope.launch(Dispatchers.IO) {
                    runCatching {
                        centralClient?.recordVisionActivity(
                            captureMode = "drive",
                            clientEventId = clientObservationId,
                            captureSource = "drive_live",
                            locationSource = "device_gps",
                        )
                    }.onFailure { Log.w(TAG, "Could not record personal vision activity", it) }
                }
            }
            val result = dispatcher.detect(
                prepared.analysisBase64,
                clientObservationId,
                capturePos.latitude,
                capturePos.longitude,
            )
            session.recordChecked()

            // 3. Evaluate decision
            if (result.decision == "reject") {
                // Rejected — frame is discarded, nothing persisted
                updateStatus(getString(R.string.drive_notification_scanning), session)
                return
            }

            if (result.decision == "review") {
                // Review-grade detections are counted but not persisted in Drive Mode
                // (mirrors web behaviour where review frames go to the queue but
                // are not auto-reported)
                updateStatus(getString(R.string.drive_notification_scanning), session)
                return
            }

            // 4. Build report entity
            val damageType = requireNotNull(result.damageType) {
                "Accepted detection must include damage_type"
            }
            val report = com.gauravsen.potholereporter.db.entities.ReportEntity(
                assessment = result.assessment,
                image_quality = result.imageQuality,
                damage_type = damageType,
                size = result.size,
                description = result.description,
                decision = result.decision,
                status = "draft",
                lat = capturePos.latitude,
                lng = capturePos.longitude,
                gps_accuracy = accuracy,
                speed_mps = speed,
                heading = heading,
                photo = prepared.thumbnailJpeg,
                photo_full = prepared.evidenceJpeg,
                drive_id = driveId,
                capture_source = "drive_live",
                source_event_key = clientObservationId,
                captured_at = observedAtMs / 1000.0,
                dedupe_eligible = true,
                detection_provider = if (visionProvider == "shared_server") {
                    "shared_server"
                } else {
                    "personal_openai"
                },
                detection_model = model,
                image_detail = detail,
                prompt_version = LlmContractGenerated.DETECT_PROMPT_VERSION,
                schema_version = LlmContractGenerated.DETECT_SCHEMA_VERSION,
                evidence_count = 1,
            )

            // 5. Dedup and persist
            val (insertedId, duplicate) = database.withTransaction {
                val (newId, duplicateMatch) = deduper.insertPending(report)
                val observation = CentralObservationEntity(
                    client_observation_id = report.source_event_key!!,
                    report_id = newId,
                    observed_at_ms = observedAtMs,
                    lat = capturePos.latitude,
                    lng = capturePos.longitude,
                    gps_accuracy_m = accuracy,
                    heading_deg = heading,
                    speed_mps = speed,
                    damage_type = damageType,
                    size = result.size,
                    // This must match the decoded data URL sent to shared detection.
                    // The thumbnail is a different JPEG and cannot verify its receipt.
                    image_hash = CentralServiceIdentity.sha256Hex(prepared.analysisJpeg),
                    detection_receipt = result.detectionReceipt,
                    detector_provider = report.detection_provider ?: "unknown",
                    detector_model = model,
                    image_detail = detail,
                    evidence_count = 1,
                    drive_id = driveId,
                    local_match_report_id = duplicateMatch?.priorReport?.id,
                    local_match_kind = duplicateMatch?.kind,
                )
                database.centralObservationDao().enqueue(observation)
                newId to duplicateMatch
            }
            // WorkManager is the sole owner of report/tender synchronization. Keeping
            // foreground and background senders separate avoids duplicate requests and
            // shared-credit usage when both happen to run on a connected device.
            UploadWorker.enqueue(this, ensureAfterCurrent = true)

            if (duplicate != null) {
                session.recordFinding(isDuplicate = true)
                Log.d(TAG, "Possible duplicate (${duplicate.kind}) queued for central confirmation")
            } else {
                session.recordFinding(isDuplicate = false)
                Log.i(TAG, "New road damage report queued: id=$insertedId, type=$damageType")
            }

            updateStatus(
                getString(R.string.drive_notification_checked, session.checked, session.found),
                session
            )
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            session.recordVisionFailure(e.message ?: "Vision request failed")
            updateStatus("Vision unavailable; retrying shortly", session)
            Log.e(TAG, "Detection failed for seq=$seq", e)
        }
    }

    /**
     * Saves the drive session summary to Room when the drive ends.
     */
    private suspend fun saveDriveSession(session: DriveSession) {
        try {
            val entity = com.gauravsen.potholereporter.db.entities.DriveSessionEntity(
                id = session.sessionId,
                started_at = session.startedAtMs / 1000.0,
                ended_at = System.currentTimeMillis() / 1000.0,
                checked = session.checked,
                found = session.found,
                already = session.already,
            )
            database.driveSessionDao().insertOrUpdate(entity)
            Log.i(TAG, "Drive session saved: ${session.sessionId}")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to save drive session", e)
        }
    }

    // --- Status Updates ---

    private fun updateStatus(status: String, session: DriveSession) {
        if (session.stopping) {
            updateStoppingStatus(session)
            return
        }
        notificationController?.updateNotification(
            status,
            isPaused = session.state == DriveSession.State.PAUSED,
            found = session.found,
            checked = session.checked
        )
        notifyStatus()
    }

    private fun notifyStatus() {
        val s = session ?: return
        statusListener?.invoke(s.snapshot())
    }

    // --- Utility ---

    /**
     * Haversine distance in meters, matching the web client's distMeters().
     */
    private fun distMeters(lat1: Double, lng1: Double, lat2: Double, lng2: Double): Double {
        val r = 6371000.0
        val rad = Math.PI / 180.0
        val dLat = (lat2 - lat1) * rad
        val dLng = (lng2 - lng1) * rad
        val a = Math.sin(dLat / 2).let { it * it } +
            Math.cos(lat1 * rad) * Math.cos(lat2 * rad) *
            Math.sin(dLng / 2).let { it * it }
        return 2 * r * Math.asin(Math.sqrt(a))
    }
}
