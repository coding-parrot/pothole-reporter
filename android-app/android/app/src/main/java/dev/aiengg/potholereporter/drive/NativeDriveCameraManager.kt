package dev.aiengg.potholereporter.drive

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Matrix
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
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
import java.util.ArrayDeque
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
    private data class ActiveVideoSegment(
        val sequence: Int,
        val file: File,
        val startedAtMs: Long,
        val completion: CompletableDeferred<NativeVideoSegment?>,
        val storage: NativeVideoSegmentStorage,
        var recording: Recording? = null,
        var stopRequested: Boolean = false
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
    private val cameraOperationMutex = Mutex()
    private val rollingFrameLock = Any()
    private val rollingFrames = ArrayDeque<CapturedFrame>(NativeRollingBurstWindow.CAPACITY)
    private var lastRollingSourceTimestampNs = 0L
    private var deliveredFramesSinceRollingSample = 0
    @Volatile private var analyzerSamplingEnabled = true
    private val analyzerSamplingGeneration = NativeGenerationGate()
    @Volatile private var analyzerFatalError: OutOfMemoryError? = null
    @Volatile private var analyzerFatalSignalled = false
    private var analyzerConversionFailures = 0
    private var analyzerConversionRecoverySignalled = false
    private val cameraStartGeneration = NativeGenerationGate()
    private val cameraGraphGeneration = NativeGenerationGate()
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
        private const val RECORDING_SEGMENT_MS = 60_000L
        private const val RECORDING_RESTART_DELAY_MS = 1_000L
        private const val RECORDING_FINALIZE_TIMEOUT_MS = 12_000L
        private const val MAX_ANALYZER_CONVERSION_FAILURES = 8
    }

    fun startCamera(onReady: (Boolean) -> Unit = {}) {
        if (destroyed) {
            onReady(false)
            return
        }
        requested = true
        val startToken = cameraStartGeneration.issue()
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            // Pause/Resume is an ABA transition: `requested` can be true again while this
            // callback still belongs to the pre-Pause request. Only the exact token may bind.
            if (!requested || destroyed || !cameraStartGeneration.isCurrent(startToken)) {
                return@addListener
            }
            try {
                val provider = future.get()
                if (!requested || destroyed || !cameraStartGeneration.isCurrent(startToken)) {
                    return@addListener
                }
                if (!provider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA)) {
                    val reason = "This device has no rear camera for Drive Mode."
                    publishState(false, reason)
                    onCameraRecoveryRequired(NativeCameraRecoveryAction.STOP_TERMINALLY, reason)
                    onReady(false)
                    return@addListener
                }
                cameraProvider = provider
                onReady(bindCameraUseCases())
            } catch (e: Exception) {
                if (!cameraStartGeneration.isCurrent(startToken)) return@addListener
                val reason = "Camera could not start: ${e.message ?: "unknown error"}"
                publishState(false, reason)
                onCameraRecoveryRequired(NativeCameraRecoveryAction.RELEASE_AND_RETRY, reason)
                onReady(false)
            }
        }, ContextCompat.getMainExecutor(context))
    }

    @SuppressLint("UnsafeOptInUsageError")
    private fun bindCameraUseCases(transitionReason: String? = null): Boolean {
        val provider = cameraProvider ?: return false
        if (!requested || destroyed) return false

        return try {
            // A graph reconfiguration briefly has no active CameraX stream. Publish that
            // truth before unbindAll so the foreground HUD cannot claim a live preview
            // while enabling/disabling VideoCapture produces a black transition frame.
            transitionReason?.let { publishState(false, it) }
            val graphToken = cameraGraphGeneration.issue()
            // A new graph gets one recovery signal per critical code. CameraState can
            // repeat the same error while moving through CLOSING and CLOSED.
            lastSignalledRecoveryErrorCode = null
            boundCamera?.cameraInfo?.cameraState?.removeObservers(lifecycleOwner)
            imageAnalysis?.clearAnalyzer()
            clearRollingFrames()
            analyzerFatalError = null
            analyzerFatalSignalled = false
            analyzerConversionFailures = 0
            analyzerConversionRecoverySignalled = false
            provider.unbindAll()
            previewIsBound = false

            val analysis = ImageAnalysis.Builder()
                .setTargetResolution(Size(1280, 720))
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build().also { analysis ->
                    imageAnalysis = analysis
                    analysis.setAnalyzer(cameraExecutor) { imageProxy ->
                        processImageProxy(imageProxy, graphToken)
                    }
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
            fun bindWithoutVideo(): Camera {
                videoCapture = null
                val camera = if (desiredPreviewProvider != null) {
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
                return camera
            }
            boundCamera = (if (!isVideoRecordingEnabled) {
                // Video is opt-in. Do not consume an encoder/third camera stream on every
                // default Drive; this is both faster and substantially more compatible
                // with Samsung/Xiaomi stream-combination limits.
                isVideoSupported = true
                bindWithoutVideo()
            } else try {
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
                // Keep visible frame detection available and report video as unsupported.
                videoCapture = null
                isVideoSupported = false
                onRecordingStateChange(true, false, false, "Video recording unavailable on this camera")
                bindWithoutVideo()
            }).also { camera ->
                camera.cameraInfo.cameraState.observe(lifecycleOwner) { state ->
                    if (cameraGraphGeneration.isCurrent(graphToken)) handleCameraState(state)
                }
            }
            if (isVideoRecordingEnabled && isVideoSupported) startRecordingSegment()
            // An attach/detach request may have run before previewUseCase existed and
            // returned without work. Reconcile the newest generation once this graph is
            // complete so that early requests cannot be lost.
            scheduleLatestPreviewReconciliation()
            true
        } catch (e: Exception) {
            val reason = "Camera unavailable: ${e.message ?: "another app may be using it"}"
            runCatching { releaseCameraUseCases() }
            publishState(false, reason)
            onCameraRecoveryRequired(NativeCameraRecoveryAction.RELEASE_AND_RETRY, reason)
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
            clearRollingFrames()
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
                clearRollingFrames()
                publishState(false, "Waiting for camera")
            }
            CameraState.Type.CLOSED -> {
                clearRollingFrames()
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

    /**
     * Stops expensive ImageProxy-to-Bitmap work without touching the CameraX graph.
     * Preview and optional VideoCapture remain bound; disabling also discards any partial
     * detection window so a later enable can only produce a fully fresh burst.
     */
    fun setAnalyzerSamplingEnabled(enabled: Boolean) {
        var discardedFrames: List<CapturedFrame> = emptyList()
        synchronized(rollingFrameLock) {
            if (analyzerSamplingEnabled == enabled) return
            analyzerSamplingEnabled = enabled
            analyzerSamplingGeneration.invalidate()
            if (!enabled) {
                lastRollingSourceTimestampNs = 0L
                deliveredFramesSinceRollingSample = 0
                discardedFrames = rollingFrames.toList()
                rollingFrames.clear()
            }
        }
        discardedFrames.forEach { it.bitmap.recycleSafely() }
    }

    private fun processImageProxy(imageProxy: ImageProxy, sourceGraphGeneration: Long) {
        var ownedBitmap: Bitmap? = null
        var samplingGeneration = 0L
        try {
            val capturedAtMs = System.currentTimeMillis()
            val capturedAtElapsedMs = SystemClock.elapsedRealtime()
            val sourceTimestampNs = imageProxy.imageInfo.timestamp.takeIf { it > 0L }
                ?: System.nanoTime()
            val shouldSample = synchronized(rollingFrameLock) {
                samplingGeneration = analyzerSamplingGeneration.current()
                val eligibleFrameCount = if (lastRollingSourceTimestampNs == 0L) 0 else {
                    (deliveredFramesSinceRollingSample + 1)
                        .coerceAtMost(NativeRollingBurstWindow.SOURCE_FRAME_STRIDE)
                }
                if (NativeAnalyzerSamplingPolicy.shouldConvert(
                        enabled = analyzerSamplingEnabled,
                        requested = requested,
                        destroyed = destroyed,
                        cameraReady = isCameraReady,
                        graphCurrent = cameraGraphGeneration.isCurrent(sourceGraphGeneration),
                        // Never overwrite a complete due window. Preview and optional
                        // video keep flowing while the persistence loop takes ownership.
                        windowFull = rollingFrames.size >= NativeRollingBurstWindow.CAPACITY,
                        sourceTimestampNs = sourceTimestampNs,
                        lastSampleTimestampNs = lastRollingSourceTimestampNs,
                        deliveredFramesSinceLastSample = eligibleFrameCount,
                        sourceFrameStride = NativeRollingBurstWindow.SOURCE_FRAME_STRIDE,
                        minimumGapNs = NativeRollingBurstWindow.SAMPLE_SPACING_NS,
                        maximumGapNs = NativeRollingBurstWindow.MAX_SAMPLE_GAP_NS
                    )) {
                    true
                } else {
                    if (analyzerSamplingEnabled && requested && !destroyed && isCameraReady &&
                        cameraGraphGeneration.isCurrent(sourceGraphGeneration) &&
                        sourceTimestampNs > lastRollingSourceTimestampNs &&
                        rollingFrames.size < NativeRollingBurstWindow.CAPACITY
                    ) deliveredFramesSinceRollingSample = eligibleFrameCount
                    false
                }
            }
            if (!shouldSample) return
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
                capturedAtElapsedMs,
                sourceTimestampNs,
                sourceGraphGeneration
            )
            val retained = synchronized(rollingFrameLock) {
                if (!analyzerSamplingEnabled ||
                    !analyzerSamplingGeneration.isCurrent(samplingGeneration) ||
                    !requested || destroyed || !isCameraReady ||
                    !cameraGraphGeneration.isCurrent(sourceGraphGeneration)
                ) false else {
                    if (rollingFrames.size >= NativeRollingBurstWindow.CAPACITY) false
                    else {
                        rollingFrames.addLast(frame)
                        // Sampling state commits only after Bitmap conversion and ring
                        // retention succeed. A failed conversion retries on the next
                        // delivered frame instead of silently skipping another stride.
                        lastRollingSourceTimestampNs = sourceTimestampNs
                        deliveredFramesSinceRollingSample = 0
                        true
                    }
                }
            }
            if (retained) {
                analyzerConversionFailures = 0
                analyzerConversionRecoverySignalled = false
                ownedBitmap = null
            } else upright.recycleSafely()
        } catch (error: OutOfMemoryError) {
            ownedBitmap?.recycleSafely()
            // An invalidated graph can finish conversion after its replacement started.
            // It owns its temporary Bitmap, but it must not terminate the new session.
            if (analyzerSamplingEnabled &&
                analyzerSamplingGeneration.isCurrent(samplingGeneration) &&
                requested && !destroyed &&
                cameraGraphGeneration.isCurrent(sourceGraphGeneration)
            ) {
                analyzerFatalError = error
                if (!analyzerFatalSignalled) {
                    analyzerFatalSignalled = true
                    onCameraRecoveryRequired(
                        NativeCameraRecoveryAction.STOP_TERMINALLY,
                        "This device ran out of image memory while reading camera frames."
                    )
                }
            }
        } catch (_: Exception) {
            ownedBitmap?.recycleSafely()
            if (analyzerSamplingEnabled &&
                analyzerSamplingGeneration.isCurrent(samplingGeneration) &&
                requested && !destroyed &&
                cameraGraphGeneration.isCurrent(sourceGraphGeneration)
            ) {
                analyzerConversionFailures++
                if (analyzerConversionFailures >= MAX_ANALYZER_CONVERSION_FAILURES &&
                    !analyzerConversionRecoverySignalled
                ) {
                    analyzerConversionRecoverySignalled = true
                    onCameraRecoveryRequired(
                        NativeCameraRecoveryAction.RELEASE_AND_RETRY,
                        "Camera frames could not be read. Detection and video are restarting."
                    )
                }
            }
        } finally {
            imageProxy.close()
        }
    }

    suspend fun captureBurst(): Pair<List<BurstFrame>, Int>? {
        if (!isCameraReady || !requested || destroyed) return null
        return captureMutex.withLock {
            analyzerFatalError?.let { error ->
                analyzerFatalError = null
                throw error
            }
            var discarded: List<CapturedFrame> = emptyList()
            val selected = synchronized(rollingFrameLock) {
                val sourceFrames = rollingFrames.toList()
                val generation = sourceFrames.firstOrNull()?.cameraGeneration
                    ?: return@synchronized null
                val sampleMetadata = sourceFrames.map {
                        NativeRollingBurstWindow.Sample(
                            it.capturedAtElapsedMs,
                            it.sourceTimestampNs,
                            it.cameraGeneration
                        )
                    }
                val disposition = NativeRollingBurstWindow.disposition(
                    sampleMetadata,
                    nowElapsedMs = SystemClock.elapsedRealtime(),
                    expectedGeneration = generation
                )
                if (disposition != NativeRollingBurstWindow.Disposition.READY) {
                    if (disposition == NativeRollingBurstWindow.Disposition.DISCARD) {
                        discarded = sourceFrames
                        rollingFrames.clear()
                        lastRollingSourceTimestampNs = 0L
                        deliveredFramesSinceRollingSample = 0
                    }
                    return@synchronized null
                }
                val sourceIndexes = sourceFrames.indices.toList()
                // The selector currently requires and returns the complete three-frame
                // ring. Transfer those Bitmaps to the caller instead of cloning another
                // ~11 MB on every admission. The analyzer immediately starts refilling a
                // new bounded ring, while the service owns and eventually recycles these.
                val transferred = sourceIndexes.map(sourceFrames::get)
                rollingFrames.clear()
                lastRollingSourceTimestampNs = 0L
                deliveredFramesSinceRollingSample = 0
                transferred
            }
            discarded.forEach { it.bitmap.recycleSafely() }
            selected ?: return@withLock null
            var ownershipTransferred = false
            try {
                val burstFrames = selected.map { frame ->
                    BurstFrame(
                        frame.bitmap,
                        FrameQualityEvaluator.evaluateFrameQuality(frame.bitmap),
                        frame.capturedAtMs,
                        frame.sourceTimestampNs,
                        frame.cameraGeneration,
                        frame.capturedAtElapsedMs
                    )
                }
                if (burstFrames.size < MIN_DETECTION_SOURCE_FRAMES) {
                    burstFrames.forEach { it.bitmap.recycleSafely() }
                    return@withLock null
                }
                val result = Pair(
                    burstFrames,
                    FrameQualityEvaluator.selectBestBurstIndex(burstFrames.map(BurstFrame::quality))
                )
                ownershipTransferred = true
                result
            } finally {
                if (!ownershipTransferred) {
                    selected.forEach { it.bitmap.recycleSafely() }
                }
            }
        }
    }

    fun hasCompleteBurst(): Boolean = synchronized(rollingFrameLock) {
        rollingFrames.size == NativeRollingBurstWindow.CAPACITY
    }

    private fun Bitmap.recycleSafely() {
        if (!isRecycled) recycle()
    }

    private data class CapturedFrame(
        val bitmap: Bitmap,
        val capturedAtMs: Long,
        val capturedAtElapsedMs: Long,
        val sourceTimestampNs: Long,
        val cameraGeneration: Long
    )

    suspend fun setVideoRecordingEnabled(enabled: Boolean) = cameraOperationMutex.withLock {
        val transition = withContext(Dispatchers.Main.immediate) {
            val changed = isVideoRecordingEnabled != enabled
            isVideoRecordingEnabled = enabled
            if (enabled) storageBlocked = false
            Pair(if (!enabled) stopActiveRecording() else null, changed)
        }
        // A rapid Off -> On request cancels the service coroutine that asked for Off.
        // Once Recording.stop() has been sent, its bounded Finalize cleanup must still
        // finish; otherwise On sees the stale active segment and can never restart video.
        withContext(NonCancellable) { awaitFinalization(transition.first) }
        withContext(Dispatchers.Main.immediate) {
            // A new graph is the only reliable way to add/remove VideoCapture across OEM
            // camera stacks. Revalidate because Pause/Stop may have won during Finalize.
            if (transition.second && requested && !destroyed &&
                isVideoRecordingEnabled == enabled
            ) {
                val rebound = bindCameraUseCases("Applying video setting")
                if (!rebound) {
                    onRecordingStateChange(
                        enabled,
                        false,
                        isVideoSupported,
                        "Camera could not apply the video setting"
                    )
                }
            } else if (enabled && requested && !destroyed && !isVideoSupported) {
                onRecordingStateChange(true, false, false, "Video recording unavailable on this camera")
            }
            if (!enabled) {
                onRecordingStateChange(false, false, isVideoSupported, "Scanning frames; video is not saved")
            }
        }
    }

    /** True = bound, null = accepted while CameraX is starting, false = bind failed. */
    fun attachPreview(surfaceProvider: Preview.SurfaceProvider): Boolean? =
        updatePreviewSurfaceProvider(surfaceProvider)

    fun detachPreview(expectedSurfaceProvider: Preview.SurfaceProvider) {
        val generation = synchronized(previewStateLock) {
            // An old Activity can finish after its replacement already attached. Its late
            // onDestroy must not detach the replacement Activity's PreviewView.
            if (previewSurfaceProvider !== expectedSurfaceProvider) return
            previewSurfaceProvider = null
            ++previewStateGeneration
        }
        runOnMain { reconcilePreviewSurfaceProvider(generation) }
    }

    private fun updatePreviewSurfaceProvider(surfaceProvider: Preview.SurfaceProvider?): Boolean? {
        val generation = synchronized(previewStateLock) {
            if (previewSurfaceProvider === surfaceProvider &&
                (surfaceProvider == null || previewIsBound)
            ) return previewIsBound
            previewSurfaceProvider = surfaceProvider
            ++previewStateGeneration
        }
        return if (Looper.myLooper() == Looper.getMainLooper()) {
            reconcilePreviewSurfaceProvider(generation)
        } else {
            ContextCompat.getMainExecutor(context).execute {
                reconcilePreviewSurfaceProvider(generation)
            }
            null
        }
    }

    private fun scheduleLatestPreviewReconciliation() {
        val generation = synchronized(previewStateLock) { previewStateGeneration }
        runOnMain {
            reconcilePreviewSurfaceProvider(generation)
        }
    }

    private fun runOnMain(block: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) block()
        else ContextCompat.getMainExecutor(context).execute(block)
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
    private fun reconcilePreviewSurfaceProvider(generation: Long): Boolean? {
        val surfaceProvider = synchronized(previewStateLock) {
            if (generation != previewStateGeneration || fullyClosed) return false
            previewSurfaceProvider
        }
        if (!requested || destroyed) return false
        val provider = cameraProvider ?: return null
        val preview = previewUseCase ?: return null

        if (surfaceProvider == null) {
            if (previewIsBound) {
                // Unbind Preview before removing its Activity-owned surface. Removing a
                // provider from a still-bound Preview can tear down the whole camera graph
                // on affected devices even though Analysis should continue in the FGS.
                val detached = runCatching { provider.unbind(preview) }.isSuccess
                if (!detached) return false
                previewIsBound = false
            }
            preview.setSurfaceProvider(null)
            return true
        }

        preview.setSurfaceProvider(surfaceProvider)
        if (previewIsBound) return true
        val attached = runCatching {
            provider.bindToLifecycle(
                lifecycleOwner,
                CameraSelector.DEFAULT_BACK_CAMERA,
                preview
            )
        }.onSuccess {
            previewIsBound = true
        }.isSuccess
        if (attached) return true

        // When the app is visible, a hidden camera is not an acceptable fallback. Keep
        // the requested provider registered, close capture immediately, and let the
        // service's serialized recovery path finalize video before rebuilding the whole
        // graph with Preview present from the first bind.
        if (generation == synchronized(previewStateLock) { previewStateGeneration }) {
            preview.setSurfaceProvider(null)
            val reason = "Live camera preview could not attach. Camera is restarting visibly."
            publishState(false, reason)
            onCameraRecoveryRequired(
                NativeCameraRecoveryAction.RELEASE_AND_RETRY,
                reason
            )
        }
        return false
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
            sequence = sequence,
            file = file,
            startedAtMs = System.currentTimeMillis(),
            completion = completion,
            storage = NativeVideoSegmentStorage(file, reservation, storageQuota)
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
            val discarded = segment.storage.discard()
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
            val wasDiscarded = segment.storage.isDiscarded()
            if (wasDiscarded) {
                val discarded = segment.storage.discard()
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
            if (segment.storage.commit(bytes)) {
                onSegmentFinalized(result)
            } else {
                // CameraX's file-size limit may be crossed by a final encoded sample.
                // Such a file must never silently exceed the shared Drive-media cap.
                quotaRejected = true
                storageBlocked = true
                segment.storage.discard()
                result = null
            }
        } else {
            segment.storage.discard()
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
        val shouldRequestStop = synchronized(active) {
            if (active.stopRequested) false else {
                active.stopRequested = true
                true
            }
        }
        if (!shouldRequestStop) return active.completion
        runCatching { active.recording?.stop() }.onFailure {
            if (activeVideoSegment === active) activeVideoSegment = null
            val discarded = active.storage.discard()
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
                val discarded = active.storage.discard()
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
    suspend fun pauseCameraSafely() = cameraOperationMutex.withLock {
        val completion = withContext(Dispatchers.Main.immediate) {
            cameraStartGeneration.invalidate()
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
        if (cameraProvider == null) startCamera(onReady) else {
            cameraStartGeneration.issue()
            onReady(bindCameraUseCases())
        }
    }

    suspend fun stopCameraSafely() = cameraOperationMutex.withLock {
        if (fullyClosed) return
        var recordingFinalized = false
        try {
            val completion = withContext(Dispatchers.Main.immediate) {
                if (fullyClosed) return@withContext null
                destroyed = true
                cameraStartGeneration.invalidate()
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
            cameraStartGeneration.invalidate()
            requested = false
            isCameraReady = false
            recordingHandler.removeCallbacks(restartRecording)
            storagePreparationGeneration++
            storagePreparationInFlight = false
            storageScope.cancel()
            if (abandonActiveRecording) abandonActiveVideoSegment()
            var released = false
            try {
                releaseCameraUseCases()
                released = true
            } finally {
                synchronized(previewStateLock) {
                    previewSurfaceProvider = null
                    previewStateGeneration++
                }
                videoCapture = null
                isVideoRecording = false
                runCatching { cameraExecutor.shutdownNow() }
                // Keep the provider and permit an emergency retry when CameraX rejected
                // unbindAll. Claiming success here would leave a live camera uncloseable.
                if (released) {
                    cameraProvider = null
                    fullyClosed = true
                }
            }
        }
    }

    private fun abandonActiveVideoSegment() {
        val active = activeVideoSegment
        activeVideoSegment = null
        if (active != null) {
            val shouldRequestStop = synchronized(active) {
                if (active.stopRequested) false else {
                    active.stopRequested = true
                    true
                }
            }
            if (shouldRequestStop) runCatching { active.recording?.stop() }
            runCatching { active.recording?.close() }
            if (!active.completion.isCompleted) {
                active.storage.discard()
                active.completion.complete(null)
            }
        }
    }

    private fun releaseCameraUseCases() {
        cameraGraphGeneration.invalidate()
        val camera = boundCamera
        boundCamera = null
        runCatching { camera?.cameraInfo?.cameraState?.removeObservers(lifecycleOwner) }
        val analysis = imageAnalysis
        imageAnalysis = null
        analysis?.clearAnalyzer()
        clearRollingFrames()
        // Keep the provider reachable until unbind succeeds so permanent teardown can
        // retry instead of falsely reporting a clean close.
        cameraProvider?.unbindAll()
        previewUseCase = null
        previewIsBound = false
        videoCapture = null
    }

    private fun clearRollingFrames() {
        val frames = synchronized(rollingFrameLock) {
            lastRollingSourceTimestampNs = 0L
            deliveredFramesSinceRollingSample = 0
            rollingFrames.toList().also { rollingFrames.clear() }
        }
        frames.forEach { it.bitmap.recycleSafely() }
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
