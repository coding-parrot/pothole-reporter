package dev.aiengg.potholereporter.drive

import android.content.Context
import android.graphics.Bitmap
import android.graphics.ImageFormat
import android.media.Image
import android.media.ImageReader
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.os.SystemClock
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.Tracks
import androidx.media3.common.VideoSize
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.rtsp.RtspMediaSource
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.ArrayDeque
import javax.net.SocketFactory
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Media3 RTSP/H.264 frame producer.
 *
 * Media3 decodes into a service-owned ImageReader, so analysis continues when the Activity
 * preview is detached. Frames are uniformly scaled at the decoder surface and every retained
 * bitmap contains the complete source view.
 */
@androidx.annotation.OptIn(UnstableApi::class)
internal class NativeRtspFrameSource(
    private val context: Context,
    private val rtspUrl: String,
    initialVideoRecordingEnabled: Boolean,
    private val onStateChange: (
        ready: Boolean,
        state: NativeFrameSourceState,
        issue: String?
    ) -> Unit,
    private val onRecordingStateChange: (
        enabled: Boolean,
        recording: Boolean,
        supported: Boolean,
        message: String?
    ) -> Unit,
    private val onFatalError: (String) -> Unit
) : NativeFrameSource {
    private data class DecodedFrame(
        val bitmap: Bitmap,
        val capturedAtMs: Long,
        val capturedAtElapsedMs: Long,
        val sourceTimestampNs: Long,
        val generation: Long
    )

    override val kind = NativeFrameSourceKind.DASHCAM
    @Volatile override var isReady = false
        private set
    @Volatile override var state = NativeFrameSourceState.IDLE
        private set
    @Volatile override var issue: String? = "Waiting for dashcam stream"
        private set
    @Volatile override var isVideoRecordingEnabled = false
        private set
    @Volatile override var isVideoRecording = false
        private set
    override val isVideoSupported = false

    private val mainHandler = Handler(Looper.getMainLooper())
    private val decodeThread = HandlerThread("PotholeReporter-RTSP-frames").apply { start() }
    private val decodeHandler = Handler(decodeThread.looper)
    private val sourceGeneration = NativeGenerationGate()
    private val samplingGeneration = NativeGenerationGate()
    private val captureMutex = Mutex()
    private val frameLock = Any()
    private val rollingFrames = ArrayDeque<DecodedFrame>(NativeRollingBurstWindow.CAPACITY)
    private val mediaLedger = NativeSourceMediaLedger(context)

    private var player: ExoPlayer? = null
    private var imageReader: ImageReader? = null
    private var requested = false
    private var destroyed = false
    private var terminalSourceError = false
    @Volatile private var samplingEnabled = true
    private var lastSourceTimestampNs = 0L
    private var deliveredFramesSinceSample = 0
    private var conversionFailures = 0
    private var partialFrameFailures = 0
    private var reconnectAttempt = 0
    private var connectingStartedElapsedMs = 0L
    @Volatile private var lastDecodedFrameElapsedMs = 0L
    private var previewListener: NativeDashcamPreviewListener? = null
    private var previewGeneration = 0L
    private var lastPreviewAtElapsedMs = 0L
    private var previewDispatchPending = false

    private val reconnectRunnable = Runnable {
        if (requested && !destroyed && !terminalSourceError) buildPlayer(reconnecting = true)
    }
    private val watchdogRunnable = object : Runnable {
        override fun run() {
            if (!requested || destroyed || terminalSourceError) return
            val now = SystemClock.elapsedRealtime()
            val stalled = watchdogStalled(
                ready = isReady,
                playerActive = player != null,
                connectingStartedElapsedMs = connectingStartedElapsedMs,
                lastDecodedFrameElapsedMs = lastDecodedFrameElapsedMs,
                nowElapsedMs = now
            )
            if (stalled) reconnect("Dashcam stream stopped delivering complete frames")
            if (requested && !destroyed) mainHandler.postDelayed(this, WATCHDOG_INTERVAL_MS)
        }
    }

    init {
        if (initialVideoRecordingEnabled) {
            issue = LOCAL_RECORDING_UNAVAILABLE
        }
    }

    override fun start(onReady: (Boolean) -> Unit) {
        runOnMain {
            if (destroyed) {
                onReady(false)
                return@runOnMain
            }
            requested = true
            terminalSourceError = false
            reconnectAttempt = 0
            buildPlayer(reconnecting = false)
            onReady(true)
        }
    }

    override fun resume(onReady: (Boolean) -> Unit) = start(onReady)

    private fun buildPlayer(reconnecting: Boolean) {
        check(Looper.myLooper() == Looper.getMainLooper())
        if (!requested || destroyed || terminalSourceError) return
        mainHandler.removeCallbacks(reconnectRunnable)
        releasePlayback(clearFrames = true)
        val generation = sourceGeneration.issue()
        connectingStartedElapsedMs = SystemClock.elapsedRealtime()
        publishState(
            false,
            if (reconnecting) NativeFrameSourceState.RECONNECTING
            else NativeFrameSourceState.CONNECTING,
            if (reconnecting) "Reconnecting to dashcam (attempt ${reconnectAttempt.coerceAtLeast(1)})"
            else "Connecting to dashcam"
        )
        // Keep exactly one decoder surface for the complete session. Even a controlled
        // metadata-driven rebuild can race an RTSP decoder that is still returning output
        // buffers (observed as stale BufferQueue generations / queueBuffer -32). SCALE_TO_FIT
        // keeps the entire source frame visible inside this bounded surface, adding only
        // letterbox bars when the dashcam aspect ratio differs; it never crops evidence.
        val initialSize = NativeDashcamFrameDimensions.fitInside(
            DEFAULT_SOURCE_WIDTH,
            DEFAULT_SOURCE_HEIGHT
        )
        val reader = try {
            createReader(initialSize.width, initialSize.height, generation)
        } catch (_: OutOfMemoryError) {
            stopForFatalMemoryError(generation)
            return
        } catch (_: Exception) {
            reconnect("Dashcam decoder surface could not start")
            return
        }
        imageReader = reader
        val mediaSourceFactory = RtspMediaSource.Factory()
            .setDebugLoggingEnabled(false)
            .setTimeoutMs(CONNECT_TIMEOUT_MS)
            // RTP-over-TCP interleaves media on the same routed socket as RTSP control.
            // Otherwise Media3 creates separate UDP datagrams that can follow cellular.
            .setForceUseRtpTcp(true)
        // Bind the RTSP/RTP socket to dashcam Wi-Fi. Never bind the whole process: Maps,
        // geocoding and OpenAI must remain free to use the phone's default/mobile network.
        preferredWifiSocketFactory()?.let(mediaSourceFactory::setSocketFactory)
        val mediaSource = mediaSourceFactory.createMediaSource(MediaItem.fromUri(rtspUrl))
        val loadControl = DefaultLoadControl.Builder()
            .setBufferDurationsMsForStreaming(
                NativeRtspLatencyPolicy.MIN_BUFFER_MS,
                NativeRtspLatencyPolicy.MAX_BUFFER_MS,
                NativeRtspLatencyPolicy.PLAYBACK_BUFFER_MS,
                NativeRtspLatencyPolicy.REBUFFER_MS
            )
            .setPrioritizeTimeOverSizeThresholdsForStreaming(true)
            .build()
        val candidate = try {
            ExoPlayer.Builder(context)
                .setLoadControl(loadControl)
                .build()
        } catch (_: OutOfMemoryError) {
            stopForFatalMemoryError(generation)
            return
        } catch (_: Exception) {
            reconnect("Dashcam decoder could not start")
            return
        }
        // Publish ownership before prepare(): any synchronous setup failure can now use
        // releasePlayback() and cannot leak a half-built decoder or its ImageReader.
        player = candidate
        try {
            candidate.setVideoScalingMode(C.VIDEO_SCALING_MODE_SCALE_TO_FIT)
            candidate.trackSelectionParameters = candidate.trackSelectionParameters.buildUpon()
                .setTrackTypeDisabled(C.TRACK_TYPE_AUDIO, true)
                .build()
            candidate.setVideoSurface(reader.surface)
            candidate.addListener(playerListener(generation))
            candidate.setMediaSource(mediaSource)
            candidate.playWhenReady = true
            candidate.prepare()
        } catch (_: OutOfMemoryError) {
            stopForFatalMemoryError(generation)
            return
        } catch (_: Exception) {
            reconnect("Dashcam stream could not start")
            return
        }
        mainHandler.removeCallbacks(watchdogRunnable)
        mainHandler.postDelayed(watchdogRunnable, WATCHDOG_INTERVAL_MS)
    }

    @Suppress("DEPRECATION")
    private fun preferredWifiSocketFactory(): SocketFactory? = runCatching {
        val connectivity = context.getSystemService(ConnectivityManager::class.java)
            ?: return@runCatching null
        connectivity.allNetworks.firstOrNull { network ->
            connectivity.getNetworkCapabilities(network)
                ?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
        }?.socketFactory
    }.getOrNull()

    private fun playerListener(generation: Long) = object : Player.Listener {
        override fun onPlaybackStateChanged(playbackState: Int) {
            if (!sourceGeneration.isCurrent(generation) || !requested || destroyed) return
            when (playbackState) {
                Player.STATE_BUFFERING -> if (!isReady) publishState(
                    false,
                    if (reconnectAttempt > 0) NativeFrameSourceState.RECONNECTING
                    else NativeFrameSourceState.CONNECTING,
                    if (reconnectAttempt > 0) "Reconnecting to dashcam (attempt $reconnectAttempt)"
                    else "Connecting to dashcam"
                )
                Player.STATE_ENDED -> reconnect("Dashcam stream ended")
            }
        }

        override fun onPlayerError(error: PlaybackException) {
            if (!sourceGeneration.isCurrent(generation) || !requested || destroyed) return
            reconnect("Dashcam stream unavailable (${error.errorCodeName})")
        }

        override fun onTracksChanged(tracks: Tracks) {
            if (!sourceGeneration.isCurrent(generation) || terminalSourceError) return
            val selectedVideoFormats = tracks.groups.flatMap { group ->
                if (group.type != C.TRACK_TYPE_VIDEO) emptyList() else
                    (0 until group.length)
                        .filter(group::isTrackSelected)
                        .map(group::getTrackFormat)
            }
            if (selectedVideoFormats.isNotEmpty() && selectedVideoFormats.none {
                    it.sampleMimeType == MimeTypes.VIDEO_H264
                }
            ) {
                stopForFatalSourceError(
                    generation,
                    "Dashcam stream must use H.264 video"
                )
            }
        }

        override fun onVideoSizeChanged(videoSize: VideoSize) {
            // Deliberately no surface mutation here. Android may emit provisional and then
            // final dimensions for one RTSP stream. The stable SCALE_TO_FIT surface above
            // preserves the complete frame through both events without a decoder restart.
        }
    }

    private fun createReader(width: Int, height: Int, generation: Long): ImageReader =
        ImageReader.newInstance(width, height, ImageFormat.YUV_420_888, MAX_READER_IMAGES).apply {
            setOnImageAvailableListener({ reader -> onImageAvailable(reader, generation) }, decodeHandler)
        }

    private fun onImageAvailable(reader: ImageReader, generation: Long) {
        val image = runCatching { reader.acquireLatestImage() }.getOrNull() ?: return
        image.use {
            if (!requested || destroyed || !sourceGeneration.isCurrent(generation) ||
                image.format != ImageFormat.YUV_420_888 || image.width <= 0 || image.height <= 0
            ) return
            if (image.cropRect.left != 0 || image.cropRect.top != 0 ||
                image.cropRect.right != image.width || image.cropRect.bottom != image.height
            ) {
                partialFrameFailures++
                if (partialFrameFailures >= MAX_PARTIAL_FRAME_FAILURES) {
                    stopForFatalSourceError(
                        generation,
                        "This device's dashcam decoder did not expose complete frames."
                    )
                }
                return
            }
            partialFrameFailures = 0
            val sourceTimestampNs = image.timestamp.takeIf { timestamp -> timestamp > 0L }
                ?: System.nanoTime()
            val nowElapsed = SystemClock.elapsedRealtime()
            // Image.timestamp has a producer-defined timebase and is safe only for media
            // ordering. Media3 does not expose the dashcam's absolute capture time, so GPS
            // uses this monotonic decoder-delivery instant instead of invented backdating.
            var samplingToken = 0L
            val shouldConvert = synchronized(frameLock) {
                samplingToken = samplingGeneration.current()
                val delivered = if (lastSourceTimestampNs == 0L) 0 else
                    (deliveredFramesSinceSample + 1)
                        .coerceAtMost(NativeRollingBurstWindow.SOURCE_FRAME_STRIDE)
                val should = NativeAnalyzerSamplingPolicy.shouldConvert(
                    enabled = samplingEnabled,
                    requested = requested,
                    destroyed = destroyed,
                    cameraReady = true,
                    graphCurrent = sourceGeneration.isCurrent(generation),
                    windowFull = rollingFrames.size >= NativeRollingBurstWindow.CAPACITY,
                    sourceTimestampNs = sourceTimestampNs,
                    lastSampleTimestampNs = lastSourceTimestampNs,
                    deliveredFramesSinceLastSample = delivered,
                    sourceFrameStride = NativeRollingBurstWindow.SOURCE_FRAME_STRIDE,
                    minimumGapNs = NativeRollingBurstWindow.SAMPLE_SPACING_NS,
                    maximumGapNs = NativeRollingBurstWindow.MAX_SAMPLE_GAP_NS
                )
                if (!should && samplingEnabled && rollingFrames.size < NativeRollingBurstWindow.CAPACITY &&
                    sourceTimestampNs > lastSourceTimestampNs
                ) deliveredFramesSinceSample = delivered
                should
            }
            // The Activity preview is a transparency feature, not detection evidence. It
            // must keep updating when GPS pauses detection and after the bounded three-frame
            // evidence window is full. It remains rate-limited and full-frame-only.
            val needsPreview = shouldPublishPreview(nowElapsed)
            if (!shouldConvert && !needsPreview) {
                markStreamHealthy()
                return
            }
            val bitmap = try {
                if (shouldConvert) NativeYuv420FullFrameConverter.toBitmap(image)
                else NativeYuv420FullFrameConverter.toPreviewBitmap(image)
            } catch (_: OutOfMemoryError) {
                stopForFatalMemoryError(generation)
                return
            } catch (_: Exception) {
                conversionFailures++
                if (conversionFailures >= MAX_CONVERSION_FAILURES) {
                    mainHandler.post {
                        if (sourceGeneration.isCurrent(generation)) {
                            reconnect("Dashcam frames could not be decoded safely")
                        }
                    }
                }
                return
            }
            conversionFailures = 0
            if (!shouldConvert) {
                markStreamHealthy()
                publishOwnedPreview(bitmap, nowElapsed, generation)
                return
            }
            val frame = DecodedFrame(
                bitmap = bitmap,
                capturedAtMs = System.currentTimeMillis(),
                capturedAtElapsedMs = nowElapsed,
                sourceTimestampNs = sourceTimestampNs,
                generation = generation
            )
            val retained = synchronized(frameLock) {
                if (!samplingEnabled || destroyed || !requested ||
                    !samplingGeneration.isCurrent(samplingToken) ||
                    !sourceGeneration.isCurrent(generation) ||
                    rollingFrames.size >= NativeRollingBurstWindow.CAPACITY
                ) false else {
                    rollingFrames.addLast(frame)
                    lastSourceTimestampNs = sourceTimestampNs
                    deliveredFramesSinceSample = 0
                    true
                }
            }
            if (!retained) {
                bitmap.recycleSafely()
                return
            }
            markStreamHealthy()
            publishPreviewCopy(bitmap, nowElapsed, generation)
        }
    }

    private fun stopForFatalMemoryError(sourceToken: Long) {
        stopForFatalSourceError(
            sourceToken,
            "This device ran out of image memory while reading dashcam frames."
        )
    }

    private fun stopForFatalSourceError(sourceToken: Long, reason: String) {
        runOnMain {
            if (!sourceGeneration.isCurrent(sourceToken) || destroyed || terminalSourceError) {
                return@runOnMain
            }
            terminalSourceError = true
            requested = false
            sourceGeneration.invalidate()
            mainHandler.removeCallbacks(reconnectRunnable)
            mainHandler.removeCallbacks(watchdogRunnable)
            releasePlayback(clearFrames = true)
            publishState(false, NativeFrameSourceState.ERROR, reason)
            onFatalError(reason)
        }
    }

    private fun markStreamHealthy() {
        lastDecodedFrameElapsedMs = SystemClock.elapsedRealtime()
        if (!isReady) {
            reconnectAttempt = 0
            publishState(true, NativeFrameSourceState.STREAMING, null)
        }
    }

    override fun setSamplingEnabled(enabled: Boolean) {
        val discarded = synchronized(frameLock) {
            if (samplingEnabled == enabled) return
            samplingEnabled = enabled
            samplingGeneration.invalidate()
            if (enabled) emptyList() else rollingFrames.toList().also {
                rollingFrames.clear()
                lastSourceTimestampNs = 0L
                deliveredFramesSinceSample = 0
            }
        }
        discarded.forEach { it.bitmap.recycleSafely() }
    }

    override fun hasCompleteBurst(): Boolean = synchronized(frameLock) {
        rollingFrames.size == NativeRollingBurstWindow.CAPACITY
    }

    override suspend fun captureBurst(): Pair<List<BurstFrame>, Int>? = captureMutex.withLock {
        if (!isReady || !requested || destroyed) return@withLock null
        var discarded: List<DecodedFrame> = emptyList()
        val selected = synchronized(frameLock) {
            val frames = rollingFrames.toList()
            val generation = frames.firstOrNull()?.generation ?: return@synchronized null
            val samples = frames.map {
                NativeRollingBurstWindow.Sample(
                    it.capturedAtElapsedMs,
                    it.sourceTimestampNs,
                    it.generation
                )
            }
            when (NativeRollingBurstWindow.disposition(
                samples,
                SystemClock.elapsedRealtime(),
                generation
            )) {
                NativeRollingBurstWindow.Disposition.WAIT -> null
                NativeRollingBurstWindow.Disposition.DISCARD -> {
                    discarded = frames
                    rollingFrames.clear()
                    lastSourceTimestampNs = 0L
                    deliveredFramesSinceSample = 0
                    null
                }
                NativeRollingBurstWindow.Disposition.READY -> frames.also {
                    rollingFrames.clear()
                    lastSourceTimestampNs = 0L
                    deliveredFramesSinceSample = 0
                }
            }
        }
        discarded.forEach { it.bitmap.recycleSafely() }
        selected ?: return@withLock null
        var transferred = false
        try {
            val burst = selected.map { frame ->
                BurstFrame(
                    frame.bitmap,
                    FrameQualityEvaluator.evaluateFrameQuality(frame.bitmap),
                    frame.capturedAtMs,
                    frame.sourceTimestampNs,
                    frame.generation,
                    frame.capturedAtElapsedMs
                )
            }
            if (burst.size != NativeFrameBurstContract.FRAME_COUNT) {
                burst.forEach { it.bitmap.recycleSafely() }
                return@withLock null
            }
            val result = Pair(
                burst,
                FrameQualityEvaluator.selectBestBurstIndex(burst.map(BurstFrame::quality))
            )
            transferred = true
            result
        } finally {
            if (!transferred) selected.forEach { it.bitmap.recycleSafely() }
        }
    }

    override suspend fun setVideoRecordingEnabled(enabled: Boolean) {
        isVideoRecordingEnabled = false
        isVideoRecording = false
        onRecordingStateChange(
            false,
            false,
            false,
            if (enabled) LOCAL_RECORDING_UNAVAILABLE else "Scanning dashcam frames; video is not saved"
        )
    }

    override suspend fun pauseSafely() {
        runOnMainAndWait {
            requested = false
            mainHandler.removeCallbacks(reconnectRunnable)
            mainHandler.removeCallbacks(watchdogRunnable)
            sourceGeneration.invalidate()
            releasePlayback(clearFrames = true)
            publishState(false, NativeFrameSourceState.PAUSED, "Paused")
        }
    }

    override suspend fun stopSafely() {
        runOnMainAndWait { closePermanently() }
    }

    override fun closeImmediately() {
        runOnMain { closePermanently() }
    }

    private fun closePermanently() {
        if (destroyed) return
        destroyed = true
        requested = false
        sourceGeneration.invalidate()
        mainHandler.removeCallbacks(reconnectRunnable)
        mainHandler.removeCallbacks(watchdogRunnable)
        releasePlayback(clearFrames = true)
        synchronized(frameLock) {
            previewListener = null
            previewGeneration++
        }
        publishState(false, NativeFrameSourceState.STOPPED, null)
        runCatching { decodeThread.quitSafely() }
    }

    private fun reconnect(reason: String) {
        runOnMain {
            if (!requested || destroyed || terminalSourceError) return@runOnMain
            if (reconnectAttempt >= MAX_RECONNECT_ATTEMPT) {
                stopForFatalSourceError(
                    sourceGeneration.current(),
                    "Dashcam stayed unavailable after $MAX_RECONNECT_ATTEMPT reconnect attempts."
                )
                return@runOnMain
            }
            sourceGeneration.invalidate()
            // A reconnect delay is intentionally idle: there is no active player for the
            // watchdog to supervise. Leaving the old connect timestamp/watchdog alive can
            // repeatedly cancel the delayed rebuild and exhaust all attempts in seconds.
            mainHandler.removeCallbacks(watchdogRunnable)
            releasePlayback(clearFrames = true)
            reconnectAttempt += 1
            publishState(
                false,
                NativeFrameSourceState.RECONNECTING,
                "$reason · reconnecting (attempt $reconnectAttempt)"
            )
            val delayMs = reconnectDelayMs(reconnectAttempt)
            mainHandler.removeCallbacks(reconnectRunnable)
            mainHandler.postDelayed(reconnectRunnable, delayMs)
        }
    }

    private fun releasePlayback(clearFrames: Boolean) {
        val currentPlayer = player
        player = null
        runCatching { currentPlayer?.clearVideoSurface() }
        runCatching { currentPlayer?.release() }
        val currentReader = imageReader
        imageReader = null
        runCatching { currentReader?.close() }
        if (clearFrames) clearFrames()
        isReady = false
        connectingStartedElapsedMs = 0L
        lastDecodedFrameElapsedMs = 0L
    }

    private fun clearFrames() {
        val frames = synchronized(frameLock) {
            lastSourceTimestampNs = 0L
            deliveredFramesSinceSample = 0
            rollingFrames.toList().also { rollingFrames.clear() }
        }
        frames.forEach { it.bitmap.recycleSafely() }
    }

    fun attachPreview(listener: NativeDashcamPreviewListener): Boolean {
        synchronized(frameLock) {
            previewListener = listener
            previewGeneration++
            lastPreviewAtElapsedMs = 0L
        }
        return !destroyed
    }

    fun detachPreview(expectedListener: NativeDashcamPreviewListener) {
        synchronized(frameLock) {
            if (previewListener !== expectedListener) return
            previewListener = null
            previewGeneration++
        }
    }

    private fun shouldPublishPreview(nowElapsedMs: Long): Boolean = synchronized(frameLock) {
        NativeDashcamPreviewSamplingPolicy.shouldDecodeForPreview(
            listenerAttached = previewListener != null && !previewDispatchPending,
            nowElapsedMs = nowElapsedMs,
            lastPreviewElapsedMs = lastPreviewAtElapsedMs,
            intervalMs = PREVIEW_INTERVAL_MS
        )
    }

    private data class PreviewTarget(
        val listener: NativeDashcamPreviewListener,
        val generation: Long
    )

    private fun takePreviewTarget(nowElapsedMs: Long): PreviewTarget? = synchronized(frameLock) {
            val listener = previewListener ?: return@synchronized null
            if (previewDispatchPending) return@synchronized null
            if (nowElapsedMs - lastPreviewAtElapsedMs < PREVIEW_INTERVAL_MS) {
                return@synchronized null
            }
            lastPreviewAtElapsedMs = nowElapsedMs
            previewDispatchPending = true
            PreviewTarget(listener, previewGeneration)
        }

    /** Transfers a preview-only bitmap without cloning another full ARGB frame. */
    private fun publishOwnedPreview(source: Bitmap, nowElapsedMs: Long, sourceToken: Long) {
        val target = takePreviewTarget(nowElapsedMs)
        if (target == null) {
            source.recycleSafely()
            return
        }
        deliverPreview(target, source, sourceToken)
    }

    /** Evidence retains the full bitmap; the visible copy is uniformly downscaled. */
    private fun publishPreviewCopy(source: Bitmap, nowElapsedMs: Long, sourceToken: Long) {
        val target = takePreviewTarget(nowElapsedMs) ?: return
        val dimensions = NativeDashcamFrameDimensions.fitInside(
            source.width,
            source.height,
            NativeDashcamFrameDimensions.PREVIEW_MAX_WIDTH,
            NativeDashcamFrameDimensions.PREVIEW_MAX_HEIGHT
        )
        val copy = try {
            val resized = Bitmap.createScaledBitmap(
                source,
                dimensions.width,
                dimensions.height,
                true
            )
            if (resized === source) source.copy(Bitmap.Config.ARGB_8888, false) else resized
        } catch (_: OutOfMemoryError) {
            releasePreviewSlot()
            stopForFatalMemoryError(sourceToken)
            return
        } catch (_: Exception) {
            releasePreviewSlot()
            return
        }
        deliverPreview(target, copy, sourceToken)
    }

    private fun releasePreviewSlot() {
        synchronized(frameLock) { previewDispatchPending = false }
    }

    private fun deliverPreview(target: PreviewTarget, bitmap: Bitmap, sourceToken: Long) {
        val accepted = mainHandler.post {
            try {
                val current = synchronized(frameLock) {
                    previewListener === target.listener && previewGeneration == target.generation
                }
                if (current && sourceGeneration.isCurrent(sourceToken) && !destroyed) {
                    target.listener.onFrame(bitmap)
                } else bitmap.recycleSafely()
            } finally {
                releasePreviewSlot()
            }
        }
        if (!accepted) {
            releasePreviewSlot()
            bitmap.recycleSafely()
        }
    }

    private fun publishState(
        ready: Boolean,
        nextState: NativeFrameSourceState,
        nextIssue: String?
    ) {
        isReady = ready
        state = nextState
        issue = nextIssue
        runOnMain { onStateChange(ready, nextState, nextIssue) }
    }

    private fun runOnMain(block: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) block() else mainHandler.post(block)
    }

    private suspend fun runOnMainAndWait(block: () -> Unit) {
        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Main.immediate) { block() }
    }

    override suspend fun reserveMediaBytes(bytes: Long) = mediaLedger.reserve(bytes)
    override fun commitMediaBytes(
        reservation: NativeMediaStorageQuota.Reservation,
        actualBytes: Long
    ) = mediaLedger.commit(reservation, actualBytes)
    override fun releaseMediaBytes(reservation: NativeMediaStorageQuota.Reservation) =
        mediaLedger.release(reservation)
    override fun noteDeletedMediaBytes(bytes: Long) = mediaLedger.noteDeletion(bytes)
    override fun noteUnexpectedMediaBytes(bytes: Long) = mediaLedger.noteUnexpectedFile(bytes)
    override fun mediaDeletionRecorderIfReconciled() = mediaLedger.deletionRecorderIfReconciled()

    private fun Bitmap.recycleSafely() {
        if (!isRecycled) recycle()
    }

    companion object {
        private const val DEFAULT_SOURCE_WIDTH = 1280
        private const val DEFAULT_SOURCE_HEIGHT = 720
        private const val MAX_READER_IMAGES = 4
        private const val MAX_CONVERSION_FAILURES = 8
        private const val MAX_PARTIAL_FRAME_FAILURES = 8
        private const val WATCHDOG_INTERVAL_MS = 2_000L
        private const val CONNECT_TIMEOUT_MS = 15_000L
        private const val FRAME_STALL_MS = 6_000L
        private const val PREVIEW_INTERVAL_MS = 100L
        private const val MAX_RECONNECT_ATTEMPT = 30
        private const val LOCAL_RECORDING_UNAVAILABLE =
            "Local video recording is unavailable for dashcam streams"

        internal fun reconnectDelayMs(attempt: Int): Long {
            val shift = (attempt - 1).coerceIn(0, 4)
            return (1_000L shl shift).coerceAtMost(15_000L)
        }

        internal fun watchdogStalled(
            ready: Boolean,
            playerActive: Boolean,
            connectingStartedElapsedMs: Long,
            lastDecodedFrameElapsedMs: Long,
            nowElapsedMs: Long
        ): Boolean {
            if (!playerActive) return false
            return if (ready) {
                lastDecodedFrameElapsedMs > 0L &&
                    nowElapsedMs - lastDecodedFrameElapsedMs > FRAME_STALL_MS
            } else {
                connectingStartedElapsedMs > 0L &&
                    nowElapsedMs - connectingStartedElapsedMs > CONNECT_TIMEOUT_MS
            }
        }
    }
}

