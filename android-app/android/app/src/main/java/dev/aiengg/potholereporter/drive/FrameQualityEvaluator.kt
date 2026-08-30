package dev.aiengg.potholereporter.drive

import android.graphics.Bitmap
import android.graphics.Color
import android.util.Base64
import java.io.ByteArrayOutputStream
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sqrt

data class QualityScore(
    val sharpness: Float,
    val luminance: Float,
    val clipped: Float,
    val score: Float
)

data class BurstFrame(
    val bitmap: Bitmap,
    val quality: QualityScore,
    val capturedAtMs: Long,
    val sourceTimestampNs: Long = capturedAtMs * 1_000_000L,
    val cameraGeneration: Long = 0L,
    val capturedAtElapsedMs: Long = capturedAtMs
)

/** Referential ownership guard shared by bitmap transforms and deterministic JVM tests. */
internal object NativeBitmapOwnership {
    fun <T : Any> recycleIfOwned(
        candidate: T,
        vararg borrowed: T,
        recycle: (T) -> Unit
    ) {
        if (borrowed.none { it === candidate }) recycle(candidate)
    }
}

/** Pure, downscale-only dimension policy shared by every full-frame preparation path. */
internal object NativePreparedImageScale {
    fun downscaleOnly(width: Int, height: Int, maxDimension: Int): Pair<Int, Int> {
        require(width > 0 && height > 0) { "Image dimensions must be positive" }
        require(maxDimension > 0) { "Maximum image dimension must be positive" }
        val longest = max(width, height)
        if (longest <= maxDimension) return width to height
        val scale = maxDimension.toFloat() / longest.toFloat()
        return max(1, (width * scale).roundToInt()) to
            max(1, (height * scale).roundToInt())
    }
}

/**
 * Integer-only low-light enhancement shared, formula-for-formula, with the Web and
 * evaluation runtimes. Android Drive is the reference behaviour: contrast is a gain
 * around black, not CSS/Pillow contrast around a grey or image-mean pivot.
 *
 * Keeping the decision and channel transform free of Bitmap/Canvas makes the exact
 * pixels testable on the JVM and prevents renderer-specific colour-filter rounding.
 */
internal data class DetectionEnhancementPlan(
    val enhanced: Boolean,
    val sampleCount: Int,
    val luminanceSum: Long,
    val darkCount: Int,
    val brightCount: Int,
    val gainNumerator: Long,
    val gainDenominator: Long
)

internal object DetectionImageEnhancement {
    private const val LUMA_RED = 2_126L
    private const val LUMA_GREEN = 7_152L
    private const val LUMA_BLUE = 722L
    private const val LUMA_SCALE = 10_000L
    private const val SAMPLE_TARGET = 12_000.0

    fun plan(argb: IntArray, width: Int, height: Int): DetectionEnhancementPlan {
        require(width > 0 && height > 0 && argb.size == width * height) {
            "Enhancement pixels must match positive dimensions"
        }
        val step = max(1, sqrt(width.toDouble() * height.toDouble() / SAMPLE_TARGET).toInt())
        var luminanceSum = 0L
        var sampleCount = 0
        var darkCount = 0
        var brightCount = 0
        for (y in 0 until height step step) {
            for (x in 0 until width step step) {
                val pixel = argb[y * width + x]
                val red = (pixel ushr 16) and 0xff
                val green = (pixel ushr 8) and 0xff
                val blue = pixel and 0xff
                val luminance = LUMA_RED * red + LUMA_GREEN * green + LUMA_BLUE * blue
                luminanceSum += luminance
                sampleCount++
                if (luminance < 12L * LUMA_SCALE) darkCount++
                if (luminance > 245L * LUMA_SCALE) brightCount++
            }
        }

        val enhance = luminanceSum < 72L * LUMA_SCALE * sampleCount &&
            brightCount.toLong() * 100L < 8L * sampleCount
        if (!enhance) {
            return DetectionEnhancementPlan(
                false, sampleCount, luminanceSum, darkCount, brightCount, 1L, 1L
            )
        }

        // gain = 1.10 * clamp(1.15, 1.65, 85 / max(35, mean_luminance)).
        // The rational form avoids Float/Double and renderer rounding differences.
        var gainNumerator = 935_000L * sampleCount
        var gainDenominator = max(luminanceSum, 35L * LUMA_SCALE * sampleCount)
        when {
            gainNumerator * 1_000L < 1_265L * gainDenominator -> {
                gainNumerator = 1_265L
                gainDenominator = 1_000L
            }
            gainNumerator * 1_000L > 1_815L * gainDenominator -> {
                gainNumerator = 1_815L
                gainDenominator = 1_000L
            }
        }
        return DetectionEnhancementPlan(
            true,
            sampleCount,
            luminanceSum,
            darkCount,
            brightCount,
            gainNumerator,
            gainDenominator
        )
    }

