package com.gauravsen.potholereporter.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(
    entities = [ReportEntity::class, EventSightingEntity::class, SessionEntity::class],
    version = 1,
    exportSchema = false
)
abstract class PotholeDatabase : RoomDatabase() {
    abstract fun reportDao(): ReportDao
    abstract fun eventSightingDao(): EventSightingDao
    abstract fun sessionDao(): SessionDao

    companion object {
        @Volatile
        private var INSTANCE: PotholeDatabase? = null

        fun getDatabase(context: Context): PotholeDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    PotholeDatabase::class.java,
                    "native_potholes.db"
                ).build()
                INSTANCE = instance
                instance
            }
        }
    }
}
