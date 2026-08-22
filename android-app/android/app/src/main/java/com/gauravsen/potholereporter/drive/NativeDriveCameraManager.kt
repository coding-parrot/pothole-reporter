package com.gauravsen.potholereporter.drive

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.ImageFormat
import android.graphics.Matrix
import android.graphics.PixelFormat
import android.util.Size
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.CameraState
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.nio.ByteBuffer
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

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
    private val onCameraStateChange: (Boolean, String?) -> Unit
) {
    private var cameraProvider: ProcessCameraProvider? = null
    private var imageAnalysis: ImageAnalysis? = null
    private var boundCamera: Camera? = null
    private val cameraExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private val captureMutex = Mutex()
    private val frameLock = Any()

    @Volatile
    var isCameraReady: Boolean = false
        private set

    @Volatile
    private var latestFrameTimeMs: Long = 0L

    private var latestBitmap: Bitmap? = null
    private var requested = false
    private var destroyed = false

    companion object {
        const val BURST_COUNT = 3
        const val BURST_SPACING_MS = 180L
        private const val MAX_FRAME_AGE_MS = 2_000L
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

            imageAnalysis = ImageAnalysis.Builder()
                .setTargetResolution(Size(1280, 720))
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                .build().also { analysis ->
                    analysis.setAnalyzer(cameraExecutor, ::processImageProxy)
                }

            boundCamera = provider.bindToLifecycle(
                lifecycleOwner,
                CameraSelector.DEFAULT_BACK_CAMERA,
                imageAnalysis
            ).also { camera ->
                camera.cameraInfo.cameraState.observe(lifecycleOwner, ::handleCameraState)
            }
            true
        } catch (e: Exception) {
            publishState(false, "Camera unavailable: ${e.message ?: "another app may be using it"}")
            false
        }
    }

    private fun handleCameraState(state: CameraState) {
        when (state.type) {
            CameraState.Type.OPEN -> publishState(true, null)
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
        try {
            if (imageProxy.format != ImageFormat.FLEX_RGBA_8888 &&
                imageProxy.format != PixelFormat.RGBA_8888
            ) return

            val plane = imageProxy.planes.firstOrNull() ?: return
            val upright = rgbaPlaneToBitmap(
                plane.buffer,
                imageProxy.width,
                imageProxy.height,
                plane.rowStride,
                plane.pixelStride,
                imageProxy.imageInfo.rotationDegrees
            ) ?: return

            val old: Bitmap?
            synchronized(frameLock) {
                old = latestBitmap
                latestBitmap = upright
                latestFrameTimeMs = System.currentTimeMillis()
            }
            old?.takeUnless(Bitmap::isRecycled)?.recycle()
        } catch (_: Exception) {
            // A malformed frame is disposable; the next CameraX frame replaces it.
        } finally {
            imageProxy.close()
        }
    }

    private fun rgbaPlaneToBitmap(
        buffer: ByteBuffer,
        width: Int,
        height: Int,
        rowStride: Int,
        pixelStride: Int,
        rotationDegrees: Int
    ): Bitmap? {
        if (width <= 0 || height <= 0 || pixelStride <= 0 || rowStride <= 0) return null
        val paddedWidth = rowStride / pixelStride
        if (paddedWidth < width) return null

        buffer.rewind()
        val padded = Bitmap.createBitmap(paddedWidth, height, Bitmap.Config.ARGB_8888)
        padded.copyPixelsFromBuffer(buffer)
        val cropped = if (paddedWidth == width) padded else {
            Bitmap.createBitmap(padded, 0, 0, width, height).also { padded.recycle() }
        }
        if (rotationDegrees == 0) return cropped

        val matrix = Matrix().apply { postRotate(rotationDegrees.toFloat()) }
        return Bitmap.createBitmap(cropped, 0, 0, cropped.width, cropped.height, matrix, true)
            .also { if (it !== cropped) cropped.recycle() }
    }

    suspend fun captureBurst(): Pair<List<BurstFrame>, Int>? = withContext(Dispatchers.Default) {
        if (!isCameraReady || !requested || destroyed) return@withContext null

        captureMutex.withLock {
            val frames = mutableListOf<BurstFrame>()
            val qualities = mutableListOf<QualityScore>()
            try {
                repeat(BURST_COUNT) { index ->
                    if (index > 0) delay(BURST_SPACING_MS)
                    val copy = synchronized(frameLock) {
                        val current = latestBitmap
                        if (current == null || current.isRecycled ||
                            System.currentTimeMillis() - latestFrameTimeMs > MAX_FRAME_AGE_MS
                        ) null else current.copy(Bitmap.Config.ARGB_8888, false)
                    }
                    if (copy != null) {
                        val quality = FrameQualityEvaluator.evaluateRoadFrameQuality(copy)
                        frames += BurstFrame(copy, quality, System.currentTimeMillis())
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

    /** Releases the camera for a user-requested pause without destroying the manager. */
    fun pauseCamera() {
        requested = false
        isCameraReady = false
        boundCamera?.cameraInfo?.cameraState?.removeObservers(lifecycleOwner)
        boundCamera = null
        imageAnalysis?.clearAnalyzer()
        imageAnalysis = null
        cameraProvider?.unbindAll()
        clearLatestFrame()
        onCameraStateChange(false, "Paused")
    }

    fun resumeCamera(onReady: (Boolean) -> Unit = {}) {
        if (destroyed) {
            onReady(false)
            return
        }
        requested = true
        if (cameraProvider == null) startCamera(onReady) else onReady(bindCameraUseCases())
    }

    fun stopCamera() {
        if (destroyed) return
        destroyed = true
        requested = false
        isCameraReady = false
        boundCamera?.cameraInfo?.cameraState?.removeObservers(lifecycleOwner)
        boundCamera = null
        imageAnalysis?.clearAnalyzer()
        imageAnalysis = null
        cameraProvider?.unbindAll()
        clearLatestFrame()
        cameraExecutor.shutdownNow()
    }

    private fun clearLatestFrame() {
        val old = synchronized(frameLock) {
            val value = latestBitmap
            latestBitmap = null
            latestFrameTimeMs = 0L
            value
        }
        old?.takeUnless(Bitmap::isRecycled)?.recycle()
    }
}
