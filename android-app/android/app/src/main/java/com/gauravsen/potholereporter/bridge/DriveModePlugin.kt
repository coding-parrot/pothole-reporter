package com.gauravsen.potholereporter.bridge

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.SystemClock
import android.util.Log
import androidx.core.content.ContextCompat
import androidx.room.withTransaction
import androidx.work.WorkManager
import com.gauravsen.potholereporter.drivemode.DriveModeService
import com.gauravsen.potholereporter.drivemode.DriveSession
import com.gauravsen.potholereporter.drivemode.CentralServiceIdentity
import com.gauravsen.potholereporter.drivemode.UploadWorker
import com.gauravsen.potholereporter.drivemode.LlmContractGenerated
import com.gauravsen.potholereporter.drivemode.normalizeVisionDetail
import com.gauravsen.potholereporter.drivemode.normalizeVisionLanguage
import com.gauravsen.potholereporter.drivemode.normalizeVisionModel
import com.gauravsen.potholereporter.drivemode.effectiveVisionProvider
import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin
import kotlinx.coroutines.launch
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers

internal enum class DriveModePollDecision {
    WAIT,
    COMPLETE,
    TIMED_OUT,
}

internal fun driveModeStartPollDecision(
    sessionId: String?,
    nowMs: Long,
    deadlineMs: Long,
): DriveModePollDecision = when {
    !sessionId.isNullOrBlank() -> DriveModePollDecision.COMPLETE
    nowMs >= deadlineMs -> DriveModePollDecision.TIMED_OUT
    else -> DriveModePollDecision.WAIT
}

internal fun driveModeStopPollDecision(
    isRunning: Boolean,
    nowMs: Long,
    deadlineMs: Long,
): DriveModePollDecision = when {
    !isRunning -> DriveModePollDecision.COMPLETE
    nowMs >= deadlineMs -> DriveModePollDecision.TIMED_OUT
    else -> DriveModePollDecision.WAIT
}

internal fun requiredDriveModeRuntimePermissions(): List<String> = listOf(
    Manifest.permission.CAMERA,
    Manifest.permission.ACCESS_FINE_LOCATION,
)

/**
 * Authorize replacing a native report's cached complaint from an open WebView snapshot.
 *
 * The values supplied by JavaScript are not authority; they only prove which Room row
 * the UI observed. The freshly prepared municipal result may be written only if that
 * snapshot is still current and the row remains an accepted, nonduplicate draft.
 */
internal data class ComplaintCivicSnapshot(
    val roadOwnership: String?,
    val tenderResolutionCheckedAt: Double?,
    val tenderNumber: String?,
    val serverPotholeId: Long?,
    val address: String?,
    val bodyLgd: String?,
    val bodyName: String?,
    val emailTo: String?,
    val officerTitle: String?,
    val contractor: String?,
    val tenderNote: String?,
)

internal fun canSaveComplaintPreparation(
    decision: String,
    status: String,
    serverDuplicate: Boolean,
    unroutedReason: String?,
    hasPendingCentralObservation: Boolean,
    current: ComplaintCivicSnapshot,
    expected: ComplaintCivicSnapshot,
): Boolean {
    if (decision != "accept" || serverDuplicate || hasPendingCentralObservation ||
        status !in setOf("draft", "queued") || current.serverPotholeId == null ||
        current.serverPotholeId <= 0L
    ) {
        return false
    }
    if (current != expected) return false
    if (current.roadOwnership !in setOf<String?>(null, "municipal")) return false
    // Null is a legitimate first central check only for a clean unresolved draft. A
    // retry-failed/unrouted null must never be turned municipal by stale JavaScript.
    if (current.roadOwnership == null &&
        (current.tenderResolutionCheckedAt != null || unroutedReason != null)
    ) {
        return false
    }
    return true
}

