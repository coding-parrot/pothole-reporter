package com.gauravsen.potholereporter.bridge

import android.app.Activity
import android.content.ClipData
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.util.Base64
import androidx.activity.result.ActivityResult
import androidx.core.content.IntentCompat
import androidx.media3.common.MediaItem
import androidx.media3.common.util.UnstableApi
import androidx.media3.effect.Presentation
import androidx.media3.exoplayer.SeekParameters
import androidx.media3.inspector.frame.FrameExtractor
import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.ActivityCallback
import com.getcapacitor.annotation.CapacitorPlugin
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

internal fun isVideoIngressAction(action: String?): Boolean = action in setOf(
    Intent.ACTION_SEND,
    Intent.ACTION_SEND_MULTIPLE,
)

/**
 * Native video ingress and frame extraction for gallery, Meta-glasses and dashcam clips.
 *
 * ACTION_OPEN_DOCUMENT is the primary native path: it retains a seekable content URI and
 * never copies the complete video. ACTION_SEND is a bounded fallback because
 * another app's URI grant is commonly temporary. JavaScript receives one JPEG frame at a
 * time, so a multi-gigabyte source never becomes a WebView Blob or base64 video.
 */
@UnstableApi
@CapacitorPlugin(name = "VideoImport")
class VideoImportPlugin : Plugin() {
    private data class FrameSession(
        val id: String,
        val videoId: String,
        val extractor: FrameExtractor,
        var lastUsedAtMs: Long,
    )

