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
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
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
    private val onRecordingStateChange: (enabled: Boolean, recording: Boolean, supported: Boolean, message: String?) -> Unit,
    private val onSegmentFinalized: (NativeVideoSegment) -> Unit
) {
    private data class ActiveVideoSegment(
        val sequence: Int,
        val file: File,
        val startedAtMs: Long,
        val completion: CompletableDeferred<NativeVideoSegment?>,
        var recording: Recording? = null
    )

    private var cameraProvider: ProcessCameraProvider? = null
    private var imageAnalysis: ImageAnalysis? = null
    private var previewUseCase: Preview? = null
    private var previewSurfaceProvider: Preview.SurfaceProvider? = null
    private var boundCamera: Camera? = null
    private var videoCapture: VideoCapture<Recorder>? = null
    private var activeVideoSegment: ActiveVideoSegment? = null
    private val cameraExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private val captureMutex = Mutex()
    private val pendingFrameLock = Any()
    private var pendingFrameRequest: CompletableDeferred<Bitmap?>? = null
    private val recordingHandler = Handler(Looper.getMainLooper())

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
    @Volatile private var fullyClosed = false
    private val permanentCloseLock = Any()

    companion object {
        const val BURST_COUNT = 3
        const val BURST_SPACING_MS = 180L
        private const val MAX_FRAME_WAIT_MS = 2_000L
        private const val RECORDING_SEGMENT_MS = 60_000L
        private const val RECORDING_SEGMENT_MAX_BYTES = 80L * 1024 * 1024
        private const val MIN_FREE_STORAGE_BYTES = 500L * 1024 * 1024
        private const val MAX_TOTAL_FOOTAGE_BYTES = 4L * 1024 * 1024 * 1024
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
            boundCamera?.cameraInfo?.cameraState?.removeObservers(lifecycleOwner)
            provider.unbindAll()

            val analysis = ImageAnalysis.Builder()
                .setTargetResolution(Size(1280, 720))
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build().also { analysis ->
                    imageAnalysis = analysis
                    analysis.setAnalyzer(cameraExecutor, ::processImageProxy)
                }

            val preview = Preview.Builder().build().also { preview ->
                previewUseCase = preview
                previewSurfaceProvider?.let(preview::setSurfaceProvider)
            }

            fun bindWithVideo(selector: QualitySelector): Camera {
                val recorder = Recorder.Builder().setQualitySelector(selector).build()
                val candidate = VideoCapture.withOutput(recorder)
                val camera = provider.bindToLifecycle(
                    lifecycleOwner,
                    CameraSelector.DEFAULT_BACK_CAMERA,
                    preview,
                    analysis,
                    candidate
                )
                videoCapture = candidate
                return camera
            }
            boundCamera = try {
                isVideoSupported = true
                bindWithVideo(QualitySelector.from(
                    Quality.HD,
                    FallbackStrategy.lowerQualityOrHigherThan(Quality.SD)
                ))
            } catch (_: Exception) {
                provider.unbindAll()
                try {
                    bindWithVideo(QualitySelector.from(
                        Quality.SD,
                        FallbackStrategy.lowerQualityOrHigherThan(Quality.SD)
                    ))
                } catch (_: Exception) {
                    // Some cameras cannot run Preview + Analysis + VideoCapture together.
                    // Detection stays available and the UI says video is unavailable.
                    provider.unbindAll()
                    videoCapture = null
                    isVideoSupported = false
                    onRecordingStateChange(isVideoRecordingEnabled, false, false, "Video recording unavailable on this camera")
                    provider.bindToLifecycle(
                        lifecycleOwner,
                        CameraSelector.DEFAULT_BACK_CAMERA,
                        preview,
                        analysis
                    )
                }
            }.also { camera ->
                camera.cameraInfo.cameraState.observe(lifecycleOwner, ::handleCameraState)
            }
            if (isVideoRecordingEnabled && isVideoSupported) startRecordingSegment()
            true
        } catch (e: Exception) {
            publishState(false, "Camera unavailable: ${e.message ?: "another app may be using it"}")
            false
        }
    }

    private fun handleCameraState(state: CameraState) {
        when (state.type) {
            CameraState.Type.OPEN -> {
                publishState(true, null)
                if (isVideoRecordingEnabled && !isVideoRecording) {
                    recordingHandler.removeCallbacks(restartRecording)
                    recordingHandler.postDelayed(restartRecording, RECORDING_RESTART_DELAY_MS)
                }
            }
            CameraState.Type.PENDING_OPEN -> {
                val reason = state.error?.let(::cameraErrorText) ?: "Waiting for camera"
                publishState(false, reason)
            }
            CameraState.Type.CLOSED -> {
                if (requested) {
                    val reason = state.error?.let(::cameraErrorText) ?: "Camera paused"
                    publishState(false, reason)
                }
            }
            else -> Unit
        }
    }

    private fun cameraErrorText(error: CameraState.StateError): String = when (error.code) {
        CameraState.ERROR_CAMERA_IN_USE,
        CameraState.ERROR_MAX_CAMERAS_IN_USE -> "Camera in use by another app; scanning will resume automatically"
        CameraState.ERROR_CAMERA_DISABLED -> "Camera disabled by device policy"
        CameraState.ERROR_DO_NOT_DISTURB_MODE_ENABLED -> "Camera blocked by Do Not Disturb on this device"
        CameraState.ERROR_STREAM_CONFIG -> "Camera configuration failed"
        CameraState.ERROR_CAMERA_FATAL_ERROR -> "Camera hardware error"
        else -> "Camera temporarily unavailable; scanning will resume automatically"
    }

    private fun publishState(available: Boolean, reason: String?) {
        isCameraReady = available
        onCameraStateChange(available, reason)
    }

    private fun processImageProxy(imageProxy: ImageProxy) {
        val request = synchronized(pendingFrameLock) {
            pendingFrameRequest?.also { pendingFrameRequest = null }
        }
        try {
            if (request == null) return
            val raw = imageProxy.toBitmap()
            val rotation = imageProxy.imageInfo.rotationDegrees
            val upright = if (rotation == 0) raw else {
                val matrix = Matrix().apply { postRotate(rotation.toFloat()) }
                Bitmap.createBitmap(raw, 0, 0, raw.width, raw.height, matrix, true)
                    .also { if (it !== raw) raw.recycle() }
            }
            if (!request.complete(upright) && !upright.isRecycled) upright.recycle()
        } catch (_: Exception) {
            request?.complete(null)
        } finally {
            imageProxy.close()
        }
    }

    suspend fun captureBurst(): Pair<List<BurstFrame>, Int>? = withContext(Dispatchers.Default) {
        if (!isCameraReady || !requested || destroyed) return@withContext null

        captureMutex.withLock {
            val frames = mutableListOf<BurstFrame>()
            val qualities = mutableListOf<QualityScore>()
            try {
                repeat(BURST_COUNT) { index ->
                    if (index > 0) delay(BURST_SPACING_MS)
                    val frame = awaitNextFrame()
                    if (frame != null) {
                        val quality = FrameQualityEvaluator.evaluateRoadFrameQuality(frame)
                        frames += BurstFrame(frame, quality, System.currentTimeMillis())
                        qualities += quality
                    }
                }
                if (frames.isEmpty()) return@withContext null
                Pair(frames, FrameQualityEvaluator.selectBestBurstIndex(qualities))
            } catch (error: Throwable) {
                frames.forEach { if (!it.bitmap.isRecycled) it.bitmap.recycle() }
                throw error
            }
        }
    }

    private suspend fun awaitNextFrame(): Bitmap? {
        val request = CompletableDeferred<Bitmap?>()
        synchronized(pendingFrameLock) {
            if (!requested || destroyed || !isCameraReady) return null
            pendingFrameRequest?.cancel()
            pendingFrameRequest = request
        }
        val frame = withTimeoutOrNull(MAX_FRAME_WAIT_MS) { request.await() }
        if (frame == null) {
            synchronized(pendingFrameLock) {
                if (pendingFrameRequest === request) pendingFrameRequest = null
            }
            request.cancel()
        }
        return frame
    }

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
        previewSurfaceProvider = surfaceProvider
        ContextCompat.getMainExecutor(context).execute {
            previewUseCase?.setSurfaceProvider(surfaceProvider)
        }
    }

    fun detachPreview() {
        previewSurfaceProvider = null
        ContextCompat.getMainExecutor(context).execute {
            previewUseCase?.setSurfaceProvider(null)
        }
    }

    @SuppressLint("MissingPermission")
    private fun startRecordingSegment() {
        if (!requested || destroyed || !isCameraReady || !isVideoRecordingEnabled || !isVideoSupported ||
            activeVideoSegment != null || storageBlocked
        ) return
        val recorder = videoCapture?.output ?: return
        val footageRoot = File(context.filesDir, "footage/$sessionId")
        val allFootageBytes = File(context.filesDir, "footage").walkTopDown()
            .filter(File::isFile)
            .sumOf(File::length)
        if ((!footageRoot.exists() && !footageRoot.mkdirs()) ||
            context.filesDir.usableSpace < MIN_FREE_STORAGE_BYTES + RECORDING_SEGMENT_MAX_BYTES ||
            allFootageBytes >= MAX_TOTAL_FOOTAGE_BYTES
        ) {
            storageBlocked = true
            val message = if (allFootageBytes >= MAX_TOTAL_FOOTAGE_BYTES)
                "Video stopped: 4 GB footage limit reached; share or delete old clips"
            else "Video stopped: keep at least 500 MB free"
            onRecordingStateChange(true, false, true, message)
            return
        }
        val sequence = ++recordingSequence
        val file = File(footageRoot, "segment_${sequence.toString().padStart(4, '0')}.mp4")
        val completion = CompletableDeferred<NativeVideoSegment?>()
        val segment = ActiveVideoSegment(sequence, file, System.currentTimeMillis(), completion)
        activeVideoSegment = segment
        val options = FileOutputOptions.Builder(file)
            .setDurationLimitMillis(RECORDING_SEGMENT_MS)
            .setFileSizeLimit(RECORDING_SEGMENT_MAX_BYTES)
            .build()
        try {
            segment.recording = recorder.prepareRecording(context, options)
                .start(ContextCompat.getMainExecutor(context)) { event ->
                    handleRecordingEvent(segment, event)
                }
        } catch (error: Exception) {
            if (activeVideoSegment === segment) activeVideoSegment = null
            file.delete()
            completion.complete(null)
            isVideoRecording = false
            onRecordingStateChange(true, false, true, "Video could not start: ${error.message ?: "recorder error"}")
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
        if (segment.completion.isCompleted && activeVideoSegment !== segment) {
            segment.file.delete()
            return
        }
        if (activeVideoSegment === segment) activeVideoSegment = null
        isVideoRecording = false
        val endedAtMs = System.currentTimeMillis()
        val bytes = maxOf(segment.file.length(), event.recordingStats.numBytesRecorded)
        val durationMs = (event.recordingStats.recordedDurationNanos / 1_000_000L).coerceAtLeast(0L)
        val hasFile = segment.file.isFile && bytes > 0L
        val rollover = event.error == VideoRecordEvent.Finalize.ERROR_NONE ||
            event.error == VideoRecordEvent.Finalize.ERROR_DURATION_LIMIT_REACHED ||
            event.error == VideoRecordEvent.Finalize.ERROR_FILE_SIZE_LIMIT_REACHED
        val complete = hasFile && rollover
        val result = if (hasFile) NativeVideoSegment(
            sessionId = sessionId,
            filePath = segment.file.absolutePath,
            startedAtMs = segment.startedAtMs,
            endedAtMs = endedAtMs,
            durationMs = durationMs,
            bytes = bytes,
            errorCode = event.error.takeIf { it != VideoRecordEvent.Finalize.ERROR_NONE },
            complete = complete
        ) else null
        if (result != null) onSegmentFinalized(result) else segment.file.delete()
        segment.completion.complete(result)

        when (event.error) {
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
            active.completion.complete(null)
            if (activeVideoSegment === active) activeVideoSegment = null
        }
        return active.completion
    }

    private suspend fun awaitFinalization(completion: CompletableDeferred<NativeVideoSegment?>?) {
        if (completion == null) return
        val finalized = withTimeoutOrNull(RECORDING_FINALIZE_TIMEOUT_MS) { completion.await(); true } ?: false
        if (!finalized) withContext(Dispatchers.Main.immediate) {
            val active = activeVideoSegment
            if (active?.completion === completion) {
                activeVideoSegment = null
                runCatching { active.recording?.close() }
                active.file.delete()
            }
            completion.complete(null)
            isVideoRecording = false
            onRecordingStateChange(
                isVideoRecordingEnabled,
                false,
                isVideoSupported,
                "Video finalization timed out; the incomplete clip was discarded"
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
            if (abandonActiveRecording) abandonActiveVideoSegment()
            try {
                releaseCameraUseCases()
            } finally {
                previewSurfaceProvider = null
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
                active.completion.complete(null)
                active.file.delete()
            }
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
        videoCapture = null
        runCatching { cameraProvider?.unbindAll() }
        runCatching { clearPendingFrameRequest() }
    }

    private fun clearPendingFrameRequest() {
        val pending = synchronized(pendingFrameLock) {
            pendingFrameRequest.also { pendingFrameRequest = null }
        }
        pending?.complete(null)
    }
}
