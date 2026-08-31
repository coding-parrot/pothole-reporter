package dev.aiengg.potholereporter.db

import android.content.Context
import androidx.room.Database
import androidx.room.migration.Migration
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.sqlite.db.SupportSQLiteDatabase

@Database(
    entities = [
        ReportEntity::class,
        EventSightingEntity::class,
        RepairTargetEntity::class,
        RepairObservationEntity::class,
        SessionEntity::class,
        FootageSegmentEntity::class,
        DriveKeyframeEntity::class
    ],
    version = 7,
    exportSchema = false
)
abstract class PotholeDatabase : RoomDatabase() {
    abstract fun reportDao(): ReportDao
    abstract fun eventSightingDao(): EventSightingDao
    abstract fun repairTargetDao(): RepairTargetDao
    abstract fun repairObservationDao(): RepairObservationDao
    abstract fun sessionDao(): SessionDao
    abstract fun footageDao(): FootageDao
    abstract fun driveKeyframeDao(): DriveKeyframeDao

    companion object {
        @Volatile
        private var INSTANCE: PotholeDatabase? = null

        private val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """CREATE TABLE IF NOT EXISTS `footage_segments` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `sessionId` TEXT NOT NULL,
                        `filePath` TEXT NOT NULL,
                        `startedAt` INTEGER NOT NULL,
                        `endedAt` INTEGER NOT NULL,
                        `durationMs` INTEGER NOT NULL,
                        `bytes` INTEGER NOT NULL,
                        `errorCode` INTEGER,
                        `complete` INTEGER NOT NULL
                    )""".trimIndent()
                )
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_footage_segments_sessionId` ON `footage_segments` (`sessionId`)")
                db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS `index_footage_segments_filePath` ON `footage_segments` (`filePath`)")
            }
        }

        private val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """CREATE TABLE IF NOT EXISTS `repair_targets` (
                        `reportId` INTEGER NOT NULL,
                        `lat` REAL NOT NULL,
                        `lng` REAL NOT NULL,
                        `gpsAccuracy` REAL,
                        `heading` REAL,
                        `captureSource` TEXT NOT NULL,
                        `photoPath` TEXT NOT NULL,
                        `photoMime` TEXT NOT NULL,
                        `damageType` TEXT NOT NULL,
                        `conditionStatus` TEXT NOT NULL,
                        `lastDamageObservedAt` INTEGER NOT NULL,
                        `lastObservedDriveId` TEXT,
                        `lastObservedAt` INTEGER,
                        PRIMARY KEY(`reportId`)
                    )""".trimIndent()
                )
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_repair_targets_lat` ON `repair_targets` (`lat`)")
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_repair_targets_conditionStatus` ON `repair_targets` (`conditionStatus`)")

                db.execSQL(
                    """CREATE TABLE IF NOT EXISTS `repair_observations` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `targetReportId` INTEGER NOT NULL,
                        `sourceEventKey` TEXT NOT NULL,
                        `observedAt` INTEGER NOT NULL,
                        `driveId` TEXT NOT NULL,
                        `lat` REAL NOT NULL,
                        `lng` REAL NOT NULL,
                        `gpsAccuracy` REAL,
                        `speedMps` REAL,
                        `heading` REAL,
                        `currentPhotoPath` TEXT NOT NULL,
                        `currentCondition` TEXT NOT NULL,
                        `assessment` TEXT NOT NULL,
                        `imageQuality` TEXT NOT NULL,
                        `sameLocationVisible` INTEGER NOT NULL,
                        `completedRepairVisible` INTEGER NOT NULL,
                        `description` TEXT NOT NULL,
                        `detectionModel` TEXT NOT NULL,
                        `imageDetail` TEXT NOT NULL,
                        `promptVersion` TEXT NOT NULL,
                        `schemaVersion` INTEGER NOT NULL,
                        `syncedToWeb` INTEGER NOT NULL
                    )""".trimIndent()
                )
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_repair_observations_targetReportId` ON `repair_observations` (`targetReportId`)")
                db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS `index_repair_observations_sourceEventKey` ON `repair_observations` (`sourceEventKey`)")
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_repair_observations_syncedToWeb` ON `repair_observations` (`syncedToWeb`)")
            }
        }

        private val MIGRATION_3_4 = object : Migration(3, 4) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """CREATE TABLE IF NOT EXISTS `drive_keyframes` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `sessionId` TEXT NOT NULL,
                        `captureSeq` INTEGER NOT NULL,
                        `filePath` TEXT NOT NULL,
                        `capturedAtMs` INTEGER NOT NULL,
                        `sourceOffsetMs` INTEGER NOT NULL,
                        `lat` REAL,
                        `lng` REAL,
                        `gpsAccuracy` REAL,
                        `speedMps` REAL,
                        `heading` REAL,
                        `width` INTEGER NOT NULL,
                        `height` INTEGER NOT NULL,
                        `bytes` INTEGER NOT NULL,
                        `liveAnalyzed` INTEGER NOT NULL
                    )""".trimIndent()
                )
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_drive_keyframes_sessionId` ON `drive_keyframes` (`sessionId`)")
                db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS `index_drive_keyframes_sessionId_captureSeq` ON `drive_keyframes` (`sessionId`, `captureSeq`)")
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_drive_keyframes_liveAnalyzed` ON `drive_keyframes` (`liveAnalyzed`)")
                db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS `index_drive_keyframes_filePath` ON `drive_keyframes` (`filePath`)")
            }
        }

        private val MIGRATION_4_5 = object : Migration(4, 5) {
            override fun migrate(db: SupportSQLiteDatabase) {
                // Old v4 rows are rejected by their detector version during web sync.
                // Conservative defaults prevent missing evidence from being interpreted
                // as a successful v5 physical gate.
                db.execSQL("ALTER TABLE `reports` ADD COLUMN `looksLikeSpeedBreaker` INTEGER NOT NULL DEFAULT 1")
                db.execSQL("ALTER TABLE `reports` ADD COLUMN `hasLocalizedCavity` INTEGER NOT NULL DEFAULT 0")
            }
        }

        internal val MIGRATION_5_6 = object : Migration(5, 6) {
            override fun migrate(db: SupportSQLiteDatabase) {
                // v5 did not record the pavement material or measurement provenance.
                // Fail-closed defaults ensure an old row can never acquire a v6
                // pothole classification merely because it predates these fields.
                db.execSQL("ALTER TABLE `reports` ADD COLUMN `surfaceType` TEXT NOT NULL DEFAULT 'unknown'")
                db.execSQL("ALTER TABLE `reports` ADD COLUMN `defectType` TEXT NOT NULL DEFAULT 'not_pothole'")
                db.execSQL("ALTER TABLE `reports` ADD COLUMN `measurementProvenance` TEXT NOT NULL DEFAULT 'not_applicable'")
                db.execSQL("ALTER TABLE `reports` ADD COLUMN `measurementConfidence` TEXT NOT NULL DEFAULT 'not_applicable'")
            }
        }

        internal val MIGRATION_6_7 = object : Migration(6, 7) {
            override fun migrate(db: SupportSQLiteDatabase) {
                // Earlier accepted rows did not record this stricter visual fact.
                // A fail-closed default preserves their original provenance instead
                // of retroactively claiming that the lower interior was observed.
                db.execSQL("ALTER TABLE `reports` ADD COLUMN `hasUnambiguousLowerInterior` INTEGER NOT NULL DEFAULT 0")
            }
        }

        fun getDatabase(context: Context): PotholeDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    PotholeDatabase::class.java,
                    "native_potholes.db"
                ).addMigrations(
                    MIGRATION_1_2, MIGRATION_2_3, MIGRATION_3_4, MIGRATION_4_5,
                    MIGRATION_5_6, MIGRATION_6_7
                ).build()
                INSTANCE = instance
                instance
            }
        }
    }
}
