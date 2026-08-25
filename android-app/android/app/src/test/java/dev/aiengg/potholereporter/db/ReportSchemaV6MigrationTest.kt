package dev.aiengg.potholereporter.db

import androidx.sqlite.db.SupportSQLiteDatabase
import java.lang.reflect.Proxy
import org.junit.Assert.assertEquals
import org.junit.Test

class ReportSchemaV6MigrationTest {
    @Test
    fun migrationAddsConservativeClassificationDefaults() {
        val statements = mutableListOf<String>()
        val database = Proxy.newProxyInstance(
            SupportSQLiteDatabase::class.java.classLoader,
            arrayOf(SupportSQLiteDatabase::class.java)
        ) { _, method, args ->
            if (method.name == "execSQL") {
                statements += args?.firstOrNull() as String
                Unit
            } else {
                when (method.returnType) {
                    java.lang.Boolean.TYPE -> false
                    java.lang.Integer.TYPE -> 0
                    java.lang.Long.TYPE -> 0L
                    else -> null
                }
            }
        } as SupportSQLiteDatabase

        PotholeDatabase.MIGRATION_5_6.migrate(database)

        assertEquals(5, PotholeDatabase.MIGRATION_5_6.startVersion)
        assertEquals(6, PotholeDatabase.MIGRATION_5_6.endVersion)
        assertEquals(
            listOf(
                "ALTER TABLE `reports` ADD COLUMN `surfaceType` TEXT NOT NULL DEFAULT 'unknown'",
                "ALTER TABLE `reports` ADD COLUMN `defectType` TEXT NOT NULL DEFAULT 'not_pothole'",
                "ALTER TABLE `reports` ADD COLUMN `measurementProvenance` TEXT NOT NULL DEFAULT 'not_applicable'",
                "ALTER TABLE `reports` ADD COLUMN `measurementConfidence` TEXT NOT NULL DEFAULT 'not_applicable'"
            ),
            statements
        )
    }

    @Test
    fun newEntityDefaultsCannotMasqueradeAsAClassifiedPothole() {
        val report = ReportEntity()

        assertEquals("unknown", report.surfaceType)
        assertEquals("not_pothole", report.defectType)
        assertEquals("not_applicable", report.measurementProvenance)
        assertEquals("not_applicable", report.measurementConfidence)
        assertEquals("pothole-binary-v6", report.promptVersion)
        assertEquals(6, report.schemaVersion)
    }
}
