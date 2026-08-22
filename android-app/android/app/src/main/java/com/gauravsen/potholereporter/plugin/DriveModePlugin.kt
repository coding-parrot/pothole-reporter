package com.gauravsen.potholereporter.plugin

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.util.Base64
import androidx.core.content.ContextCompat
import com.gauravsen.potholereporter.db.PotholeDatabase
import com.gauravsen.potholereporter.drive.DriveForegroundService
import com.getcapacitor.JSArray
import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin
import com.getcapacitor.annotation.Permission
import com.getcapacitor.annotation.PermissionCallback
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import org.json.JSONArray
import java.io.File

@CapacitorPlugin(
    name = "DriveMode",
    permissions = [
        Permission(strings = [Manifest.permission.CAMERA], alias = "camera"),
        Permission(
            strings = [Manifest.permission.ACCESS_COARSE_LOCATION, Manifest.permission.ACCESS_FINE_LOCATION],
            alias = "location"
        ),
        Permission(strings = [Manifest.permission.POST_NOTIFICATIONS], alias = "notifications")
    ]
)
class DriveModePlugin : Plugin() {

    private val pluginScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun load() {
        super.load()
        DriveForegroundService.onStatusListener = { status ->
            val data = JSObject().apply {
                put("isRunning", status.isRunning)
                put("isPaused", status.isPaused)
                put("sessionId", status.sessionId)
                put("checked", status.checked)
                put("found", status.found)
                put("already", status.already)
                put("queued", status.queued)
                put("dropped", status.dropped)
                put("status", status.status)
            }
            notifyListeners("driveStatusChange", data)
        }

        DriveForegroundService.onReportListener = { reportId, damageType, address ->
            val data = JSObject().apply {
                put("reportId", reportId)
                put("damageType", damageType)
                put("address", address)
            }
            notifyListeners("reportFound", data)
        }

        DriveForegroundService.onDriveEndedListener = { sessionId, checked, found, already ->
            val data = JSObject().apply {
                put("sessionId", sessionId)
                put("checked", checked)
                put("found", found)
                put("already", already)
            }
            notifyListeners("driveEnded", data)
        }
    }

    @PluginMethod
    fun startDrive(call: PluginCall) {
        val apiKey = call.getString("apiKey") ?: ""
        val model = call.getString("model") ?: "gpt-5-mini"
        val detail = call.getString("detail") ?: "high"
        val language = call.getString("language") ?: "en"
        val debug = call.getBoolean("debug") ?: false

        if (!hasDrivePermissions()) {
            call.reject("Camera and location permission are required before Drive Mode can start")
            return
        }
        if (apiKey.isBlank()) {
            call.reject("An OpenAI API key is required")
            return
        }
        if (DriveForegroundService.status().isRunning) {
            call.resolve(statusObject(DriveForegroundService.status()))
            return
        }
        val context = context
        val serviceIntent = Intent(context, DriveForegroundService::class.java).apply {
            action = DriveForegroundService.ACTION_START
            putExtra(DriveForegroundService.EXTRA_API_KEY, apiKey)
            putExtra(DriveForegroundService.EXTRA_MODEL, model)
            putExtra(DriveForegroundService.EXTRA_DETAIL, detail)
            putExtra(DriveForegroundService.EXTRA_LANGUAGE, language)
            putExtra(DriveForegroundService.EXTRA_DEBUG, debug)
        }

        ContextCompat.startForegroundService(context, serviceIntent)

        val ret = JSObject().apply {
            put("success", true)
            put("status", "starting")
        }
        call.resolve(ret)
    }

    @PluginMethod
    fun pauseDrive(call: PluginCall) {
        DriveForegroundService.activeService?.pauseDrive()
        call.resolve(statusObject(DriveForegroundService.status()))
    }

    @PluginMethod
    fun resumeDrive(call: PluginCall) {
        DriveForegroundService.activeService?.resumeDrive()
        call.resolve(statusObject(DriveForegroundService.status()))
    }

    @PluginMethod
    fun stopDrive(call: PluginCall) {
        val service = DriveForegroundService.activeService
        if (service != null) {
            service.stopDriveSession()
            call.resolve(JSObject().apply { put("stopped", true) })
        } else {
            call.resolve(JSObject().apply { put("stopped", false) })
        }
    }

    @PluginMethod
    fun getStatus(call: PluginCall) {
        call.resolve(statusObject(DriveForegroundService.status()))
    }

