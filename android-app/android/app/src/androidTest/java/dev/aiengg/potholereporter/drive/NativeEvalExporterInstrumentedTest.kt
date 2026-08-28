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
 * Exports the exact JPEG inputs prepared by Android Drive for a private three-frame burst.
 *
 * Input and output stay in the target app's private files directory. No evaluation images
 * are packaged in the test APK:
 *
 *   files/native-eval-input/<event>/f0.jpg .. f2.jpg
 *   files/native-eval-output/<event>/{JPEGs,manifest.json}
 */
@RunWith(AndroidJUnit4::class)
class NativeEvalExporterInstrumentedTest {
    @Test
    fun exportProductionPreparedBurst() {
        val arguments = InstrumentationRegistry.getArguments()
        val event = requireEventName(arguments.getString("event"))
        val primaryIndex = arguments.getString("primaryIndex")?.toIntOrNull()
            ?: error("Instrumentation argument primaryIndex is required and must be an integer")
        require(primaryIndex in FRAME_INDICES) {
            "primaryIndex must be in ${FRAME_INDICES.first}..${FRAME_INDICES.last}"
        }

        val filesDir = InstrumentationRegistry.getInstrumentation().targetContext.filesDir
        val inputDir = File(filesDir, "native-eval-input/$event")
        val outputDir = File(filesDir, "native-eval-output/$event")
        val frames = FRAME_INDICES.map { index ->
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

            // Keep these calls identical to NativeInferenceEngine.analyzeBurst: full-frame
            // context from the selected primary, then road bands in chronological order.
            val prepared = mutableListOf<PreparedImage>()
            prepared += PreparedImage(
                role = "context",
                sourceIndex = primaryIndex,
                dataUrl = FrameQualityEvaluator.prepareContextDataUrl(
                    frames[primaryIndex], maxDim = 768, quality = 82
                )
            )
            frames.forEachIndexed { index, bitmap ->
                prepared += PreparedImage(
                    role = "road_band",
                    sourceIndex = index,
                    dataUrl = FrameQualityEvaluator.prepareRoadBandDataUrl(
                        bitmap, maxDim = 1024, quality = 85, boost = true
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

            val manifest = JSONObject()
                .put("event", event)
                .put("primary_index", primaryIndex)
                .put("image_order", "primary context, then f0..f2 road bands")
                .put("images", imageManifest)
            File(outputDir, "manifest.json").writeText(manifest.toString(2) + "\n")
        } finally {
            frames.forEach { bitmap ->
                if (!bitmap.isRecycled) bitmap.recycle()
            }
        }
    }

    private fun requireEventName(value: String?): String {
        val event = value ?: error("Instrumentation argument event is required")
        require(EVENT_NAME.matches(event) && event != "." && event != "..") {
            "event must contain only 1-80 letters, digits, dots, underscores, or hyphens"
        }
        return event
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

    private companion object {
        val FRAME_INDICES = 0..2
        val EVENT_NAME = Regex("[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
        const val JPEG_DATA_URL_PREFIX = "data:image/jpeg;base64,"
    }
}
