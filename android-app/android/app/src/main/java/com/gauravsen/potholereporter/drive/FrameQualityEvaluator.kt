package com.gauravsen.potholereporter.drive

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

object FrameQualityEvaluator {
    private const val ROAD_BAND = 0.60f

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
        val scaled = Bitmap.createScaledBitmap(cropped, width, height, true)
        if (cropped != fullBitmap && !cropped.isRecycled) {
            cropped.recycle()
        }

        val pixels = IntArray(width * height)
        scaled.getPixels(pixels, 0, width, 0, 0, width, height)
        if (!scaled.isRecycled) {
            scaled.recycle()
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

        val scaled = Bitmap.createScaledBitmap(cropped, targetW, targetH, true)
        if (cropped != bitmap && !cropped.isRecycled) {
            cropped.recycle()
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
        if (finalBitmap != scaled && !finalBitmap.isRecycled) {
            finalBitmap.recycle()
        }
        if (!scaled.isRecycled) {
            scaled.recycle()
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

        val scaled = Bitmap.createScaledBitmap(bitmap, targetW, targetH, true)
        val base64 = bitmapToBase64(scaled, quality)
        if (!scaled.isRecycled) {
            scaled.recycle()
        }
        return "data:image/jpeg;base64,$base64"
    }

    fun bitmapToJpegBytes(bitmap: Bitmap, quality: Int = 85): ByteArray {
        val stream = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, quality, stream)
        return stream.toByteArray()
    }

    private fun bitmapToBase64(bitmap: Bitmap, quality: Int): String {
        val bytes = bitmapToJpegBytes(bitmap, quality)
        return Base64.encodeToString(bytes, Base64.NO_WRAP)
    }

    private fun calculateAverageLuminance(bitmap: Bitmap): Pair<Float, Float> {
        val width = bitmap.width
        val height = bitmap.height
        val step = max(1, sqrt((width * height).toDouble() / 12000.0).toInt())
        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height)

        var total = 0f
        var count = 0
        var dark = 0
        var bright = 0

        for (y in 0 until height step step) {
            for (x in 0 until width step step) {
                val c = pixels[y * width + x]
                val r = Color.red(c)
                val g = Color.green(c)
                val b = Color.blue(c)
                val lum = 0.2126f * r + 0.7152f * g + 0.0722f * b
                total += lum
                count++
                if (lum < 12f) dark++
                if (lum > 245f) bright++
            }
        }

        val mean = if (count > 0) total / count else 0f
        val brightRatio = if (count > 0) bright.toFloat() / count else 0f
        return Pair(mean, brightRatio)
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
}
