package dev.aiengg.potholereporter.drive

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import java.security.MessageDigest
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Exports the exact JPEG inputs prepared by Android Drive from its private three-sample
 * rolling window. Production sends all three samples as one chronological live burst. The
 * optional durable-burst mode additionally exercises the bounded-JPEG persistence and
 * reload path used when a saved keyframe is replayed through the WebView.
 *
 * Input and output stay in the target app's private files directory. No evaluation images
 * are packaged in the test APK:
 *
 *   files/native-eval-input/<event>/f0.jpg .. f2.jpg
 *   files/native-eval-output/<event>/{source/f0.jpg..f2.jpg,JPEGs,manifest.json}
 *
 * Instrumentation arguments:
 *
 *   event=<safe event name>
 *   sourceMode=live | durable-burst (live by default)
 *   capturedAtElapsedMs=<three comma-separated monotonic capture times in milliseconds>
 *   sourceTimestampNs=<three comma-separated CameraX source times in nanoseconds>
 *   captureRequestElapsedMs=<positive monotonic time at the production capture request>
 *
 * Both independent clocks are mandatory because production uses CameraX nanoseconds for
 * analyzer cadence and elapsed-realtime milliseconds for rolling-window age/span.
 */
@RunWith(AndroidJUnit4::class)
class NativeEvalExporterInstrumentedTest {
    @Test
    fun exportProductionPreparedBurst() {
        val arguments = InstrumentationRegistry.getArguments()
        val eventArgument = arguments.getString("event")
        // This is an argument-driven fixture exporter, not a normal device assertion.
        // Keep the ordinary connected test suite green while still failing closed when
        // a caller explicitly supplies a malformed event name.
        assumeTrue("native evaluator export requires -e event <name>", eventArgument != null)
        val event = requireEventName(eventArgument)
        val sourceMode = SourceMode.parse(arguments.getString("sourceMode"))
        val capturedAtElapsedMs = parseThreePositiveChronological(
            arguments.getString("capturedAtElapsedMs"),
            "capturedAtElapsedMs"
        )
        val sourceTimestampNs = parseThreePositiveChronological(
            arguments.getString("sourceTimestampNs"),
            "sourceTimestampNs"
        )
        val captureRequestElapsedMs = arguments.getString("captureRequestElapsedMs")
            ?.trim()?.toLongOrNull()
            ?: error("captureRequestElapsedMs is required and must be an integer")
        require(captureRequestElapsedMs > 0L) {
            "captureRequestElapsedMs must be positive"
        }
        require(captureRequestElapsedMs >= capturedAtElapsedMs.last()) {
            "captureRequestElapsedMs must not precede the newest source frame"
        }
        var lastSampleTimestampNs = 0L
        sourceTimestampNs.forEach { timestampNs ->
            require(
                NativeAnalyzerSamplingPolicy.shouldConvert(
                    enabled = true,
                    requested = true,
                    destroyed = false,
                    cameraReady = true,
                    graphCurrent = true,
                    windowFull = false,
                    sourceTimestampNs = timestampNs,
                    lastSampleTimestampNs = lastSampleTimestampNs,
                    deliveredFramesSinceLastSample = NativeRollingBurstWindow.SOURCE_FRAME_STRIDE,
                    sourceFrameStride = NativeRollingBurstWindow.SOURCE_FRAME_STRIDE,
                    minimumGapNs = NativeRollingBurstWindow.SAMPLE_SPACING_NS,
                    maximumGapNs = NativeRollingBurstWindow.MAX_SAMPLE_GAP_NS
                )
            ) {
                "sourceTimestampNs violates the production analyzer cadence"
            }
            lastSampleTimestampNs = timestampNs
        }

        val filesDir = InstrumentationRegistry.getInstrumentation().targetContext.filesDir
        val inputDir = File(filesDir, "native-eval-input/$event")
        val outputDir = File(filesDir, "native-eval-output/$event")
        val inputFiles = INPUT_FRAME_INDICES.map { index ->
            val input = File(inputDir, "f$index.jpg")
            require(input.isFile && input.length() > 0L) {
                "Missing non-empty native evaluation input: ${input.absolutePath}"
            }
            input
        }
        val frames = inputFiles.map { input ->
            BitmapFactory.decodeFile(input.absolutePath)
                ?: error("Could not decode native evaluation input: ${input.absolutePath}")
        }

        try {
            if (outputDir.exists()) {
                check(outputDir.deleteRecursively()) {
                    "Could not clear prior native evaluation output: ${outputDir.absolutePath}"
                }
            }
            check(outputDir.mkdirs()) {
                "Could not create native evaluation output: ${outputDir.absolutePath}"
            }

            val rollingSourceIndexes = NativeRollingBurstWindow.selectSourceIndexes(
                INPUT_FRAME_INDICES.map { index ->
                    NativeRollingBurstWindow.Sample(
                        capturedAtElapsedMs[index],
                        sourceTimestampNs[index],
                        generation = 1L
                    )
                },
                nowElapsedMs = captureRequestElapsedMs,
                expectedGeneration = 1L
            ) ?: error("Production rolling-window policy rejected the evaluator samples")
            val selectedFrames = rollingSourceIndexes.map(frames::get)
            val selectedTimestampsMs = rollingSourceIndexes.map(capturedAtElapsedMs::get)
            val qualities = selectedFrames.map(FrameQualityEvaluator::evaluateFrameQuality)
            val livePrimaryIndex = FrameQualityEvaluator.selectBestBurstIndex(qualities)
            val primarySourceIndex = rollingSourceIndexes[livePrimaryIndex]

            val request = when (sourceMode) {
                SourceMode.LIVE -> PreparedRequest(
                    frames = selectedFrames,
                    sourceIndexes = rollingSourceIndexes,
                    primaryIndex = livePrimaryIndex,
                    recycleFrames = false
                )
                SourceMode.DURABLE_BURST -> prepareDurableBurst(
                    outputDir = outputDir,
                    selectedFrames = selectedFrames,
                    selectedTimestampsMs = selectedTimestampsMs,
                    rollingSourceIndexes = rollingSourceIndexes,
                    livePrimaryIndex = livePrimaryIndex
                )
            }

            try {
                // Keep these calls identical to production: downscaled full-frame context
                // from the primary, then complete frames in camera-time order.
                val prepared = mutableListOf<PreparedImage>()
                prepared += PreparedImage(
                    role = "context",
                    sourceIndex = primarySourceIndex,
                    dataUrl = FrameQualityEvaluator.prepareContextDataUrl(
                        request.frames[request.primaryIndex], maxDim = 768, quality = 82
                    )
                )
                request.frames.forEachIndexed { index, bitmap ->
                    prepared += PreparedImage(
                        role = "full_frame",
                        sourceIndex = request.sourceIndexes[index],
                        dataUrl = FrameQualityEvaluator.prepareDetectionFrameDataUrl(
                            bitmap,
                            maxDim = FrameQualityEvaluator.MAX_PREPARED_FRAME_DIMENSION,
                            quality = 85,
                            boost = true
                        )
                    )
                }

                val imageManifest = JSONArray()
                prepared.forEachIndexed { order, image ->
                    val jpeg = decodeJpegDataUrl(image.dataUrl)
                    val filename = when (image.role) {
                        "context" -> "%02d-context-primary-f%d.jpg".format(order, image.sourceIndex)
                        else -> "%02d-full-frame-f%d.jpg".format(order, image.sourceIndex)
                    }
                    File(outputDir, filename).writeBytes(jpeg)
                    val decoded = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size)
                        ?: error("Production output did not decode as JPEG: $filename")
                    try {
                        imageManifest.put(
                            JSONObject()
                                .put("order", order)
                                .put("role", image.role)
                                .put("source_frame", "f${image.sourceIndex}.jpg")
                                .put("file", filename)
                                .put("sha256", sha256(jpeg))
                                .put("bytes", jpeg.size)
                                .put("width", decoded.width)
                                .put("height", decoded.height)
                        )
                    } finally {
                        decoded.recycle()
                    }
                }

                val requestTimestampsMs = request.sourceIndexes.map(capturedAtElapsedMs::get)
                val imageOrder = if (sourceMode == SourceMode.LIVE) {
                    "primary context, then selected f0/f1/f2 full frames"
                } else {
                    "reloaded primary context, then persisted burst full frames in camera-time order"
                }
                val sourceFrameManifest = JSONArray()
                val exportedSourceDir = File(outputDir, "source")
                check(exportedSourceDir.mkdirs()) {
                    "Could not create exported source directory: ${exportedSourceDir.absolutePath}"
                }
                inputFiles.forEachIndexed { index, input ->
                    val bytes = input.readBytes()
                    val exportedSource = File(exportedSourceDir, "f$index.jpg")
                    exportedSource.writeBytes(bytes)
                    check(exportedSource.length() == bytes.size.toLong()) {
                        "Could not export complete source frame: ${exportedSource.absolutePath}"
                    }
                    sourceFrameManifest.put(
                        JSONObject()
                            .put("index", index)
                            .put("file", "source/${exportedSource.name}")
                            .put("sha256", sha256(bytes))
                            .put("bytes", bytes.size)
                            .put("width", frames[index].width)
                            .put("height", frames[index].height)
                    )
                }
                val manifest = JSONObject()
                    .put("event", event)
                    .put("source_mode", sourceMode.argument)
                    .put("timestamp_provenance", "instrumentation arguments")
                    .put("primary_index", request.primaryIndex)
                    .put("live_primary_index", livePrimaryIndex)
                    .put("primary_source_index", primarySourceIndex)
                    .put("rolling_source_frame_indices", JSONArray(rollingSourceIndexes))
                    .put("source_frame_indices", JSONArray(request.sourceIndexes))
                    .put("captured_at_elapsed_ms", JSONArray(capturedAtElapsedMs))
                    .put("source_timestamps_ns", JSONArray(sourceTimestampNs))
                    .put("capture_request_elapsed_ms", captureRequestElapsedMs)
                    .put("request_captured_at_elapsed_ms", JSONArray(requestTimestampsMs))
                    .put(
                        "capture_policy",
                        JSONObject()
                            .put("capacity", NativeRollingBurstWindow.CAPACITY)
                            .put("output_count", NativeRollingBurstWindow.OUTPUT_COUNT)
                            .put("source_frame_stride", NativeRollingBurstWindow.SOURCE_FRAME_STRIDE)
                            .put("max_sample_gap_ms", NativeRollingBurstWindow.MAX_SAMPLE_GAP_MS)
                            .put("sample_spacing_ms", NativeRollingBurstWindow.SAMPLE_SPACING_MS)
                            .put("min_window_span_ms", NativeRollingBurstWindow.MIN_WINDOW_SPAN_MS)
                            .put("max_oldest_age_ms", NativeRollingBurstWindow.MAX_OLDEST_AGE_MS)
                    )
                    .put("source_frames", sourceFrameManifest)
                    .put("quality_scores", JSONArray(qualities.map { it.score.toDouble() }))
                    .put("image_count", prepared.size)
                    .put("image_order", imageOrder)
                    .put("images", imageManifest)
                if (sourceMode == SourceMode.DURABLE_BURST) {
                    manifest.put(
                        "durable_persistence",
                        JSONObject()
                            .put("image_count", NativeRollingBurstWindow.OUTPUT_COUNT)
                            .put("max_bytes_per_image", NativeStoredImagePolicy.MAX_KEYFRAME_IMAGE_BYTES)
                            .put("max_total_bytes", NativeStoredImagePolicy.MAX_KEYFRAME_BURST_BYTES)
                            .put("max_dimension", NativeStoredImagePolicy.KEYFRAME_MAX_DIMENSION)
                            .put("initial_quality", KEYFRAME_JPEG_QUALITY)
                    )
                }
                File(outputDir, "manifest.json").writeText(manifest.toString(2) + "\n")
            } finally {
                if (request.recycleFrames) {
                    request.frames.forEach { bitmap ->
                        if (!bitmap.isRecycled) bitmap.recycle()
                    }
                }
            }
        } finally {
            frames.forEach { bitmap ->
                if (!bitmap.isRecycled) bitmap.recycle()
            }
        }
    }

    /**
     * Runs the same bounded full-frame encoding used by Drive, then resolves the saved
     * burst with NativeKeyframeFiles before decoding it for replay preparation.
     */
    private fun prepareDurableBurst(
        outputDir: File,
        selectedFrames: List<Bitmap>,
        selectedTimestampsMs: List<Long>,
        rollingSourceIndexes: List<Int>,
        livePrimaryIndex: Int
    ): PreparedRequest {
        val contextIndexes = NativeKeyframeFiles.selectTemporalContextIndexes(
            selectedTimestampsMs,
            livePrimaryIndex
        ) ?: error("Production keyframe policy rejected the evaluator timestamps")
        val persistenceDir = File(outputDir, ".durable-source")
        check(persistenceDir.mkdirs()) {
            "Could not create durable evaluator storage: ${persistenceDir.absolutePath}"
        }
        val plan = NativeKeyframeFiles.writePlan(
            persistenceDir,
            captureSeq = 0,
            primarySourceIndex = livePrimaryIndex,
            contextSourceIndexes = contextIndexes
        )
        val persistenceSourceIndexes = listOf(livePrimaryIndex) + contextIndexes
        val encodings = plan.files.zip(persistenceSourceIndexes.map(selectedFrames::get))
        encodings.forEach { (file, bitmap) ->
            val jpeg = FrameQualityEvaluator.bitmapToBoundedJpegBytes(
                bitmap = bitmap,
                maxBytes = NativeStoredImagePolicy.MAX_KEYFRAME_IMAGE_BYTES,
                maxDimension = NativeStoredImagePolicy.KEYFRAME_MAX_DIMENSION,
                initialQuality = KEYFRAME_JPEG_QUALITY
            ) ?: error("Could not encode durable evaluator frame within production limits")
            require(jpeg.isNotEmpty()) { "Durable evaluator produced an empty JPEG" }
            file.writeBytes(jpeg)
            check(file.length() == jpeg.size.toLong()) {
                "Could not persist complete durable evaluator frame: ${file.absolutePath}"
            }
        }

        val readSet = NativeKeyframeFiles.readSet(plan.primaryFile)
        check(readSet.hasTemporalContext && readSet.isComplete && readSet.files.size == 3) {
            "Production keyframe reader rejected the persisted evaluator burst"
        }
        val decoded = mutableListOf<Bitmap>()
        try {
            readSet.files.forEach { file ->
                decoded += BitmapFactory.decodeFile(file.absolutePath)
                    ?: error("Could not reload durable evaluator frame: ${file.absolutePath}")
            }
        } catch (error: Throwable) {
            decoded.forEach { bitmap -> if (!bitmap.isRecycled) bitmap.recycle() }
            throw error
        } finally {
            check(persistenceDir.deleteRecursively()) {
                "Could not remove durable evaluator source files: ${persistenceDir.absolutePath}"
            }
        }
        val sourceIndexByName = buildMap {
            put(plan.primaryFile.name, livePrimaryIndex)
            plan.contextFiles.zip(contextIndexes).forEach { (file, sourceIndex) ->
                put(file.name, sourceIndex)
            }
        }
        val sourceIndexes = readSet.files.map { file ->
            val selectedIndex = sourceIndexByName[file.name]
                ?: error("Production keyframe reader returned an unknown evaluator file")
            rollingSourceIndexes[selectedIndex]
        }
        return PreparedRequest(
            frames = decoded,
            sourceIndexes = sourceIndexes,
            primaryIndex = readSet.primaryIndex,
            recycleFrames = true
        )
    }

    private fun requireEventName(value: String?): String {
        val event = value ?: error("Instrumentation argument event is required")
        require(EVENT_NAME.matches(event) && event != "." && event != "..") {
            "event must contain only 1-80 letters, digits, dots, underscores, or hyphens"
        }
        return event
    }

    private fun parseThreePositiveChronological(value: String?, name: String): List<Long> {
        val timestamps = value?.split(",")?.map { item ->
            item.trim().toLongOrNull()
                ?: error("$name must contain only integer timestamps")
        } ?: error("$name is required")
        require(timestamps.size == INPUT_FRAME_INDICES.count()) {
            "$name must contain exactly three timestamps"
        }
        require(timestamps.all { it > 0L }) {
            "$name must contain only positive timestamps"
        }
        require(timestamps.zipWithNext().all { (left, right) -> right > left }) {
            "$name must be strictly chronological"
        }
        return timestamps
    }

    private fun decodeJpegDataUrl(dataUrl: String): ByteArray {
        require(dataUrl.startsWith(JPEG_DATA_URL_PREFIX)) {
            "FrameQualityEvaluator returned a non-JPEG data URL"
        }
        val jpeg = Base64.decode(dataUrl.substring(JPEG_DATA_URL_PREFIX.length), Base64.DEFAULT)
        require(jpeg.isNotEmpty()) { "FrameQualityEvaluator returned an empty JPEG" }
        return jpeg
    }

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256")
            .digest(bytes)
            .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }

    private data class PreparedImage(
        val role: String,
        val sourceIndex: Int,
        val dataUrl: String
    )

    private data class PreparedRequest(
        val frames: List<Bitmap>,
        val sourceIndexes: List<Int>,
        val primaryIndex: Int,
        val recycleFrames: Boolean
    )

    private enum class SourceMode(val argument: String) {
        LIVE("live"),
        DURABLE_BURST("durable-burst");

        companion object {
            fun parse(value: String?): SourceMode = when (value ?: LIVE.argument) {
                LIVE.argument -> LIVE
                DURABLE_BURST.argument -> DURABLE_BURST
                else -> error("sourceMode must be live or durable-burst")
            }
        }
    }

    private companion object {
        val INPUT_FRAME_INDICES = 0..2
        const val KEYFRAME_JPEG_QUALITY = 88
        val EVENT_NAME = Regex("[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
        const val JPEG_DATA_URL_PREFIX = "data:image/jpeg;base64,"
    }
}
