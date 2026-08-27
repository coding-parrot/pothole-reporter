package dev.aiengg.potholereporter.drive

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorMatrix
import android.graphics.ColorMatrixColorFilter
import android.graphics.Paint
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
    val capturedAtMs: Long
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

object FrameQualityEvaluator {
    private const val ROAD_BAND = 0.60f
    private const val LUMINANCE_SAMPLE_MAX_WIDTH = 160
    private const val LUMINANCE_SAMPLE_MAX_HEIGHT = 96

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

    fun evaluateRoadFrameQuality(fullBitmap: Bitmap): QualityScore {
        val width = 160
        val height = 96
        val sourceH = max(1, (fullBitmap.height * ROAD_BAND).roundToInt())
        val sourceY = fullBitmap.height - sourceH

        val cropped = Bitmap.createBitmap(fullBitmap, 0, sourceY, fullBitmap.width, sourceH)
        val scaled = if (cropped.width == width && cropped.height == height) cropped
        else Bitmap.createScaledBitmap(cropped, width, height, true)
        NativeBitmapOwnership.recycleIfOwned(cropped, fullBitmap, scaled) {
            if (!it.isRecycled) it.recycle()
        }

        val pixels = IntArray(width * height)
        scaled.getPixels(pixels, 0, width, 0, 0, width, height)
        NativeBitmapOwnership.recycleIfOwned(scaled, fullBitmap) {
            if (!it.isRecycled) it.recycle()
        }

        return scoreRoadPixels(pixels, width, height)
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

    fun prepareRoadBandDataUrl(
        bitmap: Bitmap,
        maxDim: Int = 1024,
        quality: Int = 85,
        boost: Boolean = true
    ): String {
        val sourceH = max(1, (bitmap.height * ROAD_BAND).roundToInt())
        val sourceY = bitmap.height - sourceH
        val cropped = Bitmap.createBitmap(bitmap, 0, sourceY, bitmap.width, sourceH)

        val sw = cropped.width
        val sh = cropped.height
        val scale = min(1f, maxDim.toFloat() / max(sw, sh).toFloat())
        val targetW = max(1, (sw * scale).roundToInt())
        val targetH = max(1, (sh * scale).roundToInt())

        val scaled = if (targetW == sw && targetH == sh) cropped
        else Bitmap.createScaledBitmap(cropped, targetW, targetH, true)
        // createScaledBitmap may return its input for an identity transform. Keep the
        // crop alive when it is also the scaled working bitmap.
        NativeBitmapOwnership.recycleIfOwned(cropped, bitmap, scaled) {
            if (!it.isRecycled) it.recycle()
        }

        val finalBitmap = if (boost) {
            val (meanLum, brightRatio) = calculateAverageLuminance(scaled)
            if (meanLum < 72f && brightRatio < 0.08f) {
                val lift = min(1.65f, max(1.15f, 85f / max(35f, meanLum)))
                applyBrightnessContrast(scaled, lift, 1.10f)
            } else {
                scaled
            }
        } else {
            scaled
        }

        val base64 = bitmapToBase64(finalBitmap, quality)
        NativeBitmapOwnership.recycleIfOwned(finalBitmap, bitmap, scaled) {
            if (!it.isRecycled) it.recycle()
        }
        NativeBitmapOwnership.recycleIfOwned(scaled, bitmap) {
            if (!it.isRecycled) it.recycle()
        }

        return "data:image/jpeg;base64,$base64"
    }

    fun prepareContextDataUrl(
        bitmap: Bitmap,
        maxDim: Int = 768,
        quality: Int = 82
    ): String {
        val sw = bitmap.width
        val sh = bitmap.height
        val scale = min(1f, maxDim.toFloat() / max(sw, sh).toFloat())
        val targetW = max(1, (sw * scale).roundToInt())
        val targetH = max(1, (sh * scale).roundToInt())

        val scaled = if (targetW == sw && targetH == sh) bitmap
        else Bitmap.createScaledBitmap(bitmap, targetW, targetH, true)
        val base64 = bitmapToBase64(scaled, quality)
        // The caller owns the source bitmap; an identity transform must not recycle it.
        NativeBitmapOwnership.recycleIfOwned(scaled, bitmap) {
            if (!it.isRecycled) it.recycle()
        }
        return "data:image/jpeg;base64,$base64"
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

    private fun calculateAverageLuminance(bitmap: Bitmap): Pair<Float, Float> {
        val scale = min(
            1f,
            min(
                LUMINANCE_SAMPLE_MAX_WIDTH.toFloat() / bitmap.width.toFloat(),
                LUMINANCE_SAMPLE_MAX_HEIGHT.toFloat() / bitmap.height.toFloat()
            )
        )
        val width = max(1, (bitmap.width * scale).roundToInt())
        val height = max(1, (bitmap.height * scale).roundToInt())
        val sampled = Bitmap.createScaledBitmap(bitmap, width, height, true)

        try {
            val pixels = IntArray(width * height)
            sampled.getPixels(pixels, 0, width, 0, 0, width, height)

            var total = 0f
            var bright = 0

            for (c in pixels) {
                val r = Color.red(c)
                val g = Color.green(c)
                val b = Color.blue(c)
                val lum = 0.2126f * r + 0.7152f * g + 0.0722f * b
                total += lum
                if (lum > 245f) bright++
            }

            val count = pixels.size
            val mean = if (count > 0) total / count else 0f
            val brightRatio = if (count > 0) bright.toFloat() / count else 0f
            return Pair(mean, brightRatio)
        } finally {
            if (sampled !== bitmap && !sampled.isRecycled) {
                sampled.recycle()
            }
        }
    }

    private fun applyBrightnessContrast(src: Bitmap, brightness: Float, contrast: Float): Bitmap {
        val cm = ColorMatrix(
            floatArrayOf(
                contrast * brightness, 0f, 0f, 0f, 0f,
                0f, contrast * brightness, 0f, 0f, 0f,
                0f, 0f, contrast * brightness, 0f, 0f,
                0f, 0f, 0f, 1f, 0f
            )
        )
        val ret = Bitmap.createBitmap(src.width, src.height, src.config ?: Bitmap.Config.ARGB_8888)
        val canvas = Canvas(ret)
        val paint = Paint().apply { colorFilter = ColorMatrixColorFilter(cm) }
        canvas.drawBitmap(src, 0f, 0f, paint)
        return ret
    }

    private const val MAX_BOUNDED_JPEG_SCALE_PASSES = 4
    private const val BOUNDED_JPEG_QUALITY_STEP = 8
    private const val BOUNDED_JPEG_INITIAL_BUFFER_BYTES = 64L * 1024L
    private const val MIN_BOUNDED_JPEG_DIMENSION = 320
    private const val DEFAULT_BOUNDED_JPEG_SCALE = 0.75
    private const val MIN_BOUNDED_JPEG_SCALE = 0.55
    private const val MAX_BOUNDED_JPEG_SCALE = 0.82
}
