package dev.aiengg.potholereporter.drive

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.core.content.ContextCompat
import com.google.android.gms.location.*
import org.json.JSONArray
import org.json.JSONObject
import java.util.Collections

data class GpsFix(
    val lat: Double,
    val lng: Double,
    val accuracy: Float?,
    val speedMps: Float?,
    val heading: Float?,
    val timestampMs: Long,
    // Monotonic receipt time used for freshness, cadence, and camera pairing. The wall
    // timestamp remains reporting metadata only; changing phone time must not admit or
    // reject a burst. The default keeps pure tests/source-compatible constructors terse.
    val elapsedRealtimeMs: Long = timestampMs
)

/**
 * Fused Location's availability flag is a forecast, not a revocation of a location it
 * just delivered. Keep a genuinely fresh fix usable for the same bounded window enforced
 * by captureAccess; a stale or absent fix still closes capture.
 */
internal object NativeLocationAvailabilityPolicy {
    // Admission and final frame/fix pairing must agree. A clock value far in the future
    // must never open CameraX only to have every resulting burst rejected downstream.
    private const val MAX_FUTURE_SKEW_MS = NativeBurstAccessPolicy.MAX_FIX_PRIMARY_DELTA_MS

    fun isFreshFix(timestampMs: Long?, nowMs: Long, maxAgeMs: Long): Boolean {
        val ageMs = timestampMs?.let { nowMs - it } ?: return false
        return ageMs in -MAX_FUTURE_SKEW_MS..maxAgeMs
    }

    fun providerIsUsable(
        reportedAvailable: Boolean,
        latestFixTimestampMs: Long?,
        nowMs: Long,
        maxAgeMs: Long
    ): Boolean = reportedAvailable || isFreshFix(latestFixTimestampMs, nowMs, maxAgeMs)
}

/** Pure monotonic policy for deciding whether a registration has stopped yielding fixes. */
internal object NativeLocationResultWatchdogPolicy {
    fun noteRealResult(
        previousResultElapsedMs: Long,
        callbackElapsedMs: Long,
        deliveredLocation: Boolean
    ): Long = if (deliveredLocation) callbackElapsedMs else previousResultElapsedMs

    fun isStalled(lastResultElapsedMs: Long, nowMs: Long, timeoutMs: Long): Boolean =
        lastResultElapsedMs > 0L && nowMs - lastResultElapsedMs >= timeoutMs
}

/** Capture requires a fresh, finite fix accurate enough for road-level routing. */
internal object NativeCaptureFixQualityPolicy {
    fun isUsable(fix: GpsFix?, nowMs: Long, maxAgeMs: Long, maxAccuracyM: Float): Boolean {
        val value = fix ?: return false
        val accuracy = value.accuracy ?: return false
        return value.lat.isFinite() && value.lat in -90.0..90.0 &&
            value.lng.isFinite() && value.lng in -180.0..180.0 &&
            accuracy.isFinite() && accuracy in 0f..maxAccuracyM &&
            value.timestampMs > 0L &&
            NativeLocationAvailabilityPolicy.isFreshFix(
                value.elapsedRealtimeMs,
                nowMs,
                maxAgeMs
            )
    }
}

/** Camera sampling cadence separated from Android location APIs for deterministic tests. */
internal object NativeCaptureCadencePolicy {
    const val STATIONARY_MPS = 0.25f

    fun intervalMs(speedMps: Float?, accurateFix: Boolean): Long {
        val speed = speedMps?.takeIf { it.isFinite() && it >= 0f }
        if (accurateFix && speed != null && speed <= STATIONARY_MPS) {
            return NativeDriveLocationProvider.PARKED_CAPTURE_MS
        }
        if (speed != null && speed > STATIONARY_MPS) {
            return (NativeDriveLocationProvider.TARGET_SPACING_M / speed * 1_000.0)
                .toLong()
                .coerceIn(
                    NativeDriveLocationProvider.MIN_CAPTURE_MS,
                    NativeDriveLocationProvider.MAX_CAPTURE_MS
                )
        }
        return NativeDriveLocationProvider.FALLBACK_CAPTURE_MS
    }
}

