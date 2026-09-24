package com.gauravsen.potholereporter.drivemode

import android.annotation.SuppressLint
import android.content.Context
import android.os.Looper
import com.google.android.gms.location.*

class LocationTracker(
    private val context: Context,
    private val driveSession: DriveSession
) {

    private val fusedLocationClient: FusedLocationProviderClient =
        LocationServices.getFusedLocationProviderClient(context)

    private var isTracking = false

    private val locationCallback = object : LocationCallback() {
        override fun onLocationResult(locationResult: LocationResult) {
            val location = locationResult.lastLocation
            if (location != null) {
                driveSession.currentLocation = location
                // A cached fix does not become fresh when Android delivers it again.
                driveSession.locationFreshAtMs = location.time
            }
        }
    }

    @SuppressLint("MissingPermission")
    fun start() {
        if (isTracking) return

        val locationRequest = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 1000L)
            .setMinUpdateIntervalMillis(500L)
            .setWaitForAccurateLocation(false)
            .setMaxUpdateDelayMillis(0L)
            .build()

        fusedLocationClient.requestLocationUpdates(
            locationRequest,
            locationCallback,
            Looper.getMainLooper()
        )
        isTracking = true
    }

    fun stop() {
        if (!isTracking) return
        fusedLocationClient.removeLocationUpdates(locationCallback)
        isTracking = false
    }
}
