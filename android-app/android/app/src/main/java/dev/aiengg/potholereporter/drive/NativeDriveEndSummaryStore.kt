package dev.aiengg.potholereporter.drive

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * Durable hand-off for terminal Drive state when a WebView is recreated after the
 * service stops but before it receives `driveEnded`.
 */
internal object NativeDriveEndSummaryStore {
    private const val PREFERENCES = "native_drive_end_summaries"
    private const val INDEX_KEY = "summary_index"
    private const val ENTRY_PREFIX = "summary_"
    private const val MAX_ENTRIES = 32
    private val safeSessionId = Regex("[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
    private val lock = Any()
    private val recent = LinkedHashMap<String, DriveEndSummary>()

    fun record(context: Context, summary: DriveEndSummary): Boolean = synchronized(lock) {
        val sessionId = summary.sessionId.takeIf(safeSessionId::matches) ?: return false
        val preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
        val ids = readIndex(preferences.getString(INDEX_KEY, null))
            .filterNot { it == sessionId }
            .toMutableList()
        ids.add(sessionId)
        val removed = ArrayList<String>()
        while (ids.size > MAX_ENTRIES) removed += ids.removeAt(0)
        recent.remove(sessionId)
        recent[sessionId] = summary
        while (recent.size > MAX_ENTRIES) recent.remove(recent.keys.first())

        val value = JSONObject().apply {
            put("sessionId", sessionId)
            put("checked", summary.checked)
            put("found", summary.found)
            put("already", summary.already)
            put("error", summary.error ?: JSONObject.NULL)
            put("discarded", summary.discarded)
            put("reason", summary.reason ?: JSONObject.NULL)
            put("startRequestId", summary.startRequestId ?: JSONObject.NULL)
        }.toString()
        repeat(3) {
            val editor = preferences.edit()
                .putString(ENTRY_PREFIX + sessionId, value)
                .putString(INDEX_KEY, JSONArray(ids).toString())
            removed.forEach { editor.remove(ENTRY_PREFIX + it) }
            if (editor.commit()) return@synchronized true
        }
        false
    }

    fun read(context: Context, sessionId: String): DriveEndSummary? = synchronized(lock) {
        if (!safeSessionId.matches(sessionId)) return null
        recent[sessionId]?.let { return it }
        val raw = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .getString(ENTRY_PREFIX + sessionId, null) ?: return null
        runCatching {
            val value = JSONObject(raw)
            if (value.getString("sessionId") != sessionId) return@runCatching null
            DriveEndSummary(
                sessionId = sessionId,
                checked = value.getInt("checked").coerceAtLeast(0),
                found = value.getInt("found").coerceAtLeast(0),
                already = value.getInt("already").coerceAtLeast(0),
                error = value.optNullableString("error"),
                discarded = value.optBoolean("discarded", false),
                reason = value.optNullableString("reason"),
                startRequestId = value.optNullableString("startRequestId")
            )
        }.getOrNull()
    }

    /** Latest terminal result not yet acknowledged by the WebView. */
    fun readLatest(context: Context): DriveEndSummary? = synchronized(lock) {
        recent.entries.lastOrNull()?.value?.let { return it }
        val preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
        val sessionId = readIndex(preferences.getString(INDEX_KEY, null)).lastOrNull()
            ?: return null
        read(context, sessionId)
    }

    /**
     * Remove a result only after its exact terminal UI hand-off completed. Failed
     * acknowledgement remains retryable after a WebView or process recreation.
     */
    fun acknowledge(context: Context, sessionId: String): Boolean = synchronized(lock) {
        if (!safeSessionId.matches(sessionId)) return false
        val preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
        val ids = readIndex(preferences.getString(INDEX_KEY, null)).filterNot { it == sessionId }
        repeat(3) {
            if (preferences.edit()
                    .remove(ENTRY_PREFIX + sessionId)
                    .putString(INDEX_KEY, JSONArray(ids).toString())
                    .commit()) {
                recent.remove(sessionId)
                return@synchronized true
            }
        }
        false
    }

    fun clear(context: Context): Boolean = synchronized(lock) {
        recent.clear()
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).edit().clear().commit()
    }

    private fun readIndex(raw: String?): List<String> = runCatching {
        val values = JSONArray(raw ?: "[]")
        buildList {
            for (index in 0 until values.length()) {
                values.optString(index).takeIf(safeSessionId::matches)?.let(::add)
            }
        }.distinct().takeLast(MAX_ENTRIES)
    }.getOrDefault(emptyList())

    private fun JSONObject.optNullableString(key: String): String? =
        if (!has(key) || isNull(key)) null else optString(key).takeIf(String::isNotBlank)
}