/**
 * Capacitor plugin bridge for native Drive Mode.
 *
 * Exposes start/stop/pause/resume/status/isAvailable methods to the
 * WebView JavaScript, allowing the existing UI to control the native
 * background service.
 *
 * Registered in MainActivity.onCreate() before super.onCreate().
 */
@CapacitorPlugin(name = "DriveMode")
class DriveModePlugin : Plugin() {

    companion object {
        private const val TAG = "DriveModePlugin"
        private const val DEFAULT_SERVICE_URL = CentralServiceIdentity.DEFAULT_SERVICE_URL
        private const val SERVICE_POLL_INTERVAL_MS = 50L
        private const val SERVICE_START_TIMEOUT_MS = 5_000L
        // Stop releases camera/GPS immediately, then deliberately keeps the service
        // alive while up to 30 already-captured detections drain in six-wide waves.
        // Each upstream request has a 30 s timeout, so five seconds would destroy the
        // service during the first wave and recreate the lost-analysis bug.
        private const val SERVICE_STOP_TIMEOUT_MS = 180_000L
    }

    @Volatile
    private var startPending = false

    override fun load() {
        super.load()
        // Set up status listener to forward updates to JavaScript
        DriveModeService.statusListener = { status ->
            val jsObj = JSObject()
            for ((key, value) in status) {
                when (value) {
                    is Map<*, *> -> {
                        val inner = JSObject()
                        for ((k, v) in value) {
                            inner.put(k.toString(), v)
                        }
                        jsObj.put(key, inner)
                    }
                    null -> jsObj.put(key, JSObject.NULL)
                    else -> jsObj.put(key, value)
                }
            }
            notifyListeners("statusUpdate", jsObj)
        }
    }

    /**
     * Check whether native Drive Mode is available on this device.
     */
    @PluginMethod
    fun isAvailable(call: PluginCall) {
        // The background session is a camera foreground service. Without the
        // FOREGROUND_SERVICE_CAMERA permission in the manifest, Android 14 refuses to
        // promote it and the process dies, so a build that does not declare it must
        // answer "not available" and let the web camera path run instead.
        val requested = try {
            val info = context.packageManager.getPackageInfo(
                context.packageName, android.content.pm.PackageManager.GET_PERMISSIONS)
            info.requestedPermissions?.toList() ?: emptyList()
        } catch (e: Exception) { emptyList<String>() }
        val cameraService = "android.permission.FOREGROUND_SERVICE_CAMERA" in requested
        val ret = JSObject()
        ret.put("available", cameraService)
        if (cameraService) ret.put("reason", JSObject.NULL)
        else ret.put("reason", "this build does not declare a camera foreground service")
        call.resolve(ret)
    }

    /**
     * Start a native Drive Mode session. Must be called while the
     * Activity is visible (to satisfy foreground service requirements).
     *
     * Expected args: { provider, apiKey?, serviceUrl, model, detail, language }
     */
    @PluginMethod
    fun start(call: PluginCall) {
        if (DriveModeService.isRunning()) {
            call.reject("Drive Mode is already running")
            return
        }

        // Check permissions
        if (!hasDriveModePermissions()) {
            call.reject("Required permissions not granted")
            return
        }

        val apiKey = call.getString("apiKey", "") ?: ""
        val requestedProvider = call.getString("provider", if (apiKey.isBlank()) "shared_server" else "personal")
            ?: "shared_server"
        val serviceUrl = call.getString("serviceUrl", DEFAULT_SERVICE_URL) ?: DEFAULT_SERVICE_URL
        val model = normalizeVisionModel(call.getString("model", LlmContractGenerated.DEFAULT_MODEL))
        val detail = normalizeVisionDetail(
            call.getString("detail", LlmContractGenerated.DEFAULT_IMAGE_DETAIL),
            model,
        )
        val language = normalizeVisionLanguage(
            call.getString("language", LlmContractGenerated.DEFAULT_LANGUAGE)
        )

        val provider = try {
            effectiveVisionProvider(requestedProvider, apiKey)
        } catch (_: IllegalArgumentException) {
            call.reject("Unknown vision provider")
            return
        }

        val context = context ?: run {
            call.reject("Context not available")
            return
        }

        val intent = Intent(context, DriveModeService::class.java).apply {
            action = DriveModeService.ACTION_START
            putExtra(DriveModeService.EXTRA_API_KEY, apiKey)
            putExtra(DriveModeService.EXTRA_PROVIDER, provider)
            putExtra(DriveModeService.EXTRA_SERVICE_URL, serviceUrl)
            putExtra(DriveModeService.EXTRA_MODEL, model)
            putExtra(DriveModeService.EXTRA_DETAIL, detail)
            putExtra(DriveModeService.EXTRA_LANGUAGE, language)
        }

        if (!claimStart()) {
            call.reject("Drive Mode is already starting")
            return
        }

        try {
            ContextCompat.startForegroundService(context, intent)
            awaitStartedSession(
                call,
                context,
                SystemClock.elapsedRealtime() + SERVICE_START_TIMEOUT_MS,
            )
        } catch (e: Exception) {
            releaseStartClaim()
            Log.e(TAG, "Failed to start Drive Mode service", e)
            call.reject("Failed to start Drive Mode: ${e.message}")
        }
    }

