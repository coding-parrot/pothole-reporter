package dev.aiengg.potholereporter.drive

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.Looper
import androidx.core.content.ContextCompat
import com.google.android.gms.location.*
import org.json.JSONArray
import org.json.JSONObject
import java.util.Collections
import kotlin.math.max
import kotlin.math.min

data class GpsFix(
    val lat: Double,
    val lng: Double,
    val accuracy: Float?,
    val speedMps: Float?,
    val heading: Float?,
    val timestampMs: Long
)

/**
 * Fused Location's availability flag is a forecast, not a revocation of a location it
 * just delivered. Keep a genuinely fresh fix usable for the same bounded window enforced
 * by captureAccess; a stale or absent fix still closes capture.
 */
internal object NativeLocationAvailabilityPolicy {
    private const val MAX_FUTURE_SKEW_MS = 60_000L

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

/** Capture requires a fresh, finite fix accurate enough for road-level routing. */
internal object NativeCaptureFixQualityPolicy {
    fun isUsable(fix: GpsFix?, nowMs: Long, maxAgeMs: Long, maxAccuracyM: Float): Boolean {
        val value = fix ?: return false
        val accuracy = value.accuracy ?: return false
        return value.lat.isFinite() && value.lat in -90.0..90.0 &&
            value.lng.isFinite() && value.lng in -180.0..180.0 &&
            accuracy.isFinite() && accuracy in 0f..maxAccuracyM &&
            NativeLocationAvailabilityPolicy.isFreshFix(value.timestampMs, nowMs, maxAgeMs)
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
    @Volatile private var reportedProviderAvailable = false
    @Volatile var latestFix: GpsFix? = null
        private set

    val gpsTrack: MutableList<JSONArray> = Collections.synchronizedList(mutableListOf())
    private val fixHistory = NativeGpsFixHistory()
    private var startedAtMs: Long = 0L

    companion object {
        const val TARGET_SPACING_M = 6.0
        const val MOVING_MPS = 1.0f
        const val MIN_CAPTURE_MS = 500L
        const val MAX_CAPTURE_MS = 1_500L
        const val PARKED_CAPTURE_MS = 8000L
        const val FALLBACK_CAPTURE_MS = 750L
        const val GPS_COARSE_M = 30f
        const val GPS_MAX_AGE_MS = 10_000L
        private const val MAX_TRACK_POINTS = 20_000
    }

    @SuppressLint("MissingPermission")
    fun startUpdates(startedAt: Long, resetTrack: Boolean = true) {
        startedAtMs = startedAt
        if (resetTrack) gpsTrack.clear()
        stopUpdates()

        val initial = captureAccess()
        if (!initial.permissionGranted || !initial.servicesEnabled) {
            onAvailabilityChange(initial)
            return
        }

        val locationRequest = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 1000L)
            .setMinUpdateIntervalMillis(500L)
            .setMinUpdateDistanceMeters(1.0f)
            .build()

        locationCallback = object : LocationCallback() {
            override fun onLocationAvailability(availability: LocationAvailability) {
                reportedProviderAvailable = availability.isLocationAvailable
                val providerUsable = NativeLocationAvailabilityPolicy.providerIsUsable(
                    reportedAvailable = reportedProviderAvailable,
                    latestFixTimestampMs = latestFix?.timestampMs,
                    nowMs = System.currentTimeMillis(),
                    maxAgeMs = GPS_MAX_AGE_MS
                )
                if (!providerUsable) latestFix = null
                onAvailabilityChange(captureAccess())
            }

            override fun onLocationResult(result: LocationResult) {
                val loc: Location = result.lastLocation ?: return
                val fix = GpsFix(
                    lat = loc.latitude,
                    lng = loc.longitude,
                    accuracy = if (loc.hasAccuracy()) loc.accuracy else null,
                    speedMps = if (loc.hasSpeed() && !loc.speed.isNaN()) loc.speed else null,
                    heading = if (loc.hasBearing() && !loc.bearing.isNaN()) (loc.bearing % 360f + 360f) % 360f else null,
                    timestampMs = if (loc.time > 0) loc.time else System.currentTimeMillis()
                )
                latestFix = fix
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

        try {
            fusedClient.requestLocationUpdates(
                locationRequest,
                locationCallback!!,
                Looper.getMainLooper()
            ).addOnFailureListener {
                reportedProviderAvailable = false
                latestFix = null
                onAvailabilityChange(captureAccess())
            }
            // Registration is not a fix. Keep capture closed until Fused Location reports
            // availability and supplies a fresh timestamped location.
            onAvailabilityChange(captureAccess())
        } catch (_: SecurityException) {
            locationCallback = null
            reportedProviderAvailable = false
            latestFix = null
            onAvailabilityChange(captureAccess())
        } catch (_: Exception) {
            locationCallback = null
            reportedProviderAvailable = false
            latestFix = null
            onAvailabilityChange(captureAccess())
        }
    }

    fun stopUpdates() {
        locationCallback?.let {
            fusedClient.removeLocationUpdates(it)
            locationCallback = null
        }
        reportedProviderAvailable = false
        latestFix = null
        fixHistory.clear()
    }

    @SuppressLint("MissingPermission")
    fun resumeUpdates() {
        if (locationCallback == null) startUpdates(startedAtMs, resetTrack = false)
    }

    @SuppressLint("MissingPermission")
    fun restartUpdates() {
        startUpdates(startedAtMs, resetTrack = false)
    }

    fun captureAccess(nowMs: Long = System.currentTimeMillis()): NativeLocationAccess {
        val permissionGranted = hasLocationPermission()
        val servicesEnabled = locationServicesEnabled()
        val fix = latestFix
        val freshFix = NativeCaptureFixQualityPolicy.isUsable(
            fix = fix,
            nowMs = nowMs,
            maxAgeMs = GPS_MAX_AGE_MS,
            maxAccuracyM = GPS_COARSE_M
        )
        val providerUsable = NativeLocationAvailabilityPolicy.providerIsUsable(
            reportedAvailable = reportedProviderAvailable,
            latestFixTimestampMs = fix?.timestampMs,
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

    fun fixNearestTo(timestampMs: Long): GpsFix? =
        fixHistory.nearestCaptureReady(timestampMs, GPS_COARSE_M)

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
        val now = System.currentTimeMillis()
        val sinceLast = now - lastCapTimeMs
        val isFresh = (now - currentFix.timestampMs) <= GPS_MAX_AGE_MS
        if (!isFresh) return false

        val coarse = currentFix.accuracy == null || currentFix.accuracy > GPS_COARSE_M
        val knownStill = currentFix.speedMps != null && currentFix.speedMps <= MOVING_MPS && !coarse
        val speed = currentFix.speedMps ?: 0f

        val cadenceMs = if (knownStill) {
            PARKED_CAPTURE_MS
        } else if (speed > MOVING_MPS) {
            val dynamic = (TARGET_SPACING_M / speed * 1000.0).toLong()
            max(MIN_CAPTURE_MS, min(MAX_CAPTURE_MS, dynamic))
        } else {
            FALLBACK_CAPTURE_MS
        }

        if (lastCapPos == null) return true

        val moved = NativeDeduplicationEngine.distMeters(
            lastCapPos.lat, lastCapPos.lng,
            currentFix.lat, currentFix.lng
        )

        return moved >= TARGET_SPACING_M || sinceLast >= cadenceMs
    }
}
