package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class DetectionImageEnhancementTest {
    @Test
    fun productionKernelMatchesSharedGoldenPixels() {
        for (fixture in fixtures()) {
            val inputArgb = fixture.inputRgb.toArgb()
            val plan = DetectionImageEnhancement.plan(inputArgb, fixture.width, fixture.height)
            val outputRgb = DetectionImageEnhancement.apply(inputArgb, plan).toRgb()

            assertEquals(fixture.name, fixture.enhanced, plan.enhanced)
            assertEquals(fixture.name, fixture.sampleCount, plan.sampleCount)
            assertEquals(fixture.name, fixture.luminanceSum, plan.luminanceSum)
            assertEquals(fixture.name, fixture.darkCount, plan.darkCount)
            assertEquals(fixture.name, fixture.brightCount, plan.brightCount)
            assertEquals(fixture.name, fixture.gainNumerator, plan.gainNumerator)
            assertEquals(fixture.name, fixture.gainDenominator, plan.gainDenominator)
            assertArrayEquals(fixture.name, fixture.expectedRgb, outputRgb)
        }
    }

    @Test
    fun malformedPixelDimensionsFailClosed() {
        assertThrows(IllegalArgumentException::class.java) {
            DetectionImageEnhancement.plan(intArrayOf(0xff000000.toInt()), 2, 1)
        }
    }

    private fun fixtures(): List<Fixture> {
        val source = requireNotNull(
            javaClass.classLoader?.getResourceAsStream("detection-image-enhancement-v1.json")
        ) { "shared enhancement fixture is missing" }.bufferedReader().use { it.readText() }
        return Regex("""\{([^{}]+)\}""").findAll(source).mapNotNull { match ->
            val body = match.groupValues[1]
            val name = stringField(body, "name") ?: return@mapNotNull null
            Fixture(
                name = name,
                width = longField(body, "width").toInt(),
                height = longField(body, "height").toInt(),
                inputRgb = csvField(body, "input_rgb"),
                enhanced = booleanField(body, "enhanced"),
                sampleCount = longField(body, "sample_count").toInt(),
                luminanceSum = longField(body, "luminance_sum"),
                darkCount = longField(body, "dark_count").toInt(),
                brightCount = longField(body, "bright_count").toInt(),
                gainNumerator = longField(body, "gain_numerator"),
                gainDenominator = longField(body, "gain_denominator"),
                expectedRgb = csvField(body, "expected_rgb")
            )
        }.toList().also { require(it.isNotEmpty()) { "shared enhancement fixture has no cases" } }
    }

    private fun stringField(body: String, name: String): String? =
        Regex(""""$name"\s*:\s*"([^"]*)"""").find(body)?.groupValues?.get(1)

    private fun longField(body: String, name: String): Long =
        requireNotNull(Regex(""""$name"\s*:\s*(-?\d+)""").find(body)) {
            "missing numeric field $name"
        }.groupValues[1].toLong()

    private fun booleanField(body: String, name: String): Boolean =
        requireNotNull(Regex(""""$name"\s*:\s*(true|false)""").find(body)) {
            "missing boolean field $name"
        }.groupValues[1].toBoolean()

    private fun csvField(body: String, name: String): IntArray =
        requireNotNull(stringField(body, name)) { "missing CSV field $name" }
            .split(',').map(String::toInt).toIntArray()

    private fun IntArray.toArgb(): IntArray {
        require(size % 3 == 0)
        return IntArray(size / 3) { index ->
            val offset = index * 3
            0xff000000.toInt() or (this[offset] shl 16) or
                (this[offset + 1] shl 8) or this[offset + 2]
        }
    }

    private fun IntArray.toRgb(): IntArray = flatMap { pixel ->
        listOf((pixel ushr 16) and 0xff, (pixel ushr 8) and 0xff, pixel and 0xff)
    }.toIntArray()

    private data class Fixture(
        val name: String,
        val width: Int,
        val height: Int,
        val inputRgb: IntArray,
        val enhanced: Boolean,
        val sampleCount: Int,
        val luminanceSum: Long,
        val darkCount: Int,
        val brightCount: Int,
        val gainNumerator: Long,
        val gainDenominator: Long,
        val expectedRgb: IntArray
    )
}