    private val workerJob = SupervisorJob()
    private val workerScope = CoroutineScope(workerJob + Dispatchers.IO)
    private val frameExecutor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "PotholeVideoFrames").apply { isDaemon = true }
    }
    // Accessed only on frameExecutor, satisfying FrameExtractor's single-thread contract.
    private val frameSessions = mutableMapOf<String, FrameSession>()
    private val inFlightFrames = ConcurrentHashMap<String, com.google.common.util.concurrent.ListenableFuture<*>>()
    private val closingSessions = ConcurrentHashMap.newKeySet<String>()
    private val frameGeneration = AtomicLong(0L)
    private val openingAnalysis = AtomicBoolean(false)
    private val extractingFrame = AtomicBoolean(false)
    private val clearingImports = AtomicBoolean(false)

    override fun load() {
        super.load()
        workerScope.launch {
            STORE_MUTEX.withLock {
                val snapshot = VideoImportStore(requireNotNull(context)).snapshot()
                if (snapshot.imports.isNotEmpty() || snapshot.error != null) {
                    emitSnapshot(snapshot)
                }
            }
        }
    }

    @PluginMethod
    fun isAvailable(call: PluginCall) {
        call.resolve(
            JSObject()
                .put("available", true)
                .put("native_frame_extraction", true)
                .put("external_share_max_bytes", VIDEO_IMPORT_MAX_ITEM_BYTES),
        )
    }

    /** Open Android's Storage Access Framework and retain read access across restarts. */
    @PluginMethod
    fun pickVideo(call: PluginCall) {
        if (clearingImports.get()) {
            call.reject("Imported-video data is being cleared")
            return
        }
        val picker = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "video/*"
            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
        }
        startActivityForResult(call, picker, "videoPicked")
    }

    @ActivityCallback
    private fun videoPicked(call: PluginCall, result: ActivityResult) {
        if (result.resultCode != Activity.RESULT_OK || result.data == null) {
            call.resolve(JSObject().put("cancelled", true))
            return
        }
        val resultIntent = requireNotNull(result.data)
        if (clearingImports.get()) {
            call.reject("Imported-video data is being cleared")
            return
        }
        val uris = pickerUris(resultIntent)
        if (uris.isEmpty()) {
            call.reject("No video was selected")
            return
        }
        val appContext = context ?: run {
            call.reject("Context not available")
            return
        }
        workerScope.launch {
            STORE_MUTEX.withLock {
                val snapshot = VideoImportStore(appContext).registerPersistedUris(
                    sources = uris.map {
                        SharedVideoSource(it, resultIntent.type, Intent.ACTION_OPEN_DOCUMENT)
                    },
                    grantFlags = resultIntent.flags,
                )
                val selectedByUri = snapshot.imports.mapNotNull { imported ->
                    imported.contentUri?.let { it to imported }
                }.toMap()
                val selected = uris.mapNotNull { selectedByUri[it.toString()] }
                val response = snapshot.json()
                    .put("cancelled", false)
                    .put("videos", org.json.JSONArray().also { array ->
                        selected.forEach { array.put(it.publicJson()) }
                    })
                    .put("video", selected.firstOrNull()?.publicJson() ?: JSONObject.NULL)
                if (snapshot.error != null || selected.isEmpty()) {
                    emitSnapshot(snapshot)
                    call.reject(snapshot.error?.message ?: "The selected videos could not be opened.")
                } else {
                    call.resolve(JSObject(response.toString()))
                    emitSnapshot(snapshot)
                }
            }
        }
    }

    @PluginMethod
    fun getPendingImports(call: PluginCall) {
        val appContext = context ?: run { call.reject("Context not available"); return }
        workerScope.launch {
            STORE_MUTEX.withLock {
                call.resolve(JSObject(VideoImportStore(appContext).snapshot().json().toString()))
            }
        }
    }

    /** Compatibility helper for a UI that consumes one shared/opened video at a time. */
    @PluginMethod
    fun getPendingImport(call: PluginCall) {
        val appContext = context ?: run { call.reject("Context not available"); return }
        workerScope.launch {
            STORE_MUTEX.withLock {
                val snapshot = VideoImportStore(appContext).snapshot()
                call.resolve(
                    JSObject(snapshot.json().toString())
                        .put("import", snapshot.imports.firstOrNull()?.publicJson() ?: JSONObject.NULL),
                )
            }
        }
    }

    /**
     * Release the URI grant/cache file only after analysis has completed or been cancelled.
     * Calling this while a native analysis session is open is rejected.
     */
    @PluginMethod
    fun consumePendingImport(call: PluginCall) = removeImport(call, "consumed")

    @PluginMethod
    fun discardPendingImport(call: PluginCall) = removeImport(call, "discarded")

    @PluginMethod
    fun clearImportError(call: PluginCall) {
        val appContext = context ?: run { call.reject("Context not available"); return }
        workerScope.launch {
            STORE_MUTEX.withLock {
                call.resolve(JSObject(VideoImportStore(appContext).clearError().json().toString()))
            }
        }
    }

    /** Close decoders, cancel frame work, release URI grants and delete temporary copies. */
    @PluginMethod
    fun clearAllImports(call: PluginCall) {
        if (!clearingImports.compareAndSet(false, true)) {
            call.reject("Imported-video data is already being cleared")
            return
        }
        frameGeneration.incrementAndGet()
        inFlightFrames.values.forEach { it.cancel(true) }
        inFlightFrames.clear()
        VideoImportStore.cancelActiveCopies()
        val appContext = context ?: run {
            clearingImports.set(false)
            call.reject("Context not available")
            return
        }
        try {
            frameExecutor.execute {
                frameSessions.keys.toList().forEach(::closeSession)
                workerScope.launch {
                    STORE_MUTEX.withLock {
                        try {
                            val snapshot = VideoImportStore(appContext).clearAll()
                            call.resolve(JSObject(snapshot.json().toString()).put("cleared", true))
                            emitSnapshot(snapshot)
                        } catch (error: Exception) {
                            call.reject("Could not clear imported-video data: ${safeError(error)}")
                        } finally {
                            clearingImports.set(false)
                        }
                    }
                }
            }
        } catch (error: Exception) {
            clearingImports.set(false)
            call.reject("Could not clear imported-video data: ${safeError(error)}")
        }
    }

    /**
     * Create one reusable decoder for sequential timestamp extraction. `seekMode: "fast"`
     * uses keyframes; the default `exact` mode is better for one-frame-per-second detection.
     */
    @PluginMethod
    fun openAnalysis(call: PluginCall) {
        val videoId = call.getString("id")?.trim().orEmpty()
        if (videoId.isBlank()) { call.reject("Video ID required"); return }
        val requestedHeight = call.getInt("maxHeight")
        val seekMode = call.getString("seekMode", "exact")?.lowercase() ?: "exact"
        if (seekMode !in setOf("exact", "fast")) { call.reject("Unknown seek mode"); return }
        if (clearingImports.get()) { call.reject("Imported-video data is being cleared"); return }
        if (!openingAnalysis.compareAndSet(false, true)) {
            call.reject("Another video decoder is already being opened")
            return
        }
        val generation = frameGeneration.get()
        val appContext = context ?: run {
            openingAnalysis.set(false)
            call.reject("Context not available")
            return
        }

        workerScope.launch {
            val imported = STORE_MUTEX.withLock { VideoImportStore(appContext).find(videoId) }
            if (imported == null) {
                openingAnalysis.set(false)
                call.reject("Imported video not found or permission expired")
                return@launch
            }
            try {
                frameExecutor.execute {
                    try {
                        if (generation != frameGeneration.get() || clearingImports.get()) {
                            call.reject("Video analysis was cancelled")
                            return@execute
                        }
                    closeIdleSessions()
                    if (frameSessions.size >= MAX_FRAME_SESSIONS) {
                        call.reject("Close the other video analysis before opening another one")
                        return@execute
                    }
                    val output = boundedFrameSize(
                        requestedHeight,
                        imported.width,
                        imported.height,
                        imported.rotationDegrees,
                    )
                    val builder = FrameExtractor.Builder(
                        appContext,
                        MediaItem.fromUri(imported.accessUri()),
                    )
                        .setEffects(listOf(Presentation.createForWidthAndHeight(
                            output.width,
                            output.height,
                            Presentation.LAYOUT_SCALE_TO_FIT,
                        )))
                    if (seekMode == "fast") builder.setSeekParameters(SeekParameters.CLOSEST_SYNC)
                    val extractor = builder.build()
                    val sessionId = UUID.randomUUID().toString()
                    frameSessions[sessionId] = FrameSession(
                        id = sessionId,
                        videoId = videoId,
                        extractor = extractor,
                        lastUsedAtMs = System.currentTimeMillis(),
                    )
                    call.resolve(
                        JSObject()
                            .put("session_id", sessionId)
                            .put("video_id", videoId)
                            .put("output_width", output.width)
                            .put("output_height", output.height)
                            .put("max_pixels", MAX_FRAME_PIXELS)
                            .put("seek_mode", seekMode)
                            .put("duration_ms", imported.durationMs ?: JSObject.NULL),
                    )
                    } catch (error: Exception) {
                        call.reject("Could not open the video decoder: ${safeError(error)}")
                    } finally {
                        openingAnalysis.set(false)
                    }
                }
            } catch (error: Exception) {
                openingAnalysis.set(false)
                call.reject("Could not queue the video decoder: ${safeError(error)}")
            }
        }
    }

    /** Return exactly one bounded JPEG frame; never return or copy the source video. */
    @PluginMethod
    fun extractFrame(call: PluginCall) {
        val sessionId = call.getString("sessionId")?.trim().orEmpty()
        val positionMs = call.getLong("positionMs", -1L) ?: -1L
        val quality = clampedJpegQuality(call.getInt("quality"))
        if (sessionId.isBlank()) { call.reject("Analysis session ID required"); return }
        if (positionMs < 0L || positionMs > MAX_VIDEO_POSITION_MS) {
            call.reject("Frame timestamp is outside the supported range")
            return
        }
        if (clearingImports.get() || closingSessions.contains(sessionId)) {
            call.reject("Video analysis session is closing")
            return
        }
        // Bound the entire decoder executor to one pending/in-flight frame. This prevents
        // a WebView bug from queueing minutes of seeks ahead of close or privacy wipe.
        if (!extractingFrame.compareAndSet(false, true)) {
            call.reject("Another video frame is already being decoded")
            return
        }
        val generation = frameGeneration.get()
        try {
            frameExecutor.execute {
                if (generation != frameGeneration.get() || clearingImports.get() ||
                    closingSessions.contains(sessionId)
                ) {
                    extractingFrame.set(false)
                    call.reject("Video frame extraction was cancelled")
                    return@execute
                }
            val session = frameSessions[sessionId]
            if (session == null) {
                extractingFrame.set(false)
                call.reject("Video analysis session is closed")
                return@execute
            }
            var future: com.google.common.util.concurrent.ListenableFuture<FrameExtractor.Frame>? = null
            try {
                future = session.extractor.getFrame(positionMs)
                inFlightFrames[sessionId] = future
                val frame = future.get(FRAME_TIMEOUT_SECONDS, TimeUnit.SECONDS)
                val pixels = frame.bitmap.width.toLong() * frame.bitmap.height.toLong()
                if (frame.bitmap.width > MAX_FRAME_DIMENSION ||
                    frame.bitmap.height > MAX_FRAME_DIMENSION || pixels > MAX_FRAME_PIXELS
                ) {
                    throw IllegalStateException("Decoded frame exceeded the native pixel limit")
                }
                val output = ByteArrayOutputStream()
                val jpeg = output.use {
                    if (!frame.bitmap.compress(Bitmap.CompressFormat.JPEG, quality, output)) {
                        throw IllegalStateException("JPEG encoder rejected the decoded frame")
                    }
                    if (output.size() > MAX_JPEG_BYTES) {
                        throw IllegalStateException("Decoded JPEG exceeded the native payload limit")
                    }
                    output.toByteArray()
                }
                session.lastUsedAtMs = System.currentTimeMillis()
                call.resolve(
                    JSObject()
                        .put("session_id", sessionId)
                        .put("video_id", session.videoId)
                        .put("requested_position_ms", positionMs)
                        .put("presentation_position_ms", frame.presentationTimeMs)
                        .put("width", frame.bitmap.width)
                        .put("height", frame.bitmap.height)
                        .put("mime_type", "image/jpeg")
                        .put("bytes", jpeg.size)
                        .put("jpeg_base64", Base64.encodeToString(jpeg, Base64.NO_WRAP)),
                )
            } catch (error: Exception) {
                future?.cancel(true)
                call.reject("Could not decode frame at $positionMs ms: ${safeError(error)}")
            } finally {
                future?.let { completed -> inFlightFrames.remove(sessionId, completed) }
                extractingFrame.set(false)
            }
            }
        } catch (error: Exception) {
            extractingFrame.set(false)
            call.reject("Could not queue frame extraction: ${safeError(error)}")
        }
    }

    @PluginMethod
    fun closeAnalysis(call: PluginCall) {
        val sessionId = call.getString("sessionId")?.trim().orEmpty()
        if (sessionId.isBlank()) { call.reject("Analysis session ID required"); return }
        if (!closingSessions.add(sessionId)) {
            call.reject("Video analysis session is already closing")
            return
        }
        // Cancel immediately rather than queueing cancellation behind a 45-second decode.
        inFlightFrames.remove(sessionId)?.cancel(true)
        try {
            frameExecutor.execute {
                closeSession(sessionId)
                closingSessions.remove(sessionId)
                call.resolve(JSObject().put("closed", true).put("session_id", sessionId))
            }
        } catch (error: Exception) {
            closingSessions.remove(sessionId)
            call.reject("Could not close video analysis: ${safeError(error)}")
        }
    }

    override fun handleOnNewIntent(intent: Intent) {
        super.handleOnNewIntent(intent)
        acceptIngressIntent(intent)
    }

    /** Public so MainActivity can explicitly guarantee cold-start delivery after bridge setup. */
    fun acceptIngressIntent(intent: Intent) {
        if (!SHARE_INGRESS_ENABLED) return
        if (!isVideoIngressAction(intent.action)) return
        if (clearingImports.get()) return
        val appContext = context ?: return
        val fingerprint = ingressFingerprint(intent)
        if (!INGRESS_IN_FLIGHT.add(fingerprint)) return
        val sources = videoUris(intent).map {
            SharedVideoSource(it, intent.type, intent.action ?: Intent.ACTION_SEND)
        }
        workerScope.launch {
            try {
                STORE_MUTEX.withLock {
                    val store = VideoImportStore(appContext)
                    val snapshot = if (sources.any { it.uri.scheme != "content" }) {
                        store.reject(
                            "unsafe_video_uri",
                            "That app did not share a safe content URI. Use Import video and select it from Files instead.",
                        )
                    } else {
                        store.import(sources)
                    }
                    emitSnapshot(snapshot)
                }
            } finally {
                INGRESS_IN_FLIGHT.remove(fingerprint)
            }
        }
    }

    override fun handleOnDestroy() {
        super.handleOnDestroy()
        clearingImports.set(true)
        frameGeneration.incrementAndGet()
        inFlightFrames.values.forEach { it.cancel(true) }
        inFlightFrames.clear()
        VideoImportStore.cancelActiveCopies()
        runCatching {
            frameExecutor.execute {
                frameSessions.keys.toList().forEach(::closeSession)
            }
        }
        frameExecutor.shutdown()
        workerScope.cancel()
    }

    private fun removeImport(call: PluginCall, disposition: String) {
        val id = call.getString("id")?.trim().orEmpty()
        if (id.isBlank()) { call.reject("Video ID required"); return }
        if (clearingImports.get()) { call.reject("Imported-video data is being cleared"); return }
        frameExecutor.execute {
            if (frameSessions.values.any { it.videoId == id }) {
                call.reject("Close or cancel this video's analysis before removing it")
                return@execute
            }
            val appContext = context ?: run { call.reject("Context not available"); return@execute }
            workerScope.launch {
                STORE_MUTEX.withLock {
                    val snapshot = VideoImportStore(appContext).delete(id)
                    call.resolve(
                        JSObject(snapshot.json().toString())
                            .put("removed", true)
                            .put("disposition", disposition)
                            .put("id", id),
                    )
                    emitSnapshot(snapshot)
                }
            }
        }
    }

    private fun emitSnapshot(snapshot: VideoImportSnapshot) {
        bridge.executeOnMainThread {
            notifyListeners("videoImportReady", JSObject(snapshot.json().toString()), true)
        }
    }

    private fun closeIdleSessions(nowMs: Long = System.currentTimeMillis()) {
        frameSessions.values
            .filter { nowMs - it.lastUsedAtMs >= FRAME_SESSION_IDLE_MS }
            .map { it.id }
            .forEach(::closeSession)
    }

    private fun closeSession(sessionId: String) {
        frameSessions.remove(sessionId)?.let { session -> runCatching { session.extractor.close() } }
        closingSessions.remove(sessionId)
    }

    @Suppress("DEPRECATION")
    private fun videoUris(intent: Intent): List<Uri> {
        val found = linkedSetOf<Uri>()
        intent.clipData?.addUrisTo(found)
        if (intent.action == Intent.ACTION_SEND) {
            IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java)?.let(found::add)
        }
        if (intent.action == Intent.ACTION_SEND_MULTIPLE) {
            val streams = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
            } else {
                intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM)
            }
            streams?.forEach(found::add)
        }
        return found.take(VIDEO_IMPORT_MAX_BATCH_ITEMS + 1)
    }

    /** Preserve the user's Files ordering; `data` is commonly a duplicate of clipData[0]. */
    private fun pickerUris(intent: Intent): List<Uri> {
        val found = linkedSetOf<Uri>()
        intent.clipData?.addUrisTo(found)
        intent.data?.let(found::add)
        return found.take(VIDEO_IMPORT_MAX_PICKER_ITEMS + 1)
    }

    private fun ClipData.addUrisTo(destination: MutableSet<Uri>) {
        for (index in 0 until itemCount) getItemAt(index).uri?.let(destination::add)
    }

    private fun safeError(error: Throwable): String =
        (error.cause?.message ?: error.message ?: error.javaClass.simpleName)
            .replace(Regex("[\\r\\n]+"), " ")
            .take(240)

    private fun ingressFingerprint(intent: Intent): String = buildString {
        append(intent.action)
        append('|')
        videoUris(intent).forEach { append(it).append('|') }
    }

    companion object {
        private const val MAX_FRAME_SESSIONS = 1
        private const val FRAME_SESSION_IDLE_MS = 10L * 60L * 1000L
        private const val FRAME_TIMEOUT_SECONDS = 45L
        private const val MAX_VIDEO_POSITION_MS = 7L * 24L * 60L * 60L * 1000L
        private val STORE_MUTEX = Mutex()
        private val INGRESS_IN_FLIGHT = ConcurrentHashMap.newKeySet<String>()
    }
}
