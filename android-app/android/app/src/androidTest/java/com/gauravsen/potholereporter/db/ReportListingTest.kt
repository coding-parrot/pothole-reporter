package com.gauravsen.potholereporter.db

import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gauravsen.potholereporter.db.dao.toListing
import com.gauravsen.potholereporter.db.entities.ReportEntity
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Home lists native reports on every render through listForHome, which must answer
 * has_photo without reading either image. The evidence copy here is bigger than a
 * CursorWindow, so a query that selected it could not return the row at all.
 */
@RunWith(AndroidJUnit4::class)
class ReportListingTest {
    private lateinit var db: AppDatabase

    @Before
    fun open() {
        db = Room.inMemoryDatabaseBuilder(
            InstrumentationRegistry.getInstrumentation().targetContext,
            AppDatabase::class.java,
        ).build()
    }

    @After
    fun close() = db.close()

    @Test
    fun listForHome_matchesTheFullRowWithoutLoadingPhotos() = runBlocking {
        val dao = db.reportDao()
        val photographed = dao.insert(
            ReportEntity(
                decision = "accept",
                status = "draft",
                damage_type = "pothole_cavity",
                lat = 12.97,
                lng = 77.59,
                email_subject = "Pothole on 80 Feet Road",
                photo = ByteArray(64 * 1024) { 1 },
                photo_full = ByteArray(3 * 1024 * 1024) { 2 },
            ),
        )
        val emptyPhoto = dao.insert(ReportEntity(photo = ByteArray(0), description = "blank frame"))
        val noPhoto = dao.insert(ReportEntity(status = "unrouted", unrouted_reason = "no_body"))

        val listed = dao.listForHome()

        assertEquals(listOf(noPhoto, emptyPhoto, photographed), listed.map { it.id })
        assertEquals(listOf(false, false, true), listed.map { it.has_photo })
        // Same shape as the single-row path, field for field, for the rows whose full
        // copy fits a cursor.
        assertEquals(dao.getById(noPhoto)!!.toListing(), listed[0])
        assertEquals(dao.getById(emptyPhoto)!!.toListing(), listed[1])
        val full = listed[2]
        assertEquals("pothole_cavity", full.damage_type)
        assertEquals("Pothole on 80 Feet Road", full.email_subject)
        assertEquals(12.97, full.lat!!, 0.0)
    }
}