    /**
     * Pause the running Drive Mode session.
     */
    @PluginMethod
    fun pause(call: PluginCall) {
        sendServiceAction(DriveModeService.ACTION_PAUSE, call)
    }

    /**
     * Resume a paused Drive Mode session.
     */
    @PluginMethod
    fun resume(call: PluginCall) {
        sendServiceAction(DriveModeService.ACTION_RESUME, call)
    }

    /**
     * Stop the running Drive Mode session and release all resources.
     */
    @PluginMethod
    fun stop(call: PluginCall) {
        if (!DriveModeService.isRunning()) {
            call.resolve(stoppedResult(null))
            return
        }

        val context = context ?: run {
            call.reject("Context not available")
            return
        }
        val sessionId = DriveModeService.currentSession?.sessionId
        val intent = Intent(context, DriveModeService::class.java).apply {
            action = DriveModeService.ACTION_STOP
        }

        try {
            context.startService(intent)
            awaitStoppedSession(
                call,
                context,
                sessionId,
                SystemClock.elapsedRealtime() + SERVICE_STOP_TIMEOUT_MS,
            )
        } catch (e: Exception) {
            Log.e(TAG, "Failed to stop Drive Mode", e)
            call.reject("Failed to stop Drive Mode: ${e.message}")
        }
    }

    /**
     * Get the current status of Drive Mode.
     */
    @PluginMethod
    fun getStatus(call: PluginCall) {
        val session = DriveModeService.currentSession
        val ret = JSObject()
        if (session == null) {
            ret.put("state", "idle")
            ret.put("sessionId", JSObject.NULL)
            ret.put("cameraState", "unavailable")
            ret.put("gpsState", "waiting")
            ret.put("durationMs", 0)
            val tally = JSObject()
            tally.put("checked", 0)
            tally.put("found", 0)
            tally.put("already", 0)
            tally.put("captured", 0)
            tally.put("dropped", 0)
            tally.put("failed", 0)
            tally.put("errors", 0)
            ret.put("lastError", JSObject.NULL)
            ret.put("tally", tally)
        } else {
            val snapshot = session.snapshot()
            for ((key, value) in snapshot) {
                when (value) {
                    is Map<*, *> -> {
                        val inner = JSObject()
                        for ((k, v) in value) {
                            inner.put(k.toString(), v)
                        }
                        ret.put(key, inner)
                    }
                    null -> ret.put(key, JSObject.NULL)
                    else -> ret.put(key, value)
                }
            }
            val gpsState = when {
                !session.isGpsFresh() && session.currentLocation == null -> "waiting"
                !session.isGpsFresh() -> "stale"
                else -> "active"
            }
            ret.put("gpsState", gpsState)
        }
        call.resolve(ret)
    }