    @PluginMethod
    fun requestDrivePermissions(call: PluginCall) {
        val aliases = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            arrayOf("camera", "location", "notifications")
        } else arrayOf("camera", "location")
        requestPermissionForAliases(aliases, call, "drivePermissionsResult")
    }

    @PermissionCallback
    private fun drivePermissionsResult(call: PluginCall) {
        val ret = JSObject().apply {
            put("granted", hasDrivePermissions())
            put("notificationsGranted", Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
                ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED)
        }
        call.resolve(ret)
    }

    @PluginMethod
    fun syncReports(call: PluginCall) {
        pluginScope.launch {
            try {
                val db = PotholeDatabase.getDatabase(context)
                val unsynced = db.reportDao().getUnsyncedReports()
                val array = JSArray()
                for (r in unsynced) {
                    val obj = JSObject().apply {
                        put("id", r.id)
                        put("created_at", r.createdAt)
                        put("lat", r.lat)
                        put("lng", r.lng)
                        put("address", r.address)
                        put("photo_data_url", r.photoDataUrl)
                        put("photo_path", r.photoPath)
                        put("photo_full_data_url", fileAsDataUrl(r.photoFullPath ?: r.photoPath))
                        put("is_reportable", r.isReportable)
                        put("is_pothole", r.isPothole)
                        put("damage_type", r.damageType)
                        put("assessment", r.assessment)
                        put("image_quality", r.imageQuality)
                        put("on_drivable_surface", r.onDrivableSurface)
                        put("has_broken_edge_or_rim", r.hasBrokenEdgeOrRim)
                        put("has_depth_or_surface_loss", r.hasDepthOrSurfaceLoss)
                        put("temporal_consistency", r.temporalConsistency)
                        put("size", r.size)
                        put("decision", r.decision)
                        put("description", r.description)
                        put("email_subject", r.emailSubject)
                        put("email_body", r.emailBody)
                        put("status", r.status)
                        put("detection_model", r.detectionModel)
                        put("image_detail", r.imageDetail)
                        put("prompt_version", r.promptVersion)
                        put("schema_version", r.schemaVersion)
                        put("evidence_count", r.evidenceCount)
                        put("drive_id", r.driveId)
                        put("capture_source", r.captureSource)
                        put("source_event_key", r.sourceEventKey)
                        put("captured_at", r.capturedAt)
                        put("source_offset_s", r.sourceOffsetS)
                        put("gps_accuracy", r.gpsAccuracy)
                        put("speed_mps", r.speedMps)
                        put("heading", r.heading)
                        put("seen_count", r.seenCount)
                        put("last_seen_at", r.lastSeenAt)
                        put("primary_frame_index", r.primaryFrameIndex)
                        put("debug_capture", r.debugCapture)
                    }
                    array.put(obj)
                }

                val ret = JSObject().apply {
                    put("reports", array)
                    put("count", array.length())
                }
                call.resolve(ret)
            } catch (e: Exception) {
                call.reject("Failed to sync reports: ${e.message}")
            }
        }
    }

    @PluginMethod
    fun acknowledgeReports(call: PluginCall) {
        val idsArray = call.getArray("ids") ?: JSArray()
        val ids = mutableListOf<Long>()
        for (index in 0 until idsArray.length()) {
            val value = idsArray.optLong(index, -1L)
            if (value > 0) ids.add(value)
        }
        pluginScope.launch {
            try {
                if (ids.isNotEmpty()) PotholeDatabase.getDatabase(context).reportDao().markReportsSynced(ids)
                call.resolve(JSObject().apply { put("acknowledged", ids.size) })
            } catch (error: Exception) {
                call.reject("Failed to acknowledge reports: ${error.message}")
            }
        }
    }

    @PluginMethod
    fun openMaps(call: PluginCall) {
        val navigation = Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q="))
        navigation.setPackage("com.google.android.apps.maps")
        val chosen = if (navigation.resolveActivity(context.packageManager) != null) navigation
            else Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q="))
        chosen.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(chosen)
        call.resolve()
    }

    @PluginMethod
    fun clearNativeData(call: PluginCall) {
        DriveForegroundService.activeService?.stopDriveSession("Data cleared")
        pluginScope.launch {
            try {
                val db = PotholeDatabase.getDatabase(context)
                db.eventSightingDao().clearAll()
                db.reportDao().clearAll()
                db.sessionDao().clearAll()
                File(context.filesDir, "reports").deleteRecursively()
                call.resolve()
            } catch (error: Exception) {
                call.reject("Failed to clear native data: ${error.message}")
            }
        }
    }

    @PluginMethod
    fun getDrives(call: PluginCall) {
        pluginScope.launch {
            try {
                val db = PotholeDatabase.getDatabase(context)
                val sessions = db.sessionDao().getAllSessions()
                val array = JSArray()
                for (s in sessions) {
                    val obj = JSObject().apply {
                        put("id", s.id)
                        put("started_at", s.startedAt)
                        put("ended_at", s.endedAt)
                        put("checked", s.checkedCount)
                        put("found", s.foundCount)
                        put("already", s.alreadyCount)
                        put("already_ids", parseJsonArray(s.alreadyIdsJson))
                        put("gps_track", parseGpsTrack(s.gpsTrackJson))
                        put("status", s.status)
                    }
                    array.put(obj)
                }
                call.resolve(JSObject().apply { put("drives", array) })
            } catch (e: Exception) {
                call.reject("Failed to get drives: ${e.message}")
            }
        }
    }

    private fun parseJsonArray(json: String): JSArray {
        val arr = JSArray()
        try {
            val parsed = JSONArray(json)
            for (i in 0 until parsed.length()) {
                arr.put(parsed.get(i))
            }
        } catch (_: Exception) {}
        return arr
    }

    private fun parseGpsTrack(json: String): JSArray {
        val arr = JSArray()
        try {
            val parsed = JSONArray(json)
            for (i in 0 until parsed.length()) {
                arr.put(parsed.getJSONArray(i))
            }
        } catch (_: Exception) {}
        return arr
    }

    private fun hasDrivePermissions(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED &&
            (ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
                ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED)

    private fun statusObject(status: com.gauravsen.potholereporter.drive.DriveStatusSnapshot) = JSObject().apply {
        put("isRunning", status.isRunning)
        put("isPaused", status.isPaused)
        put("sessionId", status.sessionId)
        put("checked", status.checked)
        put("found", status.found)
        put("already", status.already)
        put("queued", status.queued)
        put("dropped", status.dropped)
        put("status", status.status)
    }

    private fun fileAsDataUrl(path: String?): String? {
        if (path.isNullOrBlank()) return null
        val file = File(path)
        if (!file.isFile || file.length() > 8L * 1024 * 1024) return null
        return "data:image/jpeg;base64," + Base64.encodeToString(file.readBytes(), Base64.NO_WRAP)
    }

    override fun handleOnDestroy() {
        pluginScope.cancel()
        DriveForegroundService.onStatusListener = null
        DriveForegroundService.onReportListener = null
        DriveForegroundService.onDriveEndedListener = null
        super.handleOnDestroy()
    }
}
