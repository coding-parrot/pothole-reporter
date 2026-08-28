package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RoadRegionSelectorTest {
    @Test
    fun portraitFrameKeepsNearAndMidRoadAboveDashboard() {
        val region = RoadRegionSelector.select(frameWidth = 480, frameHeight = 720)

        assertEquals(RoadRegion(x = 0, y = 288, width = 480, height = 187), region)
        assertEquals(475, region.bottomExclusive)
        assertTrue(region.bottomExclusive < 720)
    }

    @Test
    fun landscapeFrameKeepsWideRoadBandAboveDashboard() {
        val region = RoadRegionSelector.select(frameWidth = 1280, frameHeight = 720)

        assertEquals(RoadRegion(x = 0, y = 346, width = 1280, height = 216), region)
        assertEquals(562, region.bottomExclusive)
        assertTrue(region.bottomExclusive < 720)
    }

    @Test
    fun squareAndTinyFramesAlwaysProduceValidBounds() {
        val square = RoadRegionSelector.select(frameWidth = 100, frameHeight = 100)
        assertEquals(RoadRegion(x = 0, y = 40, width = 100, height = 30), square)

        val tiny = RoadRegionSelector.select(frameWidth = 1, frameHeight = 1)
        assertEquals(RoadRegion(x = 0, y = 0, width = 1, height = 1), tiny)
    }
}
