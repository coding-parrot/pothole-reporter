package com.gauravsen.potholereporter.drive

import android.annotation.SuppressLint
import android.content.Context
import android.location.Location
import android.os.Looper
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

class NativeDriveLocationProvider(
    private val context: Context,
    private val onLocationUpdate: (GpsFix) -> Unit
) {
    private val fusedClient: FusedLocationProviderClient =
        LocationServices.getFusedLocationProviderClient(context)

    private var locationCallback: LocationCallback? = null
    var latestFix: GpsFix? = null
        private set

    val gpsTrack: MutableList<JSONArray> = Collections.synchronizedList(mutableListOf())
    private var startedAtMs: Long = 0L

    companion object {
        const val TARGET_SPACING_M = 6.0
        const val MOVING_MPS = 1.0f
        const val MIN_CAPTURE_MS = 750L
        const val MAX_CAPTURE_MS = 2_000L
        const val PARKED_CAPTURE_MS = 8000L
        const val FALLBACK_CAPTURE_MS = 1_000L
        const val GPS_COARSE_M = 30f
        const val GPS_MAX_AGE_MS = 10_000L
        private const val MAX_TRACK_POINTS = 20_000
    }

    @SuppressLint("MissingPermission")
    fun startUpdates(startedAt: Long, resetTrack: Boolean = true) {
        startedAtMs = startedAt
        if (resetTrack) gpsTrack.clear()
        stopUpdates()

        val locationRequest = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 1000L)
            .setMinUpdateIntervalMillis(500L)
            .setMinUpdateDistanceMeters(1.0f)
            .build()

        locationCallback = object : LocationCallback() {
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
            }
        }

        fusedClient.requestLocationUpdates(
            locationRequest,
            locationCallback!!,
            Looper.getMainLooper()
        )
    }

    fun stopUpdates() {
        locationCallback?.let {
            fusedClient.removeLocationUpdates(it)
            locationCallback = null
        }
    }

    @SuppressLint("MissingPermission")
    fun resumeUpdates() {
        if (locationCallback == null) startUpdates(startedAtMs, resetTrack = false)
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
