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
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Exports the exact JPEG inputs prepared by Android Drive from a private five-sample
 * rolling window. Production sends samples 0/2/4 as one three-frame live burst. The
 * optional durable-pair mode additionally exercises the exact bounded-JPEG persistence
 * and reload path used when a saved keyframe is replayed through the WebView.
 *
 * Input and output stay in the target app's private files directory. No evaluation images
 * are packaged in the test APK:
 *
 *   files/native-eval-input/<event>/f0.jpg .. f4.jpg
 *   files/native-eval-output/<event>/{JPEGs,manifest.json}
 *
 * Instrumentation arguments:
 *
 *   event=<safe event name>
 *   sourceMode=live | durable-pair (live by default)
 *   sourceTimestampsMs=<five comma-separated, positive, strictly increasing timestamps>
 *
 * Callers producing a production-faithful durable fixture must supply the real timestamps;
 * a deterministic production-cadence fallback remains available for existing live fixtures.
 */
@RunWith(AndroidJUnit4::class)
class NativeEvalExporterInstrumentedTest {
    @Test
    fun exportProductionPreparedBurst() {
        val arguments = InstrumentationRegistry.getArguments()
        val event = requireEventName(arguments.getString("event"))
        val sourceMode = SourceMode.parse(arguments.getString("sourceMode"))
        val timestampArgument = arguments.getString("sourceTimestampsMs")
        val sourceTimestampsMs = parseSourceTimestamps(timestampArgument)
        if (sourceMode == SourceMode.DURABLE_PAIR) {
            require(timestampArgument != null) {
                "sourceTimestampsMs is required for a production-faithful durable-pair export"
            }
        }

        val filesDir = InstrumentationRegistry.getInstrumentation().targetContext.filesDir
        val inputDir = File(filesDir, "native-eval-input/$event")
        val outputDir = File(filesDir, "native-eval-output/$event")
        val frames = INPUT_FRAME_INDICES.map { index ->
            val input = File(inputDir, "f$index.jpg")
            require(input.isFile && input.length() > 0L) {
                "Missing non-empty native evaluation input: ${input.absolutePath}"
            }
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
                    val capturedAtMs = sourceTimestampsMs[index]
                    NativeRollingBurstWindow.Sample(
                        capturedAtMs,
                        capturedAtMs * 1_000_000L,
                        generation = 1L
                    )
                },
                nowMs = sourceTimestampsMs.last() + 1L,
                expectedGeneration = 1L
            ) ?: error("Production rolling-window policy rejected the evaluator samples")
            val selectedFrames = rollingSourceIndexes.map(frames::get)
            val selectedTimestampsMs = rollingSourceIndexes.map(sourceTimestampsMs::get)
            val qualities = selectedFrames.map(FrameQualityEvaluator::evaluateRoadFrameQuality)
            val livePrimaryIndex = FrameQualityEvaluator.selectBestBurstIndex(qualities)
            val primarySourceIndex = rollingSourceIndexes[livePrimaryIndex]

            val request = when (sourceMode) {
                SourceMode.LIVE -> PreparedRequest(
                    frames = selectedFrames,
                    sourceIndexes = rollingSourceIndexes,
                    primaryIndex = livePrimaryIndex,
                    recycleFrames = false
                )
                SourceMode.DURABLE_PAIR -> prepareDurablePair(
                    outputDir = outputDir,
                    selectedFrames = selectedFrames,
                    selectedTimestampsMs = selectedTimestampsMs,
                    rollingSourceIndexes = rollingSourceIndexes,
                    livePrimaryIndex = livePrimaryIndex
                )
            }

