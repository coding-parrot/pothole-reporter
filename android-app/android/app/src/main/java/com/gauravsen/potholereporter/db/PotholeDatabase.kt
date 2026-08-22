package com.gauravsen.potholereporter.db

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
        SessionEntity::class,
        FootageSegmentEntity::class
    ],
    version = 2,
    exportSchema = false
)
abstract class PotholeDatabase : RoomDatabase() {
    abstract fun reportDao(): ReportDao
    abstract fun eventSightingDao(): EventSightingDao
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

        fun getDatabase(context: Context): PotholeDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    PotholeDatabase::class.java,
                    "native_potholes.db"
                ).addMigrations(MIGRATION_1_2).build()
                INSTANCE = instance
                instance
            }
        }
    }
}