class NativeDriveLocationProvider(
    private val context: Context,
    private val onLocationUpdate: (GpsFix) -> Unit,
    private val onAvailabilityChange: (NativeLocationAccess) -> Unit = {}
) {
    private val fusedClient: FusedLocationProviderClient =
        LocationServices.getFusedLocationProviderClient(context)

    @Volatile private var locationCallback: LocationCallback? = null
    private val registrationGeneration = NativeGenerationGate()
    private val mainHandler = Handler(Looper.getMainLooper())
    @Volatile private var updatesWanted = false
    private var registrationRetryAttempt = 0
    private var registrationRetry: Runnable? = null
    private var registrationWatchdog: Runnable? = null
    private var registrationTaskTimeout: Runnable? = null
    @Volatile private var lastLocationResultElapsedMs = 0L
    @Volatile private var reportedProviderAvailable = false
    @Volatile var latestFix: GpsFix? = null
        private set

    val gpsTrack: MutableList<JSONArray> = Collections.synchronizedList(mutableListOf())
    private val fixHistory = NativeGpsFixHistory()
    private var startedAtMs: Long = 0L

    companion object {
        const val TARGET_SPACING_M = 6.0
        const val MIN_CAPTURE_MS = 500L
        const val MAX_CAPTURE_MS = 1_500L
        const val PARKED_CAPTURE_MS = 8000L
        const val FALLBACK_CAPTURE_MS = 750L
        const val GPS_COARSE_M = 30f
        const val GPS_MAX_AGE_MS = 10_000L
        private const val MAX_TRACK_POINTS = 20_000
        private val REGISTRATION_RETRY_DELAYS_MS = longArrayOf(1_000L, 3_000L, 10_000L, 30_000L)
        private const val CALLBACK_STALL_MS = 30_000L
        private const val CALLBACK_WATCHDOG_INTERVAL_MS = 5_000L
        private const val REGISTRATION_TASK_TIMEOUT_MS = 15_000L
        // One immediate attempt plus four bounded retries (1s, 3s, 10s, 30s). This is
        // long enough to survive transient Play Services/Binder recovery without keeping
        // an ignored high-accuracy callback alive indefinitely on common OEM builds.
        private const val REMOVE_RETRY_LIMIT = 4
    }

    @SuppressLint("MissingPermission")
    fun startUpdates(startedAt: Long, resetTrack: Boolean = true) {
        startedAtMs = startedAt
        if (resetTrack) gpsTrack.clear()
        updatesWanted = true
        replaceRegistration()
    }

    @SuppressLint("MissingPermission")
    private fun replaceRegistration() {
        cancelRegistrationRetry()
        cancelRegistrationWatchdog()
        cancelRegistrationTaskTimeout()
        val previous = locationCallback
        locationCallback = null
        val generation = registrationGeneration.issue()
        lastLocationResultElapsedMs = SystemClock.elapsedRealtime()
        previous?.let(::removeRegistrationBestEffort)
        reportedProviderAvailable = false
        latestFix = null
        fixHistory.clear()

        val initial = captureAccess()
        if (!initial.permissionGranted || !initial.servicesEnabled) {
            onAvailabilityChange(initial)
            return
        }

        val locationRequest = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 1000L)
            .setMinUpdateIntervalMillis(500L)
            .setMinUpdateDistanceMeters(1.0f)
            .build()

        lateinit var callback: LocationCallback
        callback = object : LocationCallback() {
            override fun onLocationAvailability(availability: LocationAvailability) {
                if (!acceptRegistrationCallback(generation, callback)) return
                reportedProviderAvailable = availability.isLocationAvailable
                val providerUsable = NativeLocationAvailabilityPolicy.providerIsUsable(
                    reportedAvailable = reportedProviderAvailable,
                    latestFixTimestampMs = latestFix?.elapsedRealtimeMs,
                    nowMs = SystemClock.elapsedRealtime(),
                    maxAgeMs = GPS_MAX_AGE_MS
                )
                if (!providerUsable) latestFix = null
                onAvailabilityChange(captureAccess())
            }

            override fun onLocationResult(result: LocationResult) {
                if (!acceptRegistrationCallback(generation, callback)) return
                val loc: Location = result.lastLocation ?: return
                lastLocationResultElapsedMs = NativeLocationResultWatchdogPolicy.noteRealResult(
                    previousResultElapsedMs = lastLocationResultElapsedMs,
                    callbackElapsedMs = SystemClock.elapsedRealtime(),
                    deliveredLocation = true
                )
                val fix = GpsFix(
                    lat = loc.latitude,
                    lng = loc.longitude,
                    accuracy = if (loc.hasAccuracy()) loc.accuracy else null,
                    speedMps = if (loc.hasSpeed() && loc.speed.isFinite()) loc.speed else null,
                    heading = if (loc.hasBearing() && loc.bearing.isFinite())
                        (loc.bearing % 360f + 360f) % 360f else null,
                    timestampMs = if (loc.time > 0) loc.time else System.currentTimeMillis(),
                    elapsedRealtimeMs = loc.elapsedRealtimeNanos.takeIf { it > 0L }
                        ?.div(1_000_000L) ?: SystemClock.elapsedRealtime()
                )
                reportedProviderAvailable = true
                registrationRetryAttempt = 0
                cancelRegistrationRetry()
                // A single coarse urban fix must not tear down/rebind the whole CameraX
                // graph. Retain the last capture-ready fix for the existing 10-second
                // grace, while the strict frame/fix gate still rejects evidence >2.5 s away.
                if (NativeCaptureFixQualityPolicy.isUsable(
                        fix,
                        SystemClock.elapsedRealtime(),
                        GPS_MAX_AGE_MS,
                        GPS_COARSE_M
                    )
                ) latestFix = fix
                fixHistory.add(fix)

                val offsetS = ((System.currentTimeMillis() - startedAtMs) / 100.0).toInt() / 10.0
                val trackItem = JSONArray()
                trackItem.put(offsetS)
                trackItem.put(loc.latitude)
                trackItem.put(loc.longitude)
                trackItem.put(fix.accuracy?.toDouble() ?: JSONObject.NULL)
                trackItem.put(fix.speedMps?.toDouble() ?: JSONObject.NULL)
                trackItem.put(fix.heading?.toDouble() ?: JSONObject.NULL)
                if (gpsTrack.size < MAX_TRACK_POINTS) gpsTrack.add(trackItem)

                onLocationUpdate(fix)
                onAvailabilityChange(captureAccess())
            }
        }
        locationCallback = callback

        try {
            scheduleRegistrationTaskTimeout(generation, callback)
            fusedClient.requestLocationUpdates(
                locationRequest,
                callback,
                Looper.getMainLooper()
            ).addOnSuccessListener {
                if (!updatesWanted || !registrationGeneration.isCurrent(generation) ||
                    locationCallback !== callback
                ) {
                    // Removing before a slow Task succeeds can be a no-op. Remove again
                    // after late success so an ignored callback cannot keep GPS active.
                    removeRegistrationBestEffort(callback)
                } else {
                    cancelRegistrationTaskTimeout()
                    ensureRegistrationWatchdog(generation, callback)
                }
            }.addOnFailureListener {
                handleRegistrationFailure(generation, callback)
            }
            // Registration is not a fix. Keep capture closed until Fused Location reports
            // availability and supplies a fresh timestamped location.
            onAvailabilityChange(captureAccess())
        } catch (_: SecurityException) {
            handleRegistrationFailure(generation, callback)
        } catch (_: Exception) {
            handleRegistrationFailure(generation, callback)
        }
    }

    private fun handleRegistrationFailure(generation: Long, callback: LocationCallback) {
        if (!registrationGeneration.isCurrent(generation) || locationCallback !== callback) return
        // The request Task can time out after Play Services has actually installed the
        // callback. Remove it before retrying; a late Task success also repeats removal.
        registrationGeneration.invalidate()
        locationCallback = null
        cancelRegistrationWatchdog()
        cancelRegistrationTaskTimeout()
        removeRegistrationBestEffort(callback)
        reportedProviderAvailable = false
        latestFix = null
        fixHistory.clear()
        onAvailabilityChange(captureAccess())
        scheduleRegistrationRetry()
    }

    /**
     * Any callback proves that Play Services installed this exact registration even when
     * its Task has not completed yet. Availability-only callbacks deliberately do not
     * advance the last real location-result time or postpone the already-armed watchdog.
     */
    private fun acceptRegistrationCallback(
        generation: Long,
        callback: LocationCallback
    ): Boolean {
        if (!updatesWanted || !registrationGeneration.isCurrent(generation) ||
            locationCallback !== callback
        ) return false
        cancelRegistrationTaskTimeout()
        ensureRegistrationWatchdog(generation, callback)
        return true
    }

    private fun scheduleRegistrationRetry() {
        if (!updatesWanted || locationCallback != null || registrationRetry != null) return
        val delayMs = REGISTRATION_RETRY_DELAYS_MS[
            registrationRetryAttempt.coerceAtMost(REGISTRATION_RETRY_DELAYS_MS.lastIndex)
        ]
        registrationRetryAttempt++
        val retry = Runnable {
            registrationRetry = null
            if (!updatesWanted || locationCallback != null) return@Runnable
            val access = captureAccess()
            if (access.permissionGranted && access.servicesEnabled) replaceRegistration()
            else onAvailabilityChange(access)
        }
        registrationRetry = retry
        mainHandler.postDelayed(retry, delayMs)
    }

    private fun cancelRegistrationRetry() {
        registrationRetry?.let(mainHandler::removeCallbacks)
        registrationRetry = null
    }

    private fun ensureRegistrationWatchdog(generation: Long, callback: LocationCallback) {
        if (registrationWatchdog != null) return
        lateinit var watchdog: Runnable
        watchdog = Runnable {
            if (!updatesWanted || !registrationGeneration.isCurrent(generation) ||
                locationCallback !== callback
            ) return@Runnable
            if (NativeLocationResultWatchdogPolicy.isStalled(
                    lastResultElapsedMs = lastLocationResultElapsedMs,
                    nowMs = SystemClock.elapsedRealtime(),
                    timeoutMs = CALLBACK_STALL_MS
                )) {
                replaceRegistration()
            } else {
                registrationWatchdog = watchdog
                mainHandler.postDelayed(watchdog, CALLBACK_WATCHDOG_INTERVAL_MS)
            }
        }
        registrationWatchdog = watchdog
        mainHandler.postDelayed(watchdog, CALLBACK_WATCHDOG_INTERVAL_MS)
    }

    private fun cancelRegistrationWatchdog() {
        registrationWatchdog?.let(mainHandler::removeCallbacks)
        registrationWatchdog = null
    }

    private fun scheduleRegistrationTaskTimeout(generation: Long, callback: LocationCallback) {
        cancelRegistrationTaskTimeout()
        val timeout = Runnable {
            registrationTaskTimeout = null
            handleRegistrationFailure(generation, callback)
        }
        registrationTaskTimeout = timeout
        mainHandler.postDelayed(timeout, REGISTRATION_TASK_TIMEOUT_MS)
    }

    private fun cancelRegistrationTaskTimeout() {
        registrationTaskTimeout?.let(mainHandler::removeCallbacks)
        registrationTaskTimeout = null
    }

    private fun removeRegistrationBestEffort(callback: LocationCallback, attempt: Int = 0) {
        val removal = runCatching { fusedClient.removeLocationUpdates(callback) }.getOrNull()
        if (removal == null) {
            if (attempt < REMOVE_RETRY_LIMIT) mainHandler.postDelayed(
                { removeRegistrationBestEffort(callback, attempt + 1) },
                REGISTRATION_RETRY_DELAYS_MS[attempt]
            )
            return
        }
        removal.addOnFailureListener {
            if (attempt < REMOVE_RETRY_LIMIT) mainHandler.postDelayed(
                { removeRegistrationBestEffort(callback, attempt + 1) },
                REGISTRATION_RETRY_DELAYS_MS[attempt]
            )
        }
    }

    fun stopUpdates() {
        updatesWanted = false
        cancelRegistrationRetry()
        cancelRegistrationWatchdog()
        cancelRegistrationTaskTimeout()
        registrationGeneration.invalidate()
        val callback = locationCallback
        locationCallback = null
        callback?.let(::removeRegistrationBestEffort)
        reportedProviderAvailable = false
        latestFix = null
        fixHistory.clear()
    }

    @SuppressLint("MissingPermission")
    fun resumeUpdates() {
        updatesWanted = true
        if (locationCallback == null) replaceRegistration()
    }

    @SuppressLint("MissingPermission")
    fun restartUpdates() {
        updatesWanted = true
        replaceRegistration()
    }

    fun captureAccess(nowMs: Long = SystemClock.elapsedRealtime()): NativeLocationAccess {
        val permissionGranted = hasLocationPermission()
        val servicesEnabled = locationServicesEnabled()
        val fix = latestFix
        val freshFix = NativeCaptureFixQualityPolicy.isUsable(
            fix = fix,
            nowMs = nowMs,
            // The provider may remain usable briefly between callbacks, but capture must
            // close at the same 2.5 s boundary used by burst pairing. Keeping these two
            // gates aligned prevents the camera/video graph staying open while every
            // candidate frame is guaranteed to be rejected later as GPS-stale.
            maxAgeMs = NativeBurstAccessPolicy.MAX_FIX_PRIMARY_DELTA_MS,
            maxAccuracyM = GPS_COARSE_M
        )
        val providerUsable = NativeLocationAvailabilityPolicy.providerIsUsable(
            reportedAvailable = reportedProviderAvailable,
            latestFixTimestampMs = fix?.elapsedRealtimeMs,
            nowMs = nowMs,
            maxAgeMs = GPS_MAX_AGE_MS
        )
        return NativeLocationAccess(
            permissionGranted = permissionGranted,
            servicesEnabled = servicesEnabled,
            providerAvailable = providerUsable,
            freshFixAvailable = freshFix
        )
    }

    fun fixNearestToElapsed(elapsedRealtimeMs: Long): GpsFix? =
        fixHistory.nearestCaptureReady(elapsedRealtimeMs, GPS_COARSE_M)

    private fun hasLocationPermission(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    private fun locationServicesEnabled(): Boolean {
        val manager = context.getSystemService(Context.LOCATION_SERVICE) as? LocationManager
            ?: return false
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) manager.isLocationEnabled
            else manager.isProviderEnabled(LocationManager.GPS_PROVIDER) ||
                manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)
        } catch (_: Exception) {
            false
        }
    }

    fun shouldTriggerCapture(
        lastCapPos: GpsFix?,
        lastCapTimeMs: Long,
        currentFix: GpsFix
    ): Boolean {
        val now = SystemClock.elapsedRealtime()
        val sinceLast = now - lastCapTimeMs
        val isFresh = NativeLocationAvailabilityPolicy.isFreshFix(
            currentFix.elapsedRealtimeMs,
            now,
            NativeBurstAccessPolicy.MAX_FIX_PRIMARY_DELTA_MS
        )
        if (!isFresh) return false

        val accurateFix = currentFix.accuracy?.let { it.isFinite() && it <= GPS_COARSE_M } == true
        val cadenceMs = NativeCaptureCadencePolicy.intervalMs(currentFix.speedMps, accurateFix)

        if (lastCapPos == null) return true

        val moved = NativeDeduplicationEngine.distMeters(
            lastCapPos.lat, lastCapPos.lng,
            currentFix.lat, currentFix.lng
        )

        return moved >= TARGET_SPACING_M || sinceLast >= cadenceMs
    }
}