    @PluginMethod
    fun listReports(call: PluginCall) {
        val context = context ?: run { call.reject("Context not available"); return }
        kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.IO).launch {
            try {
                val db = com.gauravsen.potholereporter.db.AppDatabase.getInstance(context)
                val arr = org.json.JSONArray()
                for (id in db.reportDao().getAllIds()) {
                    db.reportDao().getById(id)?.let { arr.put(reportToJson(it)) }
                }
                val ret = JSObject()
                ret.put("reports", arr)
                call.resolve(ret)
            } catch (e: Exception) {
                call.reject("Failed to list reports: ${e.message}")
            }
        }
    }

    @PluginMethod
    fun getReport(call: PluginCall) {
        val id = call.getLong("id", -1L)
        if (id == null || id < 0) { call.reject("Report ID required"); return }
        val context = context ?: run { call.reject("Context not available"); return }
        kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.IO).launch {
            try {
                val db = com.gauravsen.potholereporter.db.AppDatabase.getInstance(context)
                val report = db.reportDao().getById(id)
                if (report == null) { call.reject("Report not found"); return@launch }
                val ret = JSObject(reportToJson(report).toString())
                call.resolve(ret)
            } catch (e: Exception) {
                call.reject("Failed: ${e.message}")
            }
        }
    }

    /** Cache the already-routed native email draft without replacing concurrent sync state. */
    @PluginMethod
    fun saveComplaintPreparation(call: PluginCall) {
        val id = call.getLong("id", -1L)
        if (id == null || id < 0) { call.reject("Report ID required"); return }
        val emailTo = call.getString("emailTo")?.trim()?.take(320)
        val emailSubject = call.getString("emailSubject")?.take(998)
        val emailBody = call.getString("emailBody")?.take(65_536)
        if (emailTo.isNullOrBlank() || emailSubject.isNullOrBlank() || emailBody.isNullOrBlank()) {
            call.reject("A complete routed email draft is required")
            return
        }
        val officerTitle = call.getString("officerTitle")?.trim()?.take(240)
        val address = call.getString("address")?.trim()?.take(1_000)
        val bodyLgd = call.getString("bodyLgd")?.trim()?.take(64)
        val bodyName = call.getString("bodyName")?.trim()?.take(300)
        val roadOwnership = call.getString("roadOwnership")?.trim()
        val roadOwnershipSource = call.getString("roadOwnershipSource")?.trim()
        if (roadOwnership != "municipal" || roadOwnershipSource != "central_v1") {
            call.reject("Verified municipal road ownership is required")
            return
        }
        val tenderNumber = call.getString("tenderNumber")?.trim()?.take(160)
        val contractor = call.getString("contractor")?.trim()?.take(300)
        val tenderNote = call.getString("tenderNote")?.trim()?.take(2_000)
        val expectedRoadOwnership = call.getString("expectedRoadOwnership")?.trim()
        val expectedTenderResolutionCheckedAt = call.getDouble("expectedTenderResolutionCheckedAt")
        val expectedTenderNumber = call.getString("expectedTenderNumber")
        val expectedServerPotholeId = call.getLong("expectedServerPotholeId")
        val expectedAddress = call.getString("expectedAddress")
        val expectedBodyLgd = call.getString("expectedBodyLgd")
        val expectedBodyName = call.getString("expectedBodyName")
        val expectedEmailTo = call.getString("expectedEmailTo")
        val expectedOfficerTitle = call.getString("expectedOfficerTitle")
        val expectedContractor = call.getString("expectedContractor")
        val expectedTenderNote = call.getString("expectedTenderNote")
        val appContext = context ?: run { call.reject("Context not available"); return }
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val db = com.gauravsen.potholereporter.db.AppDatabase.getInstance(appContext)
                val saved = db.withTransaction {
                    // Re-read inside the transaction so a just-completed central sync is
                    // never overwritten by a stale object returned to the WebView.
                    val current = db.reportDao().getById(id)
                        ?: throw IllegalArgumentException("Report not found")
                    val hasPendingCentralObservation =
                        db.centralObservationDao().hasPendingForReport(id)
                    if (!canSaveComplaintPreparation(
                            decision = current.decision,
                            status = current.status,
                            serverDuplicate = current.server_duplicate,
                            unroutedReason = current.unrouted_reason,
                            hasPendingCentralObservation = hasPendingCentralObservation,
                            current = ComplaintCivicSnapshot(
                                roadOwnership = current.road_ownership,
                                tenderResolutionCheckedAt = current.tender_resolution_checked_at,
                                tenderNumber = current.tender_number,
                                serverPotholeId = current.server_pothole_id,
                                address = current.address,
                                bodyLgd = current.body_lgd,
                                bodyName = current.body_name,
                                emailTo = current.email_to,
                                officerTitle = current.officer_title,
                                contractor = current.contractor,
                                tenderNote = current.tender_note,
                            ),
                            expected = ComplaintCivicSnapshot(
                                roadOwnership = expectedRoadOwnership,
                                tenderResolutionCheckedAt = expectedTenderResolutionCheckedAt,
                                tenderNumber = expectedTenderNumber,
                                serverPotholeId = expectedServerPotholeId,
                                address = expectedAddress,
                                bodyLgd = expectedBodyLgd,
                                bodyName = expectedBodyName,
                                emailTo = expectedEmailTo,
                                officerTitle = expectedOfficerTitle,
                                contractor = expectedContractor,
                                tenderNote = expectedTenderNote,
                            ),
                        )
                    ) {
                        throw IllegalStateException(
                            "Report authority or complaint state changed before email preparation",
                        )
                    }
                    val updated = current.copy(
                        email_to = emailTo,
                        officer_title = officerTitle,
                        email_subject = emailSubject,
                        email_body = emailBody,
                        address = address,
                        body_lgd = bodyLgd,
                        body_name = bodyName,
                        road_ownership = roadOwnership,
                        // The resolver's no-tender result is authoritative too. Null
                        // clears legacy local matches instead of silently preserving a
                        // stale contractor in the email draft/history row.
                        tender_number = tenderNumber,
                        contractor = contractor,
                        tender_note = tenderNote,
                        tender_resolution_checked_at = current.tender_resolution_checked_at
                            ?: System.currentTimeMillis() / 1000.0,
                    )
                    db.reportDao().update(updated)
                    updated
                }
                call.resolve(JSObject(reportToJson(saved).toString()))
            } catch (e: Exception) {
                call.reject("Failed to save email preparation: ${e.message}")
            }
        }
    }

    @PluginMethod
    fun deleteReport(call: PluginCall) {
        val id = call.getLong("id", -1L)
        if (id == null || id < 0) { call.reject("Report ID required"); return }
        val context = context ?: run { call.reject("Context not available"); return }
        kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.IO).launch {
            try {
                val db = com.gauravsen.potholereporter.db.AppDatabase.getInstance(context)
                db.reportDao().deleteById(id)
                call.resolve(JSObject().put("ok", true))
            } catch (e: Exception) {
                call.reject("Failed: ${e.message}")
            }
        }
    }

    /** Remove native reports, photos, sessions and queued central operations atomically. */
    @PluginMethod
    fun clearAllData(call: PluginCall) {
        val appContext = context ?: run { call.reject("Context not available"); return }
        if (DriveModeService.isRunning()) {
            call.reject("Stop Drive Mode before deleting all app data")
            return
        }
        CoroutineScope(Dispatchers.IO).launch {
            try {
                WorkManager.getInstance(appContext)
                    .cancelUniqueWork(UploadWorker.WORK_NAME)
                    .result.get(10, java.util.concurrent.TimeUnit.SECONDS)
                val db = com.gauravsen.potholereporter.db.AppDatabase.getInstance(appContext)
                db.withTransaction {
                    db.centralObservationDao().deleteAll()
                    db.reportDao().deleteAll()
                    db.driveSessionDao().deleteAll()
                }
                CentralServiceIdentity.reset(appContext)
                call.resolve(JSObject().put("ok", true))
            } catch (e: Exception) {
                call.reject("Failed to delete native app data: ${e.message}")
            }
        }
    }

    @PluginMethod
    fun listDriveSessions(call: PluginCall) {
        val context = context ?: run { call.reject("Context not available"); return }
        kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.IO).launch {
            try {
                val db = com.gauravsen.potholereporter.db.AppDatabase.getInstance(context)
                val sessions = db.driveSessionDao().getAll()
                val arr = org.json.JSONArray()
                for (s in sessions) {
                    val obj = org.json.JSONObject()
                    obj.put("id", s.id)
                    obj.put("started_at", s.started_at ?: org.json.JSONObject.NULL)
                    obj.put("ended_at", s.ended_at ?: org.json.JSONObject.NULL)
                    obj.put("checked", s.checked)
                    obj.put("found", s.found)
                    obj.put("already", s.already)
                    arr.put(obj)
                }
                val ret = JSObject()
                ret.put("sessions", arr)
                call.resolve(ret)
            } catch (e: Exception) {
                call.reject("Failed: ${e.message}")
            }
        }
    }

    @PluginMethod
    fun getReportPhoto(call: PluginCall) {
        val id = call.getLong("id", -1L)
        val full = call.getBoolean("full", false) ?: false
        if (id == null || id < 0) { call.reject("Report ID required"); return }
        val context = context ?: run { call.reject("Context not available"); return }
        kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.IO).launch {
            try {
                val db = com.gauravsen.potholereporter.db.AppDatabase.getInstance(context)
                val bytes = if (full) db.reportDao().getFullPhoto(id)
                    else db.reportDao().getThumbnail(id)
                if (bytes == null || bytes.isEmpty()) { call.reject("No photo"); return@launch }
                val base64 = android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP)
                val ret = JSObject()
                ret.put("dataUrl", "data:image/jpeg;base64,$base64")
                call.resolve(ret)
            } catch (e: Exception) {
                call.reject("Failed: ${e.message}")
            }
        }
    }

    /**
     * Return the APK's pseudonymous central identity. Registration and signing run off
     * the UI thread; the private key never leaves Android Keystore.
     */
    @PluginMethod
    fun getCentralIdentity(call: PluginCall) {
        val appContext = context ?: run { call.reject("Context not available"); return }
        val serviceUrl = call.getString("serviceUrl", DEFAULT_SERVICE_URL) ?: DEFAULT_SERVICE_URL
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val installId = CentralServiceIdentity(appContext).ensureRegistered(serviceUrl)
                call.resolve(JSObject().put("installId", installId))
            } catch (e: Exception) {
                call.reject("Central identity setup failed: ${e.message}")
            }
        }
    }

    /** Sign the exact canonical request used by the central Worker. */
    @PluginMethod
    fun signCentralRequest(call: PluginCall) {
        val appContext = context ?: run { call.reject("Context not available"); return }
        val serviceUrl = call.getString("serviceUrl", DEFAULT_SERVICE_URL) ?: DEFAULT_SERVICE_URL
        val method = call.getString("method", "POST") ?: "POST"
        val path = call.getString("path", "") ?: ""
        val timestamp = call.getString("timestamp", System.currentTimeMillis().toString())
            ?: System.currentTimeMillis().toString()
        val idempotencyKey = call.getString("idempotencyKey", "") ?: ""
        val body = call.getString("body", "") ?: ""
        if (!path.startsWith("/v1/")) { call.reject("A central API path is required"); return }
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val signed = CentralServiceIdentity(appContext).sign(
                    serviceUrl = serviceUrl,
                    method = method,
                    path = path,
                    body = body.toByteArray(Charsets.UTF_8),
                    idempotencyKey = idempotencyKey,
                    timestamp = timestamp,
                )
                call.resolve(JSObject()
                    .put("installId", signed.installId)
                    .put("timestamp", signed.timestamp)
                    .put("idempotencyKey", signed.idempotencyKey)
                    .put("signature", signed.signature))
            } catch (e: Exception) {
                call.reject("Central request signing failed: ${e.message}")
            }
        }
    }

    // --- Helpers ---

    private fun reportToJson(r: com.gauravsen.potholereporter.db.entities.ReportEntity): org.json.JSONObject {
        val obj = org.json.JSONObject()
        obj.put("id", r.id)
        obj.put("assessment", r.assessment ?: org.json.JSONObject.NULL)
        obj.put("image_quality", r.image_quality ?: org.json.JSONObject.NULL)
        obj.put("damage_type", r.damage_type ?: org.json.JSONObject.NULL)
        obj.put("size", r.size ?: org.json.JSONObject.NULL)
        obj.put("description", r.description ?: "")
        obj.put("decision", r.decision)
        obj.put("status", r.status)
        obj.put("lat", r.lat ?: org.json.JSONObject.NULL)
        obj.put("lng", r.lng ?: org.json.JSONObject.NULL)
        obj.put("gps_accuracy", r.gps_accuracy ?: org.json.JSONObject.NULL)
        obj.put("speed_mps", r.speed_mps ?: org.json.JSONObject.NULL)
        obj.put("heading", r.heading ?: org.json.JSONObject.NULL)
        obj.put("drive_id", r.drive_id ?: org.json.JSONObject.NULL)
        obj.put("capture_source", r.capture_source)
        obj.put("source_event_key", r.source_event_key ?: org.json.JSONObject.NULL)
        obj.put("captured_at", r.captured_at ?: org.json.JSONObject.NULL)
        obj.put("created_at", r.created_at)
        obj.put("last_seen_at", r.last_seen_at ?: org.json.JSONObject.NULL)
        obj.put("seen_count", r.seen_count)
        obj.put("server_pothole_id", r.server_pothole_id ?: org.json.JSONObject.NULL)
        obj.put("server_duplicate", r.server_duplicate)
        obj.put("central_sync_eligible", r.central_sync_eligible)
        obj.put("central_sync_error", r.central_sync_error ?: org.json.JSONObject.NULL)
        obj.put("detection_provider", r.detection_provider ?: org.json.JSONObject.NULL)
        obj.put("address", r.address ?: org.json.JSONObject.NULL)
        obj.put("body_lgd", r.body_lgd ?: org.json.JSONObject.NULL)
        obj.put("body_name", r.body_name ?: org.json.JSONObject.NULL)
        obj.put("road_ownership", r.road_ownership ?: org.json.JSONObject.NULL)
        obj.put("road_ownership_detail", r.road_ownership_detail ?: org.json.JSONObject.NULL)
        obj.put("email_subject", r.email_subject ?: org.json.JSONObject.NULL)
        obj.put("email_body", r.email_body ?: org.json.JSONObject.NULL)
        obj.put("email_to", r.email_to ?: org.json.JSONObject.NULL)
        obj.put("officer_title", r.officer_title ?: org.json.JSONObject.NULL)
        obj.put("unrouted_reason", r.unrouted_reason ?: org.json.JSONObject.NULL)
        obj.put("tender_number", r.tender_number ?: org.json.JSONObject.NULL)
        obj.put("contractor", r.contractor ?: org.json.JSONObject.NULL)
        obj.put("tender_note", r.tender_note ?: org.json.JSONObject.NULL)
        obj.put("tender_resolution_reason", r.tender_resolution_reason ?: org.json.JSONObject.NULL)
        obj.put("tender_resolution_checked_at", r.tender_resolution_checked_at ?: org.json.JSONObject.NULL)
        obj.put("detection_model", r.detection_model ?: org.json.JSONObject.NULL)
        obj.put("evidence_count", r.evidence_count)
        // Photo is NOT included in listing — use getReportPhoto for that
        obj.put("has_photo", r.photo != null && r.photo.isNotEmpty())
        obj.put("is_pothole", r.damage_type == "pothole_cavity")
        return obj
    }

    private fun sendServiceAction(action: String, call: PluginCall) {
        val context = context ?: run {
            call.reject("Context not available")
            return
        }

        val intent = Intent(context, DriveModeService::class.java).apply {
            this.action = action
        }

        try {
            context.startService(intent)
            call.resolve(JSObject().put("ok", true))
        } catch (e: Exception) {
            Log.e(TAG, "Failed to send action $action", e)
            call.reject("Failed: ${e.message}")
        }
    }

    @Synchronized
    private fun claimStart(): Boolean {
        if (startPending) return false
        startPending = true
        return true
    }

    @Synchronized
    private fun releaseStartClaim() {
        startPending = false
    }

    private fun awaitStartedSession(call: PluginCall, context: Context, deadlineMs: Long) {
        val session = DriveModeService.currentSession
        when (driveModeStartPollDecision(
            session?.sessionId,
            SystemClock.elapsedRealtime(),
            deadlineMs,
        )) {
            DriveModePollDecision.COMPLETE -> {
                releaseStartClaim()
                val ret = JSObject()
                ret.put("sessionId", session!!.sessionId)
                ret.put("state", session.state.name.lowercase())
                call.resolve(ret)
            }
            DriveModePollDecision.TIMED_OUT -> {
                releaseStartClaim()
                // Do not leave a late-starting foreground service holding camera/location
                // after its caller has already been told that startup failed.
                context.stopService(Intent(context, DriveModeService::class.java))
                call.reject("Drive Mode service did not create a session within ${SERVICE_START_TIMEOUT_MS}ms")
            }
            DriveModePollDecision.WAIT -> bridge.activity.window.decorView.postDelayed(
                { awaitStartedSession(call, context, deadlineMs) },
                SERVICE_POLL_INTERVAL_MS,
            )
        }
    }

    private fun awaitStoppedSession(
        call: PluginCall,
        context: Context,
        sessionId: String?,
        deadlineMs: Long,
    ) {
        when (driveModeStopPollDecision(
            DriveModeService.isRunning(),
            SystemClock.elapsedRealtime(),
            deadlineMs,
        )) {
            DriveModePollDecision.COMPLETE -> call.resolve(stoppedResult(sessionId))
            DriveModePollDecision.TIMED_OUT -> {
                // Camera and GPS were already released by ACTION_STOP. Never tear down
                // the service here: doing so would cancel the very captured analyses the
                // user is waiting for. The foreground notification remains honest while
                // Android lets the bounded network calls finish or time out.
                call.reject("Drive Mode is still finishing captured analyses after ${SERVICE_STOP_TIMEOUT_MS}ms")
            }
            DriveModePollDecision.WAIT -> bridge.activity.window.decorView.postDelayed(
                { awaitStoppedSession(call, context, sessionId, deadlineMs) },
                SERVICE_POLL_INTERVAL_MS,
            )
        }
    }

    private fun stoppedResult(sessionId: String?): JSObject = JSObject().apply {
        put("stopped", true)
        put("state", "stopped")
        put("sessionId", sessionId ?: JSObject.NULL)
    }

    private fun hasDriveModePermissions(): Boolean {
        val context = context ?: return false
        // POST_NOTIFICATIONS controls whether the foreground-service notification is
        // visible in the notification drawer on Android 13+, not whether the service may
        // start. Requiring it here made every fresh API 33+ install reject native Drive
        // Mode and silently fall back to the slower WebView camera path.
        return requiredDriveModeRuntimePermissions().all { permission ->
            ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED
        }
    }
}