    fun apply(argb: IntArray, plan: DetectionEnhancementPlan): IntArray {
        if (!plan.enhanced) return argb.copyOf()
        val lookup = IntArray(256) { channel -> scaleChannel(channel, plan) }
        return IntArray(argb.size) { index ->
            val pixel = argb[index]
            val alpha = pixel and -0x1000000
            val red = lookup[(pixel ushr 16) and 0xff]
            val green = lookup[(pixel ushr 8) and 0xff]
            val blue = lookup[pixel and 0xff]
            alpha or (red shl 16) or (green shl 8) or blue
        }
    }

    private fun scaleChannel(channel: Int, plan: DetectionEnhancementPlan): Int {
        val numerator = 2L * channel * plan.gainNumerator + plan.gainDenominator
        return (numerator / (2L * plan.gainDenominator)).toInt().coerceIn(0, 255)
    }
}

object FrameQualityEvaluator {
    internal const val MAX_PREPARED_FRAME_DIMENSION = 1280

    fun scoreRoadPixels(pixels: IntArray, width: Int, height: Int): QualityScore {
        val gray = FloatArray(width * height)
        var total = 0f
        var dark = 0
        var bright = 0

        for (i in pixels.indices) {
            val c = pixels[i]
            val r = Color.red(c)
            val g = Color.green(c)
            val b = Color.blue(c)
            val y = 0.2126f * r + 0.7152f * g + 0.0722f * b
            gray[i] = y
            total += y
            if (y < 12f) dark++
            if (y > 245f) bright++
        }

        var edgeTotal = 0f
        var samples = 0
        for (y in 1 until height - 1) {
            for (x in 1 until width - 1) {
                val i = y * width + x
                edgeTotal += abs(4 * gray[i] - gray[i - 1] - gray[i + 1] - gray[i - width] - gray[i + width])
                samples++
            }
        }

        val n = (width * height).toFloat()
        val luminance = if (n > 0) total / n else 0f
        val clipped = if (n > 0) (dark + bright).toFloat() / n else 0f
        val sharpness = if (samples > 0) edgeTotal / samples.toFloat() else 0f
        val unusableExposure = clipped > 0.70f || luminance < 18f || luminance > 238f
        val score = if (unusableExposure) -100f
        else sharpness - abs(luminance - 115f) * 0.055f - clipped * 45f

        return QualityScore(sharpness, luminance, clipped, score)
    }

    fun evaluateFrameQuality(fullBitmap: Bitmap): QualityScore {
        // Quality selection obeys the same invariant as inference: inspect the complete
        // field of view. Downscaling preserves every source pixel without choosing an ROI.
        val (width, height) = NativePreparedImageScale.downscaleOnly(
            fullBitmap.width,
            fullBitmap.height,
            160
        )
        val scaled = if (fullBitmap.width == width && fullBitmap.height == height) fullBitmap
        else Bitmap.createScaledBitmap(fullBitmap, width, height, true)
        try {
            val pixels = IntArray(width * height)
            scaled.getPixels(pixels, 0, width, 0, 0, width, height)
            return scoreRoadPixels(pixels, width, height)
        } finally {
            NativeBitmapOwnership.recycleIfOwned(scaled, fullBitmap) {
                if (!it.isRecycled) it.recycle()
            }
        }
    }

    fun selectBestBurstIndex(frames: List<QualityScore>): Int {
        if (frames.isEmpty()) return 0
        var best = 0
        for (i in 1 until frames.size) {
            if (frames[i].score > frames[best].score) {
                best = i
            }
        }
        return best
    }

