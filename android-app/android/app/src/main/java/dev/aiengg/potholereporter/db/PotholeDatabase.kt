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
        FootageSegmentEntity::class
    ],
    version = 3,
    exportSchema = false
)
abstract class PotholeDatabase : RoomDatabase() {
    abstract fun reportDao(): ReportDao
    abstract fun eventSightingDao(): EventSightingDao
    abstract fun repairTargetDao(): RepairTargetDao
    abstract fun repairObservationDao(): RepairObservationDao
    abstract fun sessionDao(): SessionDao
    abstract fun footageDao(): FootageDao

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

        fun getDatabase(context: Context): PotholeDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    PotholeDatabase::class.java,
                    "native_potholes.db"
                ).addMigrations(MIGRATION_1_2, MIGRATION_2_3).build()
                INSTANCE = instance
                instance
            }
        }
    }
}
