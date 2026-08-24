package dev.aiengg.potholereporter.drive

import dev.aiengg.potholereporter.db.RepairTargetEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeRepairCandidateMatcherTest {
    private val base = RepairTargetEntity(
        reportId = 41,
        lat = 12.911500,
        lng = 77.642700,
        gpsAccuracy = 5f,
        heading = 90f,
        captureSource = "drive_live",
        photoPath = "/tmp/historical.jpg",
        damageType = "pothole_cavity",
        lastDamageObservedAt = 1_800_000_000L
    )

    private fun select(
        targets: List<RepairTargetEntity> = listOf(base),
        lat: Double = base.lat,
        lng: Double = base.lng,
        accuracy: Float? = 5f,
        speed: Float? = 8f,
        heading: Float? = 90f,
        capturedAt: Long = base.lastDamageObservedAt + 1,
        driveId: String = "new-drive"
    ) = NativeRepairCandidateMatcher.selectCandidate(
        targets, lat, lng, accuracy, speed, heading, capturedAt, driveId
    )

    @Test
    fun selectsOnePreciseSameCarriagewayTarget() {
        assertEquals(base.reportId, select()?.reportId)
    }

    @Test
    fun rejectsOppositeOrMissingHeading() {
        assertNull(select(heading = 270f))
        assertNull(select(heading = null))
        assertNull(select(targets = listOf(base.copy(heading = null))))
    }

    @Test
    fun rejectsStationaryOrUnknownMotion() {
        assertNull(select(speed = 1.9f))
        assertNull(select(speed = null))
    }

    @Test
    fun rejectsEqualOrEarlierCaptureTime() {
        assertNull(select(capturedAt = base.lastDamageObservedAt))
        assertNull(select(capturedAt = base.lastDamageObservedAt - 1))
        assertFalse(NativeRepairTime.isStrictlyAfter(
            base.lastDamageObservedAt, base.lastDamageObservedAt
        ))
        assertTrue(NativeRepairTime.isStrictlyAfter(
            base.lastDamageObservedAt + 1, base.lastDamageObservedAt
        ))
    }

    @Test
    fun rejectsPoorGpsAndTargetsBeyondFiveMetres() {
        assertNull(select(accuracy = 12.1f))
        assertNull(select(targets = listOf(base.copy(gpsAccuracy = 12.1f))))
        assertNull(select(lat = 12.911554)) // about six metres north
    }

    @Test
    fun rejectsFixedImportedAndAlreadyObservedTargets() {
        assertNull(select(targets = listOf(base.copy(conditionStatus = "fixed"))))
        assertNull(select(targets = listOf(base.copy(captureSource = "manual_import"))))
        assertNull(select(targets = listOf(base.copy(lastObservedDriveId = "new-drive"))))
    }

    @Test
    fun failsClosedWhenTwoTargetsAreSpatiallyAmbiguous() {
        val second = base.copy(reportId = 42, lat = 12.911509)
        assertNull(select(targets = listOf(base, second)))
    }

    @Test
    fun failsClosedEvenWhenOneOfTwoTargetsIsCloser() {
        val farther = base.copy(reportId = 42, lat = 12.911536)
        assertNull(select(targets = listOf(farther, base)))
    }

    @Test
    fun fixedRequiresEveryStrictVisualGate() {
        assertEquals("fixed", NativeRepairDecision.fromModel(
            "repaired", "clear", "usable", true, true
        ))
        assertNull(NativeRepairDecision.fromModel(
            "repaired", "clear", "usable", false, true
        ))
        assertNull(NativeRepairDecision.fromModel(
            "repaired", "clear", "degraded", true, true
        ))
        assertNull(NativeRepairDecision.fromModel(
            "not_visible", "clear", "usable", true, true
        ))
    }

    @Test
    fun probableRepairCanOnlyBecomeReview() {
        assertEquals("repair_review", NativeRepairDecision.fromModel(
            "repaired", "probable", "usable", true, true
        ))
        assertNull(NativeRepairDecision.fromModel(
            "repaired", "uncertain", "usable", true, true
        ))
    }
}