    fun prepareDetectionFrameDataUrl(
        bitmap: Bitmap,
        maxDim: Int = 1024,
        quality: Int = 85,
        boost: Boolean = true,
        maxBytes: Long = NativeStoredImagePolicy.MAX_MODEL_IMAGE_BYTES
    ): String {
        val sw = bitmap.width
        val sh = bitmap.height
        // Preserve the complete field of view. Downscaling and compression are permitted;
        // spatial cropping, tiling, masks, and region-of-interest extraction are not.
        val preparedMaxDimension = min(maxDim, MAX_PREPARED_FRAME_DIMENSION)
        val (targetW, targetH) = NativePreparedImageScale.downscaleOnly(
            sw,
            sh,
            preparedMaxDimension
        )

        val scaled = if (targetW == sw && targetH == sh) bitmap
        else Bitmap.createScaledBitmap(bitmap, targetW, targetH, true)

        try {
            val finalBitmap = if (boost) applyDetectionEnhancement(scaled) else scaled
            try {
                val jpeg = bitmapToBoundedJpegBytes(
                    finalBitmap,
                    maxBytes,
                    preparedMaxDimension,
                    quality
                ) ?: throw IllegalStateException("Full-frame image could not fit the model byte limit")
                return "data:image/jpeg;base64,${Base64.encodeToString(jpeg, Base64.NO_WRAP)}"
            } finally {
                NativeBitmapOwnership.recycleIfOwned(finalBitmap, bitmap, scaled) {
                    if (!it.isRecycled) it.recycle()
                }
            }
        } finally {
            // Encoding and enhancement can fail under memory pressure. Release only the
            // temporary whole-frame working bitmap; the caller continues to own the source.
            NativeBitmapOwnership.recycleIfOwned(scaled, bitmap) {
                if (!it.isRecycled) it.recycle()
            }
        }
    }

    fun prepareContextDataUrl(
        bitmap: Bitmap,
        maxDim: Int = 768,
        quality: Int = 82
    ): String {
        val (targetW, targetH) = NativePreparedImageScale.downscaleOnly(
            bitmap.width,
            bitmap.height,
            maxDim
        )

        val scaled = if (targetW == bitmap.width && targetH == bitmap.height) bitmap
        else Bitmap.createScaledBitmap(bitmap, targetW, targetH, true)
        try {
            return "data:image/jpeg;base64,${bitmapToBase64(scaled, quality)}"
        } finally {
            // The caller owns the source bitmap; an identity transform must not recycle it.
            NativeBitmapOwnership.recycleIfOwned(scaled, bitmap) {
                if (!it.isRecycled) it.recycle()
            }
        }
    }

