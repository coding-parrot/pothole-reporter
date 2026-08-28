package dev.aiengg.potholereporter.drive

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Matrix
import android.os.Handler
import android.os.Looper
import android.util.Size
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.CameraState
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.FallbackStrategy
import androidx.camera.video.FileOutputOptions
import androidx.camera.video.Quality
import androidx.camera.video.QualitySelector
import androidx.camera.video.Recorder
import androidx.camera.video.Recording
import androidx.camera.video.VideoCapture
import androidx.camera.video.VideoRecordEvent
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

data class NativeVideoSegment(
    val sessionId: String,
    val filePath: String,
    val startedAtMs: Long,
    val endedAtMs: Long,
    val durationMs: Long,
    val bytes: Long,
    val errorCode: Int?,
    val complete: Boolean
)

/**
 * Owns the background CameraX analysis use case.
 *
 * CameraManager.AvailabilityCallback is deliberately not used: Android reports a camera
 * as unavailable when this app itself opens it. CameraX CameraState is scoped to our use
 * case and reports when a higher-priority client, such as a video call, takes the camera.
 */
class NativeDriveCameraManager(
    private val context: Context,
    private val lifecycleOwner: LifecycleOwner,
    initialVideoRecordingEnabled: Boolean,
    private val sessionId: String,
    private val onCameraStateChange: (Boolean, String?) -> Unit,
    private val onCameraRecoveryRequired: (NativeCameraRecoveryAction, String) -> Unit,
    private val onRecordingStateChange: (enabled: Boolean, recording: Boolean, supported: Boolean, message: String?) -> Unit,
    private val onSegmentFinalized: (NativeVideoSegment) -> Unit
) {
    private enum class VideoSegmentTerminalState {
        ACTIVE,
        COMMITTED,
        DISCARDED
    }

    private data class ActiveVideoSegment(
        val sequence: Int,
        val file: File,
        val startedAtMs: Long,
        val completion: CompletableDeferred<NativeVideoSegment?>,
        val storageReservation: NativeMediaStorageQuota.Reservation,
        var recording: Recording? = null,
        var terminalState: VideoSegmentTerminalState = VideoSegmentTerminalState.ACTIVE,
        val discardedMediaCleanup: NativeDiscardedMediaCleanup = NativeDiscardedMediaCleanup()
    )

    private data class PreparedStorage(
        val directoryReady: Boolean,
        val reservation: NativeMediaStorageQuota.Reservation?,
        val accountedBytes: Long
    )

    private var cameraProvider: ProcessCameraProvider? = null
    private var imageAnalysis: ImageAnalysis? = null
    private var previewUseCase: Preview? = null
    // Main-thread-only mirror of whether previewUseCase is currently part of the
    // LifecycleService-owned CameraX graph. Analysis and VideoCapture stay bound when
    // the Activity-owned preview is detached.
    private var previewIsBound = false
    private val previewStateLock = Any()
    private var previewSurfaceProvider: Preview.SurfaceProvider? = null
    private var previewStateGeneration = 0L
    private var boundCamera: Camera? = null
    private var videoCapture: VideoCapture<Recorder>? = null
    private var activeVideoSegment: ActiveVideoSegment? = null
    private val cameraExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private val captureMutex = Mutex()
    private val pendingFrameLock = Any()
    private var pendingFrameRequest: PendingFrameRequest? = null
    private var captureGeneration = 0L
    private val recordingHandler = Handler(Looper.getMainLooper())
    private val storageScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    // MP4 segments and sparse JPEG keyframes share one reserving ledger. The first writer
    // inventories the footage root off the main thread; later writes are constant-time.
    private val storageQuota = NativeMediaStorageQuota()
    private val storageInventoryMutex = Mutex()
    private var storagePreparationInFlight = false
    private var storagePreparationGeneration = 0L

    @Volatile
    var isVideoRecordingEnabled: Boolean = initialVideoRecordingEnabled
        private set

    @Volatile
    var isVideoRecording: Boolean = false
        private set

    @Volatile
    var isVideoSupported: Boolean = true
        private set

    private var recordingSequence = 0
    private var storageBlocked = false
    private val restartRecording = Runnable { startRecordingSegment() }

    @Volatile
    var isCameraReady: Boolean = false
        private set

    private var requested = false
    private var destroyed = false
    private var lastSignalledRecoveryErrorCode: Int? = null
    @Volatile private var fullyClosed = false
    private val permanentCloseLock = Any()

    companion object {
        const val BURST_COUNT = 3
        const val MIN_DETECTION_SOURCE_FRAMES = 2
        private const val MAX_FRAME_WAIT_MS = 2_000L
        private const val MAX_SOURCE_BURST_MS = 3_000L
        private const val SOURCE_SAMPLE_COUNT = NativeRollingBurstWindow.CAPACITY
        private const val SOURCE_SAMPLE_SPACING_MS = 180L
        private const val RECORDING_SEGMENT_MS = 60_000L
        private const val RECORDING_RESTART_DELAY_MS = 1_000L
        private const val RECORDING_FINALIZE_TIMEOUT_MS = 12_000L
    }

    fun startCamera(onReady: (Boolean) -> Unit = {}) {
        if (destroyed) {
            onReady(false)
            return
        }
        requested = true
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            if (!requested || destroyed) {
                onReady(false)
                return@addListener
            }
            try {
                cameraProvider = future.get()
                onReady(bindCameraUseCases())
            } catch (e: Exception) {
                publishState(false, "Camera could not start: ${e.message ?: "unknown error"}")
                onReady(false)
            }
        }, ContextCompat.getMainExecutor(context))
    }

    @SuppressLint("UnsafeOptInUsageError")
    private fun bindCameraUseCases(): Boolean {
        val provider = cameraProvider ?: return false
        if (!requested || destroyed) return false

        return try {
            // A new graph gets one recovery signal per critical code. CameraState can
            // repeat the same error while moving through CLOSING and CLOSED.
            lastSignalledRecoveryErrorCode = null
            boundCamera?.cameraInfo?.cameraState?.removeObservers(lifecycleOwner)
            clearPendingFrameRequest()
            provider.unbindAll()
            previewIsBound = false

            val analysis = ImageAnalysis.Builder()
                .setTargetResolution(Size(1280, 720))
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build().also { analysis ->
                    imageAnalysis = analysis
                    analysis.setAnalyzer(cameraExecutor, ::processImageProxy)
                }

            val desiredPreviewProvider = synchronized(previewStateLock) {
                previewSurfaceProvider
            }
            val preview = Preview.Builder().build().also { preview ->
                previewUseCase = preview
                desiredPreviewProvider?.let(preview::setSurfaceProvider)
            }

            fun bindWithVideo(selector: QualitySelector): Camera {
                val recorder = Recorder.Builder().setQualitySelector(selector).build()
                val candidate = VideoCapture.withOutput(recorder)
                val camera = if (desiredPreviewProvider != null) {
                    provider.bindToLifecycle(
                        lifecycleOwner,
                        CameraSelector.DEFAULT_BACK_CAMERA,
                        preview,
                        analysis,
                        candidate
                    )
                } else {
                    provider.bindToLifecycle(
                        lifecycleOwner,
                        CameraSelector.DEFAULT_BACK_CAMERA,
                        analysis,
                        candidate
                    )
                }
                previewIsBound = desiredPreviewProvider != null
                videoCapture = candidate
                return camera
            }
            boundCamera = try {
                isVideoSupported = true
                bindWithVideo(QualitySelector.from(
                    // SD is the requested recording profile. The fallback searches below
                    // it first and only moves higher when a camera exposes no SD profile.
                    Quality.SD,
                    FallbackStrategy.lowerQualityOrHigherThan(Quality.SD)
                ))
            } catch (_: Exception) {
                provider.unbindAll()
                previewIsBound = false
                // Some cameras cannot run Preview + Analysis + VideoCapture together.
                // Detection stays available and the UI says video is unavailable.
                videoCapture = null
                isVideoSupported = false
                onRecordingStateChange(isVideoRecordingEnabled, false, false, "Video recording unavailable on this camera")
                if (desiredPreviewProvider != null) {
                    provider.bindToLifecycle(
                        lifecycleOwner,
                        CameraSelector.DEFAULT_BACK_CAMERA,
                        preview,
                        analysis
                    ).also { previewIsBound = true }
                } else {
                    provider.bindToLifecycle(
                        lifecycleOwner,
                        CameraSelector.DEFAULT_BACK_CAMERA,
                        analysis
                    )
                }
            }.also { camera ->
                camera.cameraInfo.cameraState.observe(lifecycleOwner, ::handleCameraState)
            }
            if (isVideoRecordingEnabled && isVideoSupported) startRecordingSegment()
            // An attach/detach request may have run before previewUseCase existed and
            // returned without work. Reconcile the newest generation once this graph is
            // complete so that early requests cannot be lost.
            scheduleLatestPreviewReconciliation()
            true
        } catch (e: Exception) {
            publishState(false, "Camera unavailable: ${e.message ?: "another app may be using it"}")
            false
        }
    }

    private fun handleCameraState(state: CameraState) {
        val error = state.error
        if (error != null) {
            val action = NativeCameraRecoveryPolicy.actionFor(error.code, error.type)
            val reason = cameraErrorText(error)
            // Even recoverable errors can first arrive with Type.CLOSING. Mark the
            // stream unavailable immediately, but leave ordinary contention bound so
            // CameraX can execute its documented automatic recovery.
            clearPendingFrameRequest()
            publishState(false, reason)
            if (action != NativeCameraRecoveryAction.WAIT_FOR_CAMERAX &&
                requested && lastSignalledRecoveryErrorCode != error.code) {
                lastSignalledRecoveryErrorCode = error.code
                onCameraRecoveryRequired(action, reason)
            }
            return
        }
        when (state.type) {
            CameraState.Type.OPEN -> {
                lastSignalledRecoveryErrorCode = null
                publishState(true, null)
                if (isVideoRecordingEnabled && !isVideoRecording) {
                    recordingHandler.removeCallbacks(restartRecording)
                    recordingHandler.postDelayed(restartRecording, RECORDING_RESTART_DELAY_MS)
                }
            }
            CameraState.Type.PENDING_OPEN -> {
                clearPendingFrameRequest()
                publishState(false, "Waiting for camera")
            }
            CameraState.Type.CLOSED -> {
                clearPendingFrameRequest()
                if (requested) {
                    val reason =
                        "Camera access was interrupted. Detection and video are paused; capture resumes automatically when access returns."
                    publishState(false, reason)
                }
            }
            else -> Unit
        }
    }

    private fun cameraErrorText(error: CameraState.StateError): String = when (error.code) {
        CameraState.ERROR_CAMERA_IN_USE,
        CameraState.ERROR_MAX_CAMERAS_IN_USE ->
            "Camera is in use by another app. Detection and video are paused; capture resumes automatically when access returns."
        CameraState.ERROR_CAMERA_DISABLED ->
            "Camera access is blocked by Android privacy controls or device policy. Detection and video are paused."
        CameraState.ERROR_DO_NOT_DISTURB_MODE_ENABLED ->
            "Camera access is blocked by Do Not Disturb. Detection and video are paused."
        CameraState.ERROR_STREAM_CONFIG ->
            "Camera configuration failed. Detection and video are paused."
        CameraState.ERROR_CAMERA_FATAL_ERROR ->
            "Camera hardware failed. Detection and video are paused."
        else ->
            "Camera access is temporarily unavailable. Detection and video are paused; capture resumes automatically when access returns."
    }

    private fun publishState(available: Boolean, reason: String?) {
        isCameraReady = available
        onCameraStateChange(available, reason)
    }

    private fun processImageProxy(imageProxy: ImageProxy) {
        val request = synchronized(pendingFrameLock) {
            pendingFrameRequest?.also { pendingFrameRequest = null }
        }
        var ownedBitmap: Bitmap? = null
        try {
            if (request == null) return
            val capturedAtMs = System.currentTimeMillis()
            val sourceTimestampNs = imageProxy.imageInfo.timestamp.takeIf { it > 0L }
                ?: System.nanoTime()
            val raw = imageProxy.toBitmap()
            ownedBitmap = raw
            val rotation = imageProxy.imageInfo.rotationDegrees
            val upright = if (rotation == 0) raw else {
                val matrix = Matrix().apply { postRotate(rotation.toFloat()) }
                Bitmap.createBitmap(raw, 0, 0, raw.width, raw.height, matrix, true)
                    .also {
                        if (it !== raw) {
                            raw.recycleSafely()
                            ownedBitmap = it
                        }
                    }
            }
            val frame = CapturedFrame(
                upright,
                capturedAtMs,
                sourceTimestampNs,
                request.generation
            )
            if (request.deferred.complete(frame)) ownedBitmap = null else upright.recycleSafely()
        } catch (error: OutOfMemoryError) {
            // The analyzer runs on its own executor, so an allocation failure here will
            // never reach captureBurst unless the deferred carries it across. Recycle any
            // partial conversion and let the service stop with a visible low-memory error.
            request?.deferred?.completeExceptionally(error)
            ownedBitmap?.recycleSafely()
        } catch (_: Exception) {
            request?.deferred?.complete(null)
            ownedBitmap?.recycleSafely()
        } finally {
            imageProxy.close()
        }
    }

    suspend fun captureBurst(): Pair<List<BurstFrame>, Int>? {
        if (!isCameraReady || !requested || destroyed) return null
        return captureMutex.withLock {
            val sourceFrames = mutableListOf<CapturedFrame>()
            var ownershipTransferred = false
            try {
                val collected = withTimeoutOrNull(MAX_SOURCE_BURST_MS) {
                    repeat(SOURCE_SAMPLE_COUNT) { index ->
                        if (index > 0) delay(SOURCE_SAMPLE_SPACING_MS)
                        awaitNextFrame()?.let(sourceFrames::add)
                    }
                    true
                } == true
                if (!collected) return@withLock null
                val generation = sourceFrames.firstOrNull()?.cameraGeneration
                    ?: return@withLock null
                val sourceIndexes = NativeRollingBurstWindow.selectSourceIndexes(
                    sourceFrames.map {
                        NativeRollingBurstWindow.Sample(
                            it.capturedAtMs,
                            it.sourceTimestampNs,
                            it.cameraGeneration
                        )
                    },
                    nowMs = System.currentTimeMillis(),
                    expectedGeneration = generation
                ) ?: return@withLock null
                val selectedIndexSet = sourceIndexes.toSet()
                val selected = sourceIndexes.map { index ->
                    val frame = sourceFrames[index]
                    BurstFrame(
                        frame.bitmap,
                        FrameQualityEvaluator.evaluateRoadFrameQuality(frame.bitmap),
                        frame.capturedAtMs,
                        frame.sourceTimestampNs,
                        frame.cameraGeneration
                    )
                }
                sourceFrames.forEachIndexed { index, frame ->
                    if (index !in selectedIndexSet) frame.bitmap.recycleSafely()
                }
                if (selected.size < MIN_DETECTION_SOURCE_FRAMES) {
                    selected.forEach { it.bitmap.recycleSafely() }
                    return@withLock null
                }
                val result = Pair(
                    selected,
                    FrameQualityEvaluator.selectBestBurstIndex(selected.map(BurstFrame::quality))
                )
                ownershipTransferred = true
                result
            } finally {
                if (!ownershipTransferred) {
                    sourceFrames.forEach { it.bitmap.recycleSafely() }
                }
            }
        }
    }

    private suspend fun awaitNextFrame(): CapturedFrame? {
        val request = synchronized(pendingFrameLock) {
            if (!requested || destroyed || !isCameraReady) return null
            PendingFrameRequest(
                generation = captureGeneration,
                deferred = CompletableDeferred()
            ).also {
                pendingFrameRequest?.deferred?.cancel()
                pendingFrameRequest = it
            }
        }
        var delivered = false
        try {
            val frame = withTimeoutOrNull(MAX_FRAME_WAIT_MS) { request.deferred.await() }
            delivered = frame != null
            return frame
        } finally {
            if (!delivered) {
                // Parent cancellation (Pause/Stop or the outer burst timeout) bypasses
                // withTimeoutOrNull's ordinary null path. Cancel the exact request in
                // finally so an analyzer already holding it cannot complete an orphaned
                // deferred with a Bitmap that nobody will recycle.
                synchronized(pendingFrameLock) {
                    if (pendingFrameRequest === request) pendingFrameRequest = null
                }
                request.deferred.cancel()
            }
        }
    }

    private fun Bitmap.recycleSafely() {
        if (!isRecycled) recycle()
    }

    private data class PendingFrameRequest(
        val generation: Long,
        val deferred: CompletableDeferred<CapturedFrame?>
    )

    private data class CapturedFrame(
        val bitmap: Bitmap,
        val capturedAtMs: Long,
        val sourceTimestampNs: Long,
        val cameraGeneration: Long
    )

    suspend fun setVideoRecordingEnabled(enabled: Boolean) {
        val completion = withContext(Dispatchers.Main.immediate) {
            isVideoRecordingEnabled = enabled
            if (enabled) storageBlocked = false
            if (!enabled) {
                stopActiveRecording()
            } else {
                if (!isVideoSupported) {
                    onRecordingStateChange(true, false, false, "Video recording unavailable on this camera")
                } else if (requested && !destroyed) {
                    startRecordingSegment()
                }
                null
            }
        }
        awaitFinalization(completion)
        if (!enabled) withContext(Dispatchers.Main.immediate) {
            onRecordingStateChange(false, false, isVideoSupported, "Scanning frames; video is not saved")
        }
    }

    fun attachPreview(surfaceProvider: Preview.SurfaceProvider) {
        updatePreviewSurfaceProvider(surfaceProvider)
    }

    fun detachPreview() {
        updatePreviewSurfaceProvider(null)
    }

    private fun updatePreviewSurfaceProvider(surfaceProvider: Preview.SurfaceProvider?) {
        val generation = synchronized(previewStateLock) {
            previewSurfaceProvider = surfaceProvider
            ++previewStateGeneration
        }
        ContextCompat.getMainExecutor(context).execute {
            reconcilePreviewSurfaceProvider(generation)
        }
    }

    private fun scheduleLatestPreviewReconciliation() {
        val generation = synchronized(previewStateLock) { previewStateGeneration }
        ContextCompat.getMainExecutor(context).execute {
            reconcilePreviewSurfaceProvider(generation)
        }
    }

    /**
     * Reconciles only the Activity-owned Preview use case. CameraX's service-owned
     * ImageAnalysis and VideoCapture use cases deliberately remain bound across Back,
     * Home, and Activity destruction.
     *
     * Calls are generation checked because an Activity can detach its old PreviewView
     * after a replacement Activity has already attached a new one. A stale queued detach
     * must never unbind that replacement preview.
     */
    private fun reconcilePreviewSurfaceProvider(generation: Long) {
        val surfaceProvider = synchronized(previewStateLock) {
            if (generation != previewStateGeneration || fullyClosed) return
            previewSurfaceProvider
        }
        if (!requested || destroyed) return
        val provider = cameraProvider ?: return
        val preview = previewUseCase ?: return

        if (surfaceProvider == null) {
            if (previewIsBound) {
                // Unbind Preview before removing its Activity-owned surface. Removing a
                // provider from a still-bound Preview can tear down the whole camera graph
                // on affected devices even though Analysis should continue in the FGS.
                val detached = runCatching { provider.unbind(preview) }.isSuccess
                if (!detached) return
                previewIsBound = false
            }
            preview.setSurfaceProvider(null)
            return
        }

        preview.setSurfaceProvider(surfaceProvider)
        if (previewIsBound) return
        val attached = runCatching {
            provider.bindToLifecycle(
                lifecycleOwner,
                CameraSelector.DEFAULT_BACK_CAMERA,
                preview
            )
        }.onSuccess {
            previewIsBound = true
        }.isSuccess
        if (attached) return

        // Some devices accept Analysis + VideoCapture but cannot add Preview to that
        // already-configured graph. Use the normal controlled graph bind exactly once:
        // it retries Preview + Analysis + VideoCapture, then its existing fallback drops
        // unsupported video and binds Preview + Analysis. This preserves the transparent
        // live view instead of silently continuing with no visible preview.
        if (generation == synchronized(previewStateLock) { previewStateGeneration }) {
            bindCameraUseCases()
        }
    }

    @SuppressLint("MissingPermission")
    private fun startRecordingSegment() {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            recordingHandler.post(::startRecordingSegment)
            return
        }
        if (!requested || destroyed || !isCameraReady || !isVideoRecordingEnabled || !isVideoSupported ||
            activeVideoSegment != null || storageBlocked || storagePreparationInFlight
        ) return

        // File walking and filesystem capacity checks are disk operations. Serialize them
        // here and revalidate all camera state on the main thread before touching Recorder;
        // camera-open callbacks and segment-rollover callbacks may both request a start.
        val footageRoot = File(context.filesDir, "footage/$sessionId")
        val generation = ++storagePreparationGeneration
        storagePreparationInFlight = true
        storageScope.launch {
            val prepared = runCatching {
                // Reserving first is also a barrier against a reconciliation pass that
                // snapshotted the service before this Drive became active. Create and
                // revalidate the session directory under that same filesystem mutex only
                // after the stale pass has finished, so it cannot delete the empty root
                // between mkdirs and recorder startup.
                val reservation =
                    reserveMediaBytes(NativeMediaStorageQuota.VIDEO_SEGMENT_RESERVATION_BYTES)
                val directoryReady = NativeMediaFilesystemMutation.mutex.withLock {
                    footageRoot.isDirectory || (!footageRoot.exists() && footageRoot.mkdirs())
                }
                if (!directoryReady) reservation?.let(storageQuota::release)
                PreparedStorage(
                    directoryReady = directoryReady,
                    reservation = reservation.takeIf { directoryReady },
                    accountedBytes = storageQuota.accountedBytes() ?: 0L
                )
            }
            withContext(Dispatchers.Main.immediate) {
                val result = prepared.getOrNull()
                if (generation != storagePreparationGeneration || destroyed || fullyClosed) {
                    result?.reservation?.let(storageQuota::release)
                    return@withContext
                }
                storagePreparationInFlight = false

                // The user may pause/stop/toggle recording, or CameraX may rebind, while
                // storage is being prepared. A stale completion must never start a segment.
                if (!requested || !isCameraReady || !isVideoRecordingEnabled || !isVideoSupported ||
                    activeVideoSegment != null || storageBlocked
                ) {
                    result?.reservation?.let(storageQuota::release)
                    return@withContext
                }

                if (result == null || !result.directoryReady) {
                    storageBlocked = true
                    onRecordingStateChange(true, false, true, "Video could not start: local storage is unavailable")
                    return@withContext
                }
                val reservation = result.reservation
                if (reservation == null) {
                    blockRecordingForStorage(result.accountedBytes)
                    return@withContext
                }
                startPreparedRecordingSegment(footageRoot, reservation)
            }
        }
    }

    private fun blockRecordingForStorage(accountedBytes: Long) {
        storageBlocked = true
        val videoWouldExceedCap = accountedBytes >
            NativeMediaStorageQuota.MAX_TOTAL_BYTES -
            NativeMediaStorageQuota.VIDEO_SEGMENT_RESERVATION_BYTES
        val message = if (videoWouldExceedCap)
            "Video stopped: 4 GB Drive media limit reached; share or delete old media"
        else "Video stopped: keep at least 500 MB free"
        onRecordingStateChange(true, false, true, message)
    }

    @SuppressLint("MissingPermission")
    private fun startPreparedRecordingSegment(
        footageRoot: File,
        reservation: NativeMediaStorageQuota.Reservation
    ) {
        if (!requested || destroyed || !isCameraReady || !isVideoRecordingEnabled || !isVideoSupported ||
            activeVideoSegment != null || storageBlocked
        ) {
            storageQuota.release(reservation)
            return
        }
        val recorder = videoCapture?.output ?: run {
            storageQuota.release(reservation)
            return
        }
        if (!footageRoot.isDirectory) {
            storageQuota.release(reservation)
            storageBlocked = true
            onRecordingStateChange(true, false, true, "Video could not start: local storage is unavailable")
            return
        }
        val sequence = ++recordingSequence
        val file = File(footageRoot, "segment_${sequence.toString().padStart(4, '0')}.mp4")
        val completion = CompletableDeferred<NativeVideoSegment?>()
        val segment = ActiveVideoSegment(
            sequence, file, System.currentTimeMillis(), completion, reservation
        )
        activeVideoSegment = segment
        val options = FileOutputOptions.Builder(file)
            .setDurationLimitMillis(RECORDING_SEGMENT_MS)
            .setFileSizeLimit(NativeMediaStorageQuota.VIDEO_SEGMENT_RESERVATION_BYTES)
            .build()
        try {
            segment.recording = recorder.prepareRecording(context, options)
                .start(ContextCompat.getMainExecutor(context)) { event ->
                    handleRecordingEvent(segment, event)
                }
        } catch (error: Exception) {
            if (activeVideoSegment === segment) activeVideoSegment = null
            val discarded = discardReservedVideoFile(segment)
            if (!discarded) storageBlocked = true
            completion.complete(null)
            isVideoRecording = false
            val detail = if (discarded) error.message ?: "recorder error"
            else "the incomplete clip could not be removed; recording is blocked"
            onRecordingStateChange(true, false, true, "Video could not start: $detail")
        }
    }

    private fun handleRecordingEvent(segment: ActiveVideoSegment, event: VideoRecordEvent) {
        when (event) {
            is VideoRecordEvent.Start -> {
                if (activeVideoSegment !== segment) return
                isVideoRecording = true
                onRecordingStateChange(true, true, true, "Recording video locally")
            }
            is VideoRecordEvent.Finalize -> finalizeVideoSegment(segment, event)
            else -> Unit
        }
    }

    private fun finalizeVideoSegment(segment: ActiveVideoSegment, event: VideoRecordEvent.Finalize) {
        if (activeVideoSegment !== segment) {
            // stop()/close() may time out before CameraX emits Finalize. The recorder can
            // extend or recreate its output after our first cleanup attempt, so reconcile
            // the discarded writer again instead of silently returning with stale quota.
            val wasDiscarded = synchronized(segment) {
                segment.terminalState == VideoSegmentTerminalState.DISCARDED
            }
            if (wasDiscarded) {
                val discarded = discardReservedVideoFile(segment)
                if (!discarded) {
                    storageBlocked = true
                    if (!fullyClosed) onRecordingStateChange(
                        isVideoRecordingEnabled,
                        false,
                        isVideoSupported,
                        "Video stopped: a late incomplete clip could not be removed; recording is blocked"
                    )
                }
                segment.completion.complete(null)
                return
            }
            if (segment.completion.isCompleted) return
        }
        if (activeVideoSegment === segment) activeVideoSegment = null
        isVideoRecording = false
        val endedAtMs = System.currentTimeMillis()
        // The filesystem is the quota source of truth. Recorder stats can temporarily be
        // larger than the finalized file; charging that value while deletion later credits
        // the real file length would permanently inflate the process-local ledger.
        val bytes = segment.file.length().coerceAtLeast(0L)
        val durationMs = (event.recordingStats.recordedDurationNanos / 1_000_000L).coerceAtLeast(0L)
        val hasFile = segment.file.isFile && bytes > 0L
        val rollover = event.error == VideoRecordEvent.Finalize.ERROR_NONE ||
            event.error == VideoRecordEvent.Finalize.ERROR_DURATION_LIMIT_REACHED ||
            event.error == VideoRecordEvent.Finalize.ERROR_FILE_SIZE_LIMIT_REACHED
        val complete = hasFile && rollover
        var result = if (hasFile) NativeVideoSegment(
            sessionId = sessionId,
            filePath = segment.file.absolutePath,
            startedAtMs = segment.startedAtMs,
            endedAtMs = endedAtMs,
            durationMs = durationMs,
            bytes = bytes,
            errorCode = event.error.takeIf { it != VideoRecordEvent.Finalize.ERROR_NONE },
            complete = complete
        ) else null
        var quotaRejected = false
        if (result != null) {
            if (commitReservedVideoFile(segment, bytes)) {
                onSegmentFinalized(result)
            } else {
                // CameraX's file-size limit may be crossed by a final encoded sample.
                // Such a file must never silently exceed the shared Drive-media cap.
                quotaRejected = true
                storageBlocked = true
                discardReservedVideoFile(segment)
                result = null
            }
        } else {
            discardReservedVideoFile(segment)
        }
        segment.completion.complete(result)

        if (quotaRejected) {
            onRecordingStateChange(
                isVideoRecordingEnabled,
                false,
                true,
                if (segment.file.isFile)
                    "Video stopped: an oversized clip could not be removed; recording is blocked"
                else "Video stopped: a clip exceeded its 80 MB storage reservation and was discarded"
            )
        } else when (event.error) {
            VideoRecordEvent.Finalize.ERROR_INSUFFICIENT_STORAGE -> {
                storageBlocked = true
                onRecordingStateChange(isVideoRecordingEnabled, false, true, "Video stopped: storage is too low")
            }
            VideoRecordEvent.Finalize.ERROR_INVALID_OUTPUT_OPTIONS,
            VideoRecordEvent.Finalize.ERROR_RECORDER_ERROR -> {
                storageBlocked = true
                onRecordingStateChange(isVideoRecordingEnabled, false, true, "Video stopped after a recorder error")
            }
            VideoRecordEvent.Finalize.ERROR_SOURCE_INACTIVE ->
                onRecordingStateChange(isVideoRecordingEnabled, false, true, "Camera interrupted; video will resume automatically")
            else -> onRecordingStateChange(
                isVideoRecordingEnabled,
                false,
                true,
                if (rollover) "Video segment saved locally" else "Video interrupted; retrying"
            )
        }
        if (requested && !destroyed && isVideoRecordingEnabled && !storageBlocked) {
            recordingHandler.removeCallbacks(restartRecording)
            recordingHandler.postDelayed(restartRecording, RECORDING_RESTART_DELAY_MS)
        }
    }

    private fun stopActiveRecording(): CompletableDeferred<NativeVideoSegment?>? {
        recordingHandler.removeCallbacks(restartRecording)
        val active = activeVideoSegment ?: return null
        runCatching { active.recording?.stop() }.onFailure {
            if (activeVideoSegment === active) activeVideoSegment = null
            val discarded = discardReservedVideoFile(active)
            if (!discarded) storageBlocked = true
            active.completion.complete(null)
            onRecordingStateChange(
                isVideoRecordingEnabled,
                false,
                isVideoSupported,
                if (discarded) "Video stopped after a recorder error; the incomplete clip was discarded"
                else "Video stopped after a recorder error; the incomplete clip could not be removed"
            )
        }
        return active.completion
    }

    private suspend fun awaitFinalization(completion: CompletableDeferred<NativeVideoSegment?>?) {
        if (completion == null) return
        val finalized = withTimeoutOrNull(RECORDING_FINALIZE_TIMEOUT_MS) { completion.await(); true } ?: false
        if (!finalized) withContext(Dispatchers.Main.immediate) {
            val active = activeVideoSegment
            var message = "Video finalization timed out"
            if (active?.completion === completion) {
                activeVideoSegment = null
                runCatching { active.recording?.close() }
                val discarded = discardReservedVideoFile(active)
                if (!discarded) storageBlocked = true
                message = if (discarded)
                    "Video finalization timed out; the incomplete clip was discarded"
                else "Video finalization timed out; the incomplete clip could not be removed"
            }
            completion.complete(null)
            isVideoRecording = false
            onRecordingStateChange(
                isVideoRecordingEnabled,
                false,
                isVideoSupported,
                message
            )
        }
    }

    /** Finalizes the active MP4 before releasing the camera. */
    suspend fun pauseCameraSafely() {
        val completion = withContext(Dispatchers.Main.immediate) {
            requested = false
            isCameraReady = false
            stopActiveRecording()
        }
        awaitFinalization(completion)
        withContext(Dispatchers.Main.immediate) {
            releaseCameraUseCases()
            onCameraStateChange(false, "Paused")
        }
    }

    fun resumeCamera(onReady: (Boolean) -> Unit = {}) {
        if (destroyed) {
            onReady(false)
            return
        }
        requested = true
        if (cameraProvider == null) startCamera(onReady) else onReady(bindCameraUseCases())
    }

    suspend fun stopCameraSafely() {
        if (fullyClosed) return
        var recordingFinalized = false
        try {
            val completion = withContext(Dispatchers.Main.immediate) {
                if (fullyClosed) return@withContext null
                destroyed = true
                requested = false
                isCameraReady = false
                stopActiveRecording()
            }
            awaitFinalization(completion)
            recordingFinalized = true
        } finally {
            // Stop can itself be cancelled by service teardown. CameraX and its executor
            // still have to be released even when MP4 finalization was interrupted.
            withContext(NonCancellable + Dispatchers.Main.immediate) {
                closeCameraPermanently(abandonActiveRecording = !recordingFinalized)
                runCatching {
                    onRecordingStateChange(
                        isVideoRecordingEnabled,
                        false,
                        isVideoSupported,
                        "Video stopped"
                    )
                }
            }
        }
    }

    /** Best-effort process teardown. Explicit Stop and Pause use the awaited methods above. */
    fun closeImmediately() {
        closeCameraPermanently(abandonActiveRecording = true)
    }

    /**
     * Permanently releases CameraX exactly once. `destroyed` is set before an awaited video
     * finalization, so it cannot be used as evidence that the camera has already been closed.
     */
    private fun closeCameraPermanently(abandonActiveRecording: Boolean) {
        synchronized(permanentCloseLock) {
            if (fullyClosed) return
            destroyed = true
            requested = false
            isCameraReady = false
            recordingHandler.removeCallbacks(restartRecording)
            storagePreparationGeneration++
            storagePreparationInFlight = false
            storageScope.cancel()
            if (abandonActiveRecording) abandonActiveVideoSegment()
            try {
                releaseCameraUseCases()
            } finally {
                synchronized(previewStateLock) {
                    previewSurfaceProvider = null
                    previewStateGeneration++
                }
                cameraProvider = null
                videoCapture = null
                isVideoRecording = false
                runCatching { cameraExecutor.shutdownNow() }
                fullyClosed = true
            }
        }
    }

    private fun abandonActiveVideoSegment() {
        val active = activeVideoSegment
        activeVideoSegment = null
        if (active != null) {
            runCatching { active.recording?.stop() }
            runCatching { active.recording?.close() }
            if (!active.completion.isCompleted) {
                discardReservedVideoFile(active)
                active.completion.complete(null)
            }
        }
    }

    private fun commitReservedVideoFile(segment: ActiveVideoSegment, bytes: Long): Boolean =
        synchronized(segment) {
            if (segment.terminalState != VideoSegmentTerminalState.ACTIVE) {
                return@synchronized false
            }
            if (!storageQuota.commit(segment.storageReservation, bytes)) {
                return@synchronized false
            }
            segment.terminalState = VideoSegmentTerminalState.COMMITTED
            true
        }

    /**
     * Releases the reservation once and reconciles every later cleanup attempt by delta.
     * This remains safe when a late CameraX Finalize follows a failed stop or timeout.
     */
    private fun discardReservedVideoFile(segment: ActiveVideoSegment): Boolean {
        return synchronized(segment) {
            when (segment.terminalState) {
                VideoSegmentTerminalState.COMMITTED -> return true
                VideoSegmentTerminalState.ACTIVE -> {
                    segment.terminalState = VideoSegmentTerminalState.DISCARDED
                    storageQuota.release(segment.storageReservation)
                }
                VideoSegmentTerminalState.DISCARDED -> Unit
            }

            val result = segment.discardedMediaCleanup.reconcile(segment.file)
            storageQuota.noteDeletion(result.removedBytes)
            storageQuota.noteUnexpectedExistingFile(result.addedBytes)
            result.deleted
        }
    }

    private fun releaseCameraUseCases() {
        val camera = boundCamera
        boundCamera = null
        runCatching { camera?.cameraInfo?.cameraState?.removeObservers(lifecycleOwner) }
        val analysis = imageAnalysis
        imageAnalysis = null
        runCatching { analysis?.clearAnalyzer() }
        previewUseCase = null
        previewIsBound = false
        videoCapture = null
        runCatching { cameraProvider?.unbindAll() }
        runCatching { clearPendingFrameRequest() }
    }

    private fun clearPendingFrameRequest() {
        val pending = synchronized(pendingFrameLock) {
            captureGeneration++
            pendingFrameRequest.also { pendingFrameRequest = null }
        }
        pending?.deferred?.complete(null)
    }

    private suspend fun ensureStorageInventory() {
        if (storageQuota.isReconciled()) return
        NativeMediaFilesystemMutation.mutex.withLock {
            storageInventoryMutex.withLock {
                if (storageQuota.isReconciled()) return@withLock
                val actualBytes = File(context.filesDir, "footage").walkTopDown()
                    .filter(File::isFile)
                    .sumOf(File::length)
                storageQuota.reconcile(actualBytes)
            }
        }
    }

    /** Reserves bytes from the shared MP4 + keyframe cap before a file is created. */
    internal suspend fun reserveMediaBytes(bytes: Long): NativeMediaStorageQuota.Reservation? {
        ensureStorageInventory()
        return storageQuota.tryReserve(bytes, context.filesDir.usableSpace)
    }

    internal fun commitMediaBytes(
        reservation: NativeMediaStorageQuota.Reservation,
        actualBytes: Long
    ) = storageQuota.commit(reservation, actualBytes)

    internal fun releaseMediaBytes(reservation: NativeMediaStorageQuota.Reservation) =
        storageQuota.release(reservation)

    internal fun noteDeletedMediaBytes(bytes: Long) = storageQuota.noteDeletion(bytes)

    internal fun noteUnexpectedMediaBytes(bytes: Long) =
        storageQuota.noteUnexpectedExistingFile(bytes)

    /**
     * Returns a recorder only after this manager has inventoried disk. When it has not,
     * the deletion mutex makes the later inventory observe the post-delete filesystem.
     */
    internal fun mediaDeletionRecorderIfReconciled(): ((Long) -> Unit)? =
        if (storageQuota.isReconciled()) storageQuota::noteDeletion else null
}