internal object NativeDashcamFrameDimensions {
    const val MAX_WIDTH = 1280
    const val MAX_HEIGHT = 720
    const val PREVIEW_MAX_WIDTH = 480
    const val PREVIEW_MAX_HEIGHT = 270

    data class Dimensions(val width: Int, val height: Int)

    /** Uniformly fits the complete source frame within the analysis budget. */
    fun fitInside(
        sourceWidth: Int,
        sourceHeight: Int,
        maximumWidth: Int = MAX_WIDTH,
        maximumHeight: Int = MAX_HEIGHT
    ): Dimensions {
        require(sourceWidth > 0 && sourceHeight > 0)
        require(maximumWidth >= 2 && maximumHeight >= 2)
        val scale = min(
            1.0,
            min(maximumWidth.toDouble() / sourceWidth, maximumHeight.toDouble() / sourceHeight)
        )
        fun even(value: Int, maximum: Int): Int {
            val bounded = value.coerceIn(2, maximum)
            return if (bounded % 2 == 0) bounded else (bounded - 1).coerceAtLeast(2)
        }
        return Dimensions(
            even((sourceWidth * scale).roundToInt(), maximumWidth),
            even((sourceHeight * scale).roundToInt(), maximumHeight)
        )
    }
}

/** Converts every pixel in one complete YUV_420_888 image into one complete ARGB bitmap. */
internal object NativeYuv420FullFrameConverter {
    private val reusablePixels = ThreadLocal<IntArray>()