    fun bitmapToJpegBytes(bitmap: Bitmap, quality: Int = 85): ByteArray {
        val stream = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, quality, stream)
        return stream.toByteArray()
    }

    /**
     * Encodes an image under a hard byte ceiling. The bitmap is first bounded by
     * [maxDimension], then JPEG quality and dimensions are reduced in small steps. The
     * caller's bitmap is never recycled or mutated.
     */
    fun bitmapToBoundedJpegBytes(
        bitmap: Bitmap,
        maxBytes: Long,
        maxDimension: Int,
        initialQuality: Int = 88,
        minimumQuality: Int = 52
    ): ByteArray? {
        require(maxBytes in 1..Int.MAX_VALUE.toLong()) { "JPEG byte limit is invalid" }
        require(maxDimension > 0) { "JPEG dimension limit is invalid" }
        require(initialQuality in 1..100 && minimumQuality in 1..initialQuality) {
            "JPEG quality range is invalid"
        }
        if (bitmap.isRecycled || bitmap.width <= 0 || bitmap.height <= 0) return null

        var working = scaleToMaximumDimension(bitmap, maxDimension)
        var ownsWorking = working !== bitmap
        try {
            repeat(MAX_BOUNDED_JPEG_SCALE_PASSES) { pass ->
                var lastEncodedSize = Long.MAX_VALUE
                for (quality in boundedQualitySteps(initialQuality, minimumQuality)) {
                    val stream = ByteArrayOutputStream(
                        min(maxBytes, BOUNDED_JPEG_INITIAL_BUFFER_BYTES).toInt()
                    )
                    if (!working.compress(Bitmap.CompressFormat.JPEG, quality, stream)) return null
                    lastEncodedSize = stream.size().toLong()
                    // Do not duplicate an already-oversized ByteArrayOutputStream just to
                    // discover that it cannot be retained.
                    if (lastEncodedSize in 1..maxBytes) return stream.toByteArray()
                }

                if (working.width <= MIN_BOUNDED_JPEG_DIMENSION &&
                    working.height <= MIN_BOUNDED_JPEG_DIMENSION) return null
                if (pass == MAX_BOUNDED_JPEG_SCALE_PASSES - 1) return null
                val estimatedScale = if (lastEncodedSize in 1 until Long.MAX_VALUE) {
                    sqrt(maxBytes.toDouble() / lastEncodedSize.toDouble()) * 0.92
                } else DEFAULT_BOUNDED_JPEG_SCALE
                val scale = estimatedScale
                    .coerceIn(MIN_BOUNDED_JPEG_SCALE, MAX_BOUNDED_JPEG_SCALE)
                val targetWidth = max(
                    1,
                    min(working.width - 1, (working.width * scale).roundToInt())
                )
                val targetHeight = max(
                    1,
                    min(working.height - 1, (working.height * scale).roundToInt())
                )
                if (targetWidth == working.width && targetHeight == working.height) return null
                val scaled = Bitmap.createScaledBitmap(working, targetWidth, targetHeight, true)
                if (ownsWorking && !working.isRecycled) working.recycle()
                working = scaled
                ownsWorking = working !== bitmap
            }
            return null
        } finally {
            if (ownsWorking && !working.isRecycled) working.recycle()
        }
    }

    private fun scaleToMaximumDimension(bitmap: Bitmap, maxDimension: Int): Bitmap {
        val longest = max(bitmap.width, bitmap.height)
        if (longest <= maxDimension) return bitmap
        val scale = maxDimension.toFloat() / longest.toFloat()
        return Bitmap.createScaledBitmap(
            bitmap,
            max(1, (bitmap.width * scale).roundToInt()),
            max(1, (bitmap.height * scale).roundToInt()),
            true
        )
    }

    private fun boundedQualitySteps(initialQuality: Int, minimumQuality: Int): List<Int> {
        val qualities = mutableListOf<Int>()
        var quality = initialQuality
        while (quality > minimumQuality) {
            qualities += quality
            quality -= BOUNDED_JPEG_QUALITY_STEP
        }
        qualities += minimumQuality
        return qualities.distinct()
    }

    private fun bitmapToBase64(bitmap: Bitmap, quality: Int): String {
        val bytes = bitmapToJpegBytes(bitmap, quality)
        return Base64.encodeToString(bytes, Base64.NO_WRAP)
    }

    private fun applyDetectionEnhancement(src: Bitmap): Bitmap {
        val pixels = IntArray(src.width * src.height)
        src.getPixels(pixels, 0, src.width, 0, 0, src.width, src.height)
        val plan = DetectionImageEnhancement.plan(pixels, src.width, src.height)
        if (!plan.enhanced) return src
        val output = DetectionImageEnhancement.apply(pixels, plan)
        return Bitmap.createBitmap(src.width, src.height, Bitmap.Config.ARGB_8888).also {
            it.setPixels(output, 0, src.width, 0, 0, src.width, src.height)
        }
    }

    private const val MAX_BOUNDED_JPEG_SCALE_PASSES = 4
    private const val BOUNDED_JPEG_QUALITY_STEP = 8
    private const val BOUNDED_JPEG_INITIAL_BUFFER_BYTES = 64L * 1024L
    private const val MIN_BOUNDED_JPEG_DIMENSION = 320
    private const val DEFAULT_BOUNDED_JPEG_SCALE = 0.75
    private const val MIN_BOUNDED_JPEG_SCALE = 0.55
    private const val MAX_BOUNDED_JPEG_SCALE = 0.82
}