            try {
                // Keep these calls identical to the relevant production path: full-frame
                // context from the primary, then request road bands in camera-time order.
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
                        role = "road_band",
                        sourceIndex = request.sourceIndexes[index],
                        dataUrl = FrameQualityEvaluator.prepareRoadBandDataUrl(
                            bitmap, maxDim = 1920, quality = 85, boost = true
                        )
                    )
                }

                val imageManifest = JSONArray()
                prepared.forEachIndexed { order, image ->
                    val jpeg = decodeJpegDataUrl(image.dataUrl)
                    val filename = when (image.role) {
                        "context" -> "%02d-context-primary-f%d.jpg".format(order, image.sourceIndex)
                        else -> "%02d-road-band-f%d.jpg".format(order, image.sourceIndex)
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

                val requestTimestampsMs = request.sourceIndexes.map(sourceTimestampsMs::get)
                val imageOrder = if (sourceMode == SourceMode.LIVE) {
                    "primary context, then selected f0/f2/f4 road bands"
                } else {
                    "reloaded primary context, then persisted pair road bands in camera-time order"
                }
                val manifest = JSONObject()
                    .put("event", event)
                    .put("source_mode", sourceMode.argument)
                    .put("timestamp_provenance", if (timestampArgument == null) {
                        "production-cadence fallback"
                    } else {
                        "instrumentation argument"
                    })
                    .put("primary_index", request.primaryIndex)
                    .put("live_primary_index", livePrimaryIndex)
                    .put("primary_source_index", primarySourceIndex)
                    .put("rolling_source_frame_indices", JSONArray(rollingSourceIndexes))
                    .put("source_frame_indices", JSONArray(request.sourceIndexes))
                    .put("source_timestamps_ms", JSONArray(sourceTimestampsMs))
                    .put("request_source_timestamps_ms", JSONArray(requestTimestampsMs))
                    .put("quality_scores", JSONArray(qualities.map { it.score.toDouble() }))
                    .put("image_count", prepared.size)
                    .put("image_order", imageOrder)
                    .put("images", imageManifest)
                if (sourceMode == SourceMode.DURABLE_PAIR) {
                    manifest.put(
                        "durable_persistence",
                        JSONObject()
                            .put("max_bytes_per_image", NativeStoredImagePolicy.MAX_KEYFRAME_IMAGE_BYTES)
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
     * Runs the same full-frame persistence transaction used by Drive, then resolves the
     * saved pair with NativeKeyframeFiles before decoding it for replay preparation.
     */
    private fun prepareDurablePair(
        outputDir: File,
        selectedFrames: List<Bitmap>,
        selectedTimestampsMs: List<Long>,
        rollingSourceIndexes: List<Int>,
        livePrimaryIndex: Int
    ): PreparedRequest {
        val companionIndex = NativeKeyframeFiles.selectTemporalCompanionIndex(
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
            companionSourceIndex = companionIndex
        )
        val encodings = listOf(
            plan.primaryFile to selectedFrames[livePrimaryIndex],
            plan.companionFile to selectedFrames[companionIndex]
        )
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
        check(readSet.hasTemporalContext && readSet.isComplete && readSet.files.size == 2) {
            "Production keyframe reader rejected the persisted evaluator pair"
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
        val sourceIndexes = readSet.files.map { file ->
            if (file.name == plan.primaryFile.name) {
                rollingSourceIndexes[livePrimaryIndex]
            } else {
                rollingSourceIndexes[companionIndex]
            }
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

    private fun parseSourceTimestamps(value: String?): List<Long> {
        val timestamps = value?.split(",")?.map { item ->
            item.trim().toLongOrNull()
                ?: error("sourceTimestampsMs must contain only integer timestamps")
        } ?: INPUT_FRAME_INDICES.map { index -> 1_000L + index * SAMPLE_SPACING_MS }
        require(timestamps.size == INPUT_FRAME_INDICES.count()) {
            "sourceTimestampsMs must contain exactly five timestamps"
        }
        require(timestamps.all { it > 0L }) {
            "sourceTimestampsMs must contain only positive timestamps"
        }
        require(timestamps.zipWithNext().all { (left, right) -> right > left }) {
            "sourceTimestampsMs must be strictly chronological"
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
        DURABLE_PAIR("durable-pair");

        companion object {
            fun parse(value: String?): SourceMode = when (value ?: LIVE.argument) {
                LIVE.argument -> LIVE
                DURABLE_PAIR.argument -> DURABLE_PAIR
                else -> error("sourceMode must be live or durable-pair")
            }
        }
    }

    private companion object {
        val INPUT_FRAME_INDICES = 0..4
        const val SAMPLE_SPACING_MS = 180L
        const val KEYFRAME_JPEG_QUALITY = 88
        val EVENT_NAME = Regex("[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
        const val JPEG_DATA_URL_PREFIX = "data:image/jpeg;base64,"
    }
}