    fun toBitmap(image: Image): Bitmap = toBitmap(image, image.width, image.height)

    fun toPreviewBitmap(image: Image): Bitmap {
        val dimensions = NativeDashcamFrameDimensions.fitInside(
            image.width,
            image.height,
            NativeDashcamFrameDimensions.PREVIEW_MAX_WIDTH,
            NativeDashcamFrameDimensions.PREVIEW_MAX_HEIGHT
        )
        return toBitmap(image, dimensions.width, dimensions.height)
    }

    private fun toBitmap(image: Image, outputWidth: Int, outputHeight: Int): Bitmap {
        require(image.format == ImageFormat.YUV_420_888)
        require(image.planes.size == 3)
        val width = image.width
        val height = image.height
        require(width > 0 && height > 0)
        require(outputWidth in 2..width && outputHeight in 2..height)
        val yPlane = image.planes[0]
        val uPlane = image.planes[1]
        val vPlane = image.planes[2]
        val yBuffer = yPlane.buffer.duplicate()
        val uBuffer = uPlane.buffer.duplicate()
        val vBuffer = vPlane.buffer.duplicate()
        val yBase = yBuffer.position()
        val uBase = uBuffer.position()
        val vBase = vBuffer.position()
        val pixelCount = outputWidth * outputHeight
        val pixels = reusablePixels.get()?.takeIf { it.size >= pixelCount }
            ?: IntArray(pixelCount).also(reusablePixels::set)
        var output = 0
        for (row in 0 until outputHeight) {
            val sourceRow = row * (height - 1) / (outputHeight - 1)
            val chromaRow = sourceRow / 2
            for (column in 0 until outputWidth) {
                val sourceColumn = column * (width - 1) / (outputWidth - 1)
                val chromaColumn = sourceColumn / 2
                val y = yBuffer.get(
                    yBase + sourceRow * yPlane.rowStride + sourceColumn * yPlane.pixelStride
                ).toInt() and 0xff
                val u = uBuffer.get(
                    uBase + chromaRow * uPlane.rowStride + chromaColumn * uPlane.pixelStride
                ).toInt() and 0xff
                val v = vBuffer.get(
                    vBase + chromaRow * vPlane.rowStride + chromaColumn * vPlane.pixelStride
                ).toInt() and 0xff
                val adjustedY = (y - 16).coerceAtLeast(0)
                val adjustedU = u - 128
                val adjustedV = v - 128
                val red = ((298 * adjustedY + 409 * adjustedV + 128) shr 8).coerceIn(0, 255)
                val green = ((298 * adjustedY - 100 * adjustedU - 208 * adjustedV + 128) shr 8)
                    .coerceIn(0, 255)
                val blue = ((298 * adjustedY + 516 * adjustedU + 128) shr 8).coerceIn(0, 255)
                pixels[output++] = (0xff shl 24) or (red shl 16) or (green shl 8) or blue
            }
        }
        return Bitmap.createBitmap(outputWidth, outputHeight, Bitmap.Config.ARGB_8888).apply {
            setPixels(pixels, 0, outputWidth, 0, 0, outputWidth, outputHeight)
        }
    }
}

/** Bounds app-added RTSP buffering so phone GPS stays close to the decoded road view. */
internal object NativeRtspLatencyPolicy {
    const val MIN_BUFFER_MS = 250
    const val MAX_BUFFER_MS = 1_500
    const val PLAYBACK_BUFFER_MS = 100
    const val REBUFFER_MS = 250
}
